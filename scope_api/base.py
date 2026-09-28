"""Herstellerneutrale Oszilloskop-Schnittstelle.

Alles hier ist in physikalischen Einheiten (V, s, Hz, %) -- Gerätecodes wie
Spannungsbereich-Nummern, Timebase-Indizes oder ADC-Counts kennt nur der
jeweilige Adapter (z.B. pico2000_adapter.py). Damit bleiben Auswertung,
Erfassungsspeicher, Netzwerk-Schnittstelle und MCP-Werkzeuge unverändert, wenn
ein zweites Oszilloskop (geplant: SCPI-Tischgerät per LAN) dazukommt.

Grundsätze (siehe Umsetzungsvorschlag vom 2026-09-28):
- Fähigkeiten zuerst: Capabilities beschreibt, was ein Gerät kann; wer eine
  Erfassung plant, richtet sich danach statt nach dem Modellnamen.
- Zustandslose Erfassung: jede AcquireRequest trägt die vollständige
  Einstellung. Zwischen zwei Erfassungen kann PicoScope 7 oder ein Testablauf
  am Gerät gewesen sein -- auf einen verbliebenen Gerätezustand verlässt sich
  niemand.
- Tatsächliche Werte zurückmelden: der Adapter wählt den nächsten passenden
  Bereich bzw. die nächste Zeitbasis und schreibt die eingestellten Werte in
  die Capture, nicht die angefragten.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import numpy as np

TRIGGER_MODES = ("none", "auto", "single")
TRIGGER_EDGES = ("rising", "falling")
COUPLINGS = ("DC", "AC")

# Obergrenze für das Warten auf einen Trigger -- hält den Scope-Thread nicht
# beliebig lange fest (ALLE AUS und andere Anfragen warten sonst mit).
MAX_TRIGGER_TIMEOUT_S = 30.0


class ScopeError(RuntimeError):
    """Fehler mit stabilem Code (für HTTP-Antworten und MCP-Hinweise).

    Codes: invalid_request, setting_not_supported, level_out_of_range,
    duration_too_long, trigger_timeout, device_error, unknown_capture,
    unknown_measurement. `details` landet unverändert in der Antwort, z.B. der
    nächste erlaubte Wert.
    """

    def __init__(self, code: str, message: str, **details) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.details}


# -- Fähigkeiten ---------------------------------------------------------------


@dataclass(frozen=True)
class ChannelCaps:
    name: str
    # Vollausschlag +-V am Scope-Eingang (ohne Tastkopf), aufsteigend.
    ranges_v: tuple[float, ...]
    couplings: tuple[str, ...] = COUPLINGS
    # Analoger Offset einstellbar? (2204A nein, viele Tischgeräte ja)
    offset: bool = False


@dataclass(frozen=True)
class Capabilities:
    vendor: str
    model: str
    serial: str
    channels: tuple[ChannelCaps, ...]
    max_sample_rate_hz: float
    # Erfassungsspeicher in Samples insgesamt; der tatsächlich nutzbare Wert je
    # Kanalkonfiguration kann kleiner sein und steht in der Capture.
    memory_samples: int
    bandwidth_hz: float
    resolution_bits: int
    trigger_modes: tuple[str, ...] = TRIGGER_MODES
    trigger_types: tuple[str, ...] = ("edge",)
    trigger_sources: tuple[str, ...] = ()
    max_pre_trigger_pct: float = 100.0
    max_trigger_timeout_s: float = MAX_TRIGGER_TIMEOUT_S
    signal_generator: bool = False
    notes: tuple[str, ...] = ()

    def channel(self, name: str) -> ChannelCaps | None:
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "model": self.model,
            "serial": self.serial,
            "channels": [
                {"name": c.name, "ranges_v": list(c.ranges_v), "couplings": list(c.couplings),
                 "offset": c.offset}
                for c in self.channels
            ],
            "max_sample_rate_hz": self.max_sample_rate_hz,
            "memory_samples": self.memory_samples,
            "bandwidth_hz": self.bandwidth_hz,
            "resolution_bits": self.resolution_bits,
            "trigger": {
                "modes": list(self.trigger_modes),
                "types": list(self.trigger_types),
                "sources": list(self.trigger_sources),
                "max_pre_trigger_pct": self.max_pre_trigger_pct,
                "max_timeout_s": self.max_trigger_timeout_s,
            },
            "signal_generator": self.signal_generator,
            "notes": list(self.notes),
        }


# -- Anfrage -------------------------------------------------------------------


@dataclass
class ChannelSetting:
    name: str
    # Gewünschter Vollausschlag +-V an der Tastkopfspitze. Der Adapter nimmt
    # den kleinsten Gerätebereich, der das abdeckt.
    range_v: float
    coupling: str = "DC"
    # Tastkopfteiler (1 oder 10 ...): wird nur umgerechnet, das Gerät erfährt
    # davon nichts.
    probe: float = 1.0
    offset_v: float = 0.0


@dataclass
class Timing:
    # Genau eine der beiden Angaben ist Pflicht: Dauer (Adapter wählt die
    # feinste Zeitbasis, bei der die Dauer in den Speicher passt) oder
    # Abtastintervall (Adapter wählt das gröbste Intervall <= Anfrage).
    duration_s: float | None = None
    sample_interval_s: float | None = None
    # Optional: feste Sample-Zahl (sonst so viele wie nötig bzw. möglich).
    samples: int | None = None
    pre_trigger_pct: float = 0.0


@dataclass
class Trigger:
    # none: freilaufend. auto: auf Trigger warten, nach timeout_s trotzdem
    # erfassen. single: nur mit Trigger, sonst Fehler trigger_timeout.
    mode: str = "auto"
    source: str | None = None
    level_v: float = 0.0
    edge: str = "rising"
    timeout_s: float = 1.0


@dataclass
class AcquireRequest:
    channels: list[ChannelSetting]
    timing: Timing
    trigger: Trigger = field(default_factory=Trigger)

    @classmethod
    def from_dict(cls, data: dict) -> "AcquireRequest":
        """Baut eine Anfrage aus JSON (HTTP/MCP) und prüft Form und Typen.

        Geräteabhängige Grenzen (Bereiche, Speicher) prüft erst der Adapter.
        """
        if not isinstance(data, dict):
            raise ScopeError("invalid_request", "Anfrage muss ein JSON-Objekt sein.")
        raw_channels = data.get("channels")
        if not isinstance(raw_channels, list) or not raw_channels:
            raise ScopeError("invalid_request", "'channels' muss eine nicht leere Liste sein.")
        channels: list[ChannelSetting] = []
        seen: set[str] = set()
        for raw in raw_channels:
            if not isinstance(raw, dict):
                raise ScopeError("invalid_request", "Jeder Kanal muss ein Objekt sein.")
            name = str(raw.get("name", "")).upper()
            if not name or name in seen:
                raise ScopeError("invalid_request", f"Kanalname fehlt oder doppelt: {raw.get('name')!r}")
            seen.add(name)
            coupling = str(raw.get("coupling", "DC")).upper()
            if coupling not in COUPLINGS:
                raise ScopeError("invalid_request", f"Kopplung {coupling!r} unbekannt.", allowed=list(COUPLINGS))
            channels.append(ChannelSetting(
                name=name,
                range_v=_positive(raw.get("range_v"), f"channels[{name}].range_v"),
                coupling=coupling,
                probe=_positive(raw.get("probe", 1.0), f"channels[{name}].probe"),
                offset_v=_number(raw.get("offset_v", 0.0), f"channels[{name}].offset_v"),
            ))

        raw_timing = data.get("timing") or {}
        if not isinstance(raw_timing, dict):
            raise ScopeError("invalid_request", "'timing' muss ein Objekt sein.")
        duration = raw_timing.get("duration_s")
        interval = raw_timing.get("sample_interval_s")
        if (duration is None) == (interval is None):
            raise ScopeError("invalid_request",
                             "In 'timing' genau eines von 'duration_s' oder 'sample_interval_s' angeben.")
        samples = raw_timing.get("samples")
        if samples is not None:
            if isinstance(samples, bool) or not isinstance(samples, (int, float)) or int(samples) != samples \
                    or samples < 2:
                raise ScopeError("invalid_request", "'timing.samples' muss eine ganze Zahl >= 2 sein.")
            samples = int(samples)
        pre = _number(raw_timing.get("pre_trigger_pct", 0.0), "timing.pre_trigger_pct")
        if not 0.0 <= pre <= 100.0:
            raise ScopeError("invalid_request", "'timing.pre_trigger_pct' muss zwischen 0 und 100 liegen.")
        timing = Timing(
            duration_s=_positive(duration, "timing.duration_s") if duration is not None else None,
            sample_interval_s=_positive(interval, "timing.sample_interval_s") if interval is not None else None,
            samples=samples,
            pre_trigger_pct=pre,
        )

        raw_trigger = data.get("trigger") or {}
        if not isinstance(raw_trigger, dict):
            raise ScopeError("invalid_request", "'trigger' muss ein Objekt sein.")
        mode = str(raw_trigger.get("mode", "auto")).lower()
        if mode not in TRIGGER_MODES:
            raise ScopeError("invalid_request", f"Trigger-Modus {mode!r} unbekannt.", allowed=list(TRIGGER_MODES))
        edge = str(raw_trigger.get("edge", "rising")).lower()
        if edge not in TRIGGER_EDGES:
            raise ScopeError("invalid_request", f"Flanke {edge!r} unbekannt.", allowed=list(TRIGGER_EDGES))
        source = raw_trigger.get("source")
        source = str(source).upper() if source is not None else None
        if mode != "none":
            if source is None:
                source = channels[0].name
            if source not in seen:
                raise ScopeError("invalid_request",
                                 f"Trigger-Quelle {source!r} muss einer der erfassten Kanäle sein.",
                                 allowed=sorted(seen))
        timeout = _positive(raw_trigger.get("timeout_s", 1.0), "trigger.timeout_s")
        if timeout > MAX_TRIGGER_TIMEOUT_S:
            raise ScopeError("invalid_request", f"'trigger.timeout_s' höchstens {MAX_TRIGGER_TIMEOUT_S:g} s.",
                             max=MAX_TRIGGER_TIMEOUT_S)
        trigger = Trigger(
            mode=mode,
            source=source if mode != "none" else None,
            level_v=_number(raw_trigger.get("level_v", 0.0), "trigger.level_v"),
            edge=edge,
            timeout_s=timeout,
        )
        return cls(channels=channels, timing=timing, trigger=trigger)

    def to_dict(self) -> dict:
        return {
            "channels": [
                {"name": c.name, "range_v": c.range_v, "coupling": c.coupling, "probe": c.probe,
                 "offset_v": c.offset_v}
                for c in self.channels
            ],
            "timing": {
                "duration_s": self.timing.duration_s,
                "sample_interval_s": self.timing.sample_interval_s,
                "samples": self.timing.samples,
                "pre_trigger_pct": self.timing.pre_trigger_pct,
            },
            "trigger": {
                "mode": self.trigger.mode, "source": self.trigger.source, "level_v": self.trigger.level_v,
                "edge": self.trigger.edge, "timeout_s": self.trigger.timeout_s,
            },
        }


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ScopeError("invalid_request", f"'{name}' muss eine Zahl sein.")
    return float(value)


def _positive(value, name: str) -> float:
    number = _number(value, name)
    if number <= 0:
        raise ScopeError("invalid_request", f"'{name}' muss größer als 0 sein.")
    return number


# -- Ergebnis ------------------------------------------------------------------


@dataclass
class ChannelData:
    name: str
    # Tatsächlicher Vollausschlag +-V an der Tastkopfspitze (Gerätebereich x Tastkopf).
    range_v: float
    coupling: str
    probe: float
    # Messwerte in V an der Tastkopfspitze.
    volts: np.ndarray
    # Eingang übersteuert (Gerätemeldung oder Samples am ADC-Anschlag).
    overrange: bool = False


@dataclass
class Capture:
    scope_id: str
    model: str
    sample_interval_s: float
    # Index des Trigger-Zeitpunkts (t = 0) in den Samples.
    trigger_index: int
    # True/False wenn bekannt, None wenn nicht feststellbar (auto ohne Flanke im Bild).
    triggered: bool | None
    channels: dict[str, ChannelData]
    request: AcquireRequest
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="milliseconds"))
    capture_id: str = ""
    csv_path: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def samples(self) -> int:
        first = next(iter(self.channels.values()))
        return int(first.volts.size)

    @property
    def time_s(self) -> np.ndarray:
        return (np.arange(self.samples) - self.trigger_index) * self.sample_interval_s


class ScopeDriver(Protocol):
    """Was ein Adapter können muss. Aufrufe kommen aus EINEM Thread (ScopeService)."""

    scope_id: str

    def capabilities(self) -> Capabilities: ...

    def acquire(self, request: AcquireRequest) -> Capture: ...

    def close(self) -> None: ...

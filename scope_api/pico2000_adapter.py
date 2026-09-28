"""scope_api-Adapter für PicoScope 2204A/2205A (ps2000-API).

Übersetzt eine herstellerneutrale AcquireRequest in Gerätecodes des
picoscope2000-Treibers (oder dessen Mock) und die Rohdaten zurück in Volt:

- Bereich: kleinster 2204A-Bereich, der range_v / Tastkopf abdeckt.
- Zeitbasis: 2204A-Intervall ist 10 ns * 2**timebase; welche Timebase mit
  welcher Kanalzahl und Sample-Zahl geht, sagt ps2000_get_timebase -- die
  Suche fragt das Gerät, statt eine Tabelle zu pflegen.
- Trigger: Pegel in ADC-Counts, Pre-Trigger als negativer Delay in %, auto =
  Auto-Trigger-Timeout, single = unbegrenzt warten, aber nach timeout_s
  abbrechen (Fehler trigger_timeout).

Nicht unterstützt (setting_not_supported): Offset (hat das 2204A nicht),
Kanäle außer A/B, erweiterte Trigger (Pulsbreite, Fenster, ... -- später über
ps2000SetAdvTrigger*, siehe Umsetzungsvorschlag Phase 4).
"""
from __future__ import annotations

import math

import numpy as np

from picoscope2000.common import (
    CHANNEL_MAP,
    DIRECTION_CODES,
    MAX_ADC,
    TRIGGER_SOURCE_NONE,
    ChannelConfig,
    PicoScope2000Error,
    PicoScope2000TriggerTimeout,
    TriggerConfig,
)
from scope_api import analysis
from scope_api.base import (
    AcquireRequest,
    Capabilities,
    Capture,
    ChannelCaps,
    ChannelData,
    ScopeError,
    Timing,
)

# 2204A/2205A: +-50 mV bis +-20 V (der 20-mV-Code der ps2000-API gilt nur für
# andere Modelle der Serie).
RANGE_CODES = {0.05: 2, 0.1: 3, 0.2: 4, 0.5: 5, 1.0: 6, 2.0: 7, 5.0: 8, 10.0: 9, 20.0: 10}
RANGES_V = tuple(sorted(RANGE_CODES))
MAX_TIMEBASE = 30
# Zuschlag auf die Wartezeit für USB-Transfer und DLL (Blockerfassung selbst
# dauert Samples * Intervall).
WAIT_MARGIN_S = 2.0
_EPS = 1e-9


class PicoScope2000Scope:
    def __init__(self, device, variant: str, serial: str, simulated: bool = False) -> None:
        self._device = device
        self._variant = variant
        self._serial = serial
        self.simulated = simulated
        self.scope_id = f"scope:{serial}"

    # -- Fähigkeiten -----------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            vendor="Pico Technology",
            model=f"PicoScope {self._variant}" + (" (Simulation)" if self.simulated else ""),
            serial=self._serial,
            channels=(ChannelCaps("A", RANGES_V), ChannelCaps("B", RANGES_V)),
            max_sample_rate_hz=100e6,
            memory_samples=8000,
            bandwidth_hz=10e6,
            resolution_bits=8,
            trigger_sources=("A", "B"),
            max_pre_trigger_pct=100.0,
            signal_generator=True,
            notes=(
                "100 MS/s (10 ns) nur mit einem aktiven Kanal; mit zwei Kanälen ab 20 ns.",
                "Der Speicher (8 kS) wird von den aktiven Kanälen geteilt; die tatsächliche Sample-Zahl "
                "steht im Ergebnis.",
                "Nur Flanken-Trigger. Erweiterte Trigger und Signalgenerator folgen später.",
                "Kein analoger Offset.",
            ),
        )

    # -- Erfassung ---------------------------------------------------------------

    def acquire(self, request: AcquireRequest) -> Capture:
        warnings: list[str] = []
        configs: dict[str, ChannelConfig] = {}
        scope_ranges: dict[str, float] = {}
        for ch in request.channels:
            if ch.name not in CHANNEL_MAP:
                raise ScopeError("setting_not_supported", f"Kanal {ch.name!r} gibt es am {self._variant} nicht.",
                                 allowed=list(CHANNEL_MAP))
            if ch.offset_v != 0.0:
                raise ScopeError("setting_not_supported", f"Das {self._variant} hat keinen analogen Offset.",
                                 allowed=[0.0])
            needed = ch.range_v / ch.probe
            chosen = next((r for r in RANGES_V if r >= needed * (1 - _EPS)), None)
            if chosen is None:
                raise ScopeError("setting_not_supported",
                                 f"Bereich ±{ch.range_v:g} V zu groß für Kanal {ch.name} (Tastkopf {ch.probe:g}:1).",
                                 max_range_v=RANGES_V[-1] * ch.probe)
            scope_ranges[ch.name] = chosen
            configs[ch.name] = ChannelConfig(enabled=True, dc=ch.coupling == "DC", range_code=RANGE_CODES[chosen])

        try:
            self._device.configure_channels(configs)
            timebase, samples, interval_s = self._choose_timing(request.timing, warnings)
        except PicoScope2000Error as exc:
            raise ScopeError("device_error", str(exc)) from exc

        trig = request.trigger
        settings = {c.name: c for c in request.channels}
        if trig.mode == "none":
            trigger = TriggerConfig(source=TRIGGER_SOURCE_NONE)
            trigger_index = 0
            if request.timing.pre_trigger_pct:
                warnings.append("Pre-Trigger ohne Trigger (mode none) ignoriert.")
        else:
            src = settings[trig.source]
            full_scale = scope_ranges[trig.source]
            level_scope = trig.level_v / src.probe
            if abs(level_scope) >= full_scale:
                raise ScopeError("level_out_of_range",
                                 f"Trigger-Pegel {trig.level_v:g} V liegt außerhalb des Bereichs von Kanal "
                                 f"{trig.source} (±{full_scale * src.probe:g} V).",
                                 range_v=full_scale * src.probe)
            delay_pct = -int(round(request.timing.pre_trigger_pct))
            trigger = TriggerConfig(
                source=CHANNEL_MAP[trig.source],
                threshold_adc=int(round(level_scope / full_scale * MAX_ADC)),
                direction=DIRECTION_CODES[trig.edge],
                delay_pct=delay_pct,
                auto_trigger_ms=min(32767, max(1, int(round(trig.timeout_s * 1000)))) if trig.mode == "auto" else 0,
            )
            trigger_index = min(samples - 1, int(round(samples * -delay_pct / 100.0)))

        wait_s = samples * interval_s + WAIT_MARGIN_S + (trig.timeout_s if trig.mode != "none" else 0.0)
        try:
            raw = self._device.run_block_raw(trigger, timebase, samples, wait_s)
        except PicoScope2000TriggerTimeout as exc:
            raise ScopeError("trigger_timeout",
                             f"Kein Trigger auf Kanal {trig.source} ({trig.edge}, {trig.level_v:g} V) innerhalb "
                             f"von {trig.timeout_s:g} s.", timeout_s=trig.timeout_s) from exc
        except PicoScope2000Error as exc:
            raise ScopeError("device_error", str(exc)) from exc

        channels: dict[str, ChannelData] = {}
        for name, setting in settings.items():
            counts = np.asarray(raw.adc[name], dtype=float)
            full_scale = scope_ranges[name]
            channels[name] = ChannelData(
                name=name,
                range_v=full_scale * setting.probe,
                coupling=setting.coupling,
                probe=setting.probe,
                volts=counts / MAX_ADC * full_scale * setting.probe,
                overrange=bool(raw.overflow.get(name)) or bool(np.any(np.abs(counts) >= MAX_ADC)),
            )

        if trig.mode == "none":
            triggered = None
        elif trig.mode == "single":
            triggered = True
        else:
            triggered = analysis.trigger_seen(channels[trig.source].volts, trigger_index, trig.level_v, trig.edge)
            if not triggered:
                warnings.append("Kein Trigger erkannt: nach dem Auto-Timeout frei erfasst.")

        return Capture(
            scope_id=self.scope_id,
            model=self.capabilities().model,
            sample_interval_s=raw.sample_interval_ns * 1e-9,
            trigger_index=trigger_index,
            triggered=triggered,
            channels=channels,
            request=request,
            warnings=warnings,
        )

    def _choose_timing(self, timing: Timing, warnings: list[str]) -> tuple[int, int, float]:
        """(timebase, samples, intervall_s) passend zu Dauer oder Abtastintervall."""
        options = []
        for tb in range(MAX_TIMEBASE + 1):
            res = self._device.get_timebase(tb, 2)
            if res is not None:
                options.append((tb, res[0] * 1e-9, res[1]))
        if not options:
            raise ScopeError("device_error", "Das Gerät meldet keine gültige Zeitbasis.")

        if timing.duration_s is not None:
            for tb, interval, max_samples in options:
                if timing.samples is not None:
                    n = timing.samples
                    if interval * n < timing.duration_s * (1 - _EPS):
                        continue
                else:
                    n = max(2, math.ceil(timing.duration_s / interval * (1 - _EPS)))
                if n <= max_samples:
                    return self._confirm(tb, n, interval)
            longest = max(interval * max_samples for _, interval, max_samples in options)
            raise ScopeError("duration_too_long",
                             f"{timing.duration_s:g} s passen nicht in den Speicher"
                             + (f" mit {timing.samples} Samples" if timing.samples else "") + ".",
                             max_duration_s=analysis.num(longest))

        fitting = [o for o in options if o[1] <= timing.sample_interval_s * (1 + _EPS)]
        if not fitting:
            raise ScopeError("setting_not_supported",
                             f"Abtastintervall {timing.sample_interval_s:g} s ist feiner als möglich.",
                             min_interval_s=analysis.num(options[0][1]))
        tb, interval, max_samples = fitting[-1]
        n = timing.samples if timing.samples is not None else max_samples
        if n > max_samples:
            warnings.append(f"Nur {max_samples} Samples möglich (angefragt {n}).")
            n = max_samples
        return self._confirm(tb, n, interval)

    def _confirm(self, tb: int, n: int, interval: float) -> tuple[int, int, float]:
        res = self._device.get_timebase(tb, n)
        if res is None:
            raise ScopeError("device_error", f"Zeitbasis {tb} mit {n} Samples vom Gerät abgelehnt.")
        return tb, n, res[0] * 1e-9

    def close(self) -> None:
        self._device.close()


def open_pico2000(simulate: bool = False) -> PicoScope2000Scope:
    """Öffnet das erste freie 2204A (oder den Mock). Dauert real ~4,5 s."""
    if simulate:
        from picoscope2000.mock import MockPicoScope2000 as device_cls
    else:
        from picoscope2000.driver import PicoScope2000 as device_cls
    try:
        device = device_cls.open_first()
    except PicoScope2000Error as exc:
        raise ScopeError("device_error", str(exc)) from exc
    try:
        info = device.get_info()
    except PicoScope2000Error as exc:
        device.close()
        raise ScopeError("device_error", str(exc)) from exc
    return PicoScope2000Scope(device, info.variant, info.serial, simulated=simulate)

"""scope_api-Adapter für R&S RTB2000 (RTB2002, RTB2004) über SCPI.

Übersetzt eine herstellerneutrale AcquireRequest in SCPI-Einstellungen
(device-driver/rs_rtb2000/driver.py) und die Rohdaten zurück:

- Bereich: Volt/Teilung = kleinste 1-2-5-Stufe am Eingang, die range_v /
  Tastkopf mit ±5 Teilungen abdeckt (10 Teilungen hoch, am RTB2002 gemessen;
  das Handbuch legt 8 nahe). Danach wird CHANnel:RANGe? zurückgelesen:
  gemeldet wird der tatsächliche Vollausschlag RANGe / 2.
- Offset: R&S zieht CHANnel:OFFSet vom Signal ab (S. 254), die Bildschirmmitte
  liegt also bei +OFFSet -- anders als beim UNI-T wird offset_v unverändert
  gesetzt.
- Zeitbasis: s/Teilung = kleinste 1-2-5-Stufe, bei der TIMebase:DIVisions?
  (laut Handbuch 12) Teilungen die Dauer abdecken. Aufzeichnungslänge
  (10k..10M) aus der gewünschten Sample-Zahl, hochgerechnet auf das ganze
  Fenster.
- Pre-Trigger: Referenzpunkt in der Mitte, TIMebase:POSition = Abstand
  Trigger -> Mitte, also Position = Fenster * (0,5 - pre_trigger_pct / 100).
- Trigger: Einzelerfassung (TRIGger:A:MODE NORMal + SINGle), dann
  ACQuire:STATe? pollen. auto: nach timeout_s *TRG; none: sofort *TRG;
  single: nach timeout_s STOP und Fehler trigger_timeout.
- Daten: REAL,32 in Volt, Zeitachse aus CHANnel:DATA:HEADer? -- der Index
  des Triggers (t = 0) ergibt sich aus XStart, ohne Annahme über das Fenster,
  ab 200k Punkten verschoben um den Nachlauf der Daten
  (driver.trigger_delay_points, Firmware 02.202 gemessen).

Das Gerät bleibt nach einer Erfassung gestoppt und zeigt sie an.

Am RTB2002 (Firmware 01.210 und 02.202) bestätigt: Teilungen 12 x 10, Vorzeichen
von TIMebase:POSition und CHANnel:OFFSet, Ablauf *OPC? -> SINGle -> ACQuire:STATe?
"COMP", Trigger auf ±2 ns an der Flanke (mit abgeschalteten Triggerfiltern) bis
100k Punkte, darüber nach Ausgleich des Nachlaufs auf ±5 Punkte.
Offen (HARDWARE-PRÜFUNG, siehe README des Treibers): Bezug von CHANnel:SCALe
auf den Tastkopf (nur 1:1 geprüft).
"""
from __future__ import annotations

import logging
import math
import time

import numpy as np

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
from rs_rtb2000.driver import (
    RTB2000,
    RTB2000Error,
    RTB2000ValueError,
    SIM_RESOURCE,
    is_complete,
    parse_model,
    trigger_delay_points,
)

logger = logging.getLogger(__name__)

V_DIVS = 5.0          # ±5 Teilungen: am RTB2002 gemessen 10 hoch (RANGe = 10 * SCALe)
# Das Gerät begrenzt die Werte knapp INNERHALB des Bereichs (gemessen bei
# 99,9 % des Vollausschlags, Offset 2,5 V, ±1 V: Minimum 1,50098 V). Ohne
# Spielraum würde eine abgeschnittene Kurve nicht als übersteuert gemeldet.
OVERRANGE_FRACTION = 0.995
# Horizontale Teilungen, wenn TIMebase:DIVisions? nicht antwortet: 10 ist
# die vorsichtige Wahl -- sind es in Wahrheit 12, wird das Fenster länger.
FALLBACK_H_DIVS = 10
# Volt/Teilung am Eingang (Tastkopf 1:1), S. 253: 1 mV bis 10 V/div.
V_SCALES = tuple(round(m * 10.0 ** e, 12) for e in range(-3, 2) for m in (1, 2, 5) if m * 10.0 ** e <= 10)
RANGES_V = tuple(round(s * V_DIVS, 12) for s in V_SCALES)
# S. 258: 1 ns/div bis 50 s/div.
T_SCALES = tuple(round(m * 10.0 ** e, 15) for e in range(-9, 2) for m in (1, 2, 5) if m * 10.0 ** e <= 50)
# Aufzeichnungslängen laut ACQuire:POINts (S. 261). 20M (nur interleaved)
# liest der Adapter nicht: 80 MB je Kanal über LAN.
RECORD_LENGTHS = (10_000, 20_000, 50_000, 100_000, 200_000, 500_000,
                  1_000_000, 2_000_000, 5_000_000, 10_000_000)
DEFAULT_SAMPLES = 250_000
# Datenblatt: 2,5 GS/s interleaved mit einem Kanal. Am RTB2002 (Firmware
# 01.210) lieferte CHANnel:DATA? bei 2,5 GS/s aber unbrauchbare Werte (ein
# 0..2,5-V-Rechteck kam als -1,8..-1,4 V, in REAL wie ASCII). Der Adapter
# begrenzt die Aufzeichnungslänge darum so, dass das Gerät höchstens 1,25 GS/s
# wählt (siehe _choose_timing). Mit Firmware 02.202 sind die Daten bei 2,5 GS/s
# richtig; die Grenze bleibt für Geräte mit älterer Firmware.
MAX_RATE_HZ = 1.25e9
RESOLUTION_BITS = 10
POLL_S = 0.02
FORCE_REPEAT_S = 0.2
WAIT_MARGIN_S = 2.0
# Ab welcher Abweichung des Trigger-Index vom angefragten Pre-Trigger gewarnt wird.
PRE_TRIGGER_TOLERANCE = 0.05
_EPS = 1e-9


def capabilities_for(model: str, serial: str, simulated: bool = False, options: str = "") -> Capabilities:
    info = parse_model(model or "RTB2002", options)
    names = tuple(f"CH{n}" for n in range(1, info.channels + 1))
    return Capabilities(
        vendor="Rohde & Schwarz",
        model=(model or "RTB2000") + (" (Simulation)" if simulated else ""),
        serial=serial,
        channels=tuple(ChannelCaps(name, RANGES_V, offset=True) for name in names),
        max_sample_rate_hz=MAX_RATE_HZ,
        memory_samples=RECORD_LENGTHS[-1],
        bandwidth_hz=info.bandwidth_hz,
        resolution_bits=RESOLUTION_BITS,
        trigger_sources=names,
        max_pre_trigger_pct=100.0,
        signal_generator=False,
        notes=(
            f"Kanäle heißen {names[0]}..{names[-1]}. Bereiche gelten am Scope-Eingang (Tastkopf 1:1); der "
            "Tastkopf wird am Gerät eingestellt, damit dessen Anzeige stimmt.",
            "Analoger Offset: offset_v ist die Mitte des Bereichs. Der tatsächliche Bereich kann größer sein "
            "als angefragt (er steht im Ergebnis).",
            "Ohne timing.samples werden mindestens 250 000 Samples erfasst (rund 2 s je Erfassung); höchstens "
            "10 Mio. je Kanal, binär über LAN mit ~1,4 MB/s (rund 30 s für 10 Mio.).",
            "Höchstens 1,25 GS/s je Kanal (die 2,5 GS/s interleaved liefern mit älterer Firmware falsche Daten "
            "und werden nicht genutzt). Die tatsächliche Rate steht im Ergebnis.",
            "Bandbreite: 70 MHz, mit Option RTB-B22x/B24x mehr -- erkannt über *OPT?, sobald das Gerät "
            "verbunden ist.",
            "Nur Flanken-Trigger. Die Bildschirmeinstellungen am Gerät werden durch jede Erfassung "
            "überschrieben; danach steht das Gerät gestoppt mit dieser Erfassung.",
        ),
    )


class RtbScope:
    def __init__(self, device: RTB2000, model: str, serial: str, simulated: bool = False,
                 options: str = "", h_divs: int = FALLBACK_H_DIVS) -> None:
        self._device = device
        self._model = model
        self._serial = serial
        self._options = options
        self._h_divs = h_divs
        self.simulated = simulated
        self._channels = parse_model(model, options).channels
        self.scope_id = f"scope:{serial}"

    def capabilities(self) -> Capabilities:
        return capabilities_for(self._model, self._serial, self.simulated, self._options)

    # -- Erfassung --------------------------------------------------------------

    def acquire(self, request: AcquireRequest, cancel=None, on_armed=None, on_triggered=None) -> Capture:
        """cancel/on_armed/on_triggered: scharf schalten (PI-72 a), siehe base.ScopeDriver."""
        try:
            return self._acquire(request, cancel, on_armed, on_triggered)
        except RTB2000ValueError as exc:
            raise ScopeError("device_error", f"Gerät hat abgelehnt: {exc}") from exc
        except RTB2000Error as exc:
            raise ScopeError("device_error", str(exc)) from exc

    def _acquire(self, request: AcquireRequest, cancel=None, on_armed=None, on_triggered=None) -> Capture:
        warnings: list[str] = []
        numbers: dict[str, int] = {}
        for ch in request.channels:
            n = _channel_number(ch.name)
            if n is None or not 1 <= n <= self._channels:
                raise ScopeError("setting_not_supported", f"Kanal {ch.name!r} gibt es am {self._model} nicht.",
                                 allowed=[f"CH{i}" for i in range(1, self._channels + 1)])
            numbers[ch.name] = n

        dev = self._device
        dev.stop()
        dev.clear_errors()

        # -- Kanäle ------------------------------------------------------------
        requested = {numbers[c.name]: c for c in request.channels}
        for n in range(1, self._channels + 1):
            setting = requested.get(n)
            if setting is None:
                dev.set_channel(n, display=False)
                continue
            needed = setting.range_v / setting.probe / V_DIVS
            step = next((s for s in V_SCALES if s >= needed * (1 - _EPS)), None)
            if step is None:
                raise ScopeError("setting_not_supported",
                                 f"Bereich ±{setting.range_v:g} V zu groß für {setting.name} "
                                 f"(Tastkopf {setting.probe:g}:1).",
                                 max_range_v=RANGES_V[-1] * setting.probe)
            dev.set_channel(n, display=True, coupling=setting.coupling, probe=setting.probe,
                            scale_v=step * setting.probe, offset_v=setting.offset_v)

        # -- Zeit ----------------------------------------------------------------
        scale_s, record_length, wanted_samples = self._choose_timing(request.timing, len(requested))
        pre = request.timing.pre_trigger_pct if request.trigger.mode != "none" else 50.0
        if request.trigger.mode == "none" and request.timing.pre_trigger_pct:
            warnings.append("Pre-Trigger ohne Trigger (mode none) ignoriert.")
        window = self._h_divs * scale_s
        dev.set_acquisition(record_length)
        dev.set_timebase(scale_s, window * (0.5 - pre / 100.0))

        # -- Zurücklesen, was das Gerät eingestellt hat ---------------------------
        actual_scale_s = dev.timebase_scale()
        actual: dict[str, tuple[float, float]] = {}   # (Vollausschlag ±V an der Spitze, Mitte)
        for name, n in numbers.items():
            setting = next(c for c in request.channels if c.name == name)
            half_range = dev.channel_range(n) / 2.0
            center = dev.channel_offset(n)
            actual[name] = (half_range, center)
            if half_range < setting.range_v * (1 - 1e-6):
                warnings.append(f"{name}: Gerät stellte ±{half_range:g} V ein, kleiner als angefragt "
                                f"(±{setting.range_v:g} V).")
            if abs(center - setting.offset_v) > max(abs(setting.offset_v) * 1e-3, half_range * 1e-3):
                warnings.append(f"{name}: Gerät stellte Offset {center:g} V ein (angefragt {setting.offset_v:g} V).")
        if abs(actual_scale_s - scale_s) > scale_s * 1e-6:
            warnings.append(f"Zeitbasis: Gerät stellte {actual_scale_s:g} s/div ein (angefragt {scale_s:g}).")

        # -- Trigger ---------------------------------------------------------------
        trig = request.trigger
        if trig.mode != "none":
            src = trig.source
            half_range, center = actual[src]
            if abs(trig.level_v - center) >= half_range:
                raise ScopeError("level_out_of_range",
                                 f"Trigger-Pegel {trig.level_v:g} V liegt außerhalb des Bereichs von {src} "
                                 f"({center - half_range:g} .. {center + half_range:g} V).",
                                 range_v=half_range)
            dev.set_edge_trigger(numbers[src], trig.level_v, trig.edge == "rising")
        else:
            src = next(iter(numbers))
            dev.set_edge_trigger(numbers[src], actual[src][1], True)

        code, message = dev.pop_error()
        if code:
            warnings.append(f"Gerät meldet nach dem Einstellen Fehler {code}: {message}")
            dev.clear_errors()

        # -- Erfassen --------------------------------------------------------------
        dev.sync()
        forced = self._run_single(trig.mode, trig.timeout_s, self._h_divs * actual_scale_s, cancel, on_armed)
        if on_triggered is not None:
            on_triggered()   # Erfassung im Gerät fertig, ab hier nur noch Lesen

        # -- Daten -----------------------------------------------------------------
        channels: dict[str, ChannelData] = {}
        head = None
        sizes = []
        for setting in request.channels:
            n = numbers[setting.name]
            volts, ch_head = dev.read_waveform_volts(n)
            if ch_head.points != volts.size:
                warnings.append(f"{setting.name}: Kopf meldet {ch_head.points} Punkte, geliefert {volts.size}.")
            head = ch_head if head is None else head
            half_range, center = actual[setting.name]
            sizes.append(volts.size)
            channels[setting.name] = ChannelData(
                name=setting.name,
                range_v=half_range,
                coupling=setting.coupling,
                probe=setting.probe,
                volts=volts,
                overrange=bool(np.any(np.abs(volts - center) >= half_range * OVERRANGE_FRACTION)),
            )
        if len(set(sizes)) > 1:
            n = min(sizes)
            warnings.append(f"Kanäle lieferten unterschiedlich viele Punkte {sizes}; gekürzt auf {n}.")
            for data in channels.values():
                data.volts = data.volts[:n]
        samples = min(sizes)
        interval = head.xinc if head is not None else 0.0
        if interval <= 0:
            raise ScopeError("device_error", "Gerät meldet kein Abtastintervall (Kopf der Daten).")
        if wanted_samples and samples < wanted_samples * 0.9:
            warnings.append(f"Nur {samples} Samples erfasst (angefragt {wanted_samples}); die Abtastrate ist "
                            f"begrenzt auf {1 / interval:.4g} S/s.")
        if request.timing.sample_interval_s and interval > request.timing.sample_interval_s * (1 + 1e-6):
            warnings.append(f"Abtastintervall {interval:g} s, gröber als angefragt "
                            f"({request.timing.sample_interval_s:g} s).")

        # t = 0 ist der Trigger (S. 259), der erste Punkt liegt bei XStart --
        # bei langen Aufzeichnungen laufen die Daten dem Kopf aber nach
        # (driver.TRIGGER_DELAY_POINTS): die Flanke kommt so viele Punkte später.
        delay = trigger_delay_points(dev.acquisition_points(), len(numbers))
        trigger_index = int(round(-head.xstart / interval + delay))
        trigger_index = min(max(trigger_index, 0), samples - 1)
        if trig.mode != "none":
            got = trigger_index / max(1, samples - 1)
            if abs(got - pre / 100.0) > PRE_TRIGGER_TOLERANCE:
                warnings.append(f"Trigger liegt bei {got * 100:.0f} % des Fensters statt bei {pre:g} % "
                                "(Vorzeichen von TIMebase:POSition prüfen).")

        if trig.mode == "none":
            triggered = None
        elif forced:
            triggered = analysis.trigger_seen(channels[trig.source].volts, trigger_index, trig.level_v, trig.edge)
            if not triggered:
                warnings.append("Kein Trigger erkannt: nach dem Auto-Timeout frei erfasst.")
        else:
            triggered = True

        return Capture(
            scope_id=self.scope_id,
            model=self.capabilities().model,
            sample_interval_s=float(interval),
            trigger_index=trigger_index,
            triggered=triggered,
            channels=channels,
            request=request,
            warnings=warnings,
        )

    def _choose_timing(self, timing: Timing, active: int) -> tuple[float, int, int]:
        """(s/Teilung, Aufzeichnungslänge, gewünschte Samples)."""
        rate_max = MAX_RATE_HZ
        if timing.duration_s is not None:
            duration = timing.duration_s
            wanted = timing.samples or DEFAULT_SAMPLES
        else:
            if timing.sample_interval_s < 1.0 / rate_max * (1 - 1e-6):
                raise ScopeError("setting_not_supported",
                                 f"Abtastintervall {timing.sample_interval_s:g} s ist feiner als möglich.",
                                 min_interval_s=analysis.num(1.0 / rate_max))
            wanted = timing.samples or DEFAULT_SAMPLES
            duration = timing.sample_interval_s * wanted
        scale = next((s for s in T_SCALES if s * self._h_divs >= duration * (1 - _EPS)), None)
        if scale is None:
            raise ScopeError("duration_too_long", f"{duration:g} s sind länger als die längste Zeitbasis.",
                             max_duration_s=T_SCALES[-1] * self._h_divs)
        if wanted > RECORD_LENGTHS[-1]:
            raise ScopeError("setting_not_supported", f"Höchstens {RECORD_LENGTHS[-1]} Samples je Erfassung.",
                             max_samples=RECORD_LENGTHS[-1])
        # Das Fenster (Teilungen im 1-2-5-Raster) ist meist länger als die
        # angefragte Dauer -- damit die Auflösung stimmt, so viele Punkte wie
        # für das ganze Fenster nötig (höchstens die größte Länge).
        window = self._h_divs * scale
        needed = math.ceil(wanted * window / duration * (1 - _EPS))
        record = next((r for r in RECORD_LENGTHS if r >= needed), RECORD_LENGTHS[-1])
        # Mehr Samples, als die Rate im Fenster hergibt, gibt es nicht.
        possible = int(rate_max * window)
        # Gemessen: das Gerät nimmt die nächstkleinere Rate unter Länge /
        # Fenster (10k bei 6 µs -> 1,25 GS/s, 100k bei 6 µs -> 2,5 GS/s). Die
        # kleinste Länge, die 1,25 GS/s ausschöpft, liegt im 1-2-5-Raster
        # höchstens 1,67-fach darüber, also sicher unter 2,5 GS/s.
        cap = next((r for r in RECORD_LENGTHS if r >= possible), RECORD_LENGTHS[-1])
        record = min(record, cap)
        return scale, record, min(needed, record, max(possible, 2))

    def _run_single(self, mode: str, timeout_s: float, window_s: float, cancel=None, on_armed=None) -> bool:
        """Startet eine Einzelerfassung und wartet auf COMP (vorher STOP gesendet
        und per *OPC? synchronisiert, ein COMP ist also immer das neue). True =
        erzwungen (*TRG statt echter Trigger, bei none und auto ohne Flanke).
        Scharf schalten (PI-72 a): on_armed() beim ersten Status ungleich COMP (spaetestens vor der
        Rueckkehr), cancel bricht mit STOP ab. HARDWARE-PRUEFUNG: nur gegen den Simulator geprueft."""
        dev = self._device
        dev.single()
        started = time.monotonic()
        forced = False
        last_force = 0.0

        def armed() -> None:
            nonlocal on_armed
            if on_armed is not None:
                callback, on_armed = on_armed, None
                callback()

        # none: sofort erzwingen; sonst Wartezeit + ein Fenster (Pre-Trigger füllen).
        trigger_deadline = started if mode == "none" else started + timeout_s + window_s
        hard_deadline = trigger_deadline + window_s + WAIT_MARGIN_S
        while True:
            state = dev.acquisition_state()
            now = time.monotonic()
            finished = is_complete(state)
            if finished:
                armed()
                return forced
            armed()
            if cancel is not None and cancel.is_set():
                dev.stop()
                raise ScopeError("cancelled", "Erfassung abgebrochen.")
            if now >= trigger_deadline and not forced:
                if mode == "single":
                    dev.stop()
                    raise ScopeError("trigger_timeout", f"Kein Trigger innerhalb von {timeout_s:g} s.",
                                     timeout_s=timeout_s)
                dev.force_trigger()
                forced = True
                last_force = now
            elif forced and not finished and now - last_force > FORCE_REPEAT_S:
                # *TRG vor dem Scharfschalten geht ins Leere -- wiederholen.
                dev.force_trigger()
                last_force = now
            if now >= hard_deadline:
                dev.stop()
                raise ScopeError("device_error", f"Erfassung nicht fertig geworden (Status {state}).")
            time.sleep(POLL_S)

    def close(self) -> None:
        self._device.close()


def _channel_number(name: str) -> int | None:
    text = name.upper()
    for prefix in ("CHANNEL", "CH", "C"):
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            return int(text[len(prefix):])
    return int(text) if text.isdigit() else None


def open_rtb(resource: str) -> RtbScope:
    """Öffnet das RTB2000 unter ``resource`` (VISA-Adresse oder SIM-RTB)."""
    try:
        device = RTB2000.open(resource)
    except RTB2000Error as exc:
        raise ScopeError("device_error", str(exc)) from exc
    try:
        info = device.idn()
        options = device.options()
    except RTB2000Error as exc:
        device.close()
        raise ScopeError("device_error", str(exc)) from exc
    try:
        h_divs = device.timebase_divisions()
    except RTB2000Error as exc:
        logger.warning("RTB2000 %s: TIMebase:DIVisions? ohne Antwort (%s), rechne mit %d Teilungen",
                       info.serial, exc, FALLBACK_H_DIVS)
        h_divs = FALLBACK_H_DIVS
    if not 8 <= h_divs <= 16:
        h_divs = FALLBACK_H_DIVS
    return RtbScope(device, info.model, info.serial, simulated=resource == SIM_RESOURCE,
                    options=options, h_divs=h_divs)

"""scope_api-Adapter für UNI-T MSO1000HD (z.B. MSO1154HD-S) über SCPI.

Übersetzt eine herstellerneutrale AcquireRequest in SCPI-Einstellungen
(device-driver/unit_mso1000hd/driver.py) und die Rohdaten zurück:

- Bereich: ±4 Teilungen (8 vertikal). Volt/Teilung = kleinste 1-2-5-Stufe am
  Eingang, die range_v / Tastkopf / 4 abdeckt. Der Tastkopf wird am Gerät
  eingestellt, damit die Bildschirmanzeige stimmt. Danach wird zurückgelesen:
  zurückgemeldet wird, was das Gerät wirklich eingestellt hat.
- Offset: analoger Offset ist vorhanden. offset_v ist die Mitte des Bereichs,
  am Gerät CHANnel:OFFSet = -offset_v (Kurve wird nach unten geschoben).
- Zeitbasis: 10 Teilungen. s/Teilung = kleinste 1-2-5-Stufe >= Dauer / 10;
  Speichertiefe aus der gewünschten Sample-Zahl, hochgerechnet auf das ganze
  (meist längere) Fenster, damit die Auflösung stimmt. Das tatsächliche Intervall
  meldet das Gerät in der Preamble (XINCrement).
- Pre-Trigger: Fenster um TIMebase:OFFSet zentriert, also
  Offset = Dauer * (0,5 - pre_trigger_pct / 100).
- Trigger: immer Einzelerfassung (TRIGger:SWEep SINGle + RUN), dann Status
  pollen. auto: nach timeout_s TRIGger:FORCe; none: sofort FORCe; single:
  nach timeout_s STOP und Fehler trigger_timeout.
- Daten: WORD (binär, 2 Byte je Punkt), siehe driver.read_waveform_volts.

Das Gerät bleibt nach einer Erfassung gestoppt und zeigt sie an.

Am MSO1154HD-S geprüft (1.0.2): Vorzeichen von CHANnel:OFFSet und TIMebase:OFFSet,
Speicherfenster (8 Teilungen, t = 0 aus XORigin), Statusfolge nach RUN, Scharf schalten
und Abbruch. Einzelheiten in der README des Treibers.
"""
from __future__ import annotations

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
from unit_mso1000hd.driver import (
    MSO1000HD,
    MSO1000HDError,
    MSO1000HDValueError,
    SIM_RESOURCE,
    parse_model,
)

V_DIVS = 4.0          # ±4 Teilungen vertikal
H_DIVS = 10.0         # 10 Teilungen horizontal (Bildschirm)
# Am Gerät geprüft: der Speicher deckt bei langsamen Zeitbasen nur 8 Teilungen ab
# (1 ms/div, 25K oder 250K: 8 ms, um die Bildschirmmitte), bei schnellen mehr.
# Zeitbasis, Punktzahl und Vorlauf-Verschiebung rechnen deshalb mit 8.
RECORD_DIVS = 8.0
# Volt/Teilung am Eingang (Tastkopf 1:1). HARDWARE-PRÜFUNG: Datenblatt nennt
# für die HD-Reihe 1 mV/div bis 10 V/div an 1 MOhm.
V_SCALES = tuple(round(m * 10.0 ** e, 12) for e in range(-3, 2) for m in (1, 2, 5) if m * 10.0 ** e <= 10)
RANGES_V = tuple(round(s * V_DIVS, 12) for s in V_SCALES)
T_SCALES = tuple(round(m * 10.0 ** e, 15) for e in range(-9, 3) for m in (1, 2, 5) if m * 10.0 ** e <= 1000)
# Speichertiefen laut ACQuire:MEMory:DEPTh (S. 182). Mehr als 5 M liest der
# Adapter nicht: als ASCII über LAN dauert das Minuten.
DEPTHS = ((25_000, "25K"), (250_000, "250K"), (500_000, "500K"), (5_000_000, "5M"))
DEFAULT_SAMPLES = 250_000
MAX_RATE_HZ = 2.5e9
# Nach RUN: so lange gilt ein noch gemeldetes "STOP" als "vom letzten Mal".
ARM_GRACE_S = 0.3
# Status, ab denen das Gerät auf den Trigger wartet (am Gerät READY; Simulator WAIT).
ARMED_STATES = ("READY", "WAIT", "TRIGED", "AUTO")
# Ab 100 ms/div arbeitet das Geraet im Scan-Betrieb: Status dauerhaft "SCAN", nie READY -- ob ein Ereignis nach dem
# Scharf schalten dort sauber mit Vorlauf erfasst wird, ist nicht geprueft. Scharf schalten deshalb nur bis
# 50 ms/div (PI-93 c, 1.0.3); acquire bleibt erlaubt.
ARM_MAX_SCALE_S = 0.05
POLL_S = 0.02
WAIT_MARGIN_S = 2.0
_EPS = 1e-9


def capabilities_for(model: str, serial: str, simulated: bool = False) -> Capabilities:
    info = parse_model(model or "MSO1154HD-S")
    names = tuple(f"CH{n}" for n in range(1, info.channels + 1))
    return Capabilities(
        vendor="UNI-T",
        model=(model or "MSO1000HD") + (" (Simulation)" if simulated else ""),
        serial=serial,
        channels=tuple(ChannelCaps(name, RANGES_V, offset=True) for name in names),
        max_sample_rate_hz=MAX_RATE_HZ,
        memory_samples=DEPTHS[-1][0],
        bandwidth_hz=info.bandwidth_hz,
        resolution_bits=12,
        trigger_sources=names,
        max_pre_trigger_pct=100.0,
        signal_generator=False,
        notes=(
            "Kanäle heißen CH1..CH4. Bereiche gelten am Scope-Eingang (Tastkopf 1:1); der Tastkopf wird am "
            "Gerät eingestellt, damit dessen Anzeige stimmt.",
            "Analoger Offset: offset_v ist die Mitte des Bereichs.",
            "Ohne timing.samples werden 250 000 Samples erfasst (höchstens 5 Mio.). Eine Erfassung dauert "
            "rund 2 s mit einem Kanal, 5 s mit vier (Einstellen, Erfassen, binär über LAN lesen).",
            "2,5 GS/s gelten für einen aktiven Kanal; mit mehr Kanälen teilt das Gerät die Rate. Die "
            "tatsächliche Rate steht im Ergebnis.",
            "Scharf schalten (arm, Ablaufschritt) nur bis 0,4 s Fenster (50 ms/div); längere Fenster nur mit acquire.",
            "Nur Flanken-Trigger. Die Bildschirmeinstellungen am Gerät werden durch jede Erfassung "
            "überschrieben; danach steht das Gerät gestoppt mit dieser Erfassung.",
        ),
    )


class UnitMsoScope:
    def __init__(self, device: MSO1000HD, model: str, serial: str, simulated: bool = False) -> None:
        self._device = device
        self._model = model
        self._serial = serial
        self.simulated = simulated
        self._channels = parse_model(model).channels
        self.scope_id = f"scope:{serial}"

    def capabilities(self) -> Capabilities:
        return capabilities_for(self._model, self._serial, self.simulated)

    # -- Erfassung --------------------------------------------------------------

    def acquire(self, request: AcquireRequest, cancel=None, on_armed=None, on_triggered=None) -> Capture:
        """cancel/on_armed/on_triggered: scharf schalten (PI-72 a), siehe base.ScopeDriver."""
        try:
            return self._acquire(request, cancel, on_armed, on_triggered)
        except MSO1000HDValueError as exc:
            raise ScopeError("device_error", f"Gerät hat abgelehnt: {exc}") from exc
        except MSO1000HDError as exc:
            raise ScopeError("device_error", str(exc)) from exc

    def _acquire(self, request: AcquireRequest, cancel=None, on_armed=None, on_triggered=None) -> Capture:
        warnings: list[str] = []
        numbers: dict[str, int] = {}
        for ch in request.channels:
            n = _channel_number(ch.name)
            if n is None or n > self._channels:
                raise ScopeError("setting_not_supported", f"Kanal {ch.name!r} gibt es am {self._model} nicht.",
                                 allowed=[f"CH{i}" for i in range(1, self._channels + 1)])
            numbers[ch.name] = n

        if on_armed is not None:
            # Vor jedem Geraetebefehl pruefen (PI-93 c): die Zeitbasis-Wahl fasst das Geraet nicht an.
            arm_scale_s = self._choose_timing(request.timing, len(numbers))[0]
            if arm_scale_s > ARM_MAX_SCALE_S * (1 + _EPS):
                limit = RECORD_DIVS * ARM_MAX_SCALE_S
                raise ScopeError("setting_not_supported",
                                 f"Scharf schalten am {self._model} nur bis {ARM_MAX_SCALE_S * 1000:g} ms/div "
                                 f"(Fenster bis {limit:g} s): langsamer arbeitet das Gerät im Scan-Betrieb und meldet "
                                 "nicht, wann es auf den Trigger wartet. Kürzeres Fenster wählen oder acquire verwenden.",
                                 max_duration_s=limit)

        dev = self._device
        dev.stop()
        dev.clear_errors()

        # -- Kanäle ------------------------------------------------------------
        requested = {numbers[c.name]: c for c in request.channels}
        scales: dict[str, float] = {}
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
                            scale_v=step * setting.probe, offset_v=-setting.offset_v)

        # -- Zeit ----------------------------------------------------------------
        scale_s, depth_label, wanted_samples = self._choose_timing(request.timing, len(requested))
        pre = request.timing.pre_trigger_pct if request.trigger.mode != "none" else 50.0
        if request.trigger.mode == "none" and request.timing.pre_trigger_pct:
            warnings.append("Pre-Trigger ohne Trigger (mode none) ignoriert.")
        duration = RECORD_DIVS * scale_s
        dev.set_acquisition(depth_label)
        dev.set_timebase(scale_s, duration * (0.5 - pre / 100.0))

        # -- Zurücklesen, was das Gerät eingestellt hat ---------------------------
        actual_scale_s = dev.timebase_scale()
        actual_offset_s = dev.timebase_offset()
        actual: dict[str, tuple[float, float]] = {}
        for name, n in numbers.items():
            actual[name] = (dev.channel_scale(n), -dev.channel_offset(n))   # (V/div an der Spitze, Mitte)
            setting = next(c for c in request.channels if c.name == name)
            want = setting.range_v / V_DIVS
            if actual[name][0] < want * (1 - 1e-6):
                warnings.append(f"{name}: Gerät stellte {actual[name][0]:g} V/div ein, kleiner als angefragt "
                                f"({want:g} V/div).")
            # Am Gerät begrenzt: CHANnel:OFFSet höchstens ±8 Teilungen (0,1 V/div -> ±0,8 V), ohne Fehlermeldung.
            if abs(actual[name][1] - setting.offset_v) > max(1e-3, 0.01 * actual[name][0]):
                warnings.append(f"{name}: Gerät stellte die Mitte auf {actual[name][1]:g} V statt {setting.offset_v:g} V "
                                f"(Offset begrenzt, größeren Bereich wählen).")
        if abs(actual_scale_s - scale_s) > scale_s * 1e-6:
            warnings.append(f"Zeitbasis: Gerät stellte {actual_scale_s:g} s/div ein (angefragt {scale_s:g}).")

        # -- Trigger ---------------------------------------------------------------
        trig = request.trigger
        if trig.mode != "none":
            src = trig.source
            scale_v, center = actual[src]
            if abs(trig.level_v - center) >= scale_v * V_DIVS:
                raise ScopeError("level_out_of_range",
                                 f"Trigger-Pegel {trig.level_v:g} V liegt außerhalb des Bereichs von {src} "
                                 f"({center - scale_v * V_DIVS:g} .. {center + scale_v * V_DIVS:g} V).",
                                 range_v=scale_v * V_DIVS)
            dev.set_edge_trigger(numbers[src], trig.level_v, trig.edge == "rising", "SINGle")
        else:
            src = next(iter(numbers))
            dev.set_edge_trigger(numbers[src], actual[src][1], True, "SINGle")

        code, message = dev.pop_error()
        if code:
            warnings.append(f"Gerät meldet nach dem Einstellen Fehler {code}: {message}")
            dev.clear_errors()

        # -- Erfassen --------------------------------------------------------------
        forced = self._run_single(trig.mode, trig.timeout_s, H_DIVS * actual_scale_s, cancel, on_armed)
        if on_triggered is not None:
            on_triggered()   # Erfassung im Gerät fertig, ab hier nur noch Lesen
        dev.wait_data_ready()   # am Gerät: direkt nach STOP noch unfertige Rohdaten

        # -- Daten -----------------------------------------------------------------
        channels: dict[str, ChannelData] = {}
        interval = None
        xorigin = None
        sizes = []
        for setting in request.channels:
            n = numbers[setting.name]
            volts, pre_info = dev.read_waveform_volts(n)
            interval = pre_info.xinc if interval is None else interval
            xorigin = pre_info.xorigin if xorigin is None else xorigin
            scale_v, center = actual[setting.name]
            range_v = scale_v * V_DIVS
            sizes.append(volts.size)
            channels[setting.name] = ChannelData(
                name=setting.name,
                range_v=range_v,
                coupling=setting.coupling,
                probe=setting.probe,
                volts=volts,
                overrange=bool(np.any(np.abs(volts - center) >= range_v * (1 - 1e-6))),
            )
        if len(set(sizes)) > 1:
            n = min(sizes)
            warnings.append(f"Kanäle lieferten unterschiedlich viele Punkte {sizes}; gekürzt auf {n}.")
            for data in channels.values():
                data.volts = data.volts[:n]
        samples = min(sizes)
        if interval is None or interval <= 0:
            raise ScopeError("device_error", "Gerät meldet kein Abtastintervall (XINCrement).")
        if request.timing.sample_interval_s and interval > request.timing.sample_interval_s * (1 + 1e-6):
            warnings.append(f"Abtastintervall {interval:g} s, gröber als angefragt "
                            f"({request.timing.sample_interval_s:g} s).")

        # XORigin = Zeit des ersten Punkts relativ zum Trigger (am Gerät geprüft: 1-kHz-
        # Rechteck, Flanke bei t = 0). Das Fenster ist NICHT immer 10 Teilungen breit
        # (bei 1 ms/div 8 Teilungen, bei 1 us/div länger) -- deshalb nicht aus der Zeitbasis.
        first_t = xorigin if xorigin is not None else actual_offset_s - samples * interval / 2.0
        trigger_index = int(round(-first_t / interval))
        trigger_index = min(max(trigger_index, 0), samples - 1)

        # Auf das angefragte Fenster zuschneiden. Am Gerät verlängert eine größere Speichertiefe
        # oft nur das Fenster (die Abtastrate je Zeitbasis ist begrenzt, z. B. 62,5 MS/s bei
        # 0,5 ms/div): 500K ergaben 16 statt 8 Teilungen, der Vorlauf stimmte dann nicht mehr.
        # Die Aufzeichnung liegt um dieselbe Mitte wie das angefragte Fenster -- es ist enthalten.
        span_s = request.timing.duration_s or (request.timing.sample_interval_s * (request.timing.samples or wanted_samples))
        keep = int(round(span_s / interval)) if span_s else samples
        if 0 < keep < samples * 0.99:
            first = trigger_index - int(round(pre / 100.0 * keep))
            first = min(max(first, 0), samples - keep)
            for name, data in channels.items():
                data.volts = data.volts[first:first + keep]
                scale_v, center = actual[name]
                data.overrange = bool(np.any(np.abs(data.volts - center) >= data.range_v * (1 - 1e-6)))
            trigger_index -= first
            samples = keep
        if request.timing.samples and samples < request.timing.samples * 0.9:
            warnings.append(f"Nur {samples} Samples erfasst (angefragt {request.timing.samples}); die Abtastrate ist "
                            f"begrenzt auf {1 / interval:.4g} S/s.")

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

    def _choose_timing(self, timing: Timing, active: int) -> tuple[float, str, int]:
        """(s/Teilung, Speichertiefe, gewünschte Samples)."""
        rate_max = MAX_RATE_HZ / (1 if active <= 1 else 2 if active <= 2 else 4)
        if timing.duration_s is not None:
            duration = timing.duration_s
            wanted = timing.samples or DEFAULT_SAMPLES
        else:
            if timing.sample_interval_s < 1.0 / MAX_RATE_HZ * (1 - 1e-6):
                raise ScopeError("setting_not_supported",
                                 f"Abtastintervall {timing.sample_interval_s:g} s ist feiner als möglich.",
                                 min_interval_s=analysis.num(1.0 / rate_max))
            wanted = timing.samples or DEFAULT_SAMPLES
            duration = timing.sample_interval_s * wanted
        scale = next((s for s in T_SCALES if s * RECORD_DIVS >= duration * (1 - _EPS)), None)
        if scale is None:
            raise ScopeError("duration_too_long", f"{duration:g} s sind länger als die längste Zeitbasis.",
                             max_duration_s=T_SCALES[-1] * RECORD_DIVS)
        if wanted > DEPTHS[-1][0]:
            raise ScopeError("setting_not_supported", f"Höchstens {DEPTHS[-1][0]} Samples je Erfassung.",
                             max_samples=DEPTHS[-1][0])
        # Das Fenster (8 Teilungen im Speicher, 1-2-5-Raster) ist meist länger als die
        # angefragte Dauer -- der Speicher deckt aber das ganze Fenster ab.
        # Damit die angefragte Auflösung erhalten bleibt, so viele Punkte wie
        # für das ganze Fenster nötig (höchstens die größte Tiefe).
        needed = math.ceil(wanted * RECORD_DIVS * scale / duration * (1 - _EPS))
        depth = next(((n, label) for n, label in DEPTHS if n >= needed), DEPTHS[-1])
        # Mehr Samples, als die Rate im Fenster hergibt, gibt es nicht.
        possible = int(rate_max * RECORD_DIVS * scale)
        return scale, depth[1], min(needed, depth[0], max(possible, 2))

    def _run_single(self, mode: str, timeout_s: float, window_s: float, cancel=None, on_armed=None) -> bool:
        """Startet eine Einzelerfassung und wartet auf STOP. True = erzwungen
        (FORCe statt echter Trigger, bei none und auto ohne Flanke).
        Scharf schalten (PI-72 a): on_armed() beim ersten Status ungleich STOP (spaetestens vor der
        Rueckkehr), cancel bricht mit STOP ab. Am MSO1154HD-S geprueft (1.0.2)."""
        dev = self._device
        dev.run()
        started = time.monotonic()
        armed_seen = False
        forced = False

        def armed() -> None:
            nonlocal on_armed
            if on_armed is not None:
                callback, on_armed = on_armed, None
                callback()

        # none: sofort erzwingen; sonst Wartezeit + ein Fenster (Pre-Trigger füllen).
        trigger_deadline = started if mode == "none" else started + timeout_s + window_s
        hard_deadline = trigger_deadline + window_s + WAIT_MARGIN_S
        while True:
            status = dev.trigger_status()
            now = time.monotonic()
            if status != "STOP":
                armed_seen = True
                # Am Gerät nach RUN: (RESET ->) ARMED (Vorlauf füllen) -> READY (wartet auf
                # den Trigger) -> TRIGED -> STOP. Erst ab READY ist ein Ereignis sicher erfasst.
                if status in ARMED_STATES:
                    armed()
            elif armed_seen or now - started > ARM_GRACE_S + window_s:
                armed()
                return forced
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
            elif forced and now - started > ARM_GRACE_S and status in ("WAIT", "ARMED", "READY"):
                # FORCe vor dem Scharfschalten geht ins Leere -- wiederholen.
                dev.force_trigger()
            if now >= hard_deadline:
                dev.stop()
                raise ScopeError("device_error", f"Erfassung nicht fertig geworden (Status {status}).")
            time.sleep(POLL_S)

    def close(self) -> None:
        self._device.close()


def _channel_number(name: str) -> int | None:
    text = name.upper()
    for prefix in ("CHANNEL", "CH", "C"):
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            return int(text[len(prefix):])
    return int(text) if text.isdigit() else None


def open_unit_mso(resource: str) -> UnitMsoScope:
    """Öffnet das MSO1000HD unter ``resource`` (VISA-Adresse oder SIM)."""
    try:
        device = MSO1000HD.open(resource)
    except MSO1000HDError as exc:
        raise ScopeError("device_error", str(exc)) from exc
    try:
        info = device.idn()
    except MSO1000HDError as exc:
        device.close()
        raise ScopeError("device_error", str(exc)) from exc
    return UnitMsoScope(device, info.model, info.serial, simulated=resource == SIM_RESOURCE)

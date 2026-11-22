"""Eingebauter Funktionsgenerator (AWG1) der UNI-T-Oszilloskope MSO1000HD mit "-S" (z. B. MSO1154HD-S).

Grundlage: UNI-T "MSO1000HD Series Programming Manual" V1.0, Kapitel "AWG Command" -- geprüft am
MSO1154HD-S (Firmware V1.01.0081) über LAN. Was das Gerät anders macht als das Handbuch, steht in
README.md ("Am Gerät geprüft").

Für LabControl ist das ein Funktionsgenerator wie der JDS2915 (Geräteart "fg"): dieselbe
Schnittstelle wie jds66xx.driver.JDS66xx (get_/set_output(s), _waveform, _frequency, _amplitude,
_offset, _duty) und dieselben Modulfunktionen (read_field, readback_fields, set_*_checked,
state_dict, outputs_off), dazu Modulation (AWG1:MODe) und Lastwiderstand (AWG1:LOAD). Ein Kanal,
keine Phase, keine Arbiträrkurven.

Verbindung: eine EIGENE VXI-11-Verbindung (unit_mso1000hd.driver.VisaTransport), nicht die der
Erfassungen -- der Handle-Wächter unit_mso1000hd/guard.py blockiert den Generator also nie (ALLE AUS
geht immer durch). Das Gerät hat aber nur EINE Ausgabe für alle Verbindungen; VisaTransport lässt
deshalb nur ganze Abfragen abwechselnd zu (io_lock). Während eine Erfassung eingerichtet wird, ist
das Gerät bis ~1,5 s mit sich beschäftigt -- der DeviceWorker fragt den Generator deshalb in einem
eigenen Thread ab (ASYNC_IO).
"""
from __future__ import annotations

import math
import threading

from jds66xx.driver import FunctionGeneratorError, FunctionGeneratorValueError
from unit_mso1000hd.driver import MSO1000HDBusyError, MSO1000HDError, VisaTransport

PROFILE = "unit_mso_awg"
TITLE = "UNI-T MSO, AWG1"
CHANNELS = (1,)
HAS_PHASE = False
HAS_MODULATION = True
HAS_LOAD = True
SIM_RESOURCE = "SIM"
DEFAULT_TIMEOUT_S = 5.0   # das Gerät nimmt Befehle während einer Erfassungs-Einrichtung bis ~1,5 s nicht an

# Rahmen, damit ein offensichtlich falscher Wert gar nicht erst gesendet wird. Das Gerät begrenzt
# selbst (am Gerät: Werte außerhalb werden still geklemmt, z. B. Tastgrad 150 -> 99 %, Frequenz 0 ->
# 1 µHz); der zurückgelesene Wert zeigt, was gilt. Frequenz, Amplitude und Offset oberhalb
# 1 MHz / 2 Vss / 1 V sind am Gerät NICHT geprüft (Obergrenzen aus dem Handbuch: Offset bis
# Last*10/(50+Last) V, also 10 V hochohmig).
MIN_FREQUENCY_HZ = 1e-6
MAX_FREQUENCY_HZ = 25e6
MAX_AMPLITUDE_V = 20.0
MAX_OFFSET_V = 10.0
# Lastwiderstand: Handbuch 1 .. 1 000 000 Ohm, am Gerät erst ab 50 Ohm (kleinere Werte -> 50), ganze Ohm.
MIN_LOAD_OHM = 50.0
MAX_LOAD_OHM = 1_000_000.0
HIGH_Z_OHM = 1_000_000.0
MAX_AM_DEPTH_PCT = 120.0
MIN_SWEEP_TIME_S = 0.001
MAX_SWEEP_TIME_S = 500.0

# Kurvenformen: Codes wie beim JDS2915 (jds66xx.driver.WAVEFORMS), damit dieselben Aktionen
# (FG_WAVE_SINE ...) für beide gelten. RAMP mit Symmetrie 50 % ist ein Dreieck (FG_WAVE_TRIANGLE).
WAVE_SINE, WAVE_SQUARE, WAVE_PULSE, WAVE_RAMP, WAVE_DC, WAVE_NOISE = 0, 1, 2, 3, 6, 11
WAVE_ARB = 100   # nur Anzeige: am Gerät gewählt, über LabControl nicht einstellbar
WAVEFORMS: dict[int, str] = {
    WAVE_SINE: "Sinus",
    WAVE_SQUARE: "Rechteck",
    WAVE_PULSE: "Puls",
    WAVE_RAMP: "Rampe",
    WAVE_NOISE: "Rauschen",
    WAVE_DC: "DC",
}
_SCPI_WAVES = {WAVE_SINE: "SINe", WAVE_SQUARE: "SQUare", WAVE_PULSE: "PULSe", WAVE_RAMP: "RAMP",
               WAVE_DC: "DC", WAVE_NOISE: "NOISe"}
# Antwort (Großbuchstaben) -> Code; geprüft wird der Anfang ("SINE" wie "SIN")
_WAVE_PREFIXES = (("SIN", WAVE_SINE), ("SQU", WAVE_SQUARE), ("PUL", WAVE_PULSE), ("RAMP", WAVE_RAMP),
                  ("DC", WAVE_DC), ("NOIS", WAVE_NOISE), ("ARB", WAVE_ARB))
WAVE_DC_CODE = WAVE_DC
# Am Gerät (1.0.2): AWG1:BASe:DUTY wirkt bei Rechteck und Puls. In RAMP (und vermutlich den
# übrigen Formen) schaltet er die Ausgabe auf ein Rechteck um, während AWG1:BASe:WAVe? weiter RAMP
# meldet -- deshalb nur bei diesen beiden Formen.
DUTY_WAVEFORMS = (WAVE_SQUARE, WAVE_PULSE)

# Modulation (AWG1:MODe)
MODES = ("BASE", "AM", "FM", "ASK", "FSK", "SWEEP")
MODE_NAMES = {"BASE": "Grundsignal", "AM": "AM", "FM": "FM", "ASK": "ASK", "FSK": "FSK", "SWEEP": "Wobbeln"}
_SCPI_MODES = {"BASE": "BASe", "AM": "AM", "FM": "FM", "ASK": "ASK", "FSK": "FSK", "SWEEP": "SWEep"}
_MODE_PREFIXES = (("BAS", "BASE"), ("AM", "AM"), ("FM", "FM"), ("ASK", "ASK"), ("FSK", "FSK"),
                  ("SWE", "SWEEP"))
MOD_WAVES = ("SINE", "SQUARE", "UPRAMP", "DNRAMP", "NOISE")
MOD_WAVE_NAMES = {"SINE": "Sinus", "SQUARE": "Rechteck", "UPRAMP": "Rampe steigend",
                  "DNRAMP": "Rampe fallend", "NOISE": "Rauschen", "ARB": "Arbiträr"}
_SCPI_MOD_WAVES = {"SINE": "SINe", "SQUARE": "SQUare", "UPRAMP": "UPRamp", "DNRAMP": "DNRamp",
                   "NOISE": "NOISe"}
_MOD_WAVE_PREFIXES = (("SIN", "SINE"), ("SQU", "SQUARE"), ("UPR", "UPRAMP"), ("DNR", "DNRAMP"),
                      ("NOIS", "NOISE"), ("ARB", "ARB"))
SWEEP_TYPES = ("LIN", "LOG")
SWEEP_TYPE_NAMES = {"LIN": "linear", "LOG": "logarithmisch"}
_SCPI_SWEEP = {"LIN": "LINe", "LOG": "LOG"}

# Zahlen-Parameter: Feld -> (Befehl ohne "AWG1:", Einheit, Minimum, Maximum, deutscher Name)
NUMERIC_PARAMS: dict[str, tuple[str, str, float, float, str]] = {
    "mod_freq": ("MODulate:FREQuency", "Hz", MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ, "Modulationsfrequenz"),
    "am_depth": ("AM:MODulate:DEPTh", "%", 0.0, MAX_AM_DEPTH_PCT, "AM-Tiefe"),
    "fm_dev": ("FM:FREQuency:DEV", "Hz", 0.0, MAX_FREQUENCY_HZ, "FM-Hub"),
    "fsk_rate": ("FSK:RATio", "Hz", MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ, "Umtastrate"),
    "fsk_freq": ("FSK:FREQuency", "Hz", MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ, "Sprungfrequenz"),
    "sweep_start": ("SWEep:FREQuency:STARt", "Hz", MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ, "Startfrequenz"),
    "sweep_stop": ("SWEep:FREQuency:STOP", "Hz", MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ, "Stoppfrequenz"),
    "sweep_time": ("SWEep:TIMe", "s", MIN_SWEEP_TIME_S, MAX_SWEEP_TIME_S, "Wobbelzeit"),
    "load": ("LOAD", "Ω", MIN_LOAD_OHM, MAX_LOAD_OHM, "Last"),
}
# Auswahl-Parameter: Feld -> (Befehl, erlaubte Werte, SCPI-Wörter, Antwort-Präfixe)
CHOICE_PARAMS = {
    "mode": ("MODe", MODES, _SCPI_MODES, _MODE_PREFIXES),
    "mod_wave": ("MODulate:WAVe", MOD_WAVES, _SCPI_MOD_WAVES, _MOD_WAVE_PREFIXES),
    "sweep_type": ("SWEep:TYPe", SWEEP_TYPES, _SCPI_SWEEP, (("LIN", "LIN"), ("LOG", "LOG"))),
}
PARAM_FIELDS = ("mode", "load", "mod_wave", "mod_freq", "am_depth", "fm_dev", "fsk_rate", "fsk_freq",
                "sweep_type", "sweep_start", "sweep_stop", "sweep_time")
# Welche Parameter bei welcher Art wirken (Kachel blendet die übrigen aus). ASK: am Gerät bestimmt
# AWG1:FSK:RATio die Umtastrate (das Handbuch nennt für ASK keinen Befehl), der untere Pegel liegt
# fest bei der halben Amplitude.
MODE_PARAMS = {
    "BASE": (),
    "AM": ("mod_wave", "mod_freq", "am_depth"),
    "FM": ("mod_wave", "mod_freq", "fm_dev"),
    "ASK": ("fsk_rate",),
    "FSK": ("fsk_rate", "fsk_freq"),
    "SWEEP": ("sweep_type", "sweep_start", "sweep_stop", "sweep_time"),
}

# Abfrage im Betrieb (device_worker._poll_fgs): je Zyklus der Ausgang, dazu rollierend
# SLOW_FIELDS_PER_POLL der übrigen Felder (eine Abfrage ~2 ms über LAN; 17 Felder in ~2 s).
POLL_FAST_FIELDS: tuple[tuple[str, int], ...] = (("outputs", 0),)
POLL_SLOW_FIELDS: tuple[tuple[str, int], ...] = tuple(
    (field, 1) for field in ("waveform", "frequency", "amplitude", "offset", "duty")
) + tuple((field, 0) for field in PARAM_FIELDS)
SLOW_FIELDS_PER_POLL = 4


def device_id_for(scope_device_id: str) -> str:
    """Geräte-ID des Generators aus der des Oszilloskops: "scpiscope:192.168.1.5" -> "fg:192.168.1.5:AWG1"."""
    return f"fg:{scope_device_id.split(':', 1)[-1]}:AWG1"


def is_device_id(device_id: str) -> bool:
    """Gehört diese (lokale) Geräte-ID zu einem eingebauten Generator? (siehe device_id_for)"""
    return device_id.startswith("fg:") and device_id.endswith(":AWG1")


def waveform_name(code: int) -> str:
    if code in WAVEFORMS:
        return WAVEFORMS[code]
    if code == WAVE_ARB:
        return "Arbiträr"
    return f"Form {code}"


# -- Wertepruefung (auch vom Simulator benutzt) --------------------------------------------------

def check_channel(channel: int) -> None:
    if channel not in CHANNELS:
        raise FunctionGeneratorValueError(f"Kanal {channel} gibt es nicht -- der Generator im Oszilloskop hat "
                                          f"nur Kanal 1")


def check_waveform(code: int) -> None:
    if code not in _SCPI_WAVES:
        raise FunctionGeneratorValueError(f"Kurvenform {waveform_name(code)} ist am Generator im Oszilloskop "
                                          f"nicht einstellbar")


def _check_range(name: str, value: float, low: float, high: float, unit: str) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise FunctionGeneratorValueError(f"{name} {value:g} {unit} außerhalb {low:g}..{high:g} {unit}")


def check_frequency(hz: float) -> None:
    _check_range("Frequenz", hz, MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ, "Hz")


def check_amplitude(volts: float) -> None:
    _check_range("Amplitude", volts, 0.0, MAX_AMPLITUDE_V, "V")


def check_offset(volts: float) -> None:
    _check_range("Offset", volts, -MAX_OFFSET_V, MAX_OFFSET_V, "V")


def check_duty(percent: float) -> None:
    _check_range("Tastverhältnis", percent, 0.0, 100.0, "%")


def check_param(field: str, value) -> object:
    """Prüft einen Modulations-/Lastparameter und liefert den Wert, wie er gesendet wird."""
    if field in CHOICE_PARAMS:
        allowed = CHOICE_PARAMS[field][1]
        text = str(value).upper()
        if text not in allowed:
            raise FunctionGeneratorValueError(f"{field}: {value!r} nicht erlaubt ({', '.join(allowed)})")
        return text
    if field in NUMERIC_PARAMS:
        _cmd, unit, low, high, name = NUMERIC_PARAMS[field]
        _check_range(name, value, low, high, unit)
        return round(float(value)) if field == "load" else float(value)
    raise FunctionGeneratorValueError(f"Unbekannter Parameter {field!r}")


def _number(value: float) -> str:
    return f"{value:.9g}"


def _choice(text: str, prefixes) -> str:
    upper = text.strip().strip('"').upper()
    for prefix, result in prefixes:
        if upper.startswith(prefix):
            return result
    raise FunctionGeneratorError(f"Unerwartete Antwort {text!r}")


class UnitMsoAwg:
    """Generator AWG1 eines MSO1000HD-S. ``transport`` liefert write/read_line/close -- echt
    (VisaTransport, eigene VXI-11-Verbindung) oder simuliert (mock.SimulatedAwg)."""

    PROFILE = PROFILE
    CHANNELS = CHANNELS
    # DeviceWorker: Abfragen und Befehle in einem eigenen Thread, nicht im Worker (siehe Modul-Docstring)
    ASYNC_IO = True

    def __init__(self, transport, identity: str = "") -> None:
        self._t = transport
        self._identity = identity
        # Treiber wird aus zwei Threads benutzt (Abfrage-Thread, ALLE AUS im Worker): Fehlerabfrage
        # nach einem abgelehnten Befehl gehört zur selben Abfolge
        self._lock = threading.RLock()

    @classmethod
    def open(cls, resource: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> "UnitMsoAwg":
        if resource == SIM_RESOURCE:
            from unit_mso_awg.mock import SimulatedAwg
            return cls(SimulatedAwg())
        try:
            return cls(VisaTransport(resource, timeout_s))
        except MSO1000HDError as exc:
            raise FunctionGeneratorError(str(exc)) from exc

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> "UnitMsoAwg":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- low level -----------------------------------------------------------------

    def _rejected(self, command: str, exc: Exception) -> FunctionGeneratorError:
        """Ein Senden/Lesen scheiterte. Am Gerät: ein unbekannter oder ungültiger Befehl gibt beim
        Senden einen I/O-Fehler, die Verbindung lebt, im Fehlerspeicher steht -113. Antwortet
        :SYSTem:ERRor? mit einem Fehler, war es der Befehl (Wertfehler) -- sonst gilt die Verbindung
        als tot."""
        if isinstance(exc, MSO1000HDBusyError):
            return FunctionGeneratorValueError(f"Oszilloskop belegt, {command} nicht gesendet: {exc}")
        try:
            self._t.write(":SYSTem:ERRor?")
            text = self._t.read_line()
        except MSO1000HDError:
            return FunctionGeneratorError(f"{command}: {exc}")
        head, comma, message = text.partition(",")
        try:
            code = int(head)
        except ValueError:
            code = None
        if code is None or not comma:   # keine Fehlermeldung -- womöglich eine verspätete Antwort
            return FunctionGeneratorError(f"{command}: {exc} (danach {text!r})")
        if code:
            return FunctionGeneratorValueError(f"Gerät lehnt {command!r} ab: {code} {message.strip().strip(chr(34))}")
        return FunctionGeneratorError(f"{command}: {exc}")

    def _write(self, command: str) -> None:
        with self._lock:
            try:
                self._t.write(command)
            except MSO1000HDError as exc:
                raise self._rejected(command, exc) from exc

    def _query(self, command: str) -> str:
        with self._lock:
            try:
                self._t.write(command)
                return self._t.read_line()
            except MSO1000HDError as exc:
                raise self._rejected(command, exc) from exc

    def _query_float(self, command: str) -> float:
        text = self._query(command)
        try:
            return float(text.strip().strip('"'))
        except ValueError as exc:
            # Eine unpassende Antwort kann eine vertauschte sein -- Verbindung neu aufbauen
            raise FunctionGeneratorError(f"{command}: unerwartete Antwort {text!r}") from exc

    # -- Erkennung -----------------------------------------------------------------

    def probe(self) -> bool:
        """True, wenn das Oszilloskop einen Generator AWG1 hat (AWG1:OUTPut? -> 0/1). Ohne
        Generator lehnt das Gerät die Abfrage ab; der Eintrag im Fehlerspeicher wird abgeholt,
        damit er keine Erfassung stört. Wirft nie."""
        try:
            return self._query("AWG1:OUTPut?").strip() in ("0", "1")
        except FunctionGeneratorError:
            return False

    def identify(self) -> str:
        """Modell, Seriennummer, Firmware (*IDN?) und "AWG1" -- einmal gelesen, danach gemerkt."""
        if not self._identity:
            parts = [p.strip() for p in self._query("*IDN?").split(",")]
            self._identity = " ".join(p for p in parts[1:4] if p) + " AWG1"
        return self._identity

    # -- Ausgang -------------------------------------------------------------------

    def get_outputs(self) -> tuple[bool]:
        text = self._query("AWG1:OUTPut?").strip().upper()
        if text not in ("0", "1", "ON", "OFF"):
            raise FunctionGeneratorError(f"AWG1:OUTPut?: unerwartete Antwort {text!r}")
        return (text in ("1", "ON"),)

    def set_outputs(self, *outputs: bool) -> None:
        if len(outputs) != len(CHANNELS):
            raise FunctionGeneratorValueError(f"{len(CHANNELS)} Ausgang erwartet, {len(outputs)} angegeben")
        self._write(f"AWG1:OUTPut {'ON' if outputs[0] else 'OFF'}")

    def get_output(self, channel: int) -> bool:
        check_channel(channel)
        return self.get_outputs()[0]

    def set_output(self, channel: int, on: bool) -> None:
        check_channel(channel)
        self.set_outputs(bool(on))

    # -- Grundsignal -----------------------------------------------------------------

    def get_waveform(self, channel: int) -> int:
        check_channel(channel)
        text = self._query("AWG1:BASe:WAVe?")
        return _wave_code(text)

    def set_waveform(self, channel: int, code: int) -> None:
        check_channel(channel)
        check_waveform(code)
        self._write(f"AWG1:BASe:WAVe {_SCPI_WAVES[code]}")

    def get_frequency(self, channel: int) -> float:
        check_channel(channel)
        return self._query_float("AWG1:BASe:FREQuency?")

    def set_frequency(self, channel: int, hertz: float) -> None:
        check_channel(channel)
        check_frequency(hertz)
        self._write(f"AWG1:BASe:FREQuency {_number(hertz)}")

    def get_amplitude(self, channel: int) -> float:
        check_channel(channel)
        return self._query_float("AWG1:BASe:AMPLitude?")

    def set_amplitude(self, channel: int, volts: float) -> None:
        check_channel(channel)
        check_amplitude(volts)
        self._write(f"AWG1:BASe:AMPLitude {_number(volts)}")

    def get_offset(self, channel: int) -> float:
        check_channel(channel)
        return self._query_float("AWG1:BASe:OFFSet?")

    def set_offset(self, channel: int, volts: float) -> None:
        check_channel(channel)
        check_offset(volts)
        self._write(f"AWG1:BASe:OFFSet {_number(volts)}")

    def get_duty(self, channel: int) -> float:
        check_channel(channel)
        return self._query_float("AWG1:BASe:DUTY?")

    def set_duty(self, channel: int, percent: float) -> None:
        check_channel(channel)
        check_duty(percent)
        self._write(f"AWG1:BASe:DUTY {_number(percent)}")

    # -- Modulation und Last ---------------------------------------------------------

    def get_param(self, channel: int, field: str):
        check_channel(channel)
        if field in CHOICE_PARAMS:
            command, _allowed, _words, prefixes = CHOICE_PARAMS[field]
            return _choice(self._query(f"AWG1:{command}?"), prefixes)
        if field in NUMERIC_PARAMS:
            return self._query_float(f"AWG1:{NUMERIC_PARAMS[field][0]}?")
        raise FunctionGeneratorValueError(f"Unbekannter Parameter {field!r}")

    def set_param(self, channel: int, field: str, value) -> None:
        check_channel(channel)
        sent = check_param(field, value)
        if field in CHOICE_PARAMS:
            command, _allowed, words, _prefixes = CHOICE_PARAMS[field]
            self._write(f"AWG1:{command} {words[sent]}")
        else:
            self._write(f"AWG1:{NUMERIC_PARAMS[field][0]} {_number(sent)}")

    def get_mode(self, channel: int = 1) -> str:
        return self.get_param(channel, "mode")

    def set_mode(self, channel: int, mode: str) -> None:
        self.set_param(channel, "mode", mode)

    def get_load(self, channel: int = 1) -> float:
        return self.get_param(channel, "load")

    def set_load(self, channel: int, ohm: float) -> None:
        self.set_param(channel, "load", ohm)


def _wave_code(text: str) -> int:
    upper = text.strip().strip('"').upper()
    for prefix, code in _WAVE_PREFIXES:
        if upper.startswith(prefix):
            return code
    raise FunctionGeneratorError(f"AWG1:BASe:WAVe?: unerwartete Antwort {text!r}")


# -- Modulfunktionen wie in jds66xx.driver (DeviceWorker, fg_profiles) ----------------------------

def read_field(gen, field: str, channel: int = 0):
    """Ein Feld lesen (eine Abfrage): "outputs" -> (bool,), Kanalfelder mit Kanal 1, sonst Parameter."""
    if field == "outputs":
        return gen.get_outputs()
    getter = {"waveform": gen.get_waveform, "frequency": gen.get_frequency, "amplitude": gen.get_amplitude,
              "offset": gen.get_offset, "duty": gen.get_duty}.get(field)
    if getter is not None:
        return getter(channel or 1)
    return gen.get_param(1, field)


def readback_fields(field: str, channel: int = 0) -> tuple[tuple[str, int], ...]:
    """Felder, die nach dem Schreiben von `field` zurückgelesen werden: das Feld selbst und was das
    Gerät dabei anpasst. Am Gerät: die Last ändert die Anzeige von Amplitude und Offset (die Spannung
    am Ausgang bleibt, gezählt wird sie an der angenommenen Last)."""
    if field == "outputs":
        return (("outputs", 0),)
    if field == "load":
        return (("load", 0), ("amplitude", 1), ("offset", 1))
    if field == "waveform":
        return (("waveform", 1), ("frequency", 1), ("amplitude", 1), ("offset", 1), ("duty", 1))
    if field in ("frequency", "amplitude", "offset"):
        return tuple(dict.fromkeys(((field, 1), ("amplitude", 1), ("offset", 1))))
    if field == "duty":
        return (("duty", 1),)
    if field in ("sweep_start", "sweep_stop"):
        return (("sweep_start", 0), ("sweep_stop", 0))
    return ((field, 0),)


def duty_refusal(code: int, channel: int) -> str:
    if code in DUTY_WAVEFORMS:
        return ""
    return (f"Tastgrad wirkt nur bei Rechteck und Puls -- Kanal {channel} steht auf {waveform_name(code)} "
            f"(erst Rechteck oder Puls wählen)")


def amplitude_refusal(code: int, channel: int) -> str:
    if code != WAVE_DC:
        return ""
    return f"In DC gibt es keine Amplitude -- Kanal {channel}: die Spannung ist der Offset"


def set_waveform_checked(gen, channel: int, code: int) -> None:
    """Form setzen. DC braucht anders als beim JDS2915 nichts weiter: am Gerät liegt der Offset an."""
    gen.set_waveform(channel, code)


def set_amplitude_checked(gen, channel: int, volts: float) -> None:
    check_channel(channel)
    check_amplitude(volts)
    refusal = amplitude_refusal(gen.get_waveform(channel), channel)
    if refusal:
        raise FunctionGeneratorValueError(refusal)
    gen.set_amplitude(channel, volts)


def set_offset_checked(gen, channel: int, volts: float) -> None:
    gen.set_offset(channel, volts)


def set_duty_checked(gen, channel: int, percent: float) -> None:
    check_channel(channel)
    check_duty(percent)
    refusal = duty_refusal(gen.get_waveform(channel), channel)
    if refusal:
        raise FunctionGeneratorValueError(refusal)
    gen.set_duty(channel, percent)


def set_param_checked(gen, channel: int, field: str, value) -> None:
    gen.set_param(channel, field, value)


def outputs_off(gen) -> None:
    gen.set_outputs(False)


def state_dict(gen) -> dict:
    """Voller Zustand im Format von device_worker.fg_state (beim Verbinden)."""
    channel = {"waveform": gen.get_waveform(1), "frequency": gen.get_frequency(1),
               "amplitude": gen.get_amplitude(1), "offset": gen.get_offset(1), "duty": gen.get_duty(1)}
    state = {"profile": PROFILE, "outputs": list(gen.get_outputs()), "channels": [channel], "phase": None}
    for field in PARAM_FIELDS:
        state[field] = gen.get_param(1, field)
    return state

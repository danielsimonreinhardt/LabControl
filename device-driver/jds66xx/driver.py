"""Treiber fuer die Joy-IT-/JUNTEK-Funktionsgeneratoren der Reihe JDS66xx/JDS2915
(zweikanalig, DDS).

Grundlage ist das Kommunikationsprotokoll "JT-JDS6600-Communication-protocol"
(Joy-IT, Stand 2024-04-23), das fuer die ganze Reihe gilt. Das JDS2915 (15 MHz)
nutzt dieselben Register; nur der Frequenzbereich unterscheidet sich vom
JDS6600 (60 MHz).

Kommunikation ueber den USB-Seriell-Wandler (CH340, Windows: COMx) mit
115200 Baud, 8N1. Ein Telegramm hat die Form

    :<w|r><Register>=<Daten>.<CR><LF>

Beispiel: ``:w23=25786,0.`` setzt die Frequenz von Kanal 1 auf 257,86 Hz;
``:r23=.`` liest sie zurueck (Antwort ``:r23=25786,0.``). Jeder Schreibbefehl
wird mit ``:ok`` quittiert.

Alle Werte werden hier in SI-Einheiten (Hz, V, Grad, Prozent) angeboten; die
Skalierung des Geraets (0,01 Hz, mV, 0,01 V mit Offset 10 V, 0,1 %, 0,1 Grad)
bleibt vollstaendig in dieser Datei.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import serial
from serial.tools import list_ports

try:   # Linux: ein abgezogener USB-Adapter meldet tcflush/tcsetattr als termios.error (kein OSError)
    import termios
    SERIAL_ERRORS: tuple = (serial.SerialException, OSError, termios.error)
except ImportError:   # Windows
    SERIAL_ERRORS = (serial.SerialException, OSError)

# Generischer WCH-CH340-Wandler. ACHTUNG: dieselbe VID/PID tragen viele fremde
# Geraete (Arduino-Klone, Entwicklungsboards, ...) -- ein Treffer heisst NICHT,
# dass ein Funktionsgenerator daran haengt. Deshalb gilt ein Port erst nach
# einem erfolgreichen Handshake (probe()) als Funktionsgenerator.
USB_VID = 0x1A86
USB_PID = 0x7523
DEFAULT_BAUDRATE = 115200

CHANNELS = (1, 2)
# Geraeteprofil (lab_gui/fg_profiles.py, 1.0.2): seit es mit dem Generator im UNI-T-Oszilloskop
# (unit_mso_awg) einen zweiten Funktionsgenerator gibt, nennt jedes Treibermodul, was es kann.
PROFILE = "jds66xx"
TITLE = "JDS66xx"
HAS_PHASE = True
HAS_MODULATION = False
HAS_LOAD = False

# Obergrenzen des JDS2915. Das Geraet klemmt selbst waveformabhaengig (Rechteck/
# Puls liegen niedriger als Sinus); hier steht nur die absolute Obergrenze, damit
# ein offensichtlich falscher Wert gar nicht erst gesendet wird.
MAX_FREQUENCY_HZ = 15_000_000.0
MIN_FREQUENCY_HZ = 0.01          # Aufloesung des Geraets bei Multiplikator 0
MAX_AMPLITUDE_V = 20.0           # Spitze-Spitze, Leerlauf
MAX_OFFSET_V = 9.99

# Wellenform-Code -> deutscher Basisname (Uebersetzungsschluessel fuer i18n.tr).
WAVEFORMS: dict[int, str] = {
    0: "Sinus",
    1: "Rechteck",
    2: "Puls",
    3: "Dreieck",
    4: "Teilsinus",
    5: "CMOS",
    6: "DC",
    7: "Halbwelle",
    8: "Vollwelle",
    9: "Positive Treppe",
    10: "Negative Treppe",
    11: "Rauschen",
    12: "Exponentieller Anstieg",
    13: "Exponentieller Abfall",
    14: "Multiton",
    15: "Sinc",
    16: "Lorenz",
}
WAVE_SINE, WAVE_SQUARE, WAVE_PULSE, WAVE_TRIANGLE, WAVE_DC = 0, 1, 2, 3, 6
# Arbitraere Kurven Arbitrary01..60 liegen bei 101..160 (siehe Protokoll 3.2.2).
ARBITRARY_FIRST, ARBITRARY_LAST = 101, 160

_REPLY_RE = re.compile(r"^:r(\d+)=(.*?)\.?$")

# Frische-Grenze (s) fuer diese Geraeteart (PI-78 a): so lange darf der letzte gemeldete Zustand alt
# sein, bevor er als veraltet gilt (Watchdog im Testablauf, "stale" der Freigabe). Hoeher als der
# Standard von 2 s, weil der Generator ueber eine geteilte serielle Leitung und mit eigenem Takt
# abgefragt wird (device_worker.FG_POLL_INTERVAL_MS) -- ein Zyklus darf sich verzoegern, ohne
# dass ein Lauf abbricht. Gelesen von lab_gui/field_catalog.stale_timeout_s.
STALE_TIMEOUT_S = 5.0

# Abfrage im Betrieb (device_worker._poll_fgs, PI-78 a): je Zyklus immer die Ausgaenge (EIN/AUS
# beider Kanaele in einem Register), dazu rollierend einige der uebrigen Felder. (Feld, Kanal);
# Kanal 0 = kanalunabhaengig. Nach einem eigenen Schreibbefehl liest der Worker das betroffene
# Feld sofort zurueck (siehe readback_fields).
POLL_FAST_FIELDS: tuple[tuple[str, int], ...] = (("outputs", 0),)
POLL_SLOW_FIELDS: tuple[tuple[str, int], ...] = tuple(
    (field, channel) for channel in CHANNELS
    for field in ("waveform", "frequency", "amplitude", "offset", "duty")
) + (("phase", 0),)
SLOW_FIELDS_PER_POLL = 3


class FunctionGeneratorError(RuntimeError):
    """Fehler bei der Kommunikation mit dem Funktionsgenerator (Verbindung
    tot, Timeout, unbrauchbare Antwort)."""


class FunctionGeneratorValueError(FunctionGeneratorError):
    """Wert ausserhalb des zulaessigen Bereichs bzw. vom Geraet nicht
    quittiert -- KEIN Verbindungsproblem, der Port bleibt offen.
    device_worker._guard_fg unterscheidet daran "Wert abgelehnt" von
    "Verbindung tot" (sonst wuerde jeder abgelehnte Sollwert die Verbindung
    trennen)."""


@dataclass
class ChannelState:
    waveform: int
    frequency: float    # Hz
    amplitude: float    # V (Spitze-Spitze)
    offset: float       # V
    duty: float         # Prozent


@dataclass
class State:
    outputs: tuple[bool, bool]
    channels: tuple[ChannelState, ChannelState]
    phase: float        # Grad, Kanal 2 relativ zu Kanal 1


def waveform_name(code: int) -> str:
    if code in WAVEFORMS:
        return WAVEFORMS[code]
    if ARBITRARY_FIRST <= code <= ARBITRARY_LAST:
        return f"Arbiträr {code - ARBITRARY_FIRST + 1}"
    return f"Form {code}"


# -- Wertepruefung (auch vom Mock benutzt, damit er dieselben Werte ablehnt) --

def check_channel(channel: int) -> None:
    if channel not in CHANNELS:
        raise FunctionGeneratorValueError(f"Kanal {channel} ungueltig, erlaubt: {CHANNELS}")


def check_waveform(code: int) -> None:
    if code not in WAVEFORMS and not ARBITRARY_FIRST <= code <= ARBITRARY_LAST:
        raise FunctionGeneratorValueError(f"Unbekannte Wellenform {code}")


def check_frequency(hz: float) -> None:
    if not MIN_FREQUENCY_HZ <= hz <= MAX_FREQUENCY_HZ:
        raise FunctionGeneratorValueError(
            f"Frequenz {hz:g} Hz ausserhalb {MIN_FREQUENCY_HZ:g}..{MAX_FREQUENCY_HZ:g} Hz"
        )


def check_amplitude(volts: float) -> None:
    if not 0.0 <= volts <= MAX_AMPLITUDE_V:
        raise FunctionGeneratorValueError(f"Amplitude {volts:g} V ausserhalb 0..{MAX_AMPLITUDE_V:g} V")


def check_offset(volts: float) -> None:
    if not -MAX_OFFSET_V <= volts <= MAX_OFFSET_V:
        raise FunctionGeneratorValueError(
            f"Offset {volts:g} V ausserhalb +-{MAX_OFFSET_V:g} V"
        )


def check_duty(percent: float) -> None:
    if not 0.0 <= percent <= 100.0:
        raise FunctionGeneratorValueError(f"Tastverhaeltnis {percent:g} % ausserhalb 0..100 %")


def check_phase(degrees: float) -> None:
    if not 0.0 <= degrees <= 360.0:
        raise FunctionGeneratorValueError(f"Phase {degrees:g} Grad ausserhalb 0..360 Grad")


class JDS66xx:
    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0):
        self._ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        self._ser.reset_input_buffer()

    # -- Discovery -----------------------------------------------------------

    @classmethod
    def discover_ports(cls) -> list:
        """Alle COM-Ports mit CH340-VID/PID -- KANDIDATEN, keine gesicherten
        Funktionsgeneratoren (siehe USB_VID). Bestaetigt wird erst per
        probe()."""
        return [
            info for info in list_ports.comports()
            if info.vid == USB_VID and info.pid == USB_PID
        ]

    @classmethod
    def open_first(cls, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0) -> "JDS66xx":
        """Oeffnet den ersten Kandidaten, der den Handshake besteht."""
        for info in cls.discover_ports():
            try:
                candidate = cls(info.device, baudrate=baudrate, timeout=timeout)
            except SERIAL_ERRORS:
                continue
            if candidate.probe():
                return candidate
            candidate.close()
        raise FunctionGeneratorError(
            f"Kein Funktionsgenerator an einem CH340-Port (VID={USB_VID:04x} PID={USB_PID:04x}) gefunden. "
            "Ist er per USB angeschlossen und eingeschaltet?"
        )

    def close(self) -> None:
        self._ser.close()

    def __enter__(self) -> "JDS66xx":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- low level -----------------------------------------------------------

    def _exchange(self, frame: str) -> str:
        """Sendet ein Telegramm und liefert die Antwortzeile (ohne CR/LF).

        pyserial wirft bei einem ungueltig gewordenen Port-Handle (z.B. nach
        Windows-Standby: "ClearCommError failed") rohe SerialException/OSError
        -- ohne diesen Fang wuerde device_worker.py (faengt nur die eigene
        Fehlerklasse) das Geraet weiter als verbunden fuehren. Ein Timeout
        (leere Antwort) ist ebenfalls ein Verbindungsfehler.

        Der Eingangspuffer wird vor jedem Senden geleert, damit eine verspaetete
        Antwort auf ein frueheres Telegramm nie als Antwort auf dieses gelesen
        wird."""
        try:
            self._ser.reset_input_buffer()
            self._ser.write((frame + "\r\n").encode("ascii"))
            raw = self._ser.readline()
        except SERIAL_ERRORS as exc:
            raise FunctionGeneratorError(f"Kommunikationsfehler auf Port {self._ser.port}: {exc}") from exc
        if not raw:
            raise FunctionGeneratorError(f"Keine Antwort vom Geraet (Timeout) auf Port {self._ser.port}")
        return raw.decode("ascii", errors="replace").strip()

    def _read(self, register: int) -> list[int]:
        reply = self._exchange(f":r{register}=.")
        match = _REPLY_RE.match(reply)
        if not match or int(match.group(1)) != register:
            raise FunctionGeneratorError(f"Unerwartete Antwort auf Register {register}: {reply!r}")
        try:
            return [int(part) for part in match.group(2).split(",") if part.strip() != ""]
        except ValueError as exc:
            raise FunctionGeneratorError(f"Register {register}: keine Zahlen in {reply!r}") from exc

    def _read_one(self, register: int) -> int:
        values = self._read(register)
        if not values:
            raise FunctionGeneratorError(f"Register {register}: leere Antwort")
        return values[0]

    def _write(self, register: int, values: list[int]) -> None:
        reply = self._exchange(f":w{register}=" + ",".join(str(v) for v in values) + ".")
        if not reply.lower().startswith(":ok"):
            raise FunctionGeneratorValueError(f"Register {register}: Geraet quittierte nicht mit :ok ({reply!r})")

    # -- identification --------------------------------------------------------

    def probe(self) -> bool:
        """Handshake: True, wenn an diesem Port tatsaechlich ein Funktions-
        generator antwortet. Liest die Wellenform von Kanal 1 (Register 21) --
        die Antwort ``:r21=<Zahl>.`` gibt es nur bei diesem Protokoll. Ein
        Board, das die Eingabe einfach zurueckspiegelt (``:r21=.``), oder eine
        Konsole ("> ..." / "error") besteht nicht. Wirft nie."""
        try:
            self._read_one(21)
            return True
        except FunctionGeneratorError:
            return False

    def identify(self) -> str:
        """Modellkennung (Register 0) bzw. Seriennummer (Register 1), sofern das
        Geraet sie liefert -- rein informativ, ein Fehlschlag ist unkritisch."""
        parts = []
        for register in (0, 1):
            try:
                reply = self._exchange(f":r{register}=.")
            except FunctionGeneratorError:
                continue
            match = _REPLY_RE.match(reply)
            if match and match.group(2):
                parts.append(match.group(2))
        return " ".join(parts)

    # -- Ausgaenge ---------------------------------------------------------------

    def get_outputs(self) -> tuple[bool, bool]:
        values = self._read(20)
        if len(values) < 2:
            raise FunctionGeneratorError(f"Register 20: zwei Werte erwartet, erhalten {values}")
        return bool(values[0]), bool(values[1])

    def set_outputs(self, ch1: bool, ch2: bool) -> None:
        """Beide Ausgaenge in EINEM Telegramm -- das Register kennt nur das Paar."""
        self._write(20, [int(ch1), int(ch2)])

    def get_output(self, channel: int) -> bool:
        check_channel(channel)
        return self.get_outputs()[channel - 1]

    def set_output(self, channel: int, on: bool) -> None:
        """Schaltet einen Kanal, ohne den anderen zu beeinflussen (Lesen-Aendern-
        Schreiben, da das Register nur das Paar kennt)."""
        check_channel(channel)
        state = list(self.get_outputs())
        state[channel - 1] = on
        self.set_outputs(*state)

    # -- Wellenform -----------------------------------------------------------------

    def get_waveform(self, channel: int) -> int:
        check_channel(channel)
        return self._read_one(20 + channel)

    def set_waveform(self, channel: int, code: int) -> None:
        check_channel(channel)
        check_waveform(code)
        self._write(20 + channel, [code])

    # -- Frequenz ---------------------------------------------------------------------

    def get_frequency(self, channel: int) -> float:
        """Frequenz in Hz. Das Geraet liefert (Wert, Multiplikator): bei 0..2 ist
        der Wert in 0,01 Hz (der Multiplikator aendert dann nur die Anzeige), bei
        3 in mHz, bei 4 in uHz (siehe Protokoll 3.2.3)."""
        check_channel(channel)
        values = self._read(22 + channel)
        if len(values) < 2:
            raise FunctionGeneratorError(f"Register {22 + channel}: zwei Werte erwartet, erhalten {values}")
        raw, multiplier = values[0], values[1]
        if multiplier == 3:
            return raw / 1_000.0
        if multiplier == 4:
            return raw / 1_000_000.0
        return raw / 100.0

    def set_frequency(self, channel: int, hertz: float) -> None:
        """Setzt die Frequenz mit 0,01 Hz Aufloesung (Multiplikator 0). Die
        Multiplikatoren 3/4 (mHz/uHz) fuer feinere Aufloesung werden bewusst nicht
        geschrieben: laut Protokoll sind sie nur bis 80 kHz bzw. 80 Hz gueltig und
        an Hardware nicht verifiziert. Feinere Werte werden gerundet."""
        check_channel(channel)
        check_frequency(hertz)
        self._write(22 + channel, [round(hertz * 100), 0])

    # -- Amplitude --------------------------------------------------------------------

    def get_amplitude(self, channel: int) -> float:
        check_channel(channel)
        return self._read_one(24 + channel) / 1000.0

    def set_amplitude(self, channel: int, volts: float) -> None:
        check_channel(channel)
        check_amplitude(volts)
        self._write(24 + channel, [round(volts * 1000)])

    # -- Offset -----------------------------------------------------------------------

    def get_offset(self, channel: int) -> float:
        check_channel(channel)
        return (self._read_one(26 + channel) - 1000) / 100.0

    def set_offset(self, channel: int, volts: float) -> None:
        # Das Geraet kodiert 0 V als 1000 in Einheiten von 0,01 V (< 1000 negativ).
        check_channel(channel)
        check_offset(volts)
        self._write(26 + channel, [round(volts * 100) + 1000])

    # -- Tastverhaeltnis ---------------------------------------------------------------

    def get_duty(self, channel: int) -> float:
        check_channel(channel)
        return self._read_one(28 + channel) / 10.0

    def set_duty(self, channel: int, percent: float) -> None:
        check_channel(channel)
        check_duty(percent)
        self._write(28 + channel, [round(percent * 10)])

    # -- Phase (Kanal 2 relativ zu Kanal 1) --------------------------------------------

    def get_phase(self) -> float:
        return self._read_one(31) / 10.0

    def set_phase(self, degrees: float) -> None:
        check_phase(degrees)
        self._write(31, [round(degrees * 10) % 3600])

    # -- Gesamtzustand -----------------------------------------------------------------

    def get_channel_state(self, channel: int) -> ChannelState:
        return ChannelState(
            waveform=self.get_waveform(channel),
            frequency=self.get_frequency(channel),
            amplitude=self.get_amplitude(channel),
            offset=self.get_offset(channel),
            duty=self.get_duty(channel),
        )

    def get_state(self) -> State:
        return State(
            outputs=self.get_outputs(),
            channels=(self.get_channel_state(1), self.get_channel_state(2)),
            phase=self.get_phase(),
        )


# -- Einzelfelder fuer die Abfrage im Betrieb (PI-78 a) ---------------------------------
# Modulfunktionen statt Methoden, damit sie fuer Treiber und Mock gleich gelten (nur die get_*-
# Methoden werden benutzt).

def read_field(gen, field: str, channel: int = 0):
    """Ein Feld lesen (eine Registerabfrage): "outputs" -> (bool, bool), "phase" -> Grad, sonst
    der Wert des Kanals."""
    if field == "outputs":
        return gen.get_outputs()
    if field == "phase":
        return gen.get_phase()
    getter = {"waveform": gen.get_waveform, "frequency": gen.get_frequency, "amplitude": gen.get_amplitude,
              "offset": gen.get_offset, "duty": gen.get_duty}[field]
    return getter(channel)


def readback_fields(field: str, channel: int = 0) -> tuple[tuple[str, int], ...]:
    """Felder, die nach dem Schreiben von `field` zurueckgelesen werden: das Feld selbst, dazu was
    das Geraet dabei still anpassen kann (Amplitude wird frequenzabhaengig begrenzt, siehe
    README; die Form kann die Frequenz klemmen). Offset: in DC setzt set_offset_checked auch die
    Amplitude (PI-75 a)."""
    if field in ("outputs", "phase"):
        return ((field, 0),)
    if field == "waveform":
        return (("waveform", channel), ("frequency", channel), ("amplitude", channel))
    if field in ("frequency", "offset"):
        return ((field, channel), ("amplitude", channel))
    return ((field, channel),)


# -- DC und Tastgrad (PI-75 a, PI-80 a; gemessen am JDS2915 am 17.11.2026, CH1 ohne Last) ------------
#
# DC: Am Ausgang liegt etwa Offset + Amplitude/2 (Amplitude 0/1/2,5/5 V bei Offset 0 -> 0,01/0,47/1,21/
# 2,41 V; Amplitude 2,5 V bei Offset 1/2,5 V -> 2,29/3,73 V). Sonderfall Amplitude genau 0: der Offset kommt
# nur zu etwa 1/10 an (Offset 2,5 V -> 0,22 V), mit 0,01 V bzw. 0,1 V Amplitude dagegen 2,61/2,57 V. Deshalb
# setzt LabControl in DC die Amplitude auf DC_AMPLITUDE_V (kleinster Wert != 0) und gibt die Spannung als
# Offset vor; der Rest der halben Amplitude (DC_RESIDUAL_V, 5 mV) bleibt.
# Tastgrad: Rechteck 1 kHz mit 10 % und 99 % ergab jeweils 50 %, Puls 99 %/10 % ergab 98,9 %/9,97 % -- das
# Register wirkt nur bei Puls. Das Geraet nimmt den Wert in jeder Form an (:ok), er wirkt nur nicht.

DC_AMPLITUDE_V = 0.01
DC_RESIDUAL_V = DC_AMPLITUDE_V / 2
DUTY_WAVEFORMS = (WAVE_PULSE,)


def duty_refusal(code: int, channel: int) -> str:
    """Text, warum der Tastgrad bei dieser Form nicht gesetzt wird ("" = Puls, geht)."""
    if code in DUTY_WAVEFORMS:
        return ""
    return (f"Tastgrad wirkt nur bei Kurvenform Puls -- Kanal {channel} steht auf {waveform_name(code)} "
            f"(erst Kurvenform Puls waehlen)")


def amplitude_refusal(code: int, channel: int) -> str:
    """Text, warum in dieser Form keine Amplitude gesetzt wird ("" = geht)."""
    if code != WAVE_DC:
        return ""
    return (f"In DC gibt es keine Amplitude -- Kanal {channel}: die Spannung ist der Offset "
            f"(Amplitude bleibt {DC_AMPLITUDE_V:g} V)")


def set_waveform_checked(gen, channel: int, code: int) -> None:
    """Form setzen; bei DC zugleich die Amplitude auf DC_AMPLITUDE_V, damit am Ausgang der Offset liegt."""
    gen.set_waveform(channel, code)
    if code == WAVE_DC:
        gen.set_amplitude(channel, DC_AMPLITUDE_V)


def set_amplitude_checked(gen, channel: int, volts: float) -> None:
    """Amplitude setzen -- in DC abgelehnt (FunctionGeneratorValueError), siehe amplitude_refusal."""
    check_channel(channel)
    check_amplitude(volts)
    refusal = amplitude_refusal(gen.get_waveform(channel), channel)
    if refusal:
        raise FunctionGeneratorValueError(refusal)
    gen.set_amplitude(channel, volts)


def set_offset_checked(gen, channel: int, volts: float) -> None:
    """Offset setzen; in DC ist das die Ausgangsspannung -- steht die Amplitude dort nicht auf DC_AMPLITUDE_V
    (am Geraet verstellt), wird sie vorher gesetzt."""
    check_channel(channel)
    check_offset(volts)
    if gen.get_waveform(channel) == WAVE_DC and abs(gen.get_amplitude(channel) - DC_AMPLITUDE_V) > 1e-6:
        gen.set_amplitude(channel, DC_AMPLITUDE_V)
    gen.set_offset(channel, volts)


def set_duty_checked(gen, channel: int, percent: float) -> None:
    """Tastgrad setzen -- nur bei Puls, sonst FunctionGeneratorValueError (kein stilles Umschalten der Form)."""
    check_channel(channel)
    check_duty(percent)
    refusal = duty_refusal(gen.get_waveform(channel), channel)
    if refusal:
        raise FunctionGeneratorValueError(refusal)
    gen.set_duty(channel, percent)


def set_param_checked(gen, channel: int, field: str, value) -> None:
    """Modulation/Last (nur Generator im UNI-T-Oszilloskop): hier nicht vorhanden."""
    raise FunctionGeneratorValueError(f"Der JDS66xx kennt {field} nicht (Modulation und Lastwiderstand nur am "
                                      f"Generator im UNI-T-Oszilloskop)")


def outputs_off(gen) -> None:
    """Beide Ausgaenge AUS in einem Telegramm (ALLE AUS)."""
    gen.set_outputs(False, False)


def state_dict(gen) -> dict:
    """Voller Zustand im Format von device_worker.fg_state (beim Verbinden, Simulation)."""
    state = gen.get_state()
    return {
        "outputs": list(state.outputs),
        "channels": [
            {"waveform": c.waveform, "frequency": c.frequency, "amplitude": c.amplitude,
             "offset": c.offset, "duty": c.duty}
            for c in state.channels
        ],
        "phase": state.phase,
    }


if __name__ == "__main__":
    with JDS66xx.open_first() as gen:
        print("Verbunden:", gen.identify())
        print("Zustand:", gen.get_state())

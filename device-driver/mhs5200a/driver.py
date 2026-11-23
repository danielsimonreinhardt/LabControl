"""Treiber fuer die zweikanaligen DDS-Funktionsgeneratoren MHS-5200A (MHS-5206A/5212A/5220A/5225A).

Grundlage: "Serial Protocol for MHS-5200A" (Dokumentversion 3, 12.07.2018, nachgebaut von Nutzern --
der Hersteller veroeffentlicht kein Protokoll). Am MHS-5225A (Kennung ``5225A5040000``) mit dem
RTB2002 nachgemessen; was das Geraet anders macht, steht in README.md ("Am Geraet gemessen").

USB-Seriell-Wandler CH340 (dieselbe VID/PID wie beim JDS2915), 57600 Baud, 8N1, RTS/CTS. Ein Befehl
ist eine Zeile ``:<s|r><Kanal><Feld><Wert>`` mit CR/LF:

    :s1f100000      Frequenz Kanal 1 = 1000,00 Hz (Einheit 0,01 Hz)   -> ok
    :r1f            Frequenz Kanal 1 lesen                           -> :r1f0000100000

Fuer LabControl ist das ein Funktionsgenerator wie der JDS2915 (Geraeteart "fg"): dieselbe
Schnittstelle wie jds66xx.driver.JDS66xx und dieselben Modulfunktionen (read_field, readback_fields,
set_*_checked, state_dict, outputs_off). Werte in SI-Einheiten (Hz, V, Prozent); die Skalierung des
Geraets bleibt in dieser Datei:

- Amplitude Spitze-Spitze: ohne Daempfung in 0,01 V, mit -20-dB-Daempfung in 0,001 V. Die Daempfung
  waehlt der Treiber selbst (bis DAMPED_MAX_V mit Daempfung, feinere Stufen).
- Offset: Prozent der halben Amplitude, 120 = 0 %, 0..240 = -120..+120 %. LabControl rechnet in Volt;
  ein Amplitudenwechsel schreibt den Prozentwert so nach, dass der Offset in Volt bleibt.
- Tastgrad in 0,1 %, wirkt nur bei Rechteck.
- Keine Phase: am Geraet ergibt die Einstellung p einen Versatz von 180 - (p mod 180) Grad, 180..360
  Grad sind nicht erreichbar (README).
"""
from __future__ import annotations

import re
import time

import serial
from serial.tools import list_ports

from jds66xx.driver import SERIAL_ERRORS, FunctionGeneratorError, FunctionGeneratorValueError

# CH340 wie beim JDS2915 -- welcher Generator an einem Port haengt, entscheidet erst der Handshake.
USB_VID = 0x1A86
USB_PID = 0x7523
DEFAULT_BAUDRATE = 57600
# Eine Abfrage dauert am Geraet 10..30 ms; nach einer Folge von Schreibbefehlen brauchte einer aber 0,65 s
# (am MHS-5225A gemessen, nicht gezielt nachstellbar).
DEFAULT_TIMEOUT_S = 1.5

CHANNELS = (1, 2)
PROFILE = "mhs5200a"
TITLE = "MHS-5200A"
HAS_PHASE = False
HAS_MODULATION = False
HAS_LOAD = False

# Grenzen: das Geraet begrenzt selbst still (quittiert mit "ok"): Amplitude bei Wert 2000, Frequenz bei
# 25 MHz (MHS-5225A), Tastgrad bei 99,9 %, Offset bei +-120 %. Hier nur, was offensichtlich falsch ist.
# Amplitude 0 erzeugt am Geraet ein Stoersignal (~0,6 Vss gemessen) -- deshalb ab MIN_AMPLITUDE_V.
MIN_FREQUENCY_HZ = 0.01
MAX_FREQUENCY_HZ = 25e6          # MHS-5225A; kleinere Modelle: max_frequency_hz aus der Kennung
MIN_AMPLITUDE_V = 0.005
MAX_AMPLITUDE_V = 20.0
DAMPED_MAX_V = 2.0               # mit -20 dB hoechstens Wert 2000 -> 2,000 V
MAX_OFFSET_PCT = 120.0
MAX_OFFSET_V = MAX_OFFSET_PCT / 100.0 * MAX_AMPLITUDE_V / 2.0

# Kurvenformen: LabControl-Codes wie beim JDS2915 (jds66xx.driver.WAVEFORMS), damit dieselben Aktionen
# (FG_WAVE_SINE ...) fuer alle Generatoren gelten. Puls und DC gibt es am MHS nicht (Rechteck hat den
# Tastgrad). Die Rampen bekommen eigene Codes (beim JDS frei).
WAVE_SINE, WAVE_SQUARE, WAVE_TRIANGLE, WAVE_RAMP_UP, WAVE_RAMP_DOWN = 0, 1, 3, 20, 21
ARBITRARY_FIRST, ARBITRARY_LAST = 101, 116   # Arbitraer 1..16 = Speicherplatz 0..15
WAVEFORMS: dict[int, str] = {
    WAVE_SINE: "Sinus",
    WAVE_SQUARE: "Rechteck",
    WAVE_TRIANGLE: "Dreieck",
    WAVE_RAMP_UP: "Rampe steigend",
    WAVE_RAMP_DOWN: "Rampe fallend",
}
_DEVICE_WAVES = {WAVE_SINE: 0, WAVE_SQUARE: 1, WAVE_TRIANGLE: 2, WAVE_RAMP_UP: 3, WAVE_RAMP_DOWN: 4}
_WAVES_FROM_DEVICE = {raw: code for code, raw in _DEVICE_WAVES.items()}
# Geschrieben wird Speicherplatz n als 100+n, zurueck meldet das Geraet 10+n (am Geraet; das Protokoll
# nennt 32..47).
_ARB_WRITE_BASE, _ARB_READ_BASE = 100, 10
DUTY_WAVEFORMS = (WAVE_SQUARE,)

# Kennung (:r0c), z. B. "5225A5040000" -> MHS-5225A, 25 MHz
_MODEL_RE = re.compile(r"^(52(\d\d)A)")

# Frische-Grenze und Abfrage im Betrieb wie beim JDS2915 (device_worker._poll_fgs): je Zyklus die
# Ausgaenge (zwei Abfragen), dazu rollierend einige der uebrigen Felder. Der Offset kostet zwei
# Abfragen (Prozentwert und Amplitude).
STALE_TIMEOUT_S = 5.0
POLL_FAST_FIELDS: tuple[tuple[str, int], ...] = (("outputs", 0),)
POLL_SLOW_FIELDS: tuple[tuple[str, int], ...] = tuple(
    (field, channel) for channel in CHANNELS
    for field in ("waveform", "frequency", "amplitude", "offset", "duty")
)
SLOW_FIELDS_PER_POLL = 3


def device_id_for(port_device_id: str) -> str:
    """Geraete-ID aus der Port-ID (device_worker._resolve_device_ids): "fg:usb-1-2" -> "fg:mhs5200a-usb-1-2".
    Eigene Form, damit der Ablauf-Editor das Treibermodul ohne Geraet erkennt (fg_profiles)."""
    return f"fg:{PROFILE}-{port_device_id.split(':', 1)[-1]}"


def is_device_id(device_id: str) -> bool:
    return device_id.startswith(f"fg:{PROFILE}-")


def waveform_name(code: int) -> str:
    if code in WAVEFORMS:
        return WAVEFORMS[code]
    if ARBITRARY_FIRST <= code <= ARBITRARY_LAST:
        return f"Arbiträr {code - ARBITRARY_FIRST + 1}"
    return f"Form {code}"


def model_info(ident: str) -> tuple[str, float]:
    """(Modellname, hoechste Frequenz in Hz) aus der Kennung; unbekannt -> MHS-5200A, MAX_FREQUENCY_HZ."""
    match = _MODEL_RE.match(ident or "")
    if not match:
        return TITLE, MAX_FREQUENCY_HZ
    return f"MHS-{match.group(1)}", min(float(match.group(2)) * 1e6, MAX_FREQUENCY_HZ)


# -- Wertepruefung (auch vom Simulator benutzt) --------------------------------------------------

def check_channel(channel: int) -> None:
    if channel not in CHANNELS:
        raise FunctionGeneratorValueError(f"Kanal {channel} ungueltig, erlaubt: {CHANNELS}")


def check_waveform(code: int) -> None:
    if code in WAVEFORMS or ARBITRARY_FIRST <= code <= ARBITRARY_LAST:
        return
    if code in (2, 6):   # Puls, DC (JDS2915)
        raise FunctionGeneratorValueError(
            f"Der MHS-5200A kennt {'Puls' if code == 2 else 'DC'} nicht"
            + (" -- Rechteck mit Tastgrad nehmen" if code == 2 else ""))
    raise FunctionGeneratorValueError(f"Unbekannte Wellenform {code}")


def check_frequency(hz: float, maximum: float = MAX_FREQUENCY_HZ) -> None:
    if not MIN_FREQUENCY_HZ <= hz <= maximum:
        raise FunctionGeneratorValueError(f"Frequenz {hz:g} Hz ausserhalb {MIN_FREQUENCY_HZ:g}..{maximum:g} Hz")


def check_amplitude(volts: float) -> None:
    if not MIN_AMPLITUDE_V <= volts <= MAX_AMPLITUDE_V:
        raise FunctionGeneratorValueError(
            f"Amplitude {volts:g} V ausserhalb {MIN_AMPLITUDE_V:g}..{MAX_AMPLITUDE_V:g} V")


def check_offset(volts: float) -> None:
    if not -MAX_OFFSET_V <= volts <= MAX_OFFSET_V:
        raise FunctionGeneratorValueError(f"Offset {volts:g} V ausserhalb +-{MAX_OFFSET_V:g} V")


def check_duty(percent: float) -> None:
    if not 0.0 <= percent <= 99.9:
        raise FunctionGeneratorValueError(f"Tastverhaeltnis {percent:g} % ausserhalb 0..99,9 %")


def offset_percent(volts: float, amplitude: float) -> float:
    """Offset in Prozent der halben Amplitude; FunctionGeneratorValueError, wenn mehr als 120 % noetig."""
    if amplitude <= 0.0:
        raise FunctionGeneratorValueError("Offset braucht eine Amplitude (der MHS-5200A stellt ihn in Prozent "
                                          "der Amplitude ein)")
    percent = volts / (amplitude / 2.0) * 100.0
    if abs(percent) > MAX_OFFSET_PCT + 0.5:
        raise FunctionGeneratorValueError(
            f"Offset {volts:g} V geht bei {amplitude:g} Vss nicht (hoechstens {MAX_OFFSET_PCT:g} % der halben "
            f"Amplitude = {MAX_OFFSET_PCT / 100 * amplitude / 2:g} V; erst Amplitude erhoehen bzw. Offset senken)")
    return max(-MAX_OFFSET_PCT, min(MAX_OFFSET_PCT, percent))


def amplitude_raw(volts: float) -> tuple[int, int]:
    """(Daempfung, Wert): bis DAMPED_MAX_V mit -20 dB in mV (Daempfung 0), darueber in 10 mV (Daempfung 1)."""
    if volts <= DAMPED_MAX_V:
        return 0, round(volts * 1000)
    return 1, round(volts * 100)


class MHS5200A:
    PROFILE = PROFILE

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = DEFAULT_TIMEOUT_S):
        self._ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout, rtscts=True)
        self._ser.reset_input_buffer()
        self.model = TITLE
        self.max_frequency_hz = MAX_FREQUENCY_HZ

    @property
    def port(self) -> str:
        return self._ser.port

    @classmethod
    def discover_ports(cls) -> list:
        """CH340-Ports -- KANDIDATEN wie beim JDS2915 (bestaetigt erst probe())."""
        return [info for info in list_ports.comports() if info.vid == USB_VID and info.pid == USB_PID]

    @classmethod
    def open_first(cls) -> "MHS5200A":
        for info in cls.discover_ports():
            try:
                candidate = cls(info.device)
            except SERIAL_ERRORS:
                continue
            if candidate.probe():
                return candidate
            candidate.close()
        raise FunctionGeneratorError("Kein MHS-5200A an einem CH340-Port gefunden. Angeschlossen und eingeschaltet?")

    def close(self) -> None:
        self._ser.close()

    def __enter__(self) -> "MHS5200A":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- low level -----------------------------------------------------------

    def _exchange(self, command: str) -> str:
        """Sendet eine Zeile, liefert die Antwortzeile (ohne CR/LF). Timeout = Verbindungsfehler."""
        try:
            self._ser.reset_input_buffer()
            self._ser.write((command + "\r\n").encode("ascii"))
            raw = self._ser.readline()
        except SERIAL_ERRORS as exc:
            raise FunctionGeneratorError(f"Kommunikationsfehler auf Port {self._ser.port}: {exc}") from exc
        if not raw:
            raise FunctionGeneratorError(f"Keine Antwort vom Geraet (Timeout) auf Port {self._ser.port}")
        return raw.decode("ascii", errors="replace").strip()

    def _read(self, channel: int, field: str) -> str:
        """Wert hinter dem Echo, z. B. :r1f -> "0000100000". Ein unbekanntes Feld beantwortet das Geraet
        nur mit dem Echo -- dann (und bei jeder anderen Antwort) FunctionGeneratorError."""
        prefix = f":r{channel}{field}"
        reply = self._exchange(prefix)
        value = reply[len(prefix):] if reply.startswith(prefix) else ""
        if not value:
            raise FunctionGeneratorError(f"Unerwartete Antwort auf {prefix}: {reply!r}")
        return value

    def _read_int(self, channel: int, field: str) -> int:
        value = self._read(channel, field)
        try:
            return int(value)
        except ValueError as exc:
            raise FunctionGeneratorError(f":r{channel}{field}: keine Zahl in {value!r}") from exc

    def _write(self, channel: int, field: str, value: int) -> None:
        """Das Geraet quittiert jeden Schreibbefehl mit "ok" -- auch unbekannte und begrenzte Werte; was gilt,
        zeigt erst das Zuruecklesen."""
        reply = self._exchange(f":s{channel}{field}{int(value)}")
        if reply.lower() != "ok":
            raise FunctionGeneratorValueError(f":s{channel}{field}{value}: Geraet quittierte nicht mit ok ({reply!r})")

    # -- identification --------------------------------------------------------

    def probe(self) -> bool:
        """Handshake: liest die Kennung (:r0c -> :r0c5225A...). Ein zweiter Versuch, falls noch Reste eines
        anderen Handshakes (JDS2915 mit 115200 Baud) im Eingang des Geraets stehen. Wirft nie."""
        for _ in range(2):
            try:
                ident = self._read(0, "c")
            except FunctionGeneratorError:
                time.sleep(0.05)
                continue
            if _MODEL_RE.match(ident):
                self.model, self.max_frequency_hz = model_info(ident)
                return True
            return False
        return False

    def identify(self) -> str:
        return self.model

    # -- Ausgaenge ---------------------------------------------------------------

    def get_outputs(self) -> tuple[bool, bool]:
        return self.get_output(1), self.get_output(2)

    def set_outputs(self, ch1: bool, ch2: bool) -> None:
        self.set_output(1, ch1)
        self.set_output(2, ch2)

    def get_output(self, channel: int) -> bool:
        check_channel(channel)
        return bool(self._read_int(channel, "b"))

    def set_output(self, channel: int, on: bool) -> None:
        check_channel(channel)
        self._write(channel, "b", int(bool(on)))

    # -- Wellenform ----------------------------------------------------------------

    def get_waveform(self, channel: int) -> int:
        check_channel(channel)
        raw = self._read_int(channel, "w")
        if raw in _WAVES_FROM_DEVICE:
            return _WAVES_FROM_DEVICE[raw]
        if _ARB_READ_BASE <= raw < _ARB_READ_BASE + 16:
            return ARBITRARY_FIRST + raw - _ARB_READ_BASE
        return 200 + raw   # unbekannt, nur Anzeige ("Form ...")

    def set_waveform(self, channel: int, code: int) -> None:
        check_channel(channel)
        check_waveform(code)
        raw = _DEVICE_WAVES.get(code, _ARB_WRITE_BASE + code - ARBITRARY_FIRST)
        self._write(channel, "w", raw)

    # -- Frequenz ---------------------------------------------------------------------

    def get_frequency(self, channel: int) -> float:
        check_channel(channel)
        return self._read_int(channel, "f") / 100.0

    def set_frequency(self, channel: int, hertz: float) -> None:
        check_channel(channel)
        check_frequency(hertz, self.max_frequency_hz)
        self._write(channel, "f", round(hertz * 100))

    # -- Amplitude (mit Daempfung) -------------------------------------------------------

    def get_amplitude(self, channel: int) -> float:
        check_channel(channel)
        damped = self._read_int(channel, "y") == 0
        return self._read_int(channel, "a") / (1000.0 if damped else 100.0)

    def set_amplitude(self, channel: int, volts: float) -> None:
        """Amplitude setzen, Daempfung nach amplitude_raw, Offset in Volt bleibt (Prozentwert neu). Reihenfolge
        so, dass zwischendurch nie mehr als alter bzw. neuer Wert anliegt: Daempfung EIN vor dem Wert, AUS
        nach dem Wert; ein kleinerer Offset-Prozentwert vor, ein groesserer nach der Amplitude."""
        check_channel(channel)
        check_amplitude(volts)
        old_amplitude = self.get_amplitude(channel)
        old_percent = self._read_int(channel, "o") - 120
        new_percent = offset_percent(old_percent / 100.0 * old_amplitude / 2.0, volts)
        damping, value = amplitude_raw(volts)
        if abs(new_percent) < abs(old_percent):
            self._write(channel, "o", round(new_percent) + 120)
        if damping == 0:
            self._write(channel, "y", 0)
            self._write(channel, "a", value)
        else:
            self._write(channel, "a", value)
            self._write(channel, "y", 1)
        if abs(new_percent) >= abs(old_percent) and round(new_percent) != old_percent:
            self._write(channel, "o", round(new_percent) + 120)

    # -- Offset (Prozent der halben Amplitude) -------------------------------------------

    def _offset_from(self, channel: int, amplitude: float) -> float:
        return (self._read_int(channel, "o") - 120) / 100.0 * amplitude / 2.0

    def get_offset(self, channel: int) -> float:
        check_channel(channel)
        return self._offset_from(channel, self.get_amplitude(channel))

    def set_offset(self, channel: int, volts: float) -> None:
        check_channel(channel)
        check_offset(volts)
        self._write(channel, "o", round(offset_percent(volts, self.get_amplitude(channel))) + 120)

    # -- Tastverhaeltnis -----------------------------------------------------------------

    def get_duty(self, channel: int) -> float:
        check_channel(channel)
        return self._read_int(channel, "d") / 10.0

    def set_duty(self, channel: int, percent: float) -> None:
        check_channel(channel)
        check_duty(percent)
        self._write(channel, "d", round(percent * 10))


# -- Modulfunktionen wie jds66xx.driver (fuer Treiber und Simulator) -------------------------

def read_field(gen, field: str, channel: int = 0):
    if field == "outputs":
        return gen.get_outputs()
    getter = {"waveform": gen.get_waveform, "frequency": gen.get_frequency, "amplitude": gen.get_amplitude,
              "offset": gen.get_offset, "duty": gen.get_duty}[field]
    return getter(channel)


def readback_fields(field: str, channel: int = 0) -> tuple[tuple[str, int], ...]:
    """Nach dem Schreiben zurueckzulesen: das Feld selbst; bei der Amplitude auch der Offset (Prozentwert
    nachgeschrieben bzw. am Geraet begrenzt)."""
    if field == "outputs":
        return (("outputs", 0),)
    if field == "amplitude":
        return (("amplitude", channel), ("offset", channel))
    return ((field, channel),)


def duty_refusal(code: int, channel: int) -> str:
    if code in DUTY_WAVEFORMS:
        return ""
    return (f"Tastgrad wirkt nur bei Rechteck -- Kanal {channel} steht auf {waveform_name(code)} "
            f"(erst Rechteck waehlen)")


def amplitude_refusal(code: int, channel: int) -> str:
    return ""


def set_waveform_checked(gen, channel: int, code: int) -> None:
    gen.set_waveform(channel, code)


def set_amplitude_checked(gen, channel: int, volts: float) -> None:
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
    raise FunctionGeneratorValueError(f"Der MHS-5200A kennt {field} nicht (Modulation und Lastwiderstand nur am "
                                      f"Generator im UNI-T-Oszilloskop)")


def outputs_off(gen) -> None:
    gen.set_outputs(False, False)


def state_dict(gen) -> dict:
    """Voller Zustand im Format von device_worker.fg_state (beim Verbinden)."""
    return {
        "profile": PROFILE,
        "outputs": list(gen.get_outputs()),
        "channels": [
            {"waveform": gen.get_waveform(ch), "frequency": gen.get_frequency(ch),
             "amplitude": gen.get_amplitude(ch), "offset": gen.get_offset(ch), "duty": gen.get_duty(ch)}
            for ch in CHANNELS
        ],
        "phase": None,
    }


if __name__ == "__main__":
    with MHS5200A.open_first() as gen:
        print("Verbunden:", gen.identify())
        print("Zustand:", state_dict(gen))

"""Treiber fuer das Manson/reichelt HCS-3400/3402/3404 USB Labornetzteil.

Kommunikation ueber den virtuellen USB-COM-Port eines Silicon Labs
CP210x-Wandlers (Windows: COMx, Linux: /dev/ttyUSB0). Anders als bei
einem echten USB-CDC-Geraet ist hier ein passender UART-Chip verbaut,
die Baudrate muss also stimmen (laut Test gegen reale Hardware: 9600 8N1).

Kommandoformat laut Anleitung Kap. 12.2:
    KOMMANDO<param>...<CR>
    Antwort:  <werte><CR>OK<CR>   (bei GET-Befehlen)
              OK<CR>              (bei SET-Befehlen)

Zahlenfelder sind i.d.R. 3-stellig, zehnfach skaliert (eine Nachkomma-
stelle, z.B. "503" = 50.3). Ausnahme: GETD liefert 4-stellige, hundert-
fach skalierte Felder (zwei Nachkommastellen) plus eine Statusziffer.
Alle Werte wurden gegen die reale Hardware (HCS-3404, Max 60.5V/9.0A)
verifiziert.

Ausgang und Reglersperre (in der Anleitung Kap. 12.2 NICHT dokumentiert, laut
sigrok-Wiki fuer die HCS-3xxx-Reihe und am 04.11.2026 am HCS-3404 (60 V, USB-
Seriennummer 0001) bestaetigt):
    SOUT0 / SOUT1   Ausgang EIN / AUS (Antwort OK)
    GOUT            Ausgangszustand: "0" = EIN, "1" = AUS (Antwort sofort nach SOUT)
    ENDS            Tastatur freigeben
Jeder Befehl sperrt die Regler (Spannung/Strom) am Geraet fuer rund 5 s (LED "Rear control");
bei Dauerabfrage ist es also nie von Hand bedienbar. ENDS hebt die Sperre sofort auf, siehe
release_keyboard. Es nach jedem Befehl zu senden (0.25.0/0.25.1) bringt nichts: das Geraet hat keine
Ausgangstaste, eine gleichzeitige Bedienung von Hand und per Software ist also nicht sinnvoll, und die
LED blinkt trotzdem im Takt der Abfrage. Befehl und ENDS in einem Schreibvorgang zu senden geht nicht
(der zweite Befehl wird nicht verarbeitet). Stattdessen gibt LabControl das Geraet zur Handbedienung
frei (DeviceWorker.set_psu_manual) und spricht es dann gar nicht mehr an.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

try:   # Linux: ein abgezogener USB-Adapter meldet tcflush/tcsetattr als termios.error (kein OSError)
    import termios
    SERIAL_ERRORS: tuple = (serial.SerialException, OSError, termios.error)
except ImportError:   # Windows
    SERIAL_ERRORS = (serial.SerialException, OSError)

USB_VID = 0x10C4
USB_PID = 0xEA60
DEFAULT_BAUDRATE = 9600

# Laut Anleitung (Kap. 10, Technische Daten) ist die Ausgangsspannung nur ab
# 1 V einstellbar ("Ausgangsspannung variabel: 1 - 16/32/60 V"). Gegen die
# reale Hardware verifiziert: VOLT000/SOVP000 werden vom Geraet ignoriert
# (keine Antwort, kein OK) -- ohne diese Pruefung liefe die Anfrage in den
# Timeout und der Treiber wuerde das faelschlich als Verbindungsabbruch werten.
MIN_VOLTAGE = 1.0

# Schreibversuche je Sollwert, bevor ein Fehler gemeldet wird (siehe _set_verified).
SET_ATTEMPTS = 3

# Ausgangszustand laut GOUT.
OUTPUT_ON_REPLY = "0"
OUTPUT_OFF_REPLY = "1"

# Ein Befehl, der laenger als das dauert, wird als Warnung geloggt (normal ~16 ms). Hoechstens
# eine Meldung je Intervall.
SLOW_EXCHANGE_S = 0.15
SLOW_LOG_INTERVAL_S = 10.0

logger = logging.getLogger(__name__)


class PowerSupplyError(RuntimeError):
    """Fehler bei der Kommunikation mit dem Labornetzteil."""


class PowerSupplyValueError(PowerSupplyError):
    """Wert ausserhalb des vom Geraet akzeptierten Bereichs.

    Kein Verbindungsproblem -- im Gegensatz zu anderen PowerSupplyError
    sollte dies NICHT dazu fuehren, dass der Aufrufer die Verbindung trennt.
    """


@dataclass
class Display:
    voltage: float
    current: float
    constant_current: bool  # True = CC-Modus, False = CV-Modus


class HCS34xx:
    _slow_logged_at = 0.0

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0):
        self._ser = serial.Serial(
            port=port, baudrate=baudrate, bytesize=8, parity="N",
            stopbits=1, timeout=timeout,
        )
        self._ser.reset_input_buffer()

    @classmethod
    def discover(cls) -> list[str]:
        """Listet alle CP210x-COM-Ports auf.

        Achtung: VID/PID 10C4:EA60 ist die generische Silicon-Labs-
        Werksvorgabe und wird von vielen CP210x-Geraeten verwendet (nicht
        nur diesem Netzteil). Bei mehreren angeschlossenen CP210x-Geraeten
        muss der Port ggf. anhand von Description/Seriennummer oder
        manuell ausgewaehlt werden.
        """
        return [
            info.device for info in list_ports.comports()
            if info.vid == USB_VID and info.pid == USB_PID
        ]

    @classmethod
    def discover_ports(cls) -> list:
        """Wie discover(), liefert aber die vollen ListPortInfo-Objekte
        (u.a. .serial_number) fuer die Unterscheidung mehrerer Geraete."""
        return [
            info for info in list_ports.comports()
            if info.vid == USB_VID and info.pid == USB_PID
        ]

    @classmethod
    def open_first(cls, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0) -> "HCS34xx":
        candidates = cls.discover()
        if not candidates:
            raise PowerSupplyError(
                f"Kein CP210x-Geraet (VID={USB_VID:04x} PID={USB_PID:04x}) gefunden."
            )
        if len(candidates) > 1:
            raise PowerSupplyError(
                f"Mehrere CP210x-Geraete gefunden: {candidates}. "
                "Bitte Port explizit angeben (HCS34xx(port=...))."
            )
        return cls(candidates[0], baudrate=baudrate, timeout=timeout)

    def close(self) -> None:
        self._ser.close()

    def __enter__(self) -> "HCS34xx":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- low level -----------------------------------------------------

    def _query(self, cmd: str) -> str:
        # pyserial wirft bei einem ungueltig gewordenen Port-Handle (z.B. nach
        # Windows-Standby/Wakeup: "ClearCommError failed" o.ae.) ein rohes
        # SerialException/OSError statt eines PowerSupplyError -- ohne diesen
        # Fang wuerde device_worker.py (faengt nur PowerSupplyError) das
        # Geraet weiterhin als verbunden fuehren, obwohl die Verbindung tot ist.
        started = time.monotonic()
        try:
            self._ser.reset_input_buffer()
            self._ser.write((cmd + "\r").encode("ascii"))
            raw = self._ser.read_until(b"OK\r")
        except SERIAL_ERRORS as exc:
            raise PowerSupplyError(f"Kommunikationsfehler auf Port {self._ser.port}: {exc}") from exc
        if not raw.endswith(b"OK\r"):
            raise PowerSupplyError(
                f"Keine/unklare Antwort auf {cmd!r} (Timeout): {raw!r}"
            )
        value = raw[: -len(b"OK\r")].strip(b"\r")
        self._log_if_slow(cmd, started)
        return value.decode("ascii", errors="replace")

    def _log_if_slow(self, cmd: str, started: float) -> None:
        now = time.monotonic()
        if now - started < SLOW_EXCHANGE_S or now - self._slow_logged_at < SLOW_LOG_INTERVAL_S:
            return
        self._slow_logged_at = now
        logger.warning("HCS34xx %s: %s dauerte %.0f ms", self._ser.port, cmd, (now - started) * 1000)

    def release_keyboard(self) -> bool:
        """Gibt die Regler am Geraet wieder frei (ENDS), statt auf das Ende der rund 5 s langen
        Sperre nach dem letzten Befehl zu warten. Best effort: ein Fehler ist kein Verbindungsfehler
        (die Sperre faellt von selbst), True nur bei quittiertem ENDS."""
        try:
            self._ser.reset_input_buffer()
            self._ser.write(b"ENDS\r")
            return self._ser.read_until(b"OK\r").endswith(b"OK\r")
        except SERIAL_ERRORS:
            return False

    @staticmethod
    def _decode_fields(text: str, field_width: int, scale: float) -> list[float]:
        if not text or len(text) % field_width != 0 or not text.isdigit():
            raise PowerSupplyError(f"Unerwartete Antwort: {text!r}")
        return [
            int(text[i : i + field_width]) / scale
            for i in range(0, len(text), field_width)
        ]

    @staticmethod
    def _encode_field(value: float, field_width: int, scale: float) -> str:
        return str(round(value * scale)).zfill(field_width)

    # -- limits ------------------------------------------------------------

    def get_max(self) -> tuple[float, float]:
        """Maximale Ausgangsspannung/-strom des Geraets (V, A)."""
        v, i = self._decode_fields(self._query("GMAX"), 3, 10)
        return v, i

    # -- setpoints -----------------------------------------------------------

    def _set_verified(self, cmd: str, value: float, index: int) -> None:
        """Sollwert setzen und per GETS zuruecklesen (BUGS_OFFEN #37).

        Das Geraet quittiert mit "OK", uebernimmt den Wert aber gelegentlich
        nicht (an der Hardware reproduziert: erster Schreibbefehl nach einer
        Pause geht verloren, erst der zweite wirkt). "OK" allein beweist
        deshalb nichts.
        Bis zu SET_ATTEMPTS Versuche; stimmt der Sollwert danach nicht,
        PowerSupplyValueError (Verbindung ist ok, der Wert wirkt aber nicht).
        """
        want = round(value * 10)
        got = None
        for _ in range(SET_ATTEMPTS):
            self._query(cmd + self._encode_field(value, 3, 10))
            got = self.get_setpoint()[index]
            if round(got * 10) == want:
                return
        raise PowerSupplyValueError(
            f"{cmd} {value}A/V nicht uebernommen: Sollwert nach {SET_ATTEMPTS} "
            f"Versuchen {got} (Geraet quittierte trotzdem mit OK)."
        )

    # -- output ---------------------------------------------------------------

    def get_output(self) -> bool:
        """True, wenn der Ausgang eingeschaltet ist (GOUT: "0" = EIN, "1" = AUS)."""
        reply = self._query("GOUT").strip()
        if reply == OUTPUT_ON_REPLY:
            return True
        if reply == OUTPUT_OFF_REPLY:
            return False
        raise PowerSupplyValueError(f"Unerwartete Antwort auf GOUT: {reply!r}")

    def set_output(self, on: bool) -> None:
        """Schaltet den Ausgang ein/aus (SOUT0/SOUT1) und liest den Zustand mit GOUT zurueck.

        Echtes Ausgang-AUS: Sollwerte (Spannung, Strom) bleiben unveraendert. Die Spannung am
        Ausgang sinkt ohne Last langsam (Entladen, am Geraet ~10 s bis unter 0,6 V), der Schalter
        selbst wirkt sofort. Wie bei den Sollwerten (siehe _set_verified) beweist "OK" allein
        nichts -- stimmt der Zustand nach SET_ATTEMPTS Versuchen nicht, gibt es einen Fehler
        (PowerSupplyValueError: die Verbindung ist ok, der Schalter wirkt aber nicht)."""
        reply = OUTPUT_ON_REPLY if on else OUTPUT_OFF_REPLY
        got = None
        for _ in range(SET_ATTEMPTS):
            self._query("SOUT" + reply)
            got = self._query("GOUT").strip()
            if got == reply:
                return
        raise PowerSupplyValueError(
            f"Ausgang {'EIN' if on else 'AUS'} nicht uebernommen: GOUT meldet nach {SET_ATTEMPTS} "
            f"Versuchen {got!r} (Geraet quittierte trotzdem mit OK)."
        )

    def set_voltage(self, volts: float) -> None:
        if volts < MIN_VOLTAGE:
            raise PowerSupplyValueError(
                f"Spannung {volts}V unterschreitet das Minimum von {MIN_VOLTAGE}V "
                "(Geraet nimmt Werte darunter kommentarlos nicht an)."
            )
        try:
            self._set_verified("VOLT", volts, 0)
        except PowerSupplyValueError:
            raise
        except PowerSupplyError:
            if self._probe_alive():
                raise PowerSupplyValueError(
                    f"Spannung {volts}V wurde vom Geraet nicht angenommen "
                    "(vermutlich ueber der aktuell eingestellten OVP-Schwelle)."
                )
            raise

    def set_current(self, amps: float) -> None:
        try:
            self._set_verified("CURR", amps, 1)
        except PowerSupplyValueError:
            raise
        except PowerSupplyError:
            if self._probe_alive():
                raise PowerSupplyValueError(
                    f"Strom {amps}A wurde vom Geraet nicht angenommen "
                    "(vermutlich ueber der aktuell eingestellten OCP-Schwelle)."
                )
            raise

    def _probe_alive(self) -> bool:
        """Lebenszeichen-Abfrage nach einem Timeout auf VOLT/CURR.

        Das Geraet ignoriert Sollwerte oberhalb der aktuell eingestellten
        OVP/OCP-Schwelle kommentarlos (keine Antwort, kein OK) -- genauso wie
        Werte unter MIN_VOLTAGE (siehe Modul-Docstring). Anders als bei
        MIN_VOLTAGE laesst sich das hier nicht statisch vorab pruefen, da OVP/
        OCP zur Laufzeit (auch manuell am Geraet) veraendert werden koennen.
        Antwortet das Geraet auf eine harmlose GET-Abfrage noch, war der
        vorige Timeout ein abgelehnter Wert und kein Verbindungsabbruch.
        """
        try:
            self.get_display()
            return True
        except PowerSupplyError:
            return False

    def get_setpoint(self) -> tuple[float, float]:
        """Aktuell voreingestellte Spannung/Strom (V, A)."""
        v, i = self._decode_fields(self._query("GETS"), 3, 10)
        return v, i

    # -- live display -----------------------------------------------------

    def get_display(self) -> Display:
        """Aktuell angezeigte Spannung/Strom sowie CV/CC-Status.

        Antwort sind 9 Ziffern (VVVVIIIIS). Kommt stattdessen etwas anderes -- am Pi am 12.11.2026 ein
        leeres "OK", vermutlich das verspaetete OK eines Sollwert-Befehls --, wird einmal neu gefragt;
        bleibt es unklar, ist das ein PowerSupplyError (vorher ein ValueError, den der Worker als
        unerwarteten Fehler behandelte)."""
        raw = self._query("GETD")
        if not (len(raw) >= 9 and raw[:9].isdigit()):
            logger.info("HCS34xx %s: unklare Antwort auf GETD (%r), frage erneut", self._ser.port, raw)
            raw = self._query("GETD")
            if not (len(raw) >= 9 and raw[:9].isdigit()):
                raise PowerSupplyError(f"Unklare Antwort auf 'GETD': {raw!r}")
        v_text, i_text, status_text = raw[0:4], raw[4:8], raw[8:9]
        return Display(
            voltage=int(v_text) / 100,
            current=int(i_text) / 100,
            constant_current=(status_text == "1"),
        )

    # -- protection thresholds --------------------------------------------

    def get_ovp(self) -> float:
        return self._decode_fields(self._query("GOVP"), 3, 10)[0]

    def set_ovp(self, volts: float) -> None:
        if volts < MIN_VOLTAGE:
            raise PowerSupplyValueError(
                f"OVP-Schwelle {volts}V unterschreitet das Minimum von {MIN_VOLTAGE}V "
                "(Geraet nimmt Werte darunter kommentarlos nicht an)."
            )
        self._query("SOVP" + self._encode_field(volts, 3, 10))

    def get_ocp(self) -> float:
        return self._decode_fields(self._query("GOCP"), 3, 10)[0]

    def set_ocp(self, amps: float) -> None:
        self._query("SOCP" + self._encode_field(amps, 3, 10))

    # -- memory presets (P1/P2/P3) ------------------------------------------

    def get_memory(self) -> list[tuple[float, float]]:
        """Liest die 3 internen Presets als [(V, A), ...]."""
        raw = self._query("GETM")
        values = self._decode_fields(raw, 3, 10)
        return list(zip(values[0::2], values[1::2]))

    def set_memory(self, presets: list[tuple[float, float]]) -> None:
        """Schreibt alle 3 internen Presets. presets = [(V, A), (V, A), (V, A)]."""
        if len(presets) != 3:
            raise ValueError("Es muessen genau 3 Presets angegeben werden")
        parts = []
        for volts, amps in presets:
            parts.append(self._encode_field(volts, 3, 10))
            parts.append(self._encode_field(amps, 3, 10))
        self._query("PROM" + "".join(parts))

    def recall_memory(self, index: int) -> None:
        """Uebernimmt Preset 0/1/2 als aktuellen Sollwert."""
        if index not in (0, 1, 2):
            raise ValueError("index muss 0, 1 oder 2 sein")
        self._query(f"RUNM{index}")


if __name__ == "__main__":
    with HCS34xx.open_first() as psu:
        print("Max V/A:", psu.get_max())
        print("Sollwert V/A:", psu.get_setpoint())
        print("Anzeige:", psu.get_display())
        print("OVP:", psu.get_ovp(), "OCP:", psu.get_ocp())
        print("Presets:", psu.get_memory())

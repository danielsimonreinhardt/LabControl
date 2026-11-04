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
bei Dauerabfrage waere es also nie bedienbar. ENDS direkt nach dem Befehl hebt
die Sperre sofort auf, siehe _release_keyboard.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

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
    # Nach jedem Befehl ENDS senden, damit die Regler am Geraet bedienbar bleiben (siehe
    # _release_keyboard). Faellt auf False, wenn ein Geraet ENDS nicht quittiert.
    _release_keys = True

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
        try:
            self._ser.reset_input_buffer()
            self._ser.write((cmd + "\r").encode("ascii"))
            raw = self._ser.read_until(b"OK\r")
        except (serial.SerialException, OSError) as exc:
            raise PowerSupplyError(f"Kommunikationsfehler auf Port {self._ser.port}: {exc}") from exc
        if not raw.endswith(b"OK\r"):
            raise PowerSupplyError(
                f"Keine/unklare Antwort auf {cmd!r} (Timeout): {raw!r}"
            )
        value = raw[: -len(b"OK\r")].strip(b"\r")
        self._release_keyboard()
        return value.decode("ascii", errors="replace")

    def _release_keyboard(self) -> None:
        """Gibt die Regler am Geraet wieder frei (ENDS).

        Jeder Befehl schaltet das Geraet fuer rund 5 s in "Rear control" und sperrt die Regler;
        mit der Dauerabfrage der App war es dadurch nie von Hand bedienbar. ENDS direkt nach dem
        Befehl hebt die Sperre sofort auf (am Geraet geprueft: auch bei 10 Hz bleibt die LED aus
        und die Regler reagieren; ein Zyklus Befehl + ENDS dauert ~32 ms).

        Ein Fehler hier ist kein Verbindungsfehler: der naechste Befehl meldet ihn selbst. Quittiert
        ein Geraet ENDS gar nicht, wird es fuer diese Verbindung abgeschaltet, statt jeden Befehl
        in den Timeout laufen zu lassen."""
        if not self._release_keys:
            return
        try:
            self._ser.reset_input_buffer()
            self._ser.write(b"ENDS\r")
            raw = self._ser.read_until(b"OK\r")
        except (serial.SerialException, OSError):
            return
        if not raw.endswith(b"OK\r"):
            self._release_keys = False

    @staticmethod
    def _decode_fields(text: str, field_width: int, scale: float) -> list[float]:
        if len(text) % field_width != 0:
            raise PowerSupplyError(f"Unerwartete Antwortlaenge: {text!r}")
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
        """Aktuell angezeigte Spannung/Strom sowie CV/CC-Status."""
        raw = self._query("GETD")
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

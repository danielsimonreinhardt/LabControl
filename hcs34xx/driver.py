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

Wichtig: Der dokumentierte Befehlssatz enthaelt KEIN Kommando zum
Ein-/Ausschalten des Ausgangs. Das ist laut Anleitung nur ueber den
analogen Fernsteueranschluss (8-pol. Rundstecker, Pin 5 gegen Masse)
oder manuell am Geraet moeglich.
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
        self._ser.reset_input_buffer()
        self._ser.write((cmd + "\r").encode("ascii"))
        raw = self._ser.read_until(b"OK\r")
        if not raw.endswith(b"OK\r"):
            raise PowerSupplyError(
                f"Keine/unklare Antwort auf {cmd!r} (Timeout): {raw!r}"
            )
        value = raw[: -len(b"OK\r")].strip(b"\r")
        return value.decode("ascii", errors="replace")

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

    def set_voltage(self, volts: float) -> None:
        if volts < MIN_VOLTAGE:
            raise PowerSupplyValueError(
                f"Spannung {volts}V unterschreitet das Minimum von {MIN_VOLTAGE}V "
                "(Geraet nimmt Werte darunter kommentarlos nicht an)."
            )
        self._query("VOLT" + self._encode_field(volts, 3, 10))

    def set_current(self, amps: float) -> None:
        self._query("CURR" + self._encode_field(amps, 3, 10))

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

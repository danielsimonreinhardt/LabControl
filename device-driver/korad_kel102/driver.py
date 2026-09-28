"""SCPI-Treiber fuer die Korad KEL102 elektronische Last.

Kommunikation ueber den virtuellen USB-CDC-COM-Port (Windows: COMx,
Linux: /dev/ttyACMx bzw. /dev/ttyUSBx). Die Baudrate wird vom Geraet
ueber USB ignoriert, ist aber fuer pyserial trotzdem anzugeben.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

USB_VID = 0x0416
USB_PID = 0x5011
DEFAULT_BAUDRATE = 115200

_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

FUNCTIONS = ("VOLT", "CURR", "RES", "POW", "SHORT")


class LoadError(RuntimeError):
    """Fehler bei der Kommunikation mit der elektronischen Last."""


@dataclass
class Measurement:
    voltage: float
    current: float
    power: float


class KoradKEL102:
    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0):
        self._ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        self._ser.reset_input_buffer()

    @classmethod
    def discover(cls) -> str | None:
        """Sucht den ersten passenden USB-VID/PID-COM-Port. Gibt None zurueck, wenn keiner gefunden wird."""
        for info in list_ports.comports():
            if info.vid == USB_VID and info.pid == USB_PID:
                return info.device
        return None

    @classmethod
    def discover_ports(cls) -> list:
        """Wie discover(), liefert aber alle passenden ListPortInfo-Objekte
        (u.a. .serial_number) fuer die Unterscheidung mehrerer Geraete."""
        return [
            info for info in list_ports.comports()
            if info.vid == USB_VID and info.pid == USB_PID
        ]

    @classmethod
    def open_first(cls, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0) -> "KoradKEL102":
        port = cls.discover()
        if port is None:
            raise LoadError(
                f"Kein Geraet mit VID={USB_VID:04x} PID={USB_PID:04x} gefunden. "
                "Ist die Last per USB angeschlossen?"
            )
        return cls(port, baudrate=baudrate, timeout=timeout)

    def close(self) -> None:
        self._ser.close()

    def __enter__(self) -> "KoradKEL102":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- low level -----------------------------------------------------

    def _write(self, cmd: str) -> None:
        # pyserial wirft bei einem ungueltig gewordenen Port-Handle (z.B. nach
        # Windows-Standby/Wakeup: "ClearCommError failed" o.ae.) ein rohes
        # SerialException/OSError statt eines LoadError -- ohne diesen Fang
        # wuerde device_worker.py (faengt nur LoadError) das Geraet weiterhin
        # als verbunden fuehren, obwohl die Verbindung tot ist.
        try:
            self._ser.write((cmd + "\n").encode("ascii"))
        except (serial.SerialException, OSError) as exc:
            raise LoadError(f"Schreibfehler auf Port {self._ser.port}: {exc}") from exc

    def _read_line(self) -> str:
        try:
            raw = self._ser.readline()
        except (serial.SerialException, OSError) as exc:
            raise LoadError(f"Lesefehler auf Port {self._ser.port}: {exc}") from exc
        if not raw:
            raise LoadError(f"Keine Antwort vom Geraet (Timeout) auf Port {self._ser.port}")
        return raw.decode("ascii", errors="replace").strip()

    def _query(self, cmd: str) -> str:
        self._write(cmd)
        return self._read_line()

    @staticmethod
    def _parse_number(text: str) -> float:
        match = _NUMBER_RE.search(text)
        if not match:
            raise LoadError(f"Konnte Zahl nicht aus Antwort lesen: {text!r}")
        return float(match.group())

    # -- identification --------------------------------------------------

    def identify(self) -> str:
        return self._query("*IDN?")

    # -- input on/off ------------------------------------------------------

    def set_input(self, on: bool) -> None:
        self._write(f":INPut {'ON' if on else 'OFF'}")

    def get_input(self) -> bool:
        resp = self._query(":INPut?").upper()
        return resp.startswith("ON") or resp.strip() == "1"

    # -- function / mode ---------------------------------------------------

    def set_function(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in FUNCTIONS:
            raise ValueError(f"Unbekannter Modus {mode!r}, erlaubt: {FUNCTIONS}")
        self._write(f":FUNCtion {mode}")

    def get_function(self) -> str:
        return self._query(":FUNCtion?").upper()

    # -- setpoints -----------------------------------------------------------

    def set_voltage(self, volts: float) -> None:
        self._write(f":VOLTage {volts}V")

    def get_voltage_setpoint(self) -> float:
        return self._parse_number(self._query(":VOLTage?"))

    def set_current(self, amps: float) -> None:
        self._write(f":CURRent {amps}A")

    def get_current_setpoint(self) -> float:
        return self._parse_number(self._query(":CURRent?"))

    def set_resistance(self, ohms: float) -> None:
        self._write(f":RESistance {ohms}OHM")

    def get_resistance_setpoint(self) -> float:
        return self._parse_number(self._query(":RESistance?"))

    def set_power(self, watts: float) -> None:
        self._write(f":POWer {watts}W")

    def get_power_setpoint(self) -> float:
        return self._parse_number(self._query(":POWer?"))

    # -- live measurement ------------------------------------------------------

    def measure_voltage(self) -> float:
        return self._parse_number(self._query(":MEASure:VOLTage?"))

    def measure_current(self) -> float:
        return self._parse_number(self._query(":MEASure:CURRent?"))

    def measure_power(self) -> float:
        return self._parse_number(self._query(":MEASure:POWer?"))

    def measure(self) -> Measurement:
        return Measurement(
            voltage=self.measure_voltage(),
            current=self.measure_current(),
            power=self.measure_power(),
        )


if __name__ == "__main__":
    with KoradKEL102.open_first() as load:
        print("Verbunden:", load.identify())
        print("Modus:", load.get_function())
        print("Messwerte:", load.measure())

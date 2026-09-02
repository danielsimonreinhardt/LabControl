"""Vendor-unabhaengiger CAN-Treiber, wrapt python-can.

Unterstuetzt aktuell Vector- (z.B. CANcase XL, benoetigt die Vector XL
Driver Library) und PEAK-Interfaces (z.B. PCAN-USB, benoetigt PCAN-Basic) --
beides Fremd-Software, die separat installiert sein muss (siehe README.md).
Ausserhalb dieses Moduls ist nie von einem konkreten Hersteller die Rede,
nur noch von interface/channel/bitrate (siehe device_worker.py) -- neue
Interface-Typen lassen sich durch Ergaenzen von INTERFACE_LIST anbinden,
sofern python-can sie unterstuetzt.
"""
from __future__ import annotations

from dataclasses import dataclass

import can

INTERFACE_LIST = ["vector", "pcan"]
DEFAULT_BITRATE = 500_000


class CanError(RuntimeError):
    """Fehler bei der Kommunikation ueber den CAN-Bus."""


class CanConnectionError(CanError):
    """Verbindung zum Interface konnte nicht (mehr) hergestellt werden."""


@dataclass
class CanFrame:
    arbitration_id: int
    data: bytes
    extended: bool
    timestamp: float


class CanBus:
    def __init__(self, interface: str, channel: str, bitrate: int = DEFAULT_BITRATE):
        # python-can wirft je nach Backend/Fehlerursache sehr unterschiedliche
        # Exception-Typen (fehlende Vendor-DLL: OSError/ImportError, falscher
        # Kanal: eigene *InitializationError-Klassen, ...) -- breit gefangen
        # und auf die App-eigene Exception-Hierarchie abgebildet, analog zum
        # SerialException-Fang in hcs34xx/driver.py.
        try:
            self._bus = can.interface.Bus(interface=interface, channel=channel, bitrate=bitrate)
        except Exception as exc:
            raise CanConnectionError(
                f"Verbindung zu {interface}:{channel} fehlgeschlagen: {exc}"
            ) from exc
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate

    @staticmethod
    def discover_configs() -> list[dict]:
        """Verfuegbare Kanaele je unterstuetztem Interface-Typ.

        Liefert nur Kanaele, deren Vendor-Treiber tatsaechlich installiert
        ist -- ein nicht installiertes Backend wirft beim Erkennen eine
        Exception (fehlende DLL o.ae.), die hier pro Interface-Typ
        uebersprungen wird, damit z.B. ein fehlendes PCAN-Basic nicht auch
        die Vector-Erkennung verhindert.
        """
        configs: list[dict] = []
        for interface in INTERFACE_LIST:
            try:
                configs.extend(can.detect_available_configs(interfaces=[interface]))
            except Exception:
                continue
        return configs

    def close(self) -> None:
        self._bus.shutdown()

    def __enter__(self) -> "CanBus":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def send(self, arbitration_id: int, data: bytes, extended: bool = False) -> None:
        msg = can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=extended)
        try:
            self._bus.send(msg)
        except Exception as exc:
            raise CanError(f"Senden fehlgeschlagen: {exc}") from exc

    def recv(self, timeout: float = 0.0) -> CanFrame | None:
        """Nicht-blockierender Empfang (timeout=0.0) fuers Polling in
        device_worker.py -- liefert None, wenn kein Frame anliegt."""
        try:
            msg = self._bus.recv(timeout=timeout)
        except Exception as exc:
            raise CanConnectionError(f"Empfang fehlgeschlagen: {exc}") from exc
        if msg is None:
            return None
        return CanFrame(
            arbitration_id=msg.arbitration_id,
            data=bytes(msg.data),
            extended=msg.is_extended_id,
            timestamp=msg.timestamp,
        )

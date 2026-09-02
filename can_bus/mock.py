"""Simulierter CAN-Bus zum Testen der GUI ohne angeschlossene Hardware.

Bildet dieselbe oeffentliche Schnittstelle wie CanBus nach (siehe driver.py),
analog zu hcs34xx/mock.py und korad_kel102/mock.py. Erzeugt beim Abfragen
reihum ein paar synthetische Demo-Frames, damit Dashboard/Control-Tab/
Testablauf ohne reale Hardware durchgespielt werden koennen; gesendete
Frames werden nur intern vorgehalten (siehe sent_frames), nicht wirklich
auf einen Bus gelegt.
"""
from __future__ import annotations

import time
from itertools import count

from can_bus.driver import CanFrame

DEMO_FRAMES: list[tuple[int, bytes]] = [
    (0x100, bytes([0x01, 0x02, 0x03, 0x04])),
    (0x200, bytes([0xAA, 0xBB])),
    (0x7FF, bytes([0x00])),
]


class MockCanBus:
    def __init__(self, interface: str = "mock", channel: str = "SIM", bitrate: int = 500_000):
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self.sent_frames: list[CanFrame] = []
        self._demo_index = count()
        self._start = time.monotonic()

    def close(self) -> None:
        pass

    def __enter__(self) -> "MockCanBus":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def send(self, arbitration_id: int, data: bytes, extended: bool = False) -> None:
        self.sent_frames.append(
            CanFrame(arbitration_id, bytes(data), extended, time.monotonic() - self._start)
        )

    def recv(self, timeout: float = 0.0) -> CanFrame | None:
        arb_id, data = DEMO_FRAMES[next(self._demo_index) % len(DEMO_FRAMES)]
        return CanFrame(arb_id, data, False, time.monotonic() - self._start)

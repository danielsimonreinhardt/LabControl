"""Simulierter CAN-Bus zum Testen der GUI ohne angeschlossene Hardware.

Bildet dieselbe oeffentliche Schnittstelle wie CanBus nach (siehe driver.py),
analog zu hcs34xx/mock.py und korad_kel102/mock.py. Erzeugt reihum ein paar
synthetische Demo-Frames, damit Dashboard/Control-Tab/Testablauf ohne reale
Hardware durchgespielt werden koennen; gesendete Frames werden nur intern
vorgehalten (siehe sent_frames), nicht wirklich auf einen Bus gelegt.

recv() liefert -- wie CanBus.recv() bei einem echten, gerade ruhigen Bus --
ausserhalb von DEMO_INTERVAL_S None statt bei jedem Aufruf sofort einen neuen
Frame: device_worker._poll() ruft recv(timeout=0.0) im 100-ms-Takt bis zu
CAN_DRAIN_LIMIT-mal auf und haengt Treffer sofort an die Live-Traffic-Tabelle
im Control-Tab an (siehe control_tab.CanControlGroup.append_frame) -- ohne
diese Drosselung lieferte jeder einzelne dieser Aufrufe einen "neuen" Frame,
wodurch die Tabelle im Mock-Betrieb mit ~CAN_DRAIN_LIMIT/POLL_INTERVAL_MS
Frames pro Sekunde scheinbar endlos zulief (Nutzerfeedback: "endlos
Botschaften ... fuellen den Log").
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

# Abstand zwischen zwei simulierten Frames -- ein Wert, der wie ruhiger
# echter Busverkehr wirkt, statt bei jedem Poll-Zyklus (100 ms, siehe
# device_worker.POLL_INTERVAL_MS) sofort neue Daten zu liefern.
DEMO_INTERVAL_S = 0.5


class MockCanBus:
    def __init__(
        self,
        interface: str = "mock",
        channel: str = "SIM",
        bitrate: int = 500_000,
        serial_baudrate: int | None = None,
    ):
        # serial_baudrate nur der Vollstaendigkeit halber angenommen (gleiche
        # Konstruktor-Signatur wie CanBus.__init__, siehe driver.py) -- ein
        # simulierter Bus hat keine echte serielle Verbindung, der Wert bleibt
        # ungenutzt, wird aber wie bei CanBus als Attribut gespiegelt, falls
        # aufrufender Code (z.B. eine Statusanzeige) ihn einmal abfragen will.
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self.serial_baudrate = serial_baudrate
        self.sent_frames: list[CanFrame] = []
        self._demo_index = count()
        self._start = time.monotonic()
        self._next_demo_at = self._start + DEMO_INTERVAL_S

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
        now = time.monotonic()
        if now < self._next_demo_at:
            return None
        self._next_demo_at = now + DEMO_INTERVAL_S
        arb_id, data = DEMO_FRAMES[next(self._demo_index) % len(DEMO_FRAMES)]
        return CanFrame(arb_id, data, False, now - self._start)

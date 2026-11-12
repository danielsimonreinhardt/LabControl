"""Prueft den CAN-Lese-Thread (device_worker.CanReader, 0.57.1; Nutzerfeedback 12.11.2026: am Pi kamen die
Botschaften stossweise, weil CAN erst nach Last und Netzteilen im Poll-Zyklus gelesen wurde):

- Frames gehen sofort an die Anzeige, auch wenn der Worker-Thread gerade blockiert (langsamer Poll-Zyklus)
- fuer die Knoten bleiben sie in Reihenfolge in `frames`, der Empfangszaehler stimmt
- ein CanError landet als Ausnahme in `frames` (Worker trennt dann), der Thread endet
- Senden wird nicht blockiert (recv ohne Warten, Sperre nur kurz)
- im Simulationsmodus laufen die Frames ueber den Lese-Thread in die CAN-Kachel und den Batterie-Simulator

Offscreen, keine Hardware.  Aufruf:  python tools/check_can_reader.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class FakeBus:
    """Liefert alle 20 ms einen Frame (wie ein Busteilnehmer), danach optional einen Fehler."""

    def __init__(self, count: int, fail_after: bool = False) -> None:
        from can_bus.driver import CanFrame
        self._frame = CanFrame
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._count = count
        self._given = 0
        self._fail = fail_after
        self.sent: list[float] = []

    def recv(self, timeout: float = 0.0):
        from can_bus.driver import CanError
        with self._lock:
            if self._given >= self._count:
                if self._fail:
                    raise CanError("Adapter abgezogen")
                return None
            due = self._start + 0.02 * (self._given + 1)
            if time.monotonic() < due:
                return None
            self._given += 1
            return self._frame(0x100 + self._given, bytes([self._given]), False, due - self._start)

    def send(self, *_args) -> None:
        t0 = time.monotonic()
        with self._lock:
            pass
        self.sent.append(time.monotonic() - t0)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from device_worker import CanReader

    print("Lese-Thread")
    bus = FakeBus(25)
    arrivals: list[float] = []
    reader = CanReader("can:test", bus, lambda _d, _f: arrivals.append(time.monotonic()))
    reader.start()
    time.sleep(0.6)   # Worker-Thread "blockiert" (langsamer Poll-Zyklus), der Lese-Thread laeuft weiter
    check("alle 25 Frames waehrend des blockierten Workers angezeigt", len(arrivals) == 25, str(len(arrivals)))
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    # Mittel ~20 ms wie am Bus; einzelne Ausreisser durch die Zeitscheiben des Betriebssystems (Windows ~16 ms),
    # aber kein Stoss wie frueher nach einem 700-ms-Zyklus
    mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
    check("gleichmaessig (Mittel ~20 ms, kein Abstand ueber 150 ms)",
          gaps and 0.015 < mean_gap < 0.03 and max(gaps) < 0.15,
          f"Mittel {mean_gap * 1000:.0f} ms, max {max(gaps) * 1000 if gaps else 0:.0f} ms")
    items = []
    while not reader.frames.empty():
        items.append(reader.frames.get_nowait())
    check("Knoten bekommen alle Frames in Reihenfolge", [f.arbitration_id for f in items]
          == [0x101 + n for n in range(25)])
    check("Empfangszaehler", reader.rx_count == 25)
    for _ in range(20):
        bus.send(0x130, b"\x00")
    check("Senden wird nicht blockiert (< 5 ms)", max(bus.sent) < 0.005, f"{max(bus.sent) * 1000:.1f} ms")
    reader.stop()
    check("Anhalten beendet den Thread", not reader.is_alive())

    print("Fehler")
    from can_bus.driver import CanError
    bus = FakeBus(3, fail_after=True)
    reader = CanReader("can:test", bus, lambda *_: None)
    reader.start()
    reader.join(timeout=2.0)
    items = []
    while not reader.frames.empty():
        items.append(reader.frames.get_nowait())
    check("CanError landet nach den Frames in der Warteschlange, Thread endet",
          len(items) == 4 and isinstance(items[-1], CanError) and not reader.is_alive(), str(items))

    print("Simulation")
    import no_hardware
    no_hardware.block()
    from device_worker import DeviceWorker, SIM_CAN_ID
    worker = DeviceWorker(simulation_mode=True, can_configs=[], scpi_scopes=[])
    seen: list = []
    worker.can_frame_received.connect(lambda d, *a: seen.append(d))
    worker._add_mock_can()
    check("Lese-Thread fuer den simulierten Bus laeuft", SIM_CAN_ID in worker._can_readers
          and worker._can_readers[SIM_CAN_ID].is_alive())
    end = time.monotonic() + 3.0
    while time.monotonic() < end and not seen:
        app.processEvents()
        time.sleep(0.02)
    check("Frames kommen ohne Poll-Zyklus in der Anzeige an", bool(seen))
    worker._poll_devices({})
    check("Poll-Zyklus uebernimmt den Empfangszaehler", worker._can_stats[SIM_CAN_ID][1] >= 1,
          str(worker._can_stats.get(SIM_CAN_ID)))
    worker._close_can(SIM_CAN_ID)
    check("Schliessen beendet den Lese-Thread", SIM_CAN_ID not in worker._can_readers)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CAN-Geraete, die ueber ein bereits konfiguriertes CAN-Interface sprechen.

Ein CAN-Interface (CanBus, siehe driver.py) ist in LabControl ein eigenes Geraet
mit Rohdaten-Anzeige. Manche Geraete sprechen selbst UEBER CAN (zuerst der
Batterie-Simulator, device-driver/battsim): sie oeffnen keinen eigenen Bus,
sondern haengen sich als CanNode an ein vorhandenes Interface. Grund: PCAN und
SLCAN lassen einen Kanal nur einmal oeffnen, und ein Simulator soll denselben
Bus nutzen koennen wie der Pruefling.

Zwei Bausteine, beide Qt-frei:

- ``CanNode``: Basisklasse eines Geraets am Bus. Bekommt empfangene Frames
  (``on_frame``), meldet faellige Sendeframes (``due_frames``) und ob das
  Geraet antwortet (``online``).
- ``CanScheduler``: eigener Thread je Interface, der die faelligen Frames aller
  angehaengten Knoten sendet. Bewusst NICHT im 100-ms-Poll des DeviceWorkers:
  der Batterie-Simulator verlangt 0x130 alle 100 ms und faellt nach 1,5-3 s
  ohne Botschaft aus dem Fernsteuerbetrieb; der Poll-Thread kann durch
  blockierende Zugriffe anderer Geraete (serielle Timeouts, Oszilloskop)
  laenger stehen. Endet LabControl ungeplant, endet mit dem Thread auch das
  Senden -- das Geraet faellt dann ueber seine eigene Zeitueberwachung zurueck.

Threading: ``on_frame`` und die Steuer-Methoden der Knoten laufen im
DeviceWorker-Thread, ``due_frames`` im Scheduler-Thread. Jeder Knoten
schuetzt seinen Zustand deshalb selbst mit einem Lock (siehe CanNode._lock).
CanBus.send/recv sind seit 0.22.0 ebenfalls per Lock serialisiert.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from can_bus.driver import CanError, CanFrame

logger = logging.getLogger(__name__)

# Aufloesung des Sendetakts. 10 ms reichen fuer Zyklen ab 100 ms (Jitter
# hoechstens ein Zehntel) und kosten praktisch keine Rechenzeit.
SCHEDULER_TICK_S = 0.01


@dataclass
class TxFrame:
    arbitration_id: int
    data: bytes
    extended: bool = False


class CanNode:
    """Basisklasse eines Geraets, das ueber ein CAN-Interface spricht.

    Unterklassen setzen ``rx_ids`` (Arbitration-IDs, die sie empfangen wollen)
    und ueberschreiben ``on_frame``/``due_frames``/``online``. Fehler beim
    Senden landen in ``last_tx_error`` und zaehlen nicht als Geraetefehler --
    ob das Geraet antwortet, entscheidet allein ``online``.
    """

    rx_ids: frozenset[int] = frozenset()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.last_tx_error = ""

    def on_frame(self, frame: CanFrame) -> None:  # pragma: no cover - Schnittstelle
        raise NotImplementedError

    def due_frames(self, now: float) -> list[TxFrame]:  # pragma: no cover - Schnittstelle
        raise NotImplementedError

    def online(self, now: float | None = None) -> bool:  # pragma: no cover - Schnittstelle
        raise NotImplementedError

    def shutdown_frames(self) -> list[TxFrame]:
        """Frames fuer ein geordnetes Ende (z.B. Ausgaenge sperren), werden
        beim Abhaengen einmal direkt gesendet. Standard: keine."""
        return []


class CanScheduler:
    """Sendet die faelligen Frames aller Knoten eines Interfaces.

    ``bus`` braucht nur ``send(arbitration_id, data, extended)`` (CanBus oder
    MockCanBus). ``sent_count`` zaehlt erfolgreich gesendete Frames, damit der
    DeviceWorker sie im TX-Zaehler des Interfaces mitfuehren kann.
    """

    def __init__(self, bus, name: str = "can") -> None:
        self._bus = bus
        self._nodes: list[CanNode] = []
        self._nodes_lock = threading.Lock()
        self._stop = threading.Event()
        self._count_lock = threading.Lock()
        self._sent = 0
        self._thread = threading.Thread(target=self._run, name=f"can-tx-{name}", daemon=True)
        self._thread.start()

    def attach(self, node: CanNode) -> None:
        with self._nodes_lock:
            if node not in self._nodes:
                self._nodes.append(node)

    def detach(self, node: CanNode, send_shutdown: bool = True) -> None:
        with self._nodes_lock:
            if node in self._nodes:
                self._nodes.remove(node)
        if send_shutdown:
            self._send_all(node, node.shutdown_frames())

    @property
    def nodes(self) -> list[CanNode]:
        with self._nodes_lock:
            return list(self._nodes)

    def take_sent_count(self) -> int:
        """Seit dem letzten Aufruf gesendete Frames (fuer den TX-Zaehler)."""
        with self._count_lock:
            sent, self._sent = self._sent, 0
        return sent

    def stop(self, send_shutdown: bool = True) -> None:
        """Haelt den Thread an; vorher auf Wunsch die Shutdown-Frames aller
        Knoten senden (geordnetes Ende, z.B. beim Schliessen der App)."""
        for node in self.nodes:
            self.detach(node, send_shutdown=send_shutdown)
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(SCHEDULER_TICK_S):
            now = time.monotonic()
            for node in self.nodes:
                try:
                    frames = node.due_frames(now)
                except Exception:  # noqa: BLE001 -- ein Knoten darf den Takt der anderen nicht beenden
                    logger.exception("CAN-Knoten %r: due_frames fehlgeschlagen", node)
                    continue
                self._send_all(node, frames)

    def _send_all(self, node: CanNode, frames: list[TxFrame]) -> None:
        for frame in frames:
            try:
                self._bus.send(frame.arbitration_id, frame.data, frame.extended)
            except CanError as exc:
                # Kein Abbruch: ein abgezogenes Interface meldet der Poll des
                # DeviceWorkers (recv), der dann den ganzen Scheduler stoppt.
                node.last_tx_error = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001 -- Thread darf nicht still sterben
                node.last_tx_error = str(exc)
                logger.exception("CAN-Senden 0x%X fehlgeschlagen", frame.arbitration_id)
                continue
            node.last_tx_error = ""
            with self._count_lock:
                self._sent += 1

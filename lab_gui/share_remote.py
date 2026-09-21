"""Bruecke fuer SCHREIBENDE Netzwerk-Anfragen: vom HTTP-Server-Thread zum
DeviceWorker und mit dem Ergebnis wieder zurueck (siehe share_api.py).

Der Weg einer Aktion:

  Server-Thread   share_api prueft (Token, Freigabe, Hauptschalter, Sperre,
                  Wertebereich) und ruft submit_action()
       |          -> Signal action_requested (aus einem Nicht-Qt-Thread ist
       v             emit() erlaubt und wird zur Queued Connection)
  GUI-Thread      main_window._on_share_action: LETZTE Pruefung von
       |          Sicherheitsabschaltung/Testlauf/Hauptschalter direkt vor
       v          dem Dispatch (der Schnappschuss darf veraltet sein)
  Worker-Thread   DeviceWorker.execute_remote_action -> _dispatch_action
       |          -> Signal remote_action_completed
       v
  GUI-Thread      RemoteBridge.on_worker_result setzt das Ergebnis und weckt
       |
       v
  Server-Thread   submit_action() kehrt mit dem Ergebnis zurueck

WARUM eine Antwort abgewartet wird (statt "abschicken und vergessen"): fuer
einen Client, der Messgeraete steuert -- besonders fuer einen KI-Agenten --
ist "Netzteil nicht verbunden" die entscheidende Information. Ein 202 ohne
Ergebnis liesse ihn raten. Nach REMOTE_TIMEOUT_S ohne Antwort kommt trotzdem
ein 202 ("pending"): das Kommando ist dann unterwegs und kann noch ausgefuehrt
werden, der Client muss per GET nachsehen.

WARUM ein eigenes Signal statt DeviceWorker.action_completed: das gehoert dem
TestRunner (siehe Docstring bei device_worker.send_can_frame). Ein
Fernsteuer-Ergebnis dort einzuspeisen wuerde einem laufenden Testablauf ein
fremdes Ergebnis unterschieben.

Schutz des Notaus: ALLE AUS darf nie hinter einem Stau von Schreibanfragen
warten. Deshalb duerfen hoechstens MAX_PENDING_WRITES Aktionen gleichzeitig auf
den Worker warten (weitere bekommen 429), und ein Token-Eimer begrenzt die
Schreibrate -- jede Aktion belegt den Worker fuer eine serielle Ein-/Ausgabe.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from share_api import RemoteResult

logger = logging.getLogger(__name__)

# Wie lange der Server-Thread auf das Geraet wartet. Ein Poll-Zyklus mit
# angeschlossener Hardware kann mehrere Sekunden dauern, deshalb grosszuegig.
REMOTE_TIMEOUT_S = 8.0
# ALLE AUS versucht jedes Geraet zweimal (siehe device_worker.all_outputs_off).
ALL_OFF_TIMEOUT_S = 15.0

# Gleichzeitig auf den Worker wartende Aktionen. Der Rest der 8 Server-Slots
# (share_server.MAX_CONCURRENT_REQUESTS) bleibt fuer Lesen und ALLE AUS frei.
MAX_PENDING_WRITES = 2

# Token-Eimer: kurzzeitig RATE_BURST Aktionen, danach RATE_PER_S je Sekunde.
RATE_BURST = 10
RATE_PER_S = 5.0


class _Pending:
    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: RemoteResult | None = None


class RemoteBridge(QObject):
    """Lebt im GUI-Thread. submit_*() sind fuer den Server-Thread gedacht und
    blockieren dort; alle Slots laufen im GUI-Thread."""

    # id, device_id, kind, action, value, channel, local (Aufruf vom selben Rechner)
    action_requested = Signal(int, str, str, str, float, int, bool)
    all_off_requested = Signal(int, str)                       # id, client

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._all_off_waiters: list[_Pending] = []
        self._write_slots = threading.BoundedSemaphore(MAX_PENDING_WRITES)
        self._tokens = float(RATE_BURST)
        self._refilled = time.monotonic()

    # -- Server-Thread ------------------------------------------------------

    def _take_token(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(float(RATE_BURST), self._tokens + (now - self._refilled) * RATE_PER_S)
            self._refilled = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True

    def submit_action(self, device_id: str, kind: str, action: str, value: float,
                      channel: int, client: str, local: bool = False) -> RemoteResult:
        if not self._take_token():
            result = RemoteResult("error", "rate_limited")
        elif not self._write_slots.acquire(blocking=False):
            result = RemoteResult("error", "busy")
        else:
            try:
                result = self._run_action(device_id, kind, action, value, channel, local)
            finally:
                self._write_slots.release()
        self._audit(client + (" (lokal)" if local else ""), f"{device_id} {action}"
                    + (f" wert={value:g}" if value else "")
                    + (f" kanal={channel}" if channel else ""), result)
        return result

    def _run_action(self, device_id: str, kind: str, action: str, value: float, channel: int,
                    local: bool) -> RemoteResult:
        req_id = next(self._ids)
        pending = _Pending()
        with self._lock:
            self._pending[req_id] = pending
        self.action_requested.emit(req_id, device_id, kind, action, value, channel, local)
        done = pending.event.wait(REMOTE_TIMEOUT_S)
        with self._lock:
            # Kam das Ergebnis nach dem Timeout, findet complete() keinen
            # Eintrag mehr und verwirft es -- der Client bekommt "pending".
            self._pending.pop(req_id, None)
        if done and pending.result is not None:
            return pending.result
        return RemoteResult("pending", message="Keine Rueckmeldung innerhalb der Wartezeit")

    def submit_all_off(self, client: str) -> RemoteResult:
        pending = _Pending()
        with self._lock:
            self._all_off_waiters.append(pending)
        self.all_off_requested.emit(next(self._ids), client)
        done = pending.event.wait(ALL_OFF_TIMEOUT_S)
        with self._lock:
            if pending in self._all_off_waiters:
                self._all_off_waiters.remove(pending)
        result = pending.result if done and pending.result is not None else RemoteResult(
            "pending", message="Keine Rueckmeldung innerhalb der Wartezeit")
        self._audit(client, "ALLE AUS", result)
        return result

    # -- GUI-Thread -----------------------------------------------------------

    @Slot(int, bool, str, str, float)
    def complete(self, request_id: int, ok: bool, code: str, message: str, value: float) -> None:
        """Meldet das Ergebnis einer Aktion. Direkt aufrufbar (Ablehnung in
        main_window._on_share_action) oder ueber on_worker_result."""
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return  # Anfrage ist bereits abgelaufen
        pending.result = RemoteResult("ok" if ok else "error", code, message, value)
        pending.event.set()

    @Slot(int, bool, str, float)
    def on_worker_result(self, request_id: int, ok: bool, message: str, value: float) -> None:
        self.complete(request_id, ok, "" if ok else "device_error", message, value)

    @Slot(str)
    def on_all_off_finished(self, failures: str) -> None:
        """Verbunden mit DeviceWorker.all_off_finished. Weckt alle wartenden
        ALLE-AUS-Anfragen.

        Nicht auf die eigene Anfrage korreliert: beendet zufaellig gerade der
        ALLE-AUS eines anderen Ausloesers (Knopf, Watchdog), wird auch unsere
        Anfrage damit beantwortet. Das ist unkritisch -- in beiden Faellen ist
        unmittelbar davor alles abgeschaltet worden.
        """
        with self._lock:
            waiters, self._all_off_waiters = self._all_off_waiters, []
        for pending in waiters:
            if failures:
                pending.result = RemoteResult("error", "all_off_incomplete", failures)
            else:
                pending.result = RemoteResult("ok")
            pending.event.set()

    # -- Protokoll ---------------------------------------------------------------

    @staticmethod
    def _audit(client: str, what: str, result: RemoteResult) -> None:
        """Jede schreibende Anfrage, die bis hierher kam, steht im Hauptlog.

        Bewusst in labdash.log und nicht im abschaltbaren Zugriffslog: wer nach
        einer ueberraschenden Geraetereaktion sucht, muss sehen koennen, ob sie
        aus dem Netz kam -- und von wem.
        """
        outcome = result.status if result.status != "error" else f"Fehler ({result.code})"
        logger.info("Fernsteuerung von %s: %s -> %s%s", client, what, outcome,
                    f" [{result.message}]" if result.message else "")

"""Oszilloskop-Erfassungen für Netzwerk-Freigabe und MCP (scope_api, Phase 2).

Aufrufe kommen aus den Threads des HTTP-Servers (share_api) und BLOCKIEREN
dort bis zum Ergebnis. Alle Aufrufe in die PicoScope-DLL laufen dagegen in
EINEM eigenen Thread (ThreadPoolExecutor mit einem Worker) -- nicht im
DeviceWorker, dessen Poll-Zyklus eine Erfassung sonst einfrieren würde, und
nicht wechselnd in den Server-Threads (die DLL ist nicht für parallele
Aufrufe gebaut).

Verbindung halten (Entscheidung E2): Öffnen dauert am 2204A ~4,5 s und
klickt hörbar. Die Verbindung bleibt deshalb nach einer Erfassung offen und
wird erst IDLE_CLOSE_S nach der letzten getrennt -- oder sofort per release()
(MCP-Werkzeug release_scope, Trennen-Knopf der Kachel, Start von PicoScope 7).

Vorrang des Testablaufs: begin_test() sperrt neue Erfassungen, wartet eine
laufende ab und trennt, bevor der DeviceWorker seine Session öffnet. Das
exklusive Handle teilen sich beide über picoscope2000/guard.py.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from picoscope2000.driver import usb_present
from picoscope2000.guard import GUARD, OWNER_SERVICE, OWNER_WORKER
from scope_api import analysis
from scope_api.base import AcquireRequest, ScopeError
from scope_api.pico2000_adapter import PicoScope2000Scope, open_pico2000
from scope_api.store import CaptureStore

logger = logging.getLogger(__name__)

# Leerlauf bis zum automatischen Trennen (E2).
IDLE_CLOSE_S = 60.0
IDLE_CHECK_MS = 5000
SIM_DEVICE_ID = "picoscope:SIM"
# Obergrenzen für Antworten (Kontext des Assistenten schonen).
MAX_ENVELOPE_POINTS = 500
MAX_EXCERPT_POINTS = 2000
# Wie lange begin_test() auf eine laufende Erfassung wartet: Trigger-Timeout
# (max. 30 s) + Öffnen (~4,5 s) + Übertragung.
TEST_HANDOVER_TIMEOUT_S = 45.0


class ScopeService(QObject):
    # device_id, "mcp" (Verbindung offen) oder "free" (getrennt) -- für Kachel und LiveState.
    connection_changed = Signal(str, str)

    def __init__(self, capture_dir: Path | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scope")
        self._lock = threading.Lock()
        # device_id -> {"present", "variant", "serial"}
        self._devices: dict[str, dict] = {}
        self._open: dict[str, PicoScope2000Scope] = {}
        self._busy: set[str] = set()
        self._test: set[str] = set()
        self._last_use: dict[str, float] = {}
        self._capture_device: dict[str, str] = {}
        self._store = CaptureStore(capture_dir)
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._close_idle)
        self._idle_timer.start(IDLE_CHECK_MS)

    # -- Gerätestand (GUI-Thread, aus DeviceWorker-Signalen) -----------------

    @Slot(str, bool)
    def on_picoscope_connected(self, device_id: str, online: bool) -> None:
        with self._lock:
            entry = self._devices.setdefault(device_id, {"present": False, "variant": "", "serial": ""})
            entry["present"] = online
        if not online:
            self._executor.submit(self._close_job, device_id)

    @Slot(str, str, str, str)
    def on_picoscope_state(self, device_id: str, status: str, variant: str, serial: str) -> None:
        with self._lock:
            entry = self._devices.setdefault(device_id, {"present": True, "variant": "", "serial": ""})
            if variant:
                entry["variant"] = variant
            if serial:
                entry["serial"] = serial

    # -- Testablauf (GUI-Thread) ----------------------------------------------

    def begin_test(self, device_id: str) -> None:
        """Sperrt Erfassungen für device_id und trennt eine offene Verbindung,
        BEVOR der DeviceWorker seine Test-Session öffnet. Blockiert, bis eine
        laufende Erfassung fertig ist (höchstens TEST_HANDOVER_TIMEOUT_S)."""
        with self._lock:
            self._test.add(device_id)
        future = self._executor.submit(self._close_job, device_id)
        try:
            future.result(timeout=TEST_HANDOVER_TIMEOUT_S)
        except Exception:  # noqa: BLE001 -- Testlauf startet trotzdem, Session scheitert dann best-effort
            logger.exception("Oszilloskop %s: Übergabe an den Testablauf gescheitert", device_id)

    def end_test(self) -> None:
        with self._lock:
            self._test.clear()

    # -- Abfragen (Server-Threads) ---------------------------------------------

    def describe(self, device_id: str) -> dict:
        with self._lock:
            entry = self._devices.get(device_id, {})
            is_open = device_id in self._open
            state = ("test" if device_id in self._test else
                     "acquiring" if device_id in self._busy else
                     "connected" if is_open else
                     "offline" if not entry.get("present") else "idle")
            idle_left = None
            if is_open and device_id not in self._busy:
                idle_left = max(0.0, IDLE_CLOSE_S - (time.monotonic() - self._last_use.get(device_id, 0.0)))
        result = {"state": state, "model": entry.get("variant", ""), "serial": entry.get("serial", "")}
        if idle_left is not None:
            result["disconnect_in_s"] = round(idle_left, 1)
        return result

    def blocked_reason(self, device_id: str) -> str:
        """"" wenn eine Erfassung jetzt angenommen würde, sonst der Fehlercode."""
        with self._lock:
            if not self._devices.get(device_id, {}).get("present"):
                return "device_offline"
            if device_id in self._test:
                return "scope_in_test_run"
            if device_id in self._busy:
                return "scope_busy"
        return ""

    def capabilities(self, device_id: str) -> dict:
        with self._lock:
            entry = dict(self._devices.get(device_id, {}))
        if not entry:
            raise ScopeError("device_offline", "Oszilloskop nicht verbunden.")
        scope = PicoScope2000Scope(None, entry.get("variant") or "2204A", entry.get("serial") or "?",
                                   simulated=device_id == SIM_DEVICE_ID)
        return scope.capabilities().to_dict()

    def acquire(self, device_id: str, body: dict) -> dict:
        request = AcquireRequest.from_dict(body)
        items = body.get("measurements") or list(analysis.DEFAULT_MEASUREMENTS)
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ScopeError("invalid_request", "'measurements' muss eine Liste von Namen sein.",
                             allowed=sorted(analysis.MEASUREMENTS))
        unknown = [i for i in items if i not in analysis.MEASUREMENTS]
        if unknown:
            raise ScopeError("unknown_measurement", f"Unbekannte Kennwerte: {', '.join(unknown)}",
                             allowed=sorted(analysis.MEASUREMENTS))
        points = body.get("envelope_points", 200)
        if isinstance(points, bool) or not isinstance(points, (int, float)):
            raise ScopeError("invalid_request", "'envelope_points' muss eine Zahl sein.")
        points = int(max(0, min(MAX_ENVELOPE_POINTS, points)))

        reason = self.blocked_reason(device_id)
        if reason:
            raise ScopeError(reason, _BLOCK_MESSAGES[reason])
        with self._lock:
            if device_id in self._busy:
                raise ScopeError("scope_busy", _BLOCK_MESSAGES["scope_busy"])
            self._busy.add(device_id)
        try:
            capture = self._executor.submit(self._acquire_job, device_id, request).result()
        finally:
            with self._lock:
                self._busy.discard(device_id)
                self._last_use[device_id] = time.monotonic()
        capture.scope_id = device_id
        capture_id = self._store.add(capture)
        with self._lock:
            self._capture_device[capture_id] = device_id
        report = analysis.report(capture, items, max(1, points))
        if points == 0:
            report.pop("envelope", None)
        report["disconnect_in_s"] = IDLE_CLOSE_S
        return report

    def capture_device(self, capture_id: str) -> str:
        """Zu welchem Oszilloskop gehört die Erfassung? (für die Freigabeprüfung)"""
        self._store.get(capture_id)  # wirft unknown_capture, wenn verdrängt
        with self._lock:
            return self._capture_device.get(capture_id, "")

    def excerpt(self, capture_id: str, channel: str | None, t_start: float | None, t_stop: float | None,
                max_points: int, mode: str) -> dict:
        return self._store.excerpt(capture_id, channel, t_start, t_stop,
                                   int(max(2, min(MAX_EXCERPT_POINTS, max_points))), mode)

    def measure(self, capture_id: str, body: dict) -> dict:
        items = body.get("measurements") or list(analysis.DEFAULT_MEASUREMENTS)
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ScopeError("invalid_request", "'measurements' muss eine Liste von Namen sein.",
                             allowed=sorted(analysis.MEASUREMENTS))
        channel = body.get("channel")
        t_start, t_stop = body.get("t_start_s"), body.get("t_stop_s")
        for name, value in (("t_start_s", t_start), ("t_stop_s", t_stop)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ScopeError("invalid_request", f"'{name}' muss eine Zahl sein.")
        return self._store.measure(capture_id, items, str(channel) if channel else None, t_start, t_stop)

    def release(self, device_id: str) -> dict:
        was_open = self._executor.submit(self._close_job, device_id).result()
        return {"released": bool(was_open)}

    @Slot(str)
    def release_async(self, device_id: str) -> None:
        """Für GUI-Knöpfe: trennen, ohne den GUI-Thread zu blockieren."""
        self._executor.submit(self._close_job, device_id)

    def shutdown(self) -> None:
        self._idle_timer.stop()
        for device_id in list(self._open):
            try:
                self._executor.submit(self._close_job, device_id).result(timeout=TEST_HANDOVER_TIMEOUT_S)
            except Exception:  # noqa: BLE001 -- Beenden darf nie hängen bleiben
                logger.exception("Oszilloskop %s: Fehler beim Trennen", device_id)
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- Scope-Thread ------------------------------------------------------------

    def _acquire_job(self, device_id: str, request: AcquireRequest):
        scope = self._ensure_open(device_id)
        try:
            return scope.acquire(request)
        except ScopeError as exc:
            if exc.code == "device_error":
                # Verbindung ist womöglich weg (USB gezogen): sauber trennen,
                # die nächste Erfassung öffnet neu.
                self._close_job(device_id)
            raise

    def _ensure_open(self, device_id: str) -> PicoScope2000Scope:
        scope = self._open.get(device_id)
        if scope is not None:
            return scope
        if device_id == SIM_DEVICE_ID:
            scope = open_pico2000(simulate=True)
        else:
            if not GUARD.try_acquire(OWNER_SERVICE):
                if GUARD.owner == OWNER_WORKER:
                    raise ScopeError("scope_busy", "LabControl nutzt das Oszilloskop gerade selbst "
                                                   "(Erkennung oder Testablauf). Kurz warten.")
                raise ScopeError("scope_busy", _BLOCK_MESSAGES["scope_busy"])
            try:
                GUARD.settle()
                scope = open_pico2000(simulate=False)
            except ScopeError as exc:
                GUARD.release(closed=False)
                if usb_present():
                    raise ScopeError("scope_busy_external",
                                     "Das Oszilloskop ist angeschlossen, lässt sich aber nicht öffnen -- "
                                     "vermutlich ist die PicoScope-7-App offen. Nur der Nutzer kann sie "
                                     "schließen.") from exc
                raise
        with self._lock:
            self._open[device_id] = scope
            self._last_use[device_id] = time.monotonic()
        logger.info("Oszilloskop %s für Erfassungen über Netzwerk/MCP verbunden", device_id)
        self.connection_changed.emit(device_id, "mcp")
        return scope

    def _close_job(self, device_id: str) -> bool:
        with self._lock:
            scope = self._open.pop(device_id, None)
        if scope is None:
            return False
        try:
            scope.close()
        except Exception:  # noqa: BLE001 -- Aufräumen darf nie scheitern
            logger.exception("Oszilloskop %s: Fehler beim Schließen", device_id)
        finally:
            if device_id != SIM_DEVICE_ID:
                GUARD.release()
        logger.info("Oszilloskop %s getrennt (Netzwerk/MCP)", device_id)
        self.connection_changed.emit(device_id, "free")
        return True

    # -- Leerlauf (GUI-Thread) --------------------------------------------------

    def _close_idle(self) -> None:
        now = time.monotonic()
        with self._lock:
            idle = [d for d in self._open
                    if d not in self._busy and now - self._last_use.get(d, now) >= IDLE_CLOSE_S]
        for device_id in idle:
            self._executor.submit(self._close_job, device_id)


_BLOCK_MESSAGES = {
    "device_offline": "Oszilloskop nicht verbunden (USB?).",
    "scope_in_test_run": "Ein Testablauf nutzt das Oszilloskop. Erfassungen gehen wieder nach dessen Ende.",
    "scope_busy": "Es läuft bereits eine Erfassung an diesem Oszilloskop. Kurz warten.",
}

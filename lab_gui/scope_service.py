"""Oszilloskop-Erfassungen für Netzwerk-Freigabe, MCP und Testablauf (scope_api, Phase 2).

Aufrufe kommen aus den Threads des HTTP-Servers (share_api) und BLOCKIEREN
dort bis zum Ergebnis. Alle Aufrufe in ein Gerät laufen dagegen in EINEM
eigenen Thread je Gerät (ThreadPoolExecutor mit einem Worker) -- nicht im
DeviceWorker, dessen Poll-Zyklus eine Erfassung sonst einfrieren würde, und
nicht wechselnd in den Server-Threads (die PicoScope-DLL ist nicht für
parallele Aufrufe gebaut). Bis 1.0.0 war es ein Thread für alle Geräte; seit
eine Erfassung scharf geschaltet im Hintergrund warten kann (PI-72 a/PI-73 a),
würde das das zweite Oszilloskop und das Trennen minutenlang blockieren.

Verbindung halten (Entscheidung E2): Öffnen dauert am 2204A ~4,5 s und
klickt hörbar. Die Verbindung bleibt deshalb nach einer Erfassung offen und
wird erst IDLE_CLOSE_S nach der letzten getrennt -- oder sofort per release()
(MCP-Werkzeug release_scope, Trennen-Knopf der Kachel, Start von PicoScope 7).

Scharf schalten (1.0.1, PI-72 a Testablauf, PI-73 a MCP): arm() stellt das Gerät
ein, startet die Erfassung und kehrt zurück, sobald das Gerät auf den Trigger
wartet -- mit der capture_id, unter der die Daten später liegen. Der Gerätethread
wartet weiter (abbrechbar über cancel(), release(), Laufende), wait_capture()
holt das Ergebnis, sobald getriggert, oder meldet nach der Höchstzeit "armed".
Derselbe Kern dient dem Ablauf (Eigentümer OWNER_RUN, ohne Blockieren über das
Signal capture_state) und REST/MCP (OWNER_API).

Vorrang des Testablaufs: begin_test() sperrt neue Erfassungen über Netzwerk/MCP,
bricht deren scharfe Erfassungen ab, wartet eine laufende ab und trennt (beim
PicoScope mit PICO_*-Schritten), bevor der DeviceWorker seine Session öffnet.
Das exklusive Handle teilen sich beide über picoscope2000/guard.py. Erfassungen
des Laufs selbst (owner=OWNER_RUN) gehen trotz Sperre.

Seit 0.21.0 auch für SCPI-Oszilloskope (Geräteart "scpiscope", UNI-T MSO1000HD
über LAN, scope_api/unit_mso_adapter.py), dazu R&S RTB2000
(scope_api/rs_rtb_adapter.py). PicoScope oder SCPI steht im Präfix der
Geräte-ID, welcher SCPI-Adapter, entscheidet *IDN? (scope_api/scpi.py).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from picoscope2000.driver import usb_present
from picoscope2000.guard import GUARD, OWNER_SERVICE, OWNER_WORKER
from scope_api import analysis
from scope_api.base import MAX_ARM_TIMEOUT_S, AcquireRequest, ScopeError
from scope_api.pico2000_adapter import PicoScope2000Scope, open_pico2000
from scope_api.scpi import capabilities_for as scpi_capabilities, is_simulated as scpi_is_simulated, open_scope
from scope_api.store import CaptureStore
from unit_mso1000hd import guard as mso_guard
from unit_mso1000hd.driver import KIND as SCPI_KIND, resource_for
import scope_plot

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

# -- Scharf schalten (PI-72 a/PI-73 a) ----------------------------------------------
OWNER_API = "api"     # REST/MCP
OWNER_RUN = "run"     # Testablauf
# Wie lange eine scharfe Erfassung ohne Angabe wartet (REST/MCP), höchstens MAX_ARM_TIMEOUT_S.
ARM_DEFAULT_TIMEOUT_S = 60.0
# Bis das Gerät scharf ist: Öffnen (~4,5 s, dazu bis 2 s Pause nach dem letzten Schließen) + Einstellen.
ARM_READY_TIMEOUT_S = 30.0
# Höchstzeit eines wait_capture über REST (Server-Thread); der MCP-Client wartet etwas länger.
MAX_WAIT_S = 50.0
# Wie lange das Öffnen eines SCPI-Oszilloskops auf eine laufende Erkennung des DeviceWorkers wartet.
SCPI_GUARD_WAIT_S = 3.0
# Abgeschlossene scharfe Erfassungen, deren Zustand (Fehler, abgebrochen) abrufbar bleibt.
KEEP_FINISHED = 50

# triggered: Erfassung im Gerät fertig, die Daten werden noch gelesen/ausgewertet (kann Sekunden dauern)
ACTIVE_STATES = ("arming", "armed", "triggered")
# Wie lange wait_capture nach dem Trigger noch auf die Daten wartet, auch wenn timeout_s schon um ist
READ_WAIT_S = 20.0


@dataclass
class _Pending:
    """Eine scharf geschaltete Erfassung (Zustand: arming -> armed -> triggered -> done | error | cancelled)."""

    capture_id: str
    device_id: str
    owner: str
    request: AcquireRequest
    items: list
    points: int
    state: str = "arming"
    created: float = field(default_factory=time.time)
    armed_at: float = 0.0
    triggered_at: float = 0.0
    finished_at: float = 0.0
    report: dict | None = None
    error: ScopeError | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    # gesetzt, sobald scharf ODER schon fertig (arm() wartet darauf)
    ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)


class ScopeService(QObject):
    # device_id, "mcp" (Verbindung offen) oder "free" (getrennt) -- für Kachel und LiveState.
    connection_changed = Signal(str, str)
    # device_id, capture_id, Kurztext (Uhrzeit + Kennwerte) -- letzte Erfassung auf der Kachel.
    capture_added = Signal(str, str, str)
    # capture_id, Zustand ("armed" | "triggered" | "done" | "error" | "cancelled") einer scharfen Erfassung -- aus
    # dem Gerätethread (beim Empfänger im GUI-Thread also eingereiht); der Testablauf wartet darauf.
    capture_state = Signal(str, str)

    def __init__(self, capture_dir: Path | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._lock = threading.Lock()
        # device_id -> {"present", "variant", "serial"}
        self._devices: dict[str, dict] = {}
        self._open: dict[str, object] = {}   # PicoScope2000Scope | UnitMsoScope | RtbScope
        self._busy: set[str] = set()
        self._test: set[str] = set()
        self._last_use: dict[str, float] = {}
        self._capture_device: dict[str, str] = {}
        self._pending: OrderedDict[str, _Pending] = OrderedDict()
        self._store = CaptureStore(capture_dir)
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._close_idle)
        self._idle_timer.start(IDLE_CHECK_MS)

    def _executor_for(self, device_id: str) -> ThreadPoolExecutor:
        """Der Gerätethread von device_id (angelegt beim ersten Bedarf)."""
        with self._lock:
            executor = self._executors.get(device_id)
            if executor is None:
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"scope-{_kind(device_id)}")
                self._executors[device_id] = executor
            return executor

    def _submit(self, device_id: str, fn, *args):
        return self._executor_for(device_id).submit(fn, *args)

    # -- Gerätestand (GUI-Thread, aus DeviceWorker-Signalen) -----------------

    @Slot(str, bool)
    def on_scope_connected(self, device_id: str, online: bool) -> None:
        self.on_picoscope_connected(device_id, online)

    @Slot(str, str, str, str)
    def on_scope_state(self, device_id: str, status: str, variant: str, serial: str) -> None:
        self.on_picoscope_state(device_id, status, variant, serial)

    @Slot(str, bool)
    def on_picoscope_connected(self, device_id: str, online: bool) -> None:
        with self._lock:
            entry = self._devices.setdefault(device_id, {"present": False, "variant": "", "serial": ""})
            entry["present"] = online
        if not online:
            self.cancel_device(device_id)
            self._submit(device_id, self._close_job, device_id)

    @Slot(str, str, str, str)
    def on_picoscope_state(self, device_id: str, status: str, variant: str, serial: str) -> None:
        with self._lock:
            entry = self._devices.setdefault(device_id, {"present": True, "variant": "", "serial": ""})
            if variant:
                entry["variant"] = variant
            if serial:
                entry["serial"] = serial

    # -- Testablauf (GUI-Thread) ----------------------------------------------

    def begin_test(self, device_id: str, close: bool = True) -> None:
        """Sperrt Erfassungen über Netzwerk/MCP für device_id, bricht deren scharfe Erfassungen ab und
        trennt eine offene Verbindung (close), BEVOR der DeviceWorker seine Test-Session öffnet. Ohne close
        (Ablauf nutzt das Oszilloskop selbst über diesen Dienst, PI-72 a) bleibt sie offen. Blockiert, bis
        eine laufende Erfassung fertig ist (höchstens TEST_HANDOVER_TIMEOUT_S)."""
        with self._lock:
            self._test.add(device_id)
        self.cancel_device(device_id, owners=(OWNER_API,))
        future = self._submit(device_id, self._close_job if close else _noop, device_id)
        try:
            future.result(timeout=TEST_HANDOVER_TIMEOUT_S)
        except Exception:  # noqa: BLE001 -- Testlauf startet trotzdem, Session scheitert dann best-effort
            logger.exception("Oszilloskop %s: Übergabe an den Testablauf gescheitert", device_id)

    def end_test(self) -> None:
        with self._lock:
            self._test.clear()

    def end_run(self, device_ids) -> None:
        """Ende eines Ablaufs, auch Stopp/Fehler (PI-72 a): scharfe Erfassungen des Laufs abbrechen und die
        vom Lauf genutzten Oszilloskope freigeben (trennen), ohne den GUI-Thread zu blockieren."""
        with self._lock:
            run_devices = {p.device_id for p in self._pending.values()
                           if p.owner == OWNER_RUN and p.state in ACTIVE_STATES}
        for device_id in run_devices:
            self.cancel_device(device_id, owners=(OWNER_RUN,))
        for device_id in set(device_ids) | run_devices:
            self.release_async(device_id)

    # -- Abfragen (Server-Threads) ---------------------------------------------

    def _active(self, device_id: str) -> _Pending | None:
        """Scharfe (oder gerade scharf werdende) Erfassung an device_id; Aufrufer hält self._lock."""
        for pending in self._pending.values():
            if pending.device_id == device_id and pending.state in ACTIVE_STATES:
                return pending
        return None

    def describe(self, device_id: str) -> dict:
        with self._lock:
            entry = self._devices.get(device_id, {})
            is_open = device_id in self._open
            armed = self._active(device_id)
            state = ("test" if device_id in self._test else
                     "armed" if armed is not None else
                     "acquiring" if device_id in self._busy else
                     "connected" if is_open else
                     "offline" if not entry.get("present") else "idle")
            idle_left = None
            if is_open and device_id not in self._busy and armed is None:
                idle_left = max(0.0, IDLE_CLOSE_S - (time.monotonic() - self._last_use.get(device_id, 0.0)))
        result = {"state": state, "model": entry.get("variant", ""), "serial": entry.get("serial", "")}
        if armed is not None:
            result["armed_capture"] = armed.capture_id
        if idle_left is not None:
            result["disconnect_in_s"] = round(idle_left, 1)
        return result

    def _blocked_locked(self, device_id: str, owner: str = OWNER_API) -> str:
        if not self._devices.get(device_id, {}).get("present"):
            return "device_offline"
        if device_id in self._test and owner != OWNER_RUN:
            return "scope_in_test_run"
        if device_id in self._busy:
            return "scope_busy"
        if self._active(device_id) is not None:
            return "scope_armed"
        return ""

    def blocked_reason(self, device_id: str, owner: str = OWNER_API) -> str:
        """"" wenn eine Erfassung jetzt angenommen würde, sonst der Fehlercode."""
        with self._lock:
            return self._blocked_locked(device_id, owner)

    def capabilities(self, device_id: str) -> dict:
        with self._lock:
            entry = dict(self._devices.get(device_id, {}))
        if not entry:
            raise ScopeError("device_offline", "Oszilloskop nicht verbunden.")
        if _kind(device_id) == SCPI_KIND:
            # Verbunden: vom Gerät selbst (RTB2000: Bandbreite aus *OPT?).
            scope = self._open.get(device_id)
            if scope is not None:
                return scope.capabilities().to_dict()
            return scpi_capabilities(entry.get("variant", ""), entry.get("serial") or "?",
                                     simulated=scpi_is_simulated(device_id)).to_dict()
        scope = PicoScope2000Scope(None, entry.get("variant") or "2204A", entry.get("serial") or "?",
                                   simulated=device_id == SIM_DEVICE_ID)
        return scope.capabilities().to_dict()

    @staticmethod
    def _report_options(body: dict) -> tuple[list, int]:
        """(Kennwerte, Hüllkurvenpunkte) einer Anfrage, geprüft."""
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
        return items, int(max(0, min(MAX_ENVELOPE_POINTS, points)))

    @staticmethod
    def _report(capture, items, points: int) -> dict:
        report = analysis.report(capture, items, max(1, points))
        if points == 0:
            report.pop("envelope", None)
        return report

    def acquire(self, device_id: str, body: dict) -> dict:
        request = AcquireRequest.from_dict(body)
        items, points = self._report_options(body)
        with self._lock:
            reason = self._blocked_locked(device_id)
            if reason:
                raise ScopeError(reason, _BLOCK_MESSAGES[reason])
            self._busy.add(device_id)
        try:
            capture = self._submit(device_id, self._acquire_job, device_id, request).result()
        finally:
            with self._lock:
                self._busy.discard(device_id)
                self._last_use[device_id] = time.monotonic()
        capture.scope_id = device_id
        capture_id = self._store.add(capture)
        with self._lock:
            self._capture_device[capture_id] = device_id
        self._announce(device_id, capture_id, capture)
        report = self._report(capture, items, points)
        report["disconnect_in_s"] = IDLE_CLOSE_S
        return report

    def _announce(self, device_id: str, capture_id: str, capture) -> None:
        try:
            self.capture_added.emit(device_id, capture_id, scope_plot.summary(capture))
        except Exception:  # noqa: BLE001 -- die Kachel darf eine Erfassung nie scheitern lassen
            logger.exception("Oszilloskop %s: Kurztext für die Kachel gescheitert", device_id)

    # -- Scharf schalten (PI-72 a/PI-73 a) -------------------------------------------

    def arm(self, device_id: str, body: dict, owner: str = OWNER_API, wait_armed: bool = True) -> dict:
        """Stellt ein und schaltet scharf; Rückgabe wie capture_status() (state "armed", oder schon "done").
        Anfrage wie acquire, Trigger ohne Angabe "single" mit timeout_s = ARM_DEFAULT_TIMEOUT_S (= wie lange
        die Erfassung höchstens scharf bleibt, bis MAX_ARM_TIMEOUT_S). wait_armed=False (Testablauf im
        GUI-Thread): sofort zurück, "armed"/Fehler kommen über capture_state."""
        if not isinstance(body, dict):
            raise ScopeError("invalid_request", "Anfrage muss ein JSON-Objekt sein.")
        trigger = body.get("trigger") if body.get("trigger") is not None else {}
        if not isinstance(trigger, dict):
            raise ScopeError("invalid_request", "'trigger' muss ein Objekt sein.")
        trigger = {"mode": "single", "timeout_s": ARM_DEFAULT_TIMEOUT_S, **trigger}
        if str(trigger.get("mode", "")).lower() == "none":
            raise ScopeError("invalid_request", "Scharf schalten braucht einen Trigger ('single' oder 'auto').",
                             allowed=["single", "auto"])
        request = AcquireRequest.from_dict({**body, "trigger": trigger}, max_timeout_s=MAX_ARM_TIMEOUT_S)
        items, points = self._report_options(body)
        with self._lock:
            reason = self._blocked_locked(device_id, owner)
            if reason:
                raise ScopeError(reason, _BLOCK_MESSAGES[reason])
            pending = _Pending(self._store.reserve_id(), device_id, owner, request, items, points)
            self._pending[pending.capture_id] = pending
            self._capture_device[pending.capture_id] = device_id
            self._trim_finished()
        self._submit(device_id, self._armed_job, pending)
        logger.info("Oszilloskop %s: Erfassung %s wird scharf geschaltet (%s)", device_id, pending.capture_id, owner)
        if wait_armed:
            if not pending.ready.wait(ARM_READY_TIMEOUT_S):
                pending.cancel.set()
                raise ScopeError("device_error", f"Oszilloskop wurde nicht innerhalb von {ARM_READY_TIMEOUT_S:g} s "
                                                 "scharf.")
            if pending.state in ("error", "cancelled"):
                raise _copy(pending.error)
        return self.capture_status(pending.capture_id)

    def wait_capture(self, capture_id: str, timeout_s: float) -> dict:
        """Ergebnis einer scharfen Erfassung, sobald getriggert (Bericht wie acquire, state "done"); nach
        timeout_s ohne Trigger der Stand (state "armed"/"arming", bleibt scharf). Fehler/Abbruch als
        ScopeError. Auch für fertige Erfassungen aus acquire (Bericht mit Standard-Kennwerten)."""
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s < 0:
            raise ScopeError("invalid_request", "'timeout_s' muss eine Zahl >= 0 sein.")
        with self._lock:
            pending = self._pending.get(capture_id)
        if pending is None:
            report = self._report(self._store.get(capture_id), list(analysis.DEFAULT_MEASUREMENTS), 200)
            return {**report, "state": "done"}
        started = time.monotonic()
        pending.done.wait(min(float(timeout_s), MAX_WAIT_S))
        if not pending.done.is_set() and pending.state == "triggered":
            # Getriggert, die Daten kommen gerade: darauf noch warten statt "noch kein Trigger" zu melden
            pending.done.wait(READ_WAIT_S)
        if not pending.done.is_set():
            return {**self.capture_status(capture_id), "waited_s": round(time.monotonic() - started, 1)}
        if pending.error is not None:
            raise _copy(pending.error)
        return {**(pending.report or {}), "state": "done"}

    def capture_status(self, capture_id: str) -> dict:
        """Stand einer scharfen Erfassung (ohne Daten)."""
        with self._lock:
            pending = self._pending.get(capture_id)
            if pending is None:
                self._store.get(capture_id)   # wirft unknown_capture
                return {"capture_id": capture_id, "state": "done"}
            trig = pending.request.trigger
            result = {
                "capture_id": capture_id,
                "scope_id": pending.device_id,
                "state": pending.state,
                "trigger": {"mode": trig.mode, "source": trig.source, "level_v": analysis.num(trig.level_v),
                            "edge": trig.edge, "timeout_s": analysis.num(trig.timeout_s)},
            }
            if pending.armed_at:
                end = pending.triggered_at or pending.finished_at or time.time()
                result["armed_for_s"] = round(max(0.0, end - pending.armed_at), 1)
                if pending.state == "armed":
                    result["expires_in_s"] = round(max(0.0, pending.armed_at + trig.timeout_s - time.time()), 1)
            if pending.error is not None:
                result["error"] = pending.error.code
                result["message"] = pending.error.message
        return result

    def cancel(self, capture_id: str) -> bool:
        """Bricht eine scharfe Erfassung ab (der Gerätethread stoppt sie). True = war noch aktiv."""
        with self._lock:
            pending = self._pending.get(capture_id)
            if pending is None or pending.state not in ACTIVE_STATES:
                return False
            pending.cancel.set()
        logger.info("Oszilloskop %s: scharfe Erfassung %s abgebrochen", pending.device_id, capture_id)
        return True

    def cancel_device(self, device_id: str, owners: tuple | None = None) -> list[str]:
        with self._lock:
            ids = [p.capture_id for p in self._pending.values()
                   if p.device_id == device_id and p.state in ACTIVE_STATES and (owners is None or p.owner in owners)]
        return [capture_id for capture_id in ids if self.cancel(capture_id)]

    def _trim_finished(self) -> None:
        finished = [k for k, p in self._pending.items() if p.state not in ACTIVE_STATES]
        for key in finished[:max(0, len(finished) - KEEP_FINISHED)]:
            del self._pending[key]

    def _pending_or_raise(self, capture_id: str) -> None:
        """Für Daten-Abrufe: noch scharf -> capture_pending, gescheitert -> dessen Fehler."""
        with self._lock:
            pending = self._pending.get(capture_id)
        if pending is None or pending.state == "done":
            return
        if pending.error is not None:
            raise _copy(pending.error)
        raise ScopeError("capture_pending", "Die Erfassung ist noch scharf und hat noch keine Daten "
                                            "(wait_capture).", state=pending.state)

    # -- Gespeicherte Erfassungen ---------------------------------------------------------

    def relocate_csv(self, old_path, new_path) -> None:
        """Rueckmeldung der Datenablage (data_store): CSV liegt jetzt auf dem Netzlaufwerk."""
        self._store.relocate(str(old_path), str(new_path))

    def capture_device(self, capture_id: str) -> str:
        """Zu welchem Oszilloskop gehört die Erfassung? (für die Freigabeprüfung)"""
        with self._lock:
            if capture_id in self._pending:
                return self._capture_device.get(capture_id, "")
        self._store.get(capture_id)  # wirft unknown_capture, wenn verdrängt
        with self._lock:
            return self._capture_device.get(capture_id, "")

    def excerpt(self, capture_id: str, channel: str | None, t_start: float | None, t_stop: float | None,
                max_points: int, mode: str) -> dict:
        """Ausschnitt; eine noch scharfe Erfassung liefert stattdessen ihren Stand (state "armed")."""
        with self._lock:
            pending = self._pending.get(capture_id)
        if pending is not None and pending.state in ACTIVE_STATES:
            return self.capture_status(capture_id)
        self._pending_or_raise(capture_id)
        result = self._store.excerpt(capture_id, channel, t_start, t_stop,
                                     int(max(2, min(MAX_EXCERPT_POINTS, max_points))), mode)
        return {**result, "state": "done"} if pending is not None else result

    def measure(self, capture_id: str, body: dict) -> dict:
        self._pending_or_raise(capture_id)
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

    def plot(self, capture_id: str, channels: list[str] | None, t_start: float | None, t_stop: float | None,
             width: int, height: int) -> bytes:
        """PNG einer gespeicherten Erfassung (scope_plot.plot_png). Zeichnet im
        aufrufenden Thread -- QImage/QPainter brauchen dafuer keinen GUI-Thread."""
        self._pending_or_raise(capture_id)
        return scope_plot.plot_png(self._store.get(capture_id), channels, t_start, t_stop, width, height)

    def release(self, device_id: str) -> dict:
        """REST/MCP: trennen. Scharfe Erfassungen über REST/MCP werden dabei abgebrochen; ein Oszilloskop,
        das ein Testablauf nutzt, bleibt dem Lauf."""
        with self._lock:
            in_test = device_id in self._test
        if in_test:
            return {"released": False}
        cancelled = self.cancel_device(device_id, owners=(OWNER_API,))
        was_open = self._submit(device_id, self._close_job, device_id).result()
        result = {"released": bool(was_open)}
        if cancelled:   # nur dann -- die Antwort bleibt sonst wie vor 1.0.1
            result["cancelled"] = cancelled
        return result

    @Slot(str)
    def release_async(self, device_id: str) -> None:
        """Für GUI-Knöpfe und das Laufende: abbrechen und trennen, ohne den GUI-Thread zu blockieren."""
        self.cancel_device(device_id)
        self._submit(device_id, self._close_job, device_id)

    def shutdown(self) -> None:
        self._idle_timer.stop()
        with self._lock:
            for pending in self._pending.values():
                pending.cancel.set()
        for device_id in list(self._open):
            try:
                self._submit(device_id, self._close_job, device_id).result(timeout=TEST_HANDOVER_TIMEOUT_S)
            except Exception:  # noqa: BLE001 -- Beenden darf nie hängen bleiben
                logger.exception("Oszilloskop %s: Fehler beim Trennen", device_id)
        with self._lock:
            executors = list(self._executors.values())
        for executor in executors:
            executor.shutdown(wait=False, cancel_futures=True)

    # -- Gerätethreads ------------------------------------------------------------

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

    def _armed_job(self, pending: _Pending) -> None:
        device_id = pending.device_id
        try:
            if pending.cancel.is_set():
                raise ScopeError("cancelled", "Erfassung abgebrochen.")
            scope = self._ensure_open(device_id)
            capture = scope.acquire(pending.request, cancel=pending.cancel,
                                    on_armed=lambda: self._set_armed(pending),
                                    on_triggered=lambda: self._set_triggered(pending))
        except ScopeError as exc:
            if exc.code == "device_error":
                self._close_job(device_id)
            self._finish(pending, error=exc)
            return
        except Exception as exc:  # noqa: BLE001 -- ein Treiberfehler darf den Gerätethread nicht verlieren
            logger.exception("Oszilloskop %s: scharfe Erfassung gescheitert", device_id)
            self._finish(pending, error=ScopeError("device_error", str(exc) or type(exc).__name__))
            return
        finally:
            with self._lock:
                self._last_use[device_id] = time.monotonic()
        capture.scope_id = device_id
        self._store.add(capture, pending.capture_id)
        try:
            report = self._report(capture, pending.items, pending.points)
            report["disconnect_in_s"] = IDLE_CLOSE_S
        except Exception as exc:  # noqa: BLE001
            logger.exception("Oszilloskop %s: Auswertung gescheitert", device_id)
            self._finish(pending, error=ScopeError("device_error", f"Auswertung gescheitert: {exc}"))
            return
        self._finish(pending, report=report)
        self._announce(device_id, pending.capture_id, capture)

    def _set_armed(self, pending: _Pending) -> None:
        with self._lock:
            if pending.state != "arming":
                return
            pending.state = "armed"
            pending.armed_at = time.time()
        pending.ready.set()
        logger.info("Oszilloskop %s: Erfassung %s scharf", pending.device_id, pending.capture_id)
        self.capture_state.emit(pending.capture_id, "armed")

    def _set_triggered(self, pending: _Pending) -> None:
        """Erfassung im Gerät fertig, jetzt wird gelesen -- ein Warteschritt meldet dann kein "kein Trigger"."""
        with self._lock:
            if pending.state not in ("arming", "armed"):
                return
            if not pending.armed_at:
                pending.armed_at = time.time()
            pending.state = "triggered"
            pending.triggered_at = time.time()
        pending.ready.set()
        self.capture_state.emit(pending.capture_id, "triggered")

    def _finish(self, pending: _Pending, report: dict | None = None, error: ScopeError | None = None) -> None:
        with self._lock:
            pending.report = report
            pending.error = error
            pending.state = ("done" if error is None else "cancelled" if error.code == "cancelled" else "error")
            pending.finished_at = time.time()
            if not pending.armed_at and error is None:
                pending.armed_at = pending.finished_at
        pending.ready.set()
        pending.done.set()
        logger.info("Oszilloskop %s: Erfassung %s %s", pending.device_id, pending.capture_id,
                    pending.state if error is None else f"{pending.state} ({error.code})")
        self.capture_state.emit(pending.capture_id, pending.state)

    def _ensure_open(self, device_id: str):
        scope = self._open.get(device_id)
        if scope is not None:
            return scope
        if _kind(device_id) == SCPI_KIND:
            scope = self._open_scpi(device_id)
        elif device_id == SIM_DEVICE_ID:
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

    def _open_scpi(self, device_id: str):
        resource = resource_for(device_id)
        if not resource:
            raise ScopeError("device_offline", "Für dieses Oszilloskop ist keine Adresse eingetragen "
                                               "(Einstellungen, Geräte).")
        guard = mso_guard.guard_for(device_id)
        # Die Erkennung im DeviceWorker hält das Gerät höchstens ~2 s (SCPISCOPE_PROBE_TIMEOUT_S): kurz warten,
        # statt eine Erfassung (oder einen Ablaufschritt "Scharf schalten") daran scheitern zu lassen.
        deadline = time.monotonic() + SCPI_GUARD_WAIT_S
        while not guard.try_acquire(mso_guard.OWNER_SERVICE):
            if time.monotonic() >= deadline:
                raise ScopeError("scope_busy", "LabControl prüft das Oszilloskop gerade (Erkennung). Kurz warten.")
            time.sleep(0.05)
        try:
            return open_scope(resource)
        except ScopeError:
            guard.release()
            raise

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
            if _kind(device_id) == SCPI_KIND:
                mso_guard.guard_for(device_id).release()
            elif device_id != SIM_DEVICE_ID:
                GUARD.release()
        logger.info("Oszilloskop %s getrennt (Netzwerk/MCP)", device_id)
        self.connection_changed.emit(device_id, "free")
        return True

    # -- Leerlauf (GUI-Thread) --------------------------------------------------

    def _close_idle(self) -> None:
        now = time.monotonic()
        with self._lock:
            idle = [d for d in self._open
                    if d not in self._busy and self._active(d) is None
                    and now - self._last_use.get(d, now) >= IDLE_CLOSE_S]
        for device_id in idle:
            self._submit(device_id, self._close_job, device_id)


def _kind(device_id: str) -> str:
    return device_id.split(":", 1)[0]


def _noop(_device_id: str) -> bool:
    """Für begin_test(close=False): wartet nur, bis der Gerätethread frei ist."""
    return False


def _copy(error: ScopeError | None) -> ScopeError:
    """Ein gespeicherter Fehler, neu geworfen (der Gerätethread hat ihn schon einmal geworfen)."""
    if error is None:
        return ScopeError("device_error", "Unbekannter Fehler.")
    return ScopeError(error.code, error.message, **error.details)


_BLOCK_MESSAGES = {
    "device_offline": "Oszilloskop nicht verbunden (USB bzw. LAN prüfen).",
    "scope_in_test_run": "Ein Testablauf nutzt das Oszilloskop. Erfassungen gehen wieder nach dessen Ende.",
    "scope_busy": "Es läuft bereits eine Erfassung an diesem Oszilloskop. Kurz warten.",
    "scope_armed": "An diesem Oszilloskop wartet eine scharf geschaltete Erfassung auf den Trigger "
                   "(wait_capture holt sie, release_scope bricht sie ab).",
}

"""Ablauf-Steuerung ohne Fenster (0.58.0): Testlauf zwischen TestRunner, DeviceWorker, Sicherheit und
Laufaufzeichnung verdrahten. Frueher in MainWindow (_wire_testcase_tab/_start_run), jetzt hier, damit
derselbe Weg auch die Kommandozeile (headless.py, `labcontrol run`) und spaeter REST/MCP-Laeufe traegt.

- DeviceDirectory: welche Geraete je Art gerade verbunden sind; loest eine leere device_id
  ("automatisch") auf das einzige verbundene Geraet der Art auf.
- Interlock: Verriegelung Netzteil/Last im Testablauf (FP-7 b). Von einer eingestellten Gruppe
  (Settings.interlock_groups) darf im Ablauf hoechstens ein Ausgang eingeschaltet sein; ein Ablauf kann
  das mit meta "source_sink" ausdruecklich aufheben (Quelle/Senke, z. B. Batterie nachbilden).
  Kachel, Speicherplaetze und Netzwerk sind bewusst NICHT verriegelt (Entscheidung FP-7 b).
- RunSession: Start, Weiterleitung der Aktionen an den Worker (oder an einen Verbund-Router), Abschluss.
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QMetaObject, QObject, Q_ARG, Qt, Signal, Slot

from i18n import tr
from run_record import RunRecorder
from testcase_model import TestStep, kind_label
from testcase_runner import TestRunner

logger = logging.getLogger(__name__)

DEVICE_KINDS = ("load", "psu", "can", "hil", "picoscope", "fg", "scpiscope", "battsim", "jlink")

# Aktionen, die einen Ausgang/Eingang EINschalten (Verriegelung FP-7 b)
SWITCH_ON_ACTIONS = {"load": {"OUT_ON"}, "psu": {"PSU_OUT_ON"}}
# Aktionen, die ihn ausschalten -- der Zustand gilt danach sofort als "aus", nicht erst mit der
# naechsten Rueckmeldung des Geraets (sonst lehnte ein direkt folgendes EIN des anderen Geraets ab)
SWITCH_OFF_ACTIONS = {"load": {"OUT_OFF"}, "psu": {"PSU_OUT_OFF"}}

# Laufergebnis -> Exit-Code der Kommandozeile (FP-3 a, wie neXtBSW boardtest)
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_ABORTED = 2
EXIT_SETUP = 3
EXIT_INTERRUPTED = 130


class DeviceDirectory:
    """Verbundene Geraete je Art (gefuellt von MainWindow._set_online bzw. headless.py)."""

    def __init__(self) -> None:
        self.online: dict[str, set[str]] = {kind: set() for kind in DEVICE_KINDS}

    def set_online(self, kind: str, device_id: str, online: bool) -> None:
        bucket = self.online.setdefault(kind, set())
        if online:
            bucket.add(device_id)
        else:
            bucket.discard(device_id)

    def is_online(self, device_id: str) -> bool:
        return any(device_id in ids for ids in self.online.values())

    def resolve(self, kind: str, device_id: str) -> tuple[str | None, str]:
        """(device_id, "") oder (None, Fehlermeldung). Eine feste ID muss verbunden sein -- sonst
        landete ein veralteter Wert aus einer Datei unbemerkt in der Watchdog-Ueberwachung
        (BUGS_OFFEN.md #28)."""
        candidates = self.online.get(kind, set())
        if device_id:
            if device_id in candidates:
                return device_id, ""
            return None, tr("Gerät '{device_id}' nicht verbunden", device_id=device_id)
        if len(candidates) == 1:
            return next(iter(candidates)), ""
        label = kind_label(kind)
        if not candidates:
            return None, tr("Kein Gerät des Typs '{label}' verbunden", label=label)
        return None, tr(
            "Mehrere Geräte des Typs '{label}' verbunden -- bitte Zielgerät in der Testcase-Zeile auswählen",
            label=label,
        )

    def step_device_ids(self, steps: list[TestStep]) -> set[str]:
        """Alle aufloesbaren Geraete eines Ablaufs (Aktionen und Messbedingungen); nicht aufloesbare
        fehlen -- der Runner scheitert an solchen Schritten ohnehin sofort."""
        device_ids: set[str] = set()
        for step in steps:
            if not step.enabled:
                continue
            if step.step_type == "action":
                resolved, _ = self.resolve(step.device_kind, step.device_id)
            elif step.step_type in ("while", "if") and step.cond_source == "measurement":
                resolved, _ = self.resolve(step.cond_device_kind, step.cond_device_id)
            else:
                continue
            if resolved:
                device_ids.add(resolved)
        return device_ids

    def missing(self, steps: list[TestStep], skip: Callable[[str], bool] | None = None) -> list[str]:
        """Fehlermeldungen fuer Schritte, deren Geraet (noch) nicht aufloesbar ist -- fuer das Warten
        auf Geraete vor einem Lauf ohne Fenster. `skip(device_id)` nimmt z. B. Partnergeraete aus."""
        problems: list[str] = []
        for step in steps:
            if not step.enabled:
                continue
            if step.step_type == "action":
                kind, device_id = step.device_kind, step.device_id
            elif step.step_type in ("while", "if") and step.cond_source == "measurement":
                kind, device_id = step.cond_device_kind, step.cond_device_id
            else:
                continue
            if skip is not None and device_id and skip(device_id):
                continue
            resolved, error = self.resolve(kind, device_id)
            if resolved is None and error not in problems:
                problems.append(error)
        return problems


class Interlock:
    """Verriegelung Netzteil/Last im Testablauf (FP-7 b, siehe Moduldoc)."""

    def __init__(self, groups: list[list[str]] | None = None,
                 is_online: Callable[[str], bool] | None = None) -> None:
        self._groups: list[list[str]] = []
        self._state: dict[str, bool] = {}       # device_id -> Ausgang ein (letzte Rueckmeldung/Aktion)
        self._is_online = is_online or (lambda _device_id: True)
        self.set_groups(groups or [])

    def set_groups(self, groups: list[list[str]]) -> None:
        self._groups = [list(group) for group in groups if len(group) >= 2]

    def groups(self) -> list[list[str]]:
        return [list(group) for group in self._groups]

    @Slot(str, bool)
    def on_output_state(self, device_id: str, on: bool) -> None:
        self._state[device_id] = bool(on)

    @Slot(str, str)
    def on_device_removed(self, _kind: str, device_id: str) -> None:
        self._state.pop(device_id, None)

    def note_action(self, device_id: str, kind: str, action: str) -> None:
        """Nach erfolgreichem Senden: Zustand sofort nachfuehren."""
        if action in SWITCH_ON_ACTIONS.get(kind, ()):
            self._state[device_id] = True
        elif action in SWITCH_OFF_ACTIONS.get(kind, ()):
            self._state[device_id] = False

    def check(self, device_id: str, kind: str, action: str) -> str:
        """"" = erlaubt, sonst der Grund der Ablehnung."""
        if action not in SWITCH_ON_ACTIONS.get(kind, ()):
            return ""
        for group in self._groups:
            if device_id not in group:
                continue
            for other in group:
                if other == device_id:
                    continue
                state = self._state.get(other)
                if state:
                    return tr("Verriegelung: {other} ist eingeschaltet", other=other)
                if state is None and self._is_online(other):
                    return tr("Verriegelung: Zustand von {other} unbekannt", other=other)
        return ""


class RunSession(QObject):
    """Ein Testlauf vom Start bis zum Ende, ohne Fenster (siehe Moduldoc)."""

    # An den DeviceWorker (Queued Connection in dessen Thread)
    dispatch_action = Signal(str, str, str, float, int)   # device_id, kind, action, value, channel
    dispatch_can_send = Signal(str, int, str, bool)
    picoscope_test_running = Signal(bool)
    close_picoscope_sessions = Signal()
    # Lauf zu Ende (nachdem die Laufaufzeichnung ihr Ergebnis hat): outcome wie RunRecord.outcome
    run_ended = Signal(str)

    def __init__(self, worker, safety, recorder: RunRecorder, directory: DeviceDirectory,
                 interlock: Interlock, live_state=None, scope_service=None, parent=None) -> None:
        super().__init__(parent)
        self.worker = worker
        self.safety = safety
        self.recorder = recorder
        self.directory = directory
        self.interlock = interlock
        self._live_state = live_state
        self._scope_service = scope_service
        self.meta: dict = {}
        self.testcase_name = ""
        # Verbund: (device_id, kind, action, value, channel) -> True, wenn der Router die Aktion
        # uebernommen hat (Partnergeraet, MainWindow._route_partner_action)
        self.partner_router: Callable[[str, str, str, float, int], bool] | None = None
        # Messmittel (instruments.Instruments, FT-8); None = nicht erfassen
        self.instruments = None
        self._active = False
        # An den Worker gesendete Schaltaktion, deren Bestaetigung die Verriegelung nachfuehrt
        self._pending_switch: tuple[str, str, str] | None = None

        runner = self.runner = TestRunner()
        runner.setParent(self)
        runner.execute_action.connect(self._on_execute_action)
        runner.execute_can_send.connect(self._on_execute_can_send)
        self.dispatch_action.connect(worker.execute_action)
        self.dispatch_can_send.connect(worker.execute_can_send)
        worker.action_completed.connect(self._on_action_completed)
        worker.load_measurement.connect(runner.on_load_measurement)
        worker.psu_measurement.connect(runner.on_psu_measurement)
        # microHIL/CAN als while/if-Bedingungsquelle (BUGS_GESCHLOSSEN.md #35)
        worker.hil_analog_input.connect(runner.on_hil_analog_input)
        worker.hil_digital_state.connect(runner.on_hil_digital_state)
        worker.can_signals_decoded.connect(runner.on_can_signals_decoded)
        worker.device_removed.connect(runner.on_device_removed)
        worker.psu_output_state.connect(interlock.on_output_state)
        worker.load_input_state.connect(interlock.on_output_state)
        worker.device_removed.connect(interlock.on_device_removed)

        # Nachlauf-Report: passiver Mitschnitt (run_record.py). VOR _on_run_end verbinden, damit das
        # Ergebnis dort schon feststeht.
        runner.step_started.connect(recorder.on_step_started)
        runner.step_result.connect(recorder.on_step_result)
        runner.step_failed.connect(recorder.on_step_failed)
        runner.run_finished.connect(recorder.on_run_finished)
        runner.run_stopped.connect(recorder.on_run_stopped)
        runner.iteration_changed.connect(recorder.on_iteration_changed)
        worker.load_measurement.connect(recorder.on_load_measurement)
        worker.psu_measurement.connect(recorder.on_psu_measurement)

        self.picoscope_test_running.connect(worker.set_test_running)
        self.close_picoscope_sessions.connect(worker.close_picoscope_sessions)
        runner.run_finished.connect(self._on_run_end)
        runner.run_stopped.connect(self._on_run_end)
        runner.step_failed.connect(lambda *_args: self._on_run_end())

    # -- Start ---------------------------------------------------------------------

    def is_running(self) -> bool:
        return self.runner.is_running()

    def refusal(self, steps: list[TestStep]) -> str:
        """Grund, warum ein Lauf nicht starten darf ("" = darf)."""
        if not any(step.enabled and step.step_type == "action" for step in steps):
            return tr("Keine aktiven Aktionsschritte")
        if self.safety.is_tripped():
            return tr("Sicherheitsabschaltung zuerst quittieren")
        if self.runner.is_running():
            return tr("Es läuft bereits ein Ablauf")
        return ""

    def start(self, steps: list[TestStep], testcase_name: str, meta: dict | None = None,
              variables: dict[str, float] | None = None) -> None:
        """Startet den Lauf (vorher refusal() pruefen). Der erste Schritt startet noch in diesem Aufruf."""
        self.meta = dict(meta or {})
        self.testcase_name = testcase_name
        self.recorder.begin(steps, testcase_name)
        record = self.recorder.record()
        if record is not None:
            record.meta = dict(self.meta)
            record.interlock_groups = self.interlock.groups()
        if self.meta.get("source_sink") and self.interlock.groups():
            logger.warning("Testablauf %s: Verriegelung Netzteil/Last aufgehoben (Quelle/Senke)", testcase_name)
        self._active = True
        step_device_ids = self.directory.step_device_ids(steps)
        if self.instruments is not None and record is not None:
            # Messmittel (FT-8): Kennung und Kalibrierung der eigenen Geraete dieses Laufs festhalten
            self.instruments.reload()
            own = {d for d in step_device_ids if not d.startswith("@")}
            record.instruments = self.instruments.for_run(
                own, {d: record.device_meta.get(d, ("", d))[1] for d in own})
        # PicoScope und J-Link nicht in die Watchdog-Ueberwachung: kein Ausgang, und waehrend eines
        # Laufs keine periodischen Messwerte (sonst "veraltet" nach STALE_TIMEOUT_S, an Hardware
        # reproduziert)
        self.safety.begin_run_supervision(
            {d for d in step_device_ids if not d.startswith(("picoscope:", "jlink:"))}
        )
        self.picoscope_test_running.emit(True)
        if self._live_state is not None:
            self._live_state.set_test_running(True)
        # PicoScope-Sitzung nur mit PICO_*-Schritt, und BLOCKIEREND: sonst lief der erste PICO-Schritt
        # schon, waehrend der Worker die Verbindung (~4,5 s) noch aufbaute (an Hardware reproduziert)
        for device_id in step_device_ids:
            if device_id.startswith("picoscope:"):
                if self._scope_service is not None:
                    # Vorrang des Testablaufs vor Erfassungen ueber Netzwerk/MCP
                    self._scope_service.begin_test(device_id)
                QMetaObject.invokeMethod(
                    self.worker, "open_picoscope_session", Qt.ConnectionType.BlockingQueuedConnection,
                    Q_ARG(str, device_id),
                )
        self.runner.start(steps, variables)

    # -- Aktionen ------------------------------------------------------------------

    @Slot(str, str, str, float, int)
    def _on_execute_action(self, device_id: str, kind: str, action: str, value: float, channel: int) -> None:
        self._pending_switch = None
        if self.partner_router is not None and self.partner_router(device_id, kind, action, value, channel):
            return
        resolved_id, error = self.directory.resolve(kind, device_id)
        if resolved_id is None:
            self.runner.on_action_completed(False, error, 0.0)
            return
        if not self.meta.get("source_sink"):
            refusal = self.interlock.check(resolved_id, kind, action)
            if refusal:
                logger.warning("Testablauf: %s abgelehnt (%s)", action, refusal)
                self.runner.on_action_completed(False, refusal, 0.0)
                return
        self._pending_switch = (resolved_id, kind, action)
        self.dispatch_action.emit(resolved_id, kind, action, value, channel)

    @Slot(str, int, str, bool)
    def _on_execute_can_send(self, device_id: str, arbitration_id: int, data_hex: str, extended: bool) -> None:
        resolved_id, error = self.directory.resolve("can", device_id)
        if resolved_id is None:
            self.runner.on_action_completed(False, error, 0.0)
            return
        self._pending_switch = None
        self.dispatch_can_send.emit(resolved_id, arbitration_id, data_hex, extended)

    @Slot(bool, str, float)
    def _on_action_completed(self, success: bool, message: str, value: float) -> None:
        pending, self._pending_switch = self._pending_switch, None
        if success and pending is not None:
            self.interlock.note_action(*pending)
        self.runner.on_action_completed(success, message, value)

    # -- Ende ----------------------------------------------------------------------

    def _on_run_end(self) -> None:
        if not self._active:
            return
        self._active = False
        # Reihenfolge wie frueher in MainWindow: Watchdog aus, PicoScope-Reconnect wieder frei, Netzwerk-
        # Sperre aus, DANN Sitzungen schliessen (der Worker arbeitet die Signale in dieser Reihenfolge ab)
        self.safety.end_run_supervision()
        self.picoscope_test_running.emit(False)
        if self._live_state is not None:
            self._live_state.set_test_running(False)
        self.close_picoscope_sessions.emit()
        if self._scope_service is not None:
            self._scope_service.end_test()
        record = self.recorder.record()
        self.run_ended.emit(record.outcome if record is not None else "error")


def exit_code(record, interrupted: bool = False) -> int:
    """Exit-Code eines abgeschlossenen Laufs (FP-3 a): 0 bestanden, 1 nicht bestanden (auch eine
    verletzte Pruefung mit Abbruch), 2 Abbruch/Fehler/Sicherheitsabschaltung, 130 unterbrochen."""
    if interrupted:
        return EXIT_INTERRUPTED
    if record is None:
        return EXIT_ABORTED
    if record.outcome == "passed":
        return EXIT_PASSED
    if record.outcome == "failed_checks":
        return EXIT_FAILED
    if record.outcome == "error" and failed_by_check(record):
        return EXIT_FAILED
    return EXIT_ABORTED


def failed_by_check(record) -> bool:
    """Endete der Lauf, weil eine Pruefung mit "Bei Verletzung abbrechen" verletzt war? Der Runner
    meldet dann zuerst step_result(False) und direkt danach step_failed fuer denselben Schritt."""
    events = [e for e in record.events if e.index == record.error_index and e.kind in ("result", "failed")]
    return len(events) >= 2 and events[-1].kind == "failed" and events[-2].kind == "result" \
        and events[-2].passed is False

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
  Oszilloskop "Scharf schalten"/"Auf Erfassung warten" (1.0.1, PI-72 a) gehen an den ScopeService (derselbe
  Erfassungskern wie REST/MCP); Laufende/-abbruch bricht eine noch scharfe Erfassung ab und gibt frei.
- Abschaltung beim Abbruch (1.0.1, PI-76 a/PI-79 a, _shutdown): nach dem Abbruch-Block (TestRunner) -- ist er
  fehlerfrei gelaufen, bleibt alles, wie er es hinterlassen hat. Sonst (kein Block, Fehler im Block,
  Sicherheitsabschaltung) werden die Geraete abgeschaltet, die der Ablauf schaltet, dazu das ausloesende Geraet;
  nur gelesene Geraete bleiben, wie sie sind. Eigene ueber den Worker (devices_off_tagged), die des Verbund-
  Partners ueber partner_devices_off (POST /devices-off beim Besitzer). Ergebnis in RunRecord.shutdown.
  ALLE AUS/NOTAUS schaltet weiterhin alles ab (MainWindow), hier wird es nur vermerkt.
"""
from __future__ import annotations

import itertools
import logging
import time
from typing import Callable

from PySide6.QtCore import QMetaObject, QObject, Q_ARG, Qt, QTimer, Signal, Slot

import scope_steps
from i18n import tr
from run_record import RunRecorder
from switch_log import LastSwitch
from testcase_model import OFF_DEVICE_KINDS, PICO_READ_ACTIONS, TestStep, kind_label, switched_devices
from testcase_runner import TestRunner

logger = logging.getLogger(__name__)

# Abschaltung beim Abbruch (PI-79 a): so lange auf die Rueckmeldung von Worker und Verbund-Partner warten (ALLE AUS
# braucht mit Wiederholung je Geraet einige Sekunden, der Partner bis zu share_remote.ALL_OFF_TIMEOUT_S)
SHUTDOWN_TIMEOUT_S = 20.0
_OFF_TAGS = itertools.count(1)


def device_kind_of(device_id: str) -> str:
    """Geraeteart aus der Kennung ("psu:0001", Partner "@pi/psu:0001")."""
    return device_id.rpartition("/")[2].partition(":")[0]

DEVICE_KINDS = ("load", "psu", "can", "hil", "picoscope", "fg", "scpiscope", "battsim", "jlink", "uds", "xcp")

# Aktionen, die einen Ausgang/Eingang EINschalten (Verriegelung FP-7 b)
SWITCH_ON_ACTIONS = {"load": {"OUT_ON"}, "psu": {"PSU_OUT_ON"}}
# Aktionen, die ihn ausschalten -- der Zustand gilt danach sofort als "aus", nicht erst mit der
# naechsten Rueckmeldung des Geraets (sonst lehnte ein direkt folgendes EIN des anderen Geraets ab)
SWITCH_OFF_ACTIONS = {"load": {"OUT_OFF"}, "psu": {"PSU_OUT_OFF"}}

# Oszilloskop "Auf Erfassung warten" (PI-72 a): rechtzeitig getriggert, aber die Daten werden noch gelesen --
# so lange wartet der Schritt zusaetzlich, bevor er scheitert
SCOPE_READ_TIMEOUT_S = 60.0

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
            if not step.enabled or step.device_kind in ("uds", "xcp"):   # Pruefling, kein Geraet (refusal)
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
            if not step.enabled or (step.step_type == "action" and step.device_kind in ("uds", "xcp")):
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
    dispatch_restbus = Signal(str, str, str, str, str)   # FT-6: can_id, Aktion, Botschaft, Signal, Wert
    picoscope_test_running = Signal(bool)
    close_picoscope_sessions = Signal()
    # Abschaltung beim Abbruch (PI-79 a): Kennung, Geraete, Grund -> DeviceWorker.devices_off_tagged
    dispatch_devices_off = Signal(int, list, str)
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
        # PI-77 a: (kind, device_id, action) -> switch_log.LastSwitch fuer ein Partnergeraet ("@rechner/id"),
        # None = kein Partnergeraet (MainWindow._partner_last_switch); eigene Geraete: worker.switch_log
        self.partner_switch_lookup: Callable[[str, str, str], LastSwitch | None] | None = None
        # Messmittel (instruments.Instruments, FT-8); None = nicht erfassen
        self.instruments = None
        # Anforderungsregister (FT-3): () -> {id: {"title", ...}}; None = ohne Titel
        self.requirement_register: Callable[[], dict] | None = None
        # UDS am Pruefling (FT-1): uds_service.UdsService, siehe set_uds
        self.uds = None
        self._uds_pending: tuple[int, TestStep] | None = None
        # Flashen ueber CAN (PI-54): canflash_service.CanFlashService, siehe set_flasher
        self.flasher = None
        self._flash_pending: int | None = None
        self._active = False
        # An den Worker gesendete Schaltaktion, deren Bestaetigung die Verriegelung nachfuehrt
        self._pending_switch: tuple[str, str, str] | None = None
        # Abschaltung beim Abbruch (PI-79 a): Geraete, die der Ablauf schaltet (aufgeloest), und laufende Abschaltung
        self._switched: list[str] = []
        self._shutdown_state: dict | None = None
        self.trip_handled = False   # letzte Sicherheitsabschaltung hat dieser Lauf uebernommen (handle_safety_trip)
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)
        # Geraete des Verbund-Partners abschalten: (Kennungen "@rechner/id", Grund, fertig(off, failed)) -> False,
        # wenn das nicht geht (kein Verbund) -- MainWindow._partner_devices_off
        self.partner_devices_off: Callable[[list[str], str, Callable[[list, list], None]], bool] | None = None

        runner = self.runner = TestRunner()
        runner.setParent(self)
        runner.shutdown_handler = self._shutdown
        runner.escalate_handler = self._escalate
        self._worker_off = hasattr(worker, "devices_off_tagged")
        if self._worker_off:
            self.dispatch_devices_off.connect(worker.devices_off_tagged)
            worker.devices_off_done.connect(self._on_devices_off_done)
        # PI-81 a: ALLE AUS meldet nicht erreichte Geraete; im Lauf (Art "all_off") kommen sie in den Bericht.
        # _all_off_requested: monotonic() der letzten ALLE-AUS-Anforderung (expect_all_off), _all_off_last:
        # (monotonic(), Kennungen) der letzten Rueckmeldung
        self._worker_all_off = hasattr(worker, "all_off_unreachable")
        self._all_off_requested = 0.0
        self._all_off_last: tuple[float, list[str]] | None = None
        if self._worker_all_off:
            worker.all_off_unreachable.connect(self._on_all_off_unreachable)
        try:
            safety.run_trip_handler = self.handle_safety_trip   # Watchdog im Lauf (PI-79 a)
        except AttributeError:
            pass
        runner.switch_lookup = self._lookup_switch   # PI-77 a: "Warten seit Aktion" ueber den ganzen Rechner
        runner.execute_action.connect(self._on_execute_action)
        runner.execute_can_send.connect(self._on_execute_can_send)
        runner.execute_restbus.connect(self._on_execute_restbus)
        self.dispatch_restbus.connect(worker.execute_restbus)
        runner.execute_uds.connect(self._on_execute_uds)
        runner.execute_xcp.connect(self._on_execute_xcp)
        # Oszilloskop scharf schalten / auf Erfassung warten (PI-72 a)
        runner.execute_scope.connect(self._on_execute_scope)
        self._scope_armed: dict[str, dict] = {}    # device_id -> {"capture_id", "channel", "index"}
        self._scope_arming: dict | None = None     # Schritt "Scharf schalten", bis das Geraet scharf ist
        self._scope_wait: dict | None = None       # Schritt "Auf Erfassung warten", bis Trigger/Hoechstzeit
        self._scope_devices: set[str] = set()      # vom Lauf ueber den ScopeService genutzt
        self._scope_timer = QTimer(self)
        self._scope_timer.setSingleShot(True)
        self._scope_timer.timeout.connect(self._on_scope_timeout)
        if scope_service is not None:
            scope_service.capture_state.connect(self._on_capture_state)
        self.xcp = None
        self._xcp_pending = None
        self.dispatch_action.connect(worker.execute_action)
        self.dispatch_can_send.connect(worker.execute_can_send)
        worker.action_completed.connect(self._on_action_completed)
        worker.load_measurement.connect(runner.on_load_measurement)
        worker.psu_measurement.connect(runner.on_psu_measurement)
        # microHIL/CAN als while/if-Bedingungsquelle (BUGS_GESCHLOSSEN.md #35)
        worker.hil_analog_input.connect(runner.on_hil_analog_input)
        worker.hil_digital_state.connect(runner.on_hil_digital_state)
        worker.can_signals_decoded.connect(runner.on_can_signals_decoded)
        worker.can_frame_received.connect(runner.on_can_frame)
        runner.hil_focus_requested.connect(worker.set_hil_focus)   # FP-18 b: beobachteten Kanal schneller
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
        runner.setpoint_sent.connect(recorder.on_setpoint_sent)
        runner.monitor_result.connect(recorder.on_monitor_result)
        runner.iteration_changed.connect(recorder.on_iteration_changed)
        runner.step_note.connect(recorder.on_step_note)   # PI-71 a: Bestaetigung, tatsaechliche Wartezeit
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
        confirms = [i for i, s in enumerate(steps) if s.enabled and s.step_type == "confirm"]
        if confirms and not self.runner.confirm_available:
            # PI-71 a: ohne Fenster kann niemand bestaetigen -- vor dem ersten Schalten ablehnen statt mittendrin
            return tr("Bestätigung in Schritt {n} geht nur im LabControl-Fenster", n=confirms[0] + 1)
        releases = [i for i, s in enumerate(steps) if s.enabled and s.step_type == "await_release"]
        if releases and not self.runner.release_possible():
            # PI-74 a: ohne laufenden Server (REST/MCP) kann niemand freigeben -- vor dem Start ablehnen
            return tr("Warten auf Freigabe in Schritt {n} braucht die Netzwerkfreigabe (REST/MCP)",
                      n=releases[0] + 1)
        uds_steps_used = [s for s in steps if s.enabled and s.step_type == "action" and s.device_kind == "uds"]
        if any(s.action != "UDS_FLASH" for s in uds_steps_used) and (self.uds is None or not self.uds.configured()):
            return tr("Kein Prüfling (UDS) eingestellt")
        if any(s.action == "UDS_FLASH" for s in uds_steps_used):
            problem = tr("Flashen über CAN ist hier nicht eingerichtet") if self.flasher is None \
                else self.flasher.problem()
            if problem:
                return problem
        if any(s.enabled and s.step_type == "action" and s.device_kind == "xcp" for s in steps) and (
                self.xcp is None or not self.xcp.configured()):
            return tr("Keine A2L beim Prüfling eingestellt (XCP)")
        return self._scope_refusal(steps)

    def _scope_refusal(self, steps: list[TestStep]) -> str:
        """PI-72 a: Oszilloskop-Schritte brauchen den ScopeService (Fenster), ein eigenes Oszilloskop und
        vertragen sich am selben PicoScope nicht mit den alten Kennwert-Schritten (die oeffnen es im Worker)."""
        scope = [s for s in steps if s.enabled and scope_steps.is_scope_step(s)]
        if not scope:
            return ""
        if self._scope_service is None:
            return tr("Oszilloskop-Schritte (Scharf schalten, Auf Erfassung warten) gehen nur im LabControl-Fenster")
        if any(s.device_id.startswith("@") for s in scope):
            return tr("Oszilloskop-Schritte gehen nur an eigenen Oszilloskopen, nicht beim Verbund-Partner")
        own = {self.directory.resolve(s.device_kind, s.device_id)[0] for s in scope} - {None}
        legacy = {self.directory.resolve(s.device_kind, s.device_id)[0] for s in steps
                  if s.enabled and s.step_type == "action" and s.action in PICO_READ_ACTIONS} - {None}
        if own & legacy:
            return tr("PicoScope-Kennwerte (Maximum, Minimum, …) und „Scharf schalten“ am selben Oszilloskop gehen "
                      "nicht in einem Ablauf – „Auf Erfassung warten“ liefert dieselben Kennwerte")
        return ""

    # -- XCP (FT-10) ------------------------------------------------------------------

    def set_xcp(self, xcp) -> None:
        self.xcp = xcp
        xcp.finished.connect(self._on_xcp_finished)
        xcp.values_polled.connect(self.runner.on_xcp_values)

    @staticmethod
    def xcp_names_needed(steps: list[TestStep]) -> set[str]:
        """XCP-Messgroessen, die Bedingungen und Ueberwachen-Bloecke eines Ablaufs lesen (FT-10, 0.64.3)."""
        return {s.cond_field.strip() for s in steps if s.enabled and s.cond_device_kind == "xcp"
                and s.cond_field.strip() and (s.step_type == "monitor"
                                              or (s.step_type in ("while", "if") and s.cond_source == "measurement"))}

    @Slot(int, object)
    def _on_execute_xcp(self, _index: int, step: TestStep) -> None:
        import xcp_steps

        self._xcp_pending = None
        if self.xcp is None or not self.xcp.configured():
            self.runner.on_action_completed(False, tr("Keine A2L beim Prüfling eingestellt (XCP)"), 0.0)
            return
        try:
            command, args = xcp_steps.command_for(step.action, step.params)
        except (ValueError, TypeError) as exc:
            self.runner.on_action_completed(False, tr("XCP-Parameter: {error}", error=str(exc)), 0.0)
            return
        self._xcp_pending = (self.xcp.submit(command, args), step, args)

    @Slot(int, object)
    def _on_xcp_finished(self, request_id: int, result) -> None:
        if self._xcp_pending is None or self._xcp_pending[0] != request_id:
            return
        _request, step, args = self._xcp_pending
        self._xcp_pending = None
        if not result.ok:
            self.runner.on_action_completed(False, tr("XCP: {error}", error=result.error), 0.0)
            return
        index = int(args.get("index", 0) or 0)
        value = result.values[index] if step.action == "XCP_READ" and index < len(result.values) else 0.0
        self.runner.on_action_completed(True, "", float(value))

    # -- UDS (FT-1) ------------------------------------------------------------------

    def set_uds(self, uds) -> None:
        self.uds = uds
        uds.finished.connect(self._on_uds_finished)

    def set_flasher(self, flasher) -> None:
        self.flasher = flasher
        flasher.finished.connect(self._on_flash_finished)

    def _start_flash(self, step: TestStep) -> None:
        import uds_steps

        if self.flasher is None:
            self.runner.on_action_completed(False, tr("Flashen über CAN ist hier nicht eingerichtet"), 0.0)
            return
        try:
            args = uds_steps.flash_args(step.params)
        except (ValueError, TypeError) as exc:
            self.runner.on_action_completed(False, tr("Flash-Parameter: {error}", error=str(exc)), 0.0)
            return
        self._flash_pending = self.flasher.start(args["file"], args["wait"], args["ecu"], source="Testablauf")

    @Slot(int, bool, str)
    def _on_flash_finished(self, request: int, ok: bool, message: str) -> None:
        if self._flash_pending != request:
            return   # Kachel oder abgebrochener Lauf
        self._flash_pending = None
        if self._active:
            self.runner.on_action_completed(ok, message, 0.0)

    @Slot(int, object)
    def _on_execute_uds(self, _index: int, step: TestStep) -> None:
        import uds_steps

        self._uds_pending = None
        if step.action == "UDS_FLASH":
            self._start_flash(step)
            return
        if self.uds is None or not self.uds.configured():
            self.runner.on_action_completed(False, tr("Kein Prüfling (UDS) eingestellt"), 0.0)
            return
        try:
            command, args = uds_steps.command_for(step.action, step.params)
        except (ValueError, TypeError) as exc:
            self.runner.on_action_completed(False, tr("UDS-Parameter: {error}", error=str(exc)), 0.0)
            return
        self._uds_pending = (self.uds.submit(command, args), step)

    @Slot(int, object)
    def _on_uds_finished(self, request_id: int, result) -> None:
        import uds_steps

        if self._uds_pending is None or self._uds_pending[0] != request_id:
            return   # Kachel/anderer Aufrufer oder abgebrochener Lauf
        step = self._uds_pending[1]
        self._uds_pending = None
        verdict = uds_steps.evaluate(step.action, step.params, result)
        if verdict.status == "ok":
            self.runner.on_action_completed(True, "", verdict.value)
        elif verdict.status == "check_failed":
            self.runner.fail_check(verdict.message, verdict.value)
        else:
            self.runner.on_action_completed(False, verdict.message, 0.0)

    def start(self, steps: list[TestStep], testcase_name: str, meta: dict | None = None,
              variables: dict[str, float] | None = None, start_index: int = 0) -> None:
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
        self._shutdown_state = None
        self._switched = self.switched_ids(steps)
        step_device_ids = self.directory.step_device_ids(steps)
        if self.requirement_register is not None and record is not None:
            # Anforderungen (FT-3): Titel der genannten IDs aus dem Register festhalten
            ids = {r for step in steps for r in step.requirements} | set(self.meta.get("requirements", []))
            if ids:
                try:
                    register = self.requirement_register()
                except Exception:  # noqa: BLE001 -- ein kaputtes Register darf keinen Lauf verhindern
                    logger.exception("Anforderungsregister nicht lesbar")
                    register = {}
                record.requirement_register = {i: register[i] for i in ids if i in register}
        if self.instruments is not None and record is not None:
            # Messmittel (FT-8): Kennung und Kalibrierung der eigenen Geraete dieses Laufs festhalten
            self.instruments.reload()
            own = {d for d in step_device_ids if not d.startswith("@")}
            record.instruments = self.instruments.for_run(
                own, {d: record.device_meta.get(d, ("", d))[1] for d in own})
        # Oszilloskope und J-Link nicht in die Watchdog-Ueberwachung: kein Ausgang, und waehrend eines
        # Laufs keine periodischen Messwerte (sonst "veraltet" nach STALE_TIMEOUT_S, an Hardware
        # reproduziert)
        self.safety.begin_run_supervision(
            {d for d in step_device_ids if not d.startswith(("picoscope:", "scpiscope:", "jlink:"))}
        )
        self.picoscope_test_running.emit(True)
        if self._live_state is not None:
            self._live_state.set_test_running(True)
        # Oszilloskop-Schritte (PI-72 a) laufen ueber den ScopeService: Netzwerk/MCP sperren, die Verbindung
        # aber offen lassen (der Lauf erfasst selbst darueber)
        self._reset_scope_state()
        scope_ids = {self.directory.resolve(s.device_kind, s.device_id)[0] for s in steps
                     if s.enabled and scope_steps.is_scope_step(s)} - {None}
        if self._scope_service is not None:
            for device_id in sorted(scope_ids):
                self._scope_service.begin_test(device_id, close=False)
        # PicoScope-Sitzung nur mit PICO_*-Schritt, und BLOCKIEREND: sonst lief der erste PICO-Schritt
        # schon, waehrend der Worker die Verbindung (~4,5 s) noch aufbaute (an Hardware reproduziert)
        for device_id in step_device_ids:
            if device_id.startswith("picoscope:") and device_id not in scope_ids:
                if self._scope_service is not None:
                    # Vorrang des Testablaufs vor Erfassungen ueber Netzwerk/MCP
                    self._scope_service.begin_test(device_id)
                QMetaObject.invokeMethod(
                    self.worker, "open_picoscope_session", Qt.ConnectionType.BlockingQueuedConnection,
                    Q_ARG(str, device_id),
                )
        needed = self.xcp_names_needed(steps)
        if needed and self.xcp is not None:   # XCP-Messliste fuer Bedingungen/Ueberwachen abfragen (0.64.3)
            self.xcp.set_polling("run", True, needed)
        self.runner.start(steps, variables, start_index)

    # -- Abschaltung beim Abbruch (PI-76 a, PI-79 a) ------------------------------------

    def switched_ids(self, steps: list[TestStep]) -> list[str]:
        """Aufgeloeste Kennungen der Geraete, die der Ablauf schaltet (auch im Abbruch-Block). Feste Kennungen
        bleiben, auch wenn das Geraet gerade fehlt (dann "nicht erreichbar"); "automatisch" nur, wenn eindeutig."""
        result: list[str] = []
        for kind, device_id in switched_devices(steps):
            resolved = device_id if device_id else self.directory.resolve(kind, "")[0]
            if resolved and resolved not in result:
                result.append(resolved)
        return result

    def _resolve_trigger(self, trigger) -> str:
        """Kennung des ausloesenden Geraets, "" = keins bzw. eins, das die Abschaltung nicht kennt."""
        if not trigger:
            return ""
        kind, device_id = trigger
        if not device_id:
            device_id = self.directory.resolve(kind, "")[0] or ""
        if not device_id:
            return ""
        kind = kind or device_kind_of(device_id)
        return device_id if kind in OFF_DEVICE_KINDS and device_kind_of(device_id) in OFF_DEVICE_KINDS else ""

    def handle_safety_trip(self, device_id: str, reason: str) -> bool:
        """SafetyMonitor.run_trip_handler: Sicherheitsabschaltung waehrend eines Laufs -- abschalten nur der
        Geraete des Ablaufs und des ausloesenden Geraets, ohne Abbruch-Block (PI-79 a). False = kein Lauf."""
        if not self._active or not self.runner.is_running():
            return False
        self.trip_handled = True   # fuer MainWindow._on_safety_tripped: kein ALLE AUS am Partner
        self.runner.abort(reason, mode="watchdog", trigger=("", device_id))
        return True

    def _shutdown(self, info: dict) -> bool:
        """TestRunner.shutdown_handler: Abschaltung nach dem Abbruch-Block. True = laeuft, finish_abort folgt."""
        record = self.recorder.record()
        mode = info["mode"]
        trigger = self._resolve_trigger(info.get("trigger"))
        steps = record.steps if record is not None else []
        block_rows = []
        for row in info.get("block") or []:
            step = steps[row["index"]] if 0 <= row["index"] < len(steps) else None
            device = ""
            if step is not None and step.step_type == "action":
                device = (step.device_id if step.device_id else
                          self.directory.resolve(step.device_kind, "")[0] or "")
            block_rows.append({"step": row["index"] + 1, "ok": bool(row["ok"]), "message": row.get("message", ""),
                               "device_id": device})
        shutdown = {"mode": "", "cause": info["kind"], "message": info.get("message", ""), "trigger": trigger,
                    "block": block_rows if info.get("has_block") else None, "fallback": False,
                    "timed_out": bool(info.get("timed_out")), "devices": [], "off": [], "unreachable": [],
                    "labels": {}}
        if mode == "all_off":
            # ALLE AUS/NOTAUS: MainWindow schaltet alle Geraete ab (auch beim Partner) -- vermerken, und seit
            # PI-81 a die Geraete, die ALLE AUS nicht erreicht hat (Ausgang unbekannt), in den Bericht
            shutdown["mode"] = "all_off"
            requested = self._all_off_requested
            if not self._worker_all_off or not requested or time.monotonic() - requested > SHUTDOWN_TIMEOUT_S:
                self._finish_shutdown(shutdown, notify=False)   # kein ALLE AUS angefordert (z. B. Kommandozeile)
                return False
            last = self._all_off_last
            if last is not None and last[0] >= requested:   # Rueckmeldung schon da
                shutdown["unreachable"] = list(last[1])
                self._finish_shutdown(shutdown, notify=False)
                return False
            self._shutdown_state = {"shutdown": shutdown, "own_tag": 0, "partner": False, "all_off": True,
                                    "reason": "", "extra_tags": set()}
            self._shutdown_timer.start(round(SHUTDOWN_TIMEOUT_S * 1000))
            return True
        if mode == "normal" and info.get("has_block"):
            for row in block_rows:
                if not row["device_id"]:
                    continue
                bucket = shutdown["off"] if row["ok"] else shutdown["unreachable"]
                if row["device_id"] not in bucket:
                    bucket.append(row["device_id"])
            shutdown["off"] = [d for d in shutdown["off"] if d not in shutdown["unreachable"]]
            shutdown["mode"] = "block"
            if info.get("block_ok"):
                shutdown["devices"] = list(shutdown["off"])
                self._finish_shutdown(shutdown, notify=False)
                return False
            shutdown["fallback"] = True   # Fehler im Block: zur Sicherheit wie ohne Block abschalten
        else:
            shutdown["mode"] = "watchdog" if mode == "watchdog" else "standard"
        targets = list(self._switched)
        if trigger and trigger not in targets:
            targets.append(trigger)
        shutdown["devices"] = targets
        reason = tr("Testablauf „{name}“ abgebrochen", name=self.testcase_name or "?")
        state = self._shutdown_state = {"shutdown": shutdown, "own_tag": 0, "partner": False, "reason": reason,
                                        "extra_tags": set()}
        self._send_off(targets, reason)
        if self._shutdown_state is not state:
            return True   # schon fertig (Rueckmeldung kam sofort), finish_abort ist gelaufen
        if not state["own_tag"] and not state["partner"]:
            self._finish_shutdown(shutdown, notify=False)
            return False
        self._shutdown_timer.start(round(SHUTDOWN_TIMEOUT_S * 1000))
        return True

    def _send_off(self, targets: list[str], reason: str) -> None:
        state = self._shutdown_state
        shutdown = state["shutdown"]
        own = [d for d in targets if not d.startswith("@")]
        partner = [d for d in targets if d.startswith("@")]
        logger.warning("Testablauf abgebrochen (%s): schalte ab: %s", shutdown["mode"], ", ".join(targets) or "-")
        if own:
            if self._worker_off:
                tag = next(_OFF_TAGS)
                state["own_tag"] = tag
                state["own_ids"] = list(own)
                self.dispatch_devices_off.emit(tag, own, reason)
            else:   # kein Worker mit Abschaltung (Pruefskript-Attrappe)
                self._merge_off([], own)
        if partner:
            sender = self.partner_devices_off
            accepted = False
            state["partner"] = True   # vor dem Aufruf: die Rueckmeldung darf auch sofort kommen
            state["partner_ids"] = list(partner)
            if sender is not None:
                try:
                    accepted = bool(sender(partner, reason,
                                           lambda off, failed, st=state: self._on_partner_off_done(st, off, failed)))
                except Exception:  # noqa: BLE001 -- dann nicht erreichbar
                    logger.exception("Abschaltung beim Verbund-Partner gescheitert")
            if not accepted and state["partner"]:
                state["partner"] = False
                self._merge_off([], partner)

    def _merge_off(self, off: list, failed: list) -> None:
        shutdown = self._shutdown_state["shutdown"]
        for device_id in off:
            if device_id in shutdown["unreachable"]:
                shutdown["unreachable"].remove(device_id)
            if device_id not in shutdown["off"]:
                shutdown["off"].append(device_id)
        for device_id in failed:
            if device_id in shutdown["off"]:
                shutdown["off"].remove(device_id)
            if device_id not in shutdown["unreachable"]:
                shutdown["unreachable"].append(device_id)

    @Slot(int, list, list)
    def _on_devices_off_done(self, tag: int, off: list, failed: list) -> None:
        state = self._shutdown_state
        if state is not None and tag in state["extra_tags"]:   # ausloesendes Geraet einer Verschaerfung
            state["extra_tags"].discard(tag)
            self._merge_off(list(off), list(failed))
            return
        if state is None or not tag or tag != state.get("own_tag"):
            return
        state["own_tag"] = 0
        self._merge_off(list(off), list(failed))
        self._maybe_finish_shutdown()

    def _on_partner_off_done(self, state: dict, off: list, failed: list) -> None:
        if state is not self._shutdown_state or not state.get("partner"):
            return
        state["partner"] = False
        self._merge_off(list(off), list(failed))
        self._maybe_finish_shutdown()

    def _maybe_finish_shutdown(self) -> None:
        state = self._shutdown_state
        if state is not None and not state["own_tag"] and not state["partner"] and not state.get("all_off"):
            self._shutdown_timer.stop()
            self._finish_shutdown(state["shutdown"])

    def expect_all_off(self) -> None:
        """MainWindow._safe_stop hat ALLE AUS beim Worker angefordert (PI-81 a): bricht das einen Lauf ab, wartet
        die Abschaltung auf dessen Rueckmeldung (all_off_unreachable), damit der Bericht die nicht erreichten
        Geraete nennt."""
        self._all_off_requested = time.monotonic()

    @Slot(list)
    def _on_all_off_unreachable(self, device_ids: list) -> None:
        """DeviceWorker.all_off_unreachable (PI-81 a): Geraete, die ALLE AUS nicht erreicht hat."""
        ids = [str(d) for d in device_ids]
        self._all_off_last = (time.monotonic(), ids)
        state = self._shutdown_state
        if state is None or not self._all_off_requested:
            return
        self._merge_off([], ids)   # auch bei einer zu ALLE AUS verschaerften Abschaltung
        if state.get("all_off"):
            state["all_off"] = False
            self._maybe_finish_shutdown()

    def _on_shutdown_timeout(self) -> None:
        state = self._shutdown_state
        if state is None:
            return
        if state.get("all_off"):
            logger.error("Abschaltung beim Abbruch: keine Rueckmeldung von ALLE AUS")
            state["all_off"] = False
        pending = (state.get("own_ids", []) if state["own_tag"] else []) + \
                  (state.get("partner_ids", []) if state["partner"] else [])
        if pending:
            logger.error("Abschaltung beim Abbruch: keine Rueckmeldung fuer %s", ", ".join(pending))
        state["own_tag"] = 0
        state["partner"] = False
        self._merge_off([], [d for d in pending if d not in state["shutdown"]["off"]])
        self._finish_shutdown(state["shutdown"])

    def _escalate(self, mode: str, trigger) -> None:
        """TestRunner.escalate_handler: Sicherheitsabschaltung/ALLE AUS, waehrend die Abschaltung schon laeuft."""
        state = self._shutdown_state
        if state is None:
            return
        shutdown = state["shutdown"]
        shutdown["escalated"] = mode
        device_id = self._resolve_trigger(trigger)
        if mode != "watchdog" or not device_id or device_id in shutdown["devices"]:
            return   # ALLE AUS schaltet MainWindow ohnehin ab
        shutdown["devices"].append(device_id)
        shutdown["trigger"] = shutdown["trigger"] or device_id
        if device_id.startswith("@") or not self._worker_off:
            logger.error("Abschaltung beim Abbruch: ausloesendes Geraet %s nachtraeglich nicht abschaltbar", device_id)
            self._merge_off([], [device_id])
            return
        tag = next(_OFF_TAGS)
        state["extra_tags"].add(tag)
        self.dispatch_devices_off.emit(tag, [device_id], state["reason"])

    def _finish_shutdown(self, shutdown: dict, notify: bool = True) -> None:
        """notify: der Runner wartet auf finish_abort (asynchrone Abschaltung); synchron meldet er sich selbst."""
        self._shutdown_state = None
        self._shutdown_timer.stop()
        ids = set(shutdown["devices"]) | set(shutdown["off"]) | set(shutdown["unreachable"])
        if shutdown["trigger"]:
            ids.add(shutdown["trigger"])
        shutdown["labels"] = {d: self._label(d) for d in sorted(ids)}
        record = self.recorder.record()
        if record is not None:
            record.shutdown = shutdown
        logger.warning("Abschaltung beim Abbruch: %s%s, aus: %s, nicht erreichbar: %s", shutdown["mode"],
                       " (Rückfall)" if shutdown["fallback"] else "", ", ".join(shutdown["off"]) or "-",
                       ", ".join(shutdown["unreachable"]) or "-")
        if notify:
            self.runner.finish_abort()

    # -- Letzte Schaltung (PI-77 a) ----------------------------------------------------

    def _lookup_switch(self, kind: str, device_id: str, action: str) -> LastSwitch | None:
        """Letzte Schaltung ueber LabControl: beim Partner fuer "@rechner/id", sonst im eigenen Worker. None =
        nichts bekannt (ohne Worker-Protokoll, z. B. in Pruefskripten mit Attrappe)."""
        if device_id and self.partner_switch_lookup is not None:
            info = self.partner_switch_lookup(kind, device_id, action)
            if info is not None:
                return info
        log = getattr(self.worker, "switch_log", None)
        return log.lookup(kind, device_id, action) if log is not None else None

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

    @Slot(str, str, str, str, str)
    def _on_execute_restbus(self, device_id: str, action: str, message: str, signal: str, value: str) -> None:
        resolved_id, error = self.directory.resolve("can", device_id)
        if resolved_id is None:
            self.runner.on_action_completed(False, error, 0.0)
            return
        self._pending_switch = None
        self.dispatch_restbus.emit(resolved_id, action, message, signal, value)

    @Slot(bool, str, float)
    def _on_action_completed(self, success: bool, message: str, value: float) -> None:
        pending, self._pending_switch = self._pending_switch, None
        if success and pending is not None:
            self.interlock.note_action(*pending)
        self.runner.on_action_completed(success, message, value)

    # -- Oszilloskop scharf schalten / auf Erfassung warten (PI-72 a) ----------------------------

    def _reset_scope_state(self) -> None:
        self._scope_timer.stop()
        self._scope_armed = {}
        self._scope_arming = None
        self._scope_wait = None
        self._scope_devices = set()

    def _scope_fail(self, message: str) -> None:
        self.runner.on_action_completed(False, message, 0.0)

    @Slot(int, object)
    def _on_execute_scope(self, index: int, step: TestStep) -> None:
        from scope_api.base import ScopeError
        from scope_service import ARM_READY_TIMEOUT_S, OWNER_RUN

        service = self._scope_service
        if service is None:
            self._scope_fail(tr("Oszilloskop-Schritte (Scharf schalten, Auf Erfassung warten) gehen nur im "
                                "LabControl-Fenster"))
            return
        device_id, error = self.directory.resolve(step.device_kind, step.device_id)
        if device_id is None:
            self._scope_fail(error)
            return
        if step.action == scope_steps.SCOPE_ARM:
            previous = self._scope_armed.get(device_id)
            if previous is not None:
                self._scope_fail(tr("Oszilloskop ist schon scharf (Schritt {n}) – erst „Auf Erfassung warten“",
                                    n=previous["index"] + 1))
                return
            body = scope_steps.arm_body(step.params, step.device_kind)
            try:
                status = service.arm(device_id, body, owner=OWNER_RUN, wait_armed=False)
            except ScopeError as exc:
                self._scope_fail(tr("Oszilloskop: {message}", message=exc.message))
                return
            self._scope_devices.add(device_id)
            entry = {"capture_id": status["capture_id"], "device_id": device_id, "index": index,
                     "channel": body["channels"][0]["name"], "kind": step.device_kind,
                     "summary": scope_steps.summary(step.action, step.params, step.device_kind)}
            self._scope_armed[device_id] = entry
            self._scope_arming = entry
            self._scope_timer.start(round((ARM_READY_TIMEOUT_S + 5.0) * 1000))
            if status.get("state") != "arming":
                self._on_capture_state(entry["capture_id"], status["state"])
            return
        # SCOPE_WAIT
        armed = self._scope_armed.get(device_id)
        if armed is None:
            self._scope_fail(tr("Am Oszilloskop {device} ist nichts scharf geschaltet – vorher „Scharf schalten“",
                                device=self._label(device_id)))
            return
        params = scope_steps.clean_params(step.action, step.params, step.device_kind)
        self._scope_wait = {**armed, "step": step, "wait_index": index, "timeout_s": params["timeout_s"],
                            "started": time.monotonic()}
        try:
            state = service.capture_status(armed["capture_id"]).get("state")
        except ScopeError:
            state = "error"
        if state in ("done", "error", "cancelled"):
            self._finish_scope_wait()
            return
        self._scope_timer.start(max(0, round(params["timeout_s"] * 1000)))

    def _label(self, device_id: str) -> str:
        record = self.recorder.record()
        if record is not None and device_id in record.device_meta:
            return record.device_meta[device_id][1]
        return device_id

    @Slot(str, str)
    def _on_capture_state(self, capture_id: str, state: str) -> None:
        if not self._active:
            return
        arming = self._scope_arming
        if arming is not None and arming["capture_id"] == capture_id:
            self._scope_arming = None
            self._scope_timer.stop()
            if state in ("armed", "triggered", "done"):
                self.runner.step_note.emit(arming["index"], tr("scharf ({summary})", summary=arming["summary"]))
                self.runner.on_action_completed(True, "", 0.0)
            else:
                self._scope_armed.pop(arming["device_id"], None)
                self._scope_fail(tr("Oszilloskop nicht scharf geschaltet: {message}",
                                    message=self._capture_message(capture_id)))
            return
        wait = self._scope_wait
        if wait is not None and wait["capture_id"] == capture_id and state in ("done", "error", "cancelled"):
            self._finish_scope_wait()

    def _capture_message(self, capture_id: str) -> str:
        from scope_api.base import ScopeError
        try:
            status = self._scope_service.capture_status(capture_id)
        except ScopeError as exc:
            return exc.message
        return status.get("message") or status.get("state", "")

    def _on_scope_timeout(self) -> None:
        if not self._active:
            return
        arming = self._scope_arming
        if arming is not None:
            self._scope_arming = None
            self._scope_armed.pop(arming["device_id"], None)
            self._scope_service.cancel(arming["capture_id"])
            self._scope_fail(tr("Oszilloskop {device} wurde nicht scharf", device=self._label(arming["device_id"])))
            return
        wait = self._scope_wait
        if wait is None:
            return
        try:
            state = self._scope_service.capture_status(wait["capture_id"]).get("state")
        except Exception:  # noqa: BLE001 -- dann wie "kein Trigger"
            state = ""
        if state == "triggered" and not wait.get("extended"):
            # Rechtzeitig getriggert, die Daten werden noch gelesen (UNI-T: 250 kS als ASCII) -- kein FAIL
            wait["extended"] = True
            self._scope_timer.start(round(SCOPE_READ_TIMEOUT_S * 1000))
            return
        self._scope_wait = None
        self._scope_armed.pop(wait["device_id"], None)
        self._scope_service.cancel(wait["capture_id"])
        if state == "triggered":
            self._scope_fail(tr("Oszilloskop {device}: Daten nicht innerhalb von {seconds:g} s gelesen",
                                device=self._label(wait["device_id"]), seconds=SCOPE_READ_TIMEOUT_S))
            return
        self._scope_fail(tr("Kein Trigger innerhalb von {seconds:g} s ({device}: {summary})",
                            seconds=wait["timeout_s"], device=self._label(wait["device_id"]), summary=wait["summary"]))

    def _finish_scope_wait(self) -> None:
        """Die scharfe Erfassung des Warteschritts ist fertig (getriggert, Fehler oder abgebrochen)."""
        from scope_api.base import ScopeError

        wait, self._scope_wait = self._scope_wait, None
        self._scope_timer.stop()
        if wait is None:
            return
        self._scope_armed.pop(wait["device_id"], None)
        service = self._scope_service
        step = wait["step"]
        try:
            report = service.wait_capture(wait["capture_id"], 0.0)
        except ScopeError as exc:
            if exc.code == "trigger_timeout":
                message = tr("Kein Trigger, solange scharf ({device}: {summary})",
                             device=self._label(wait["device_id"]), summary=wait["summary"])
            elif exc.code == "cancelled":
                message = tr("Erfassung abgebrochen ({device})", device=self._label(wait["device_id"]))
            else:
                message = tr("Oszilloskop: {message}", message=exc.message)
            self._scope_fail(message)
            return
        params = scope_steps.clean_params(step.action, step.params, step.device_kind)
        try:   # den gewaehlten Kennwert gezielt berechnen (die Erfassung selbst wertet nur Einfaches aus)
            measured = service.measure(report.get("capture_id", wait["capture_id"]),
                                       {"measurements": [params["measurement"]],
                                        "channel": params["channel"] or wait["channel"]})
        except ScopeError as exc:
            measured = {"channels": {}}
            logger.warning("Oszilloskop-Erfassung %s: Kennwert nicht berechnet (%s)", wait["capture_id"], exc.message)
        value, channel, reason = scope_steps.measured_value(measured, params, wait["channel"], step.device_kind)
        unit = scope_steps.unit(params)
        label = scope_steps.measurement_label(params["measurement"])
        took = time.monotonic() - wait["started"]
        entry = {"index": wait["wait_index"], "arm_index": wait["index"], "device_id": wait["device_id"],
                 "kind": step.device_kind, "capture_id": report.get("capture_id", wait["capture_id"]),
                 "csv_path": report.get("csv_path", ""), "triggered": report.get("triggered"),
                 "measurement": params["measurement"], "channel": channel, "value": value, "unit": unit,
                 "summary": wait["summary"], "warnings": list(report.get("warnings") or []), "t": time.time()}
        try:
            entry["png"] = service.plot(entry["capture_id"], None, None, None, 1000, 560)
        except Exception:  # noqa: BLE001 -- ohne Bild bleibt der Bericht vollstaendig (CSV)
            logger.exception("Oszilloskop-Erfassung %s: Bild fuer den Bericht gescheitert", entry["capture_id"])
        self.recorder.on_scope_capture(wait["wait_index"], entry)
        value_text = (f"{value:g} {unit}".rstrip() if value is not None
                      else tr("nicht bestimmbar ({reason})", reason=reason))
        self.runner.step_note.emit(wait["wait_index"], tr(
            "getriggert nach {seconds:.1f} s – {measurement} {channel} = {value}",
            seconds=took, measurement=label, channel=channel, value=value_text))
        if value is None:
            if step.check_enabled or step.store_var:
                self._scope_fail(tr("Kennwert {measurement} ({channel}) nicht bestimmbar: {reason}",
                                    measurement=label, channel=channel, reason=reason))
                return
            value = 0.0
        self.runner.on_action_completed(True, "", float(value))

    # -- Ende ----------------------------------------------------------------------

    def _on_run_end(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._scope_service is not None and (self._scope_devices or self._scope_armed):
            # PI-72 a: noch scharfe Erfassung des Laufs abbrechen, Oszilloskope freigeben (trennen)
            self._scope_service.end_run(self._scope_devices | set(self._scope_armed))
        self._reset_scope_state()
        if self.xcp is not None:
            self.xcp.set_polling("run", False)
        if self._flash_pending is not None and self.flasher is not None:
            self._flash_pending = None
            self.flasher.cancel()     # Stopp/Fehler: canflash beenden, Kanal kommt wieder zurueck
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

"""Hauptfenster: Dashboard (immer sichtbar) + Reiter (Control/Testcase) + Statusleiste."""
from __future__ import annotations

import logging

from PySide6.QtCore import QMetaObject, QThread, Q_ARG, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from control_tab import ControlTab
from dashboard import DashboardWidget
from device_registry import DeviceRegistry
from device_worker import DeviceWorker
from i18n import Translator, tr
from recording import Recorder
from safety import SafetyMonitor
from settings import Settings
from settings_tab import SettingsTab
from testcase_model import TestStep, kind_label
from testcase_runner import TestRunner
from testcase_tab import TestcaseTab
from theme import Palette, ThemeManager
from timeline_tab import TimelineTab
from version import __version__

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    # An den DeviceWorker weitergereichte Testablauf-Aktion, nachdem eine leere
    # device_id (siehe testcase_model.TestStep) auf ein konkretes Geraet
    # aufgeloest wurde. Eigenes Signal statt direktem Methodenaufruf, damit die
    # Verbindung -- wie alle anderen Worker-Aufrufe -- ueber eine Queued
    # Connection korrekt in den Worker-Thread gelangt.
    _dispatch_test_action = Signal(str, str, str, float)  # device_id, kind, action, value

    # An den DeviceWorker weitergereichte Sicherheitsabschaltung (Watchdog-Trip,
    # Stop-Button, Schrittfehler, manueller Panic-Button) -- eigenes Signal aus
    # demselben Grund wie _dispatch_test_action (Queued Connection in den
    # Worker-Thread).
    _request_all_off = Signal(str)  # reason

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"LAB CONTROL v{__version__}")
        self.resize(1000, 700)

        central = QWidget()
        layout = QVBoxLayout(central)

        self._safety_banner = QWidget()
        self._safety_banner.setObjectName("safetyBanner")
        banner_layout = QHBoxLayout(self._safety_banner)
        banner_layout.setContentsMargins(8, 4, 8, 4)
        self._safety_banner_label = QLabel()
        self._safety_banner_label.setWordWrap(True)
        banner_layout.addWidget(self._safety_banner_label, 1)
        self._safety_ack_button = QPushButton()
        self._safety_ack_button.clicked.connect(self._on_safety_acknowledge)
        banner_layout.addWidget(self._safety_ack_button)
        self._safety_banner.hide()
        layout.addWidget(self._safety_banner)

        self.dashboard = DashboardWidget()
        layout.addWidget(self.dashboard)

        self.tabs = QTabWidget()
        self.control_tab = ControlTab()
        self.testcase_tab = TestcaseTab()
        self.timeline_tab = TimelineTab()
        self.settings_tab = SettingsTab()
        self.tabs.addTab(self.control_tab, "")
        self.tabs.addTab(self.testcase_tab, "")
        self.tabs.addTab(self.timeline_tab, "")
        self.tabs.addTab(self.settings_tab, "")
        layout.addWidget(self.tabs)

        self.setCentralWidget(central)

        self._status_container = QWidget()
        self._status_layout = QHBoxLayout(self._status_container)
        self._status_layout.setContentsMargins(0, 0, 0, 0)
        self._safety_status_label = QLabel()
        self._status_layout.addWidget(self._safety_status_label)
        self.statusBar().addPermanentWidget(self._status_container)
        self._status_labels: dict[str, QLabel] = {}
        self._device_labels: dict[str, str] = {}
        self._device_online: dict[str, bool] = {}
        self._online_devices: dict[str, set[str]] = {"load": set(), "psu": set()}

        self._registry = DeviceRegistry()
        self._settings = settings if settings is not None else Settings()
        self._recorder = Recorder()
        self._safety = SafetyMonitor(self._settings.safety_limits)

        self._setup_worker()
        self._wire_safety()
        self._wire_registry()
        self._wire_control_tab()
        self._wire_testcase_tab()
        self._wire_recording()
        self._wire_settings_tab()

        self.dashboard.all_off_requested.connect(lambda: self._safe_stop("manual all-off"))

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._style_safety_banner()
        self._retranslate()

    def _retranslate(self) -> None:
        self.tabs.setTabText(0, tr("Steuerung"))
        self.tabs.setTabText(1, tr("Testablauf"))
        self.tabs.setTabText(2, tr("Verlauf"))
        self.tabs.setTabText(3, tr("Einstellungen"))
        for device_id in self._status_labels:
            self._render_status_label(device_id)
        self._safety_ack_button.setText(tr("Quittieren"))
        self._render_safety_status(self._safety.current_state())

    def _setup_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = DeviceWorker(simulation_mode=self._settings.simulation_mode)
        self._worker.moveToThread(self._thread)

        self._worker.device_added.connect(self._registry.on_device_added)
        self._worker.device_removed.connect(self._registry.on_device_removed)
        self._worker.load_connected.connect(self._on_load_connected)
        self._worker.psu_connected.connect(self._on_psu_connected)
        self._worker.load_measurement.connect(self.dashboard.update_load)
        self._worker.psu_measurement.connect(self.dashboard.update_psu)
        self._worker.load_measurement.connect(self.timeline_tab.update_load)
        self._worker.psu_measurement.connect(self.timeline_tab.update_psu)
        self._worker.load_measurement.connect(self._recorder.on_load_measurement)
        self._worker.psu_measurement.connect(self._recorder.on_psu_measurement)
        self._worker.load_input_state.connect(self.control_tab.set_load_input_state)
        self._worker.psu_limits.connect(self.control_tab.set_psu_limits)
        self._worker.psu_limits.connect(self.testcase_tab.on_psu_limits)

        self._thread.started.connect(self._worker.start)
        self._thread.start()

    def _wire_safety(self) -> None:
        self._worker.load_measurement.connect(self._safety.on_load_measurement)
        self._worker.psu_measurement.connect(self._safety.on_psu_measurement)
        self._worker.device_removed.connect(self._safety.on_device_removed)
        self._safety.all_off_requested.connect(self._worker.all_outputs_off)
        self._request_all_off.connect(self._worker.all_outputs_off)
        self._worker.all_off_finished.connect(self._on_all_off_finished)
        self._settings.safety_limits_changed.connect(self._safety.set_limits)
        self._safety.tripped.connect(self._on_safety_tripped)
        self._safety.state_changed.connect(self._render_safety_status)

    def _safe_stop(self, reason: str) -> None:
        logger.warning("Safe-Stop ausgelöst: %s", reason)
        self._request_all_off.emit(reason)

    def _on_all_off_finished(self, failures: str) -> None:
        if not failures:
            return
        logger.warning("ALL OFF: fehlgeschlagene Geräte: %s", failures)
        self.statusBar().showMessage(
            tr(
                "Sicherheitsabschaltung bei folgenden Geräten fehlgeschlagen: {failures}",
                failures=failures,
            ),
            10000,
        )

    # -- Sicherheits-Watchdog: Banner + Statusanzeige --------------------------

    def _on_safety_tripped(self, device_id: str, reason: str) -> None:
        self._test_runner.stop()
        self._safety_banner_label.setText(reason)
        self._safety_banner.show()

    def _on_safety_acknowledge(self) -> None:
        self._safety.acknowledge()
        self._safety_banner.hide()

    def _render_safety_status(self, state: str) -> None:
        pal = ThemeManager.instance().palette
        texts = {"off": tr("AUS"), "armed": tr("AKTIV"), "tripped": tr("AUSGELÖST")}
        colors = {"off": pal.text_muted, "armed": pal.success, "tripped": pal.danger}
        self._safety_status_label.setText(f"{tr('Sicherheit:')} {texts.get(state, state)}")
        self._safety_status_label.setStyleSheet(
            f"color: {colors.get(state, pal.text)}; font-weight: bold;"
        )

    def _style_safety_banner(self) -> None:
        pal = ThemeManager.instance().palette
        self._safety_banner.setStyleSheet(
            f"#safetyBanner {{ background-color: {pal.danger}; border-radius: 4px; }}"
        )
        self._safety_banner_label.setStyleSheet("color: #ffffff; font-weight: bold;")

    def _wire_registry(self) -> None:
        self._registry.device_known.connect(self.dashboard.on_device_known)
        self._registry.device_known.connect(self.control_tab.on_device_known)
        self._registry.device_known.connect(self.testcase_tab.on_device_known)
        self._registry.device_known.connect(self.timeline_tab.on_device_known)
        self._registry.device_known.connect(self._recorder.on_device_known)
        self._registry.device_known.connect(self._on_device_known_status)

        self._registry.label_changed.connect(self.dashboard.on_label_changed)
        self._registry.label_changed.connect(self.control_tab.on_label_changed)
        self._registry.label_changed.connect(self.testcase_tab.on_label_changed)
        self._registry.label_changed.connect(self.timeline_tab.on_label_changed)
        self._registry.label_changed.connect(self._recorder.on_label_changed)
        self._registry.label_changed.connect(self._on_label_changed_status)

        self.dashboard.rename_requested.connect(self._registry.rename)

    def _wire_control_tab(self) -> None:
        self.control_tab.section_created.connect(self._on_control_section_created)

    def _on_control_section_created(self, kind: str, device_id: str, section) -> None:
        if kind == "load":
            section.apply_function.connect(self._worker.set_load_function)
            section.apply_setpoint.connect(self._apply_load_setpoint)
            section.set_input.connect(self._worker.set_load_input)
        else:
            section.set_voltage.connect(self._worker.set_psu_voltage)
            section.set_current.connect(self._worker.set_psu_current)
            section.set_ovp.connect(self._worker.set_psu_ovp)
            section.set_ocp.connect(self._worker.set_psu_ocp)
            section.recall_memory.connect(self._worker.recall_psu_memory)

    def _wire_recording(self) -> None:
        # Aufzeichnung-Steuerung sitzt im Verlauf-Tab (siehe timeline_tab.py-
        # Docstring), Recorder haelt die Daten -- hier nur die Verkabelung.
        self.timeline_tab.start_requested.connect(self._recorder.start)
        self.timeline_tab.stop_requested.connect(self._recorder.stop)
        self.timeline_tab.clear_requested.connect(self._recorder.clear)
        self._recorder.recording_changed.connect(self.timeline_tab.on_recording_changed)
        self._recorder.stats_changed.connect(self.timeline_tab.on_stats_changed)
        self.timeline_tab.export_csv_to.connect(self._on_export_csv)
        self.timeline_tab.export_mf4_to.connect(self._on_export_mf4)

    def _on_export_csv(self, path) -> None:
        try:
            self._recorder.export_csv(path)
        except OSError as exc:
            self.timeline_tab.show_export_error(str(exc))
            return
        self.timeline_tab.show_export_success(path)

    def _on_export_mf4(self, path) -> None:
        try:
            self._recorder.export_mf4(path)
        except ImportError:
            self.timeline_tab.show_export_error(
                tr("MF4-Export benötigt das Paket 'asammdf' (siehe requirements.txt).")
            )
            return
        except (OSError, ValueError) as exc:
            self.timeline_tab.show_export_error(str(exc))
            return
        self.timeline_tab.show_export_success(path)

    def _wire_settings_tab(self) -> None:
        self.settings_tab.set_simulation_mode(self._settings.simulation_mode)
        self.settings_tab.simulation_mode_toggled.connect(self._settings.set_simulation_mode)
        self._settings.simulation_mode_changed.connect(self._worker.set_simulation_mode)

        self.settings_tab.set_dark_mode(self._settings.dark_mode)
        self.settings_tab.dark_mode_toggled.connect(self._settings.set_dark_mode)
        self._settings.dark_mode_changed.connect(ThemeManager.instance().apply)

        self.settings_tab.set_language(self._settings.language)
        self.settings_tab.language_selected.connect(self._settings.set_language)
        self._settings.language_changed.connect(Translator.instance().set_language)

        self.settings_tab.set_safety_limits(self._settings.safety_limits)
        self.settings_tab.safety_limit_changed.connect(self._settings.set_safety_limit)

    def _wire_testcase_tab(self) -> None:
        self._test_runner = TestRunner()
        self._test_runner.execute_action.connect(self._on_test_execute_action)
        self._dispatch_test_action.connect(self._worker.execute_action)
        self._worker.action_completed.connect(self._test_runner.on_action_completed)
        self._worker.load_measurement.connect(self._test_runner.on_load_measurement)
        self._worker.psu_measurement.connect(self._test_runner.on_psu_measurement)
        self._worker.device_removed.connect(self._test_runner.on_device_removed)
        self._test_runner.step_started.connect(self.testcase_tab.on_step_started)
        self._test_runner.step_result.connect(self.testcase_tab.on_step_result)
        self._test_runner.step_failed.connect(self.testcase_tab.on_step_failed)
        self._test_runner.run_finished.connect(self.testcase_tab.on_run_finished)
        self._test_runner.run_stopped.connect(self.testcase_tab.on_run_stopped)
        self._test_runner.iteration_changed.connect(self.testcase_tab.on_iteration_changed)

        # Watchdog-Ueberwachung endet mit dem Testlauf (egal ob normal beendet,
        # gestoppt oder fehlgeschlagen) -- sonst wuerde der Stale-Timer nach
        # einem bewussten Stop noch nachtriggern.
        self._test_runner.run_finished.connect(self._safety.end_run_supervision)
        self._test_runner.run_stopped.connect(self._safety.end_run_supervision)
        self._test_runner.step_failed.connect(lambda *_args: self._safety.end_run_supervision())

        # Unbeaufsichtigte Laeufe: ein Schrittfehler (Geraetefehler, veraltete
        # Messung, verletzte Pass/Fail-Pruefung mit "Bei Verletzung abbrechen")
        # schaltet sofort alle Ausgaenge ab, statt auf das manuelle Quittieren
        # per Stop-Button zu warten (siehe testcase_tab.on_step_failed).
        self._test_runner.step_failed.connect(
            lambda _index, message: self._safe_stop(f"step failed: {message}")
        )

        self.testcase_tab.run_requested.connect(self._on_run_requested)
        self.testcase_tab.stop_requested.connect(self._test_runner.stop)
        self.testcase_tab.stop_requested.connect(lambda: self._safe_stop("stop button"))

    def _on_run_requested(self) -> None:
        steps = self.testcase_tab.steps()
        if not any(step.enabled and step.step_type == "action" for step in steps):
            return
        if self._safety.is_tripped():
            self.statusBar().showMessage(
                tr("Testlauf gesperrt: Sicherheitsabschaltung zuerst quittieren"), 5000
            )
            return
        self.testcase_tab.on_run_started()
        self._safety.begin_run_supervision(self._resolve_step_device_ids(steps))
        self._test_runner.start(steps)

    def _resolve_step_device_ids(self, steps: list[TestStep]) -> set[str]:
        """Ermittelt die an einem Testlauf beteiligten Geraete-IDs fuer die
        Watchdog-Verbindungsueberwachung (siehe safety.begin_run_supervision).

        Nicht aufloesbare Ziele (z.B. "automatisch" ohne aktuell verbundenes
        Geraet dieser Art) werden uebersprungen -- der Runner scheitert an
        solchen Schritten ohnehin sofort (step_failed), was ueber die
        step_failed-Verdrahtung oben ebenfalls zum Safe-Stop fuehrt.
        """
        device_ids: set[str] = set()
        for step in steps:
            if not step.enabled:
                continue
            if step.step_type == "action":
                resolved, _ = self._resolve_device_id(step.device_kind, step.device_id)
                if resolved:
                    device_ids.add(resolved)
            elif step.step_type in ("while", "if") and step.cond_source == "measurement":
                resolved, _ = self._resolve_device_id(step.cond_device_kind, step.cond_device_id)
                if resolved:
                    device_ids.add(resolved)
        return device_ids

    def _on_test_execute_action(self, device_id: str, kind: str, action: str, value: float) -> None:
        resolved_id, error = self._resolve_device_id(kind, device_id)
        if resolved_id is None:
            self._test_runner.on_action_completed(False, error)
            return
        self._dispatch_test_action.emit(resolved_id, kind, action, value)

    def _resolve_device_id(self, kind: str, device_id: str) -> tuple[str | None, str]:
        if device_id:
            return device_id, ""
        candidates = self._online_devices.get(kind, set())
        if len(candidates) == 1:
            return next(iter(candidates)), ""
        label = kind_label(kind)
        if not candidates:
            return None, tr("Kein Gerät des Typs '{label}' verbunden", label=label)
        return None, tr(
            "Mehrere Geräte des Typs '{label}' verbunden -- bitte Zielgerät in der Testcase-Zeile auswählen",
            label=label,
        )

    def _apply_load_setpoint(self, device_id: str, mode_code: str, value: float) -> None:
        setters = {
            "CURR": self._worker.set_load_current,
            "VOLT": self._worker.set_load_voltage,
            "RES": self._worker.set_load_resistance,
            "POW": self._worker.set_load_power,
        }
        setter = setters.get(mode_code)
        if setter is not None:
            setter(device_id, value)

    @Slot(str, bool)
    def _on_load_connected(self, device_id: str, online: bool) -> None:
        self._set_online("load", device_id, online)
        self.dashboard.set_load_online(device_id, online)
        self.control_tab.set_load_online(device_id, online)

    @Slot(str, bool)
    def _on_psu_connected(self, device_id: str, online: bool) -> None:
        self._set_online("psu", device_id, online)
        self.dashboard.set_psu_online(device_id, online)
        self.control_tab.set_psu_online(device_id, online)

    def _set_online(self, kind: str, device_id: str, online: bool) -> None:
        if online:
            self._online_devices[kind].add(device_id)
        else:
            self._online_devices[kind].discard(device_id)
        self._device_online[device_id] = online
        self._render_status_label(device_id)

    # -- Statusleiste: ein Label je bekanntem Geraet --------------------------

    def _on_device_known_status(self, kind: str, device_id: str, label: str) -> None:
        if device_id not in self._status_labels:
            status_label = QLabel()
            self._status_labels[device_id] = status_label
            self._status_layout.addWidget(status_label)
            self._device_online.setdefault(device_id, False)
        self._device_labels[device_id] = label
        self._render_status_label(device_id)

    def _on_label_changed_status(self, kind: str, device_id: str, label: str) -> None:
        self._device_labels[device_id] = label
        self._render_status_label(device_id)

    def _render_status_label(self, device_id: str) -> None:
        status_label = self._status_labels.get(device_id)
        if status_label is None:
            return
        label = self._device_labels.get(device_id, device_id)
        online = self._device_online.get(device_id, False)
        status_label.setText(f"{label}: {tr('verbunden') if online else tr('getrennt')}")
        pal = ThemeManager.instance().palette
        color = pal.success if online else pal.danger
        status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _on_theme_changed(self, _palette: Palette) -> None:
        for device_id in self._status_labels:
            self._render_status_label(device_id)
        self._render_safety_status(self._safety.current_state())
        self._style_safety_banner()

    def closeEvent(self, event) -> None:
        # Synchron (BlockingQueuedConnection) statt per _request_all_off, damit
        # der Kill garantiert VOR thread.quit()/wait() abgeschlossen ist --
        # sonst koennte die Anwendung schliessen, bevor der Worker die
        # Ausgaenge tatsaechlich abgeschaltet hat.
        QMetaObject.invokeMethod(
            self._worker,
            "all_outputs_off",
            Qt.ConnectionType.BlockingQueuedConnection,
            Q_ARG(str, "window close"),
        )
        self._thread.quit()
        self._thread.wait(2000)
        super().closeEvent(event)

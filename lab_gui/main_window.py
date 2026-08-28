"""Hauptfenster: Dashboard (immer sichtbar) + Reiter (Control/Testcase) + Statusleiste."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from control_tab import ControlTab
from dashboard import DashboardWidget
from device_registry import DeviceRegistry
from device_worker import DeviceWorker
from settings import Settings
from settings_tab import SettingsTab
from testcase_model import DEVICE_KIND_LABELS
from testcase_runner import TestRunner
from testcase_tab import TestcaseTab
from theme import Palette, ThemeManager
from version import __version__


class MainWindow(QMainWindow):
    # An den DeviceWorker weitergereichte Testablauf-Aktion, nachdem eine leere
    # device_id (siehe testcase_model.TestStep) auf ein konkretes Geraet
    # aufgeloest wurde. Eigenes Signal statt direktem Methodenaufruf, damit die
    # Verbindung -- wie alle anderen Worker-Aufrufe -- ueber eine Queued
    # Connection korrekt in den Worker-Thread gelangt.
    _dispatch_test_action = Signal(str, str, str, float)  # device_id, kind, action, value

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"Labor-Steuerung v{__version__}")
        self.resize(1000, 700)

        central = QWidget()
        layout = QVBoxLayout(central)

        self.dashboard = DashboardWidget()
        layout.addWidget(self.dashboard)

        self.tabs = QTabWidget()
        self.control_tab = ControlTab()
        self.testcase_tab = TestcaseTab()
        self.settings_tab = SettingsTab()
        self.tabs.addTab(self.control_tab, "Control")
        self.tabs.addTab(self.testcase_tab, "Testcase")
        self.tabs.addTab(self.settings_tab, "Settings")
        layout.addWidget(self.tabs)

        self.setCentralWidget(central)

        self._status_container = QWidget()
        self._status_layout = QHBoxLayout(self._status_container)
        self._status_layout.setContentsMargins(0, 0, 0, 0)
        self.statusBar().addPermanentWidget(self._status_container)
        self._status_labels: dict[str, QLabel] = {}
        self._device_labels: dict[str, str] = {}
        self._device_online: dict[str, bool] = {}
        self._online_devices: dict[str, set[str]] = {"load": set(), "psu": set()}

        self._registry = DeviceRegistry()
        self._settings = settings if settings is not None else Settings()

        self._setup_worker()
        self._wire_registry()
        self._wire_control_tab()
        self._wire_testcase_tab()
        self._wire_settings_tab()

        ThemeManager.instance().changed.connect(self._on_theme_changed)

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
        self._worker.load_input_state.connect(self.control_tab.set_load_input_state)

        self._thread.started.connect(self._worker.start)
        self._thread.start()

    def _wire_registry(self) -> None:
        self._registry.device_known.connect(self.dashboard.on_device_known)
        self._registry.device_known.connect(self.control_tab.on_device_known)
        self._registry.device_known.connect(self.testcase_tab.on_device_known)
        self._registry.device_known.connect(self._on_device_known_status)

        self._registry.label_changed.connect(self.dashboard.on_label_changed)
        self._registry.label_changed.connect(self.control_tab.on_label_changed)
        self._registry.label_changed.connect(self.testcase_tab.on_label_changed)
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

    def _wire_settings_tab(self) -> None:
        self.settings_tab.set_simulation_mode(self._settings.simulation_mode)
        self.settings_tab.simulation_mode_toggled.connect(self._settings.set_simulation_mode)
        self._settings.simulation_mode_changed.connect(self._worker.set_simulation_mode)

        self.settings_tab.set_dark_mode(self._settings.dark_mode)
        self.settings_tab.dark_mode_toggled.connect(self._settings.set_dark_mode)
        self._settings.dark_mode_changed.connect(ThemeManager.instance().apply)

    def _wire_testcase_tab(self) -> None:
        self._test_runner = TestRunner()
        self._test_runner.execute_action.connect(self._on_test_execute_action)
        self._dispatch_test_action.connect(self._worker.execute_action)
        self._worker.action_completed.connect(self._test_runner.on_action_completed)
        self._test_runner.step_started.connect(self.testcase_tab.on_step_started)
        self._test_runner.step_failed.connect(self.testcase_tab.on_step_failed)
        self._test_runner.run_finished.connect(self.testcase_tab.on_run_finished)
        self._test_runner.run_stopped.connect(self.testcase_tab.on_run_stopped)

        self.testcase_tab.run_requested.connect(self._on_run_requested)
        self.testcase_tab.stop_requested.connect(self._test_runner.stop)

    def _on_run_requested(self) -> None:
        steps = self.testcase_tab.steps()
        if not any(step.enabled for step in steps):
            return
        self.testcase_tab.on_run_started()
        self._test_runner.start(steps)

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
        label = DEVICE_KIND_LABELS.get(kind, kind)
        if not candidates:
            return None, f"Kein Gerät des Typs '{label}' verbunden"
        return None, f"Mehrere Geräte des Typs '{label}' verbunden -- bitte Zielgerät in der Testcase-Zeile auswählen"

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
        status_label.setText(f"{label}: {'verbunden' if online else 'getrennt'}")
        pal = ThemeManager.instance().palette
        color = pal.success if online else pal.danger
        status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _on_theme_changed(self, _palette: Palette) -> None:
        for device_id in self._status_labels:
            self._render_status_label(device_id)

    def closeEvent(self, event) -> None:
        self._thread.quit()
        self._thread.wait(2000)
        super().closeEvent(event)

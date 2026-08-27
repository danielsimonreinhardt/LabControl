"""Hauptfenster: Dashboard (immer sichtbar) + Reiter (Control/Testcase) + Statusleiste."""
from __future__ import annotations

from PySide6.QtCore import QThread, Slot
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from control_tab import ControlTab
from dashboard import DashboardWidget
from device_worker import DeviceWorker
from testcase_runner import TestRunner
from testcase_tab import TestcaseTab

CONNECTED_STYLE = "color: green; font-weight: bold;"
DISCONNECTED_STYLE = "color: red; font-weight: bold;"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Labor-Steuerung")
        self.resize(1000, 700)

        central = QWidget()
        layout = QVBoxLayout(central)

        self.dashboard = DashboardWidget()
        layout.addWidget(self.dashboard)

        self.tabs = QTabWidget()
        self.control_tab = ControlTab()
        self.testcase_tab = TestcaseTab()
        self.tabs.addTab(self.control_tab, "Control")
        self.tabs.addTab(self.testcase_tab, "Testcase")
        layout.addWidget(self.tabs)

        self.setCentralWidget(central)

        self._load_status_label = QLabel("Last: getrennt")
        self._load_status_label.setStyleSheet(DISCONNECTED_STYLE)
        self._psu_status_label = QLabel("Netzteil: getrennt")
        self._psu_status_label.setStyleSheet(DISCONNECTED_STYLE)
        self.statusBar().addPermanentWidget(self._load_status_label)
        self.statusBar().addPermanentWidget(self._psu_status_label)

        self._setup_worker()
        self._wire_control_tab()
        self._wire_testcase_tab()

    def _setup_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = DeviceWorker()
        self._worker.moveToThread(self._thread)

        self._worker.load_connected.connect(self._on_load_connected)
        self._worker.psu_connected.connect(self._on_psu_connected)
        self._worker.load_measurement.connect(self.dashboard.update_load)
        self._worker.psu_measurement.connect(self.dashboard.update_psu)

        self._thread.started.connect(self._worker.start)
        self._thread.start()

    def _wire_control_tab(self) -> None:
        load = self.control_tab.load_group
        load.apply_function.connect(self._worker.set_load_function)
        load.apply_setpoint.connect(self._apply_load_setpoint)
        load.set_input.connect(self._worker.set_load_input)

        psu = self.control_tab.psu_group
        psu.set_voltage.connect(self._worker.set_psu_voltage)
        psu.set_current.connect(self._worker.set_psu_current)
        psu.set_ovp.connect(self._worker.set_psu_ovp)
        psu.set_ocp.connect(self._worker.set_psu_ocp)
        psu.recall_memory.connect(self._worker.recall_psu_memory)

    def _wire_testcase_tab(self) -> None:
        self._test_runner = TestRunner()
        self._test_runner.execute_action.connect(self._worker.execute_action)
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

    def _apply_load_setpoint(self, mode_code: str, value: float) -> None:
        setters = {
            "CURR": self._worker.set_load_current,
            "VOLT": self._worker.set_load_voltage,
            "RES": self._worker.set_load_resistance,
            "POW": self._worker.set_load_power,
        }
        setter = setters.get(mode_code)
        if setter is not None:
            setter(value)

    @Slot(bool)
    def _on_load_connected(self, online: bool) -> None:
        self._load_status_label.setText("Last: verbunden" if online else "Last: getrennt")
        self._load_status_label.setStyleSheet(CONNECTED_STYLE if online else DISCONNECTED_STYLE)
        self.dashboard.set_load_online(online)

    @Slot(bool)
    def _on_psu_connected(self, online: bool) -> None:
        self._psu_status_label.setText("Netzteil: verbunden" if online else "Netzteil: getrennt")
        self._psu_status_label.setStyleSheet(CONNECTED_STYLE if online else DISCONNECTED_STYLE)
        self.dashboard.set_psu_online(online)

    def closeEvent(self, event) -> None:
        self._thread.quit()
        self._thread.wait(2000)
        super().closeEvent(event)

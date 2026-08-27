"""Control-Tab: Eingabemasken fuer die wichtigsten Funktionen von Last und Netzteil."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# Anzeigename -> SCPI-Funktionscode (siehe korad_kel102/driver.py: FUNCTIONS)
LOAD_MODES = {
    "Konstantstrom (CC)": "CURR",
    "Konstantspannung (CV)": "VOLT",
    "Konstantwiderstand (CR)": "RES",
    "Konstantleistung (CW)": "POW",
    "Kurzschluss (SHORT)": "SHORT",
}

LOAD_MODE_UNITS = {
    "CURR": ("A", 0, 40),
    "VOLT": ("V", 0, 150),
    "RES": ("Ohm", 0, 7500),
    "POW": ("W", 0, 300),
    "SHORT": ("", 0, 0),
}


class LoadControlGroup(QGroupBox):
    apply_function = Signal(str)       # SCPI mode code
    apply_setpoint = Signal(str, float)  # SCPI mode code, value
    set_input = Signal(bool)

    def __init__(self) -> None:
        super().__init__("Elektronische Last (KEL102)")
        layout = QFormLayout(self)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(LOAD_MODES.keys())
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        layout.addRow("Modus:", self._mode_combo)

        self._setpoint_spin = QDoubleSpinBox()
        self._setpoint_spin.setDecimals(3)
        self._setpoint_spin.setMaximumWidth(150)
        layout.addRow("Sollwert:", self._setpoint_spin)
        self._on_mode_changed(self._mode_combo.currentText())

        apply_button = QPushButton("Übernehmen")
        apply_button.clicked.connect(self._on_apply)
        layout.addRow(apply_button)

        input_layout = QHBoxLayout()
        on_button = QPushButton("EIN")
        off_button = QPushButton("AUS")
        on_button.clicked.connect(lambda: self.set_input.emit(True))
        off_button.clicked.connect(lambda: self.set_input.emit(False))
        input_layout.addWidget(on_button)
        input_layout.addWidget(off_button)
        input_layout.addStretch()
        layout.addRow("Ausgang:", input_layout)

    def _on_mode_changed(self, label: str) -> None:
        code = LOAD_MODES[label]
        unit, lo, hi = LOAD_MODE_UNITS[code]
        self._setpoint_spin.setSuffix(f" {unit}" if unit else "")
        self._setpoint_spin.setRange(lo, hi)
        self._setpoint_spin.setEnabled(code != "SHORT")

    def _on_apply(self) -> None:
        code = LOAD_MODES[self._mode_combo.currentText()]
        self.apply_function.emit(code)
        if code != "SHORT":
            self.apply_setpoint.emit(code, self._setpoint_spin.value())


class PsuControlGroup(QGroupBox):
    set_voltage = Signal(float)
    set_current = Signal(float)
    set_ovp = Signal(float)
    set_ocp = Signal(float)
    recall_memory = Signal(int)

    def __init__(self) -> None:
        super().__init__("Labornetzteil (HCS-34xx)")
        layout = QFormLayout(self)

        self._voltage_spin = QDoubleSpinBox()
        self._voltage_spin.setDecimals(1)
        self._voltage_spin.setRange(1, 60)  # Geraet nimmt Werte unter 1V nicht an
        self._voltage_spin.setSuffix(" V")
        self._voltage_spin.setMaximumWidth(120)
        voltage_button = QPushButton("Setzen")
        voltage_button.clicked.connect(lambda: self.set_voltage.emit(self._voltage_spin.value()))
        layout.addRow("Spannung:", self._row(self._voltage_spin, voltage_button))

        self._current_spin = QDoubleSpinBox()
        self._current_spin.setDecimals(1)
        self._current_spin.setRange(0, 10)
        self._current_spin.setSuffix(" A")
        self._current_spin.setMaximumWidth(120)
        current_button = QPushButton("Setzen")
        current_button.clicked.connect(lambda: self.set_current.emit(self._current_spin.value()))
        layout.addRow("Strom:", self._row(self._current_spin, current_button))

        output_layout = QHBoxLayout()
        output_on_button = QPushButton("EIN")
        output_off_button = QPushButton("AUS")
        output_on_button.clicked.connect(self._on_output_on)
        output_off_button.clicked.connect(self._on_output_off)
        output_layout.addWidget(output_on_button)
        output_layout.addWidget(output_off_button)
        output_layout.addStretch()
        layout.addRow("Ausgang:", output_layout)

        self._ovp_spin = QDoubleSpinBox()
        self._ovp_spin.setDecimals(1)
        self._ovp_spin.setRange(1, 65)  # Geraet nimmt Werte unter 1V nicht an
        self._ovp_spin.setSuffix(" V")
        self._ovp_spin.setMaximumWidth(120)
        ovp_button = QPushButton("Setzen")
        ovp_button.clicked.connect(lambda: self.set_ovp.emit(self._ovp_spin.value()))
        layout.addRow("OVP:", self._row(self._ovp_spin, ovp_button))

        self._ocp_spin = QDoubleSpinBox()
        self._ocp_spin.setDecimals(1)
        self._ocp_spin.setRange(0, 11)
        self._ocp_spin.setSuffix(" A")
        self._ocp_spin.setMaximumWidth(120)
        ocp_button = QPushButton("Setzen")
        ocp_button.clicked.connect(lambda: self.set_ocp.emit(self._ocp_spin.value()))
        layout.addRow("OCP:", self._row(self._ocp_spin, ocp_button))

        """preset_layout = QHBoxLayout()
        for index, name in enumerate(["P1", "P2", "P3"]):
            button = QPushButton(name)
            button.clicked.connect(lambda checked=False, i=index: self.recall_memory.emit(i))
            preset_layout.addWidget(button)
        layout.addRow("Presets:", preset_layout)"""

    def _on_output_on(self) -> None:
        self.set_voltage.emit(self._voltage_spin.value())
        self.set_current.emit(max(self._current_spin.value(), 0.1))

    def _on_output_off(self) -> None:
        self.set_current.emit(0.0)

    @staticmethod
    def _row(*widgets: QWidget) -> QWidget:
        container = QWidget()
        row_layout = QHBoxLayout(container)
        row_layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            row_layout.addWidget(widget)
        row_layout.addStretch()
        return container


class ControlTab(QWidget):
    """Scrollbar, damit auf kleinen/hochskalierten Bildschirmen nichts unerreichbar wird."""

    def __init__(self) -> None:
        super().__init__()
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        self.load_group = LoadControlGroup()
        self.psu_group = PsuControlGroup()
        content_layout.addWidget(self.load_group)
        content_layout.addWidget(self.psu_group)
        content_layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area)

"""Settings-Reiter: Simulationsmodus fuer Debugging ohne Hardware, Dark Mode."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from theme import current as current_palette


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        self._sim_checkbox = QCheckBox("Simulationsmodus (simuliertes Netzteil statt Hardware)")
        self._sim_checkbox.toggled.connect(self.simulation_mode_toggled)
        layout.addWidget(self._sim_checkbox)

        hint = QLabel(
            "Im Simulationsmodus steht ein virtuelles Labornetzteil im Dashboard/Control-Tab\n"
            "zur Verfuegung, um die GUI ohne angeschlossene Hardware zu testen."
        )
        hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(hint)

        self._dark_checkbox = QCheckBox("Dark Mode (Amber Industrial statt Modern Light)")
        self._dark_checkbox.toggled.connect(self.dark_mode_toggled)
        layout.addWidget(self._dark_checkbox)

        layout.addStretch()

    def set_simulation_mode(self, enabled: bool) -> None:
        self._sim_checkbox.blockSignals(True)
        self._sim_checkbox.setChecked(enabled)
        self._sim_checkbox.blockSignals(False)

    def set_dark_mode(self, enabled: bool) -> None:
        self._dark_checkbox.blockSignals(True)
        self._dark_checkbox.setChecked(enabled)
        self._dark_checkbox.blockSignals(False)

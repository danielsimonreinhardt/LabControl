"""Settings-Reiter: Simulationsmodus fuer Debugging ohne Hardware, Dark Mode, Sprache."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from i18n import AVAILABLE_LANGUAGES, Translator, tr
from theme import current as current_palette


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    language_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        self._sim_checkbox = QCheckBox()
        self._sim_checkbox.toggled.connect(self.simulation_mode_toggled)
        layout.addWidget(self._sim_checkbox)

        self._hint = QLabel()
        self._hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._hint)

        self._dark_checkbox = QCheckBox()
        self._dark_checkbox.toggled.connect(self.dark_mode_toggled)
        layout.addWidget(self._dark_checkbox)

        language_row = QHBoxLayout()
        self._language_label = QLabel()
        language_row.addWidget(self._language_label)
        self._language_combo = QComboBox()
        for code, native_name in AVAILABLE_LANGUAGES.items():
            self._language_combo.addItem(native_name, code)
        self._language_combo.currentIndexChanged.connect(
            lambda index: self.language_selected.emit(self._language_combo.itemData(index))
        )
        language_row.addWidget(self._language_combo)
        language_row.addStretch()
        layout.addLayout(language_row)

        layout.addStretch()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._sim_checkbox.setText(tr("Simulationsmodus (simulierte Geräte statt Hardware)"))
        self._hint.setText(
            tr(
                "Im Simulationsmodus stehen ein virtuelles Labornetzteil und eine virtuelle\n"
                "elektronische Last im Dashboard/Control-Tab zur Verfuegung, um die GUI ohne\n"
                "angeschlossene Hardware zu testen."
            )
        )
        self._dark_checkbox.setText(tr("Dark Mode (Amber Industrial statt Modern Light)"))
        self._language_label.setText(tr("Sprache:"))

    def set_simulation_mode(self, enabled: bool) -> None:
        self._sim_checkbox.blockSignals(True)
        self._sim_checkbox.setChecked(enabled)
        self._sim_checkbox.blockSignals(False)

    def set_dark_mode(self, enabled: bool) -> None:
        self._dark_checkbox.blockSignals(True)
        self._dark_checkbox.setChecked(enabled)
        self._dark_checkbox.blockSignals(False)

    def set_language(self, language: str) -> None:
        index = self._language_combo.findData(language)
        if index < 0:
            return
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(index)
        self._language_combo.blockSignals(False)

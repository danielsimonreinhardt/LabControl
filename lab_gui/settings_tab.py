"""Settings-Reiter: Simulationsmodus fuer Debugging ohne Hardware, Dark Mode,
Sprache, globale Sicherheits-Grenzwerte (Watchdog, siehe safety.py)."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from i18n import AVAILABLE_LANGUAGES, Translator, tr
from safety import SAFETY_LIMIT_FIELDS
from testcase_model import DEVICE_KIND_LABELS
from theme import current as current_palette

# field -> deutscher Basis-Anzeigename (Uebersetzungsschluessel), analog zu
# testcase_model.COND_FIELD_LABELS.
_FIELD_LABELS = {
    "max_voltage": "max. Spannung",
    "max_current": "max. Strom",
    "max_power": "max. Leistung",
}


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    language_selected = Signal(str)
    safety_limit_changed = Signal(str, str, bool, float)  # kind, field, enabled, value

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

        self._safety_group = QGroupBox()
        safety_layout = QFormLayout(self._safety_group)
        self._safety_hint = QLabel()
        self._safety_hint.setWordWrap(True)
        self._safety_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        safety_layout.addRow(self._safety_hint)

        # (kind, field) -> (Checkbox, Spinbox, Zeilen-Label) fuer
        # set_safety_limits()/_on_safety_field_changed().
        self._safety_widgets: dict[tuple[str, str], tuple[QCheckBox, QDoubleSpinBox]] = {}
        self._safety_row_labels: dict[tuple[str, str], QLabel] = {}
        for kind, entries in SAFETY_LIMIT_FIELDS.items():
            for field, unit, lo, hi, _default in entries:
                checkbox = QCheckBox()
                spin = QDoubleSpinBox()
                spin.setRange(lo, hi)
                spin.setDecimals(2)
                spin.setSuffix(f" {unit}" if unit else "")
                spin.setEnabled(False)
                checkbox.toggled.connect(spin.setEnabled)
                checkbox.toggled.connect(
                    lambda enabled, k=kind, f=field: self._on_safety_field_changed(k, f)
                )
                spin.valueChanged.connect(lambda _value, k=kind, f=field: self._on_safety_field_changed(k, f))
                row = QHBoxLayout()
                row.addWidget(checkbox)
                row.addWidget(spin)
                row_label = QLabel()
                safety_layout.addRow(row_label, row)
                self._safety_widgets[(kind, field)] = (checkbox, spin)
                self._safety_row_labels[(kind, field)] = row_label
        layout.addWidget(self._safety_group)

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
        self._safety_group.setTitle(tr("Globale Grenzwerte (Sicherheitsabschaltung)"))
        self._safety_hint.setText(
            tr(
                "Bei Überschreitung werden alle Ausgänge sofort abgeschaltet "
                "(Netzteil: Strom auf 0 A)."
            )
        )
        for (kind, field), row_label in self._safety_row_labels.items():
            kind_text = tr(DEVICE_KIND_LABELS.get(kind, kind))
            field_text = tr(_FIELD_LABELS.get(field, field))
            row_label.setText(f"{kind_text}: {field_text}")

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

    def set_safety_limits(self, limits: dict) -> None:
        for (kind, field), (checkbox, spin) in self._safety_widgets.items():
            entry = limits.get(kind, {}).get(field, {"enabled": False, "value": spin.value()})
            checkbox.blockSignals(True)
            spin.blockSignals(True)
            checkbox.setChecked(entry["enabled"])
            spin.setValue(entry["value"])
            spin.setEnabled(entry["enabled"])
            checkbox.blockSignals(False)
            spin.blockSignals(False)

    def _on_safety_field_changed(self, kind: str, field: str) -> None:
        checkbox, spin = self._safety_widgets[(kind, field)]
        self.safety_limit_changed.emit(kind, field, checkbox.isChecked(), spin.value())

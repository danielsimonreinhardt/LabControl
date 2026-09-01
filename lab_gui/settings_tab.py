"""Settings-Reiter: Simulationsmodus fuer Debugging ohne Hardware, Dark Mode,
Sprache, geraete-individuelle Sicherheits-Grenzwerte (Watchdog, siehe
safety.py). Jedes verbundene/bekannte Geraet bekommt eine eigene
Grenzwert-Sektion (analog zu control_tab.py: eine Sektion pro Geraete-ID),
statt einer gemeinsamen Einstellung je Geraeteart."""
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
from theme import current as current_palette

# field -> deutscher Basis-Anzeigename (Uebersetzungsschluessel), analog zu
# testcase_model.COND_FIELD_LABELS.
_FIELD_LABELS = {
    "max_voltage": "max. Spannung",
    "max_current": "max. Strom",
    "max_power": "max. Leistung",
}


class _DeviceSafetyGroup(QGroupBox):
    """Grenzwert-Sektion fuer EIN Geraet (siehe SettingsTab.on_device_known)."""

    limit_changed = Signal(str, bool, float)  # field, enabled, value

    def __init__(self, kind: str, label: str) -> None:
        super().__init__()
        self._kind = kind
        self.setTitle(label)
        form = QFormLayout(self)

        # field -> (Checkbox, Spinbox, Zeilen-Label) fuer
        # set_limits()/_on_field_changed().
        self._widgets: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}
        self._row_labels: dict[str, QLabel] = {}
        for field, unit, lo, hi, _default in SAFETY_LIMIT_FIELDS.get(kind, []):
            checkbox = QCheckBox()
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(2)
            spin.setSuffix(f" {unit}" if unit else "")
            spin.setEnabled(False)
            checkbox.toggled.connect(spin.setEnabled)
            checkbox.toggled.connect(lambda _enabled, f=field: self._on_field_changed(f))
            spin.valueChanged.connect(lambda _value, f=field: self._on_field_changed(f))
            row = QHBoxLayout()
            row.addWidget(checkbox)
            row.addWidget(spin)
            row_label = QLabel()
            form.addRow(row_label, row)
            self._widgets[field] = (checkbox, spin)
            self._row_labels[field] = row_label

        self.retranslate()

    def retranslate(self) -> None:
        for field, row_label in self._row_labels.items():
            row_label.setText(tr(_FIELD_LABELS.get(field, field)))

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_limits(self, limits: dict) -> None:
        for field, (checkbox, spin) in self._widgets.items():
            entry = limits.get(field, {"enabled": False, "value": spin.value()})
            checkbox.blockSignals(True)
            spin.blockSignals(True)
            checkbox.setChecked(entry["enabled"])
            spin.setValue(entry["value"])
            spin.setEnabled(entry["enabled"])
            checkbox.blockSignals(False)
            spin.blockSignals(False)

    def _on_field_changed(self, field: str) -> None:
        checkbox, spin = self._widgets[field]
        self.limit_changed.emit(field, checkbox.isChecked(), spin.value())


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    language_selected = Signal(str)
    safety_limit_changed = Signal(str, str, bool, float)  # device_id, field, enabled, value
    notifications_toggled = Signal(bool)

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

        self._notify_checkbox = QCheckBox()
        self._notify_checkbox.toggled.connect(self.notifications_toggled)
        layout.addWidget(self._notify_checkbox)

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

        self._safety_hint = QLabel()
        self._safety_hint.setWordWrap(True)
        self._safety_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._safety_hint)

        # Ein eigenes Layout fuer die dynamisch je Geraet erzeugten
        # _DeviceSafetyGroup-Sektionen (siehe on_device_known), damit sie sich
        # gemeinsam vor dem abschliessenden addStretch() einreihen.
        self._safety_sections_layout = QVBoxLayout()
        layout.addLayout(self._safety_sections_layout)
        self._safety_sections: dict[str, _DeviceSafetyGroup] = {}

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
        self._notify_checkbox.setText(tr("Desktop-Benachrichtigung bei Lauf-Ende/Fehler"))
        self._language_label.setText(tr("Sprache:"))
        self._safety_hint.setText(
            tr(
                "Grenzwerte (Sicherheitsabschaltung) je Gerät -- bei Überschreitung werden "
                "alle Ausgänge sofort abgeschaltet (Netzteil: Strom auf 0 A)."
            )
        )
        for section in self._safety_sections.values():
            section.retranslate()

    def set_simulation_mode(self, enabled: bool) -> None:
        self._sim_checkbox.blockSignals(True)
        self._sim_checkbox.setChecked(enabled)
        self._sim_checkbox.blockSignals(False)

    def set_dark_mode(self, enabled: bool) -> None:
        self._dark_checkbox.blockSignals(True)
        self._dark_checkbox.setChecked(enabled)
        self._dark_checkbox.blockSignals(False)

    def set_notifications_enabled(self, enabled: bool) -> None:
        self._notify_checkbox.blockSignals(True)
        self._notify_checkbox.setChecked(enabled)
        self._notify_checkbox.blockSignals(False)

    def set_language(self, language: str) -> None:
        index = self._language_combo.findData(language)
        if index < 0:
            return
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(index)
        self._language_combo.blockSignals(False)

    # -- geraete-individuelle Sicherheits-Grenzwerte -------------------------

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_label(label)
            return
        section = _DeviceSafetyGroup(kind, label)
        section.limit_changed.connect(
            lambda field, enabled, value, d=device_id: self.safety_limit_changed.emit(d, field, enabled, value)
        )
        self._safety_sections_layout.addWidget(section)
        self._safety_sections[device_id] = section

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_label(label)

    def set_device_safety_limits(self, device_id: str, limits: dict) -> None:
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_limits(limits)

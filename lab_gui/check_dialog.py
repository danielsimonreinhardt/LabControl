"""Popup-Dialog zur Definition einer Pass/Fail-Pruefung im Testcase-Editor.

Eine Pruefung legt fuer einen Aktionsschritt einen erwarteten Wertebereich
[Minimum, Maximum] einer Messgroesse (Spannung/Strom/Leistung) fest. Nach
Ablauf der Wartezeit des Schritts bewertet der Runner die naechste Messung
des Schritt-Geraets dagegen und meldet das Ergebnis (gruene/rote Zeile,
siehe testcase_runner._finish_step). Optional bricht eine Verletzung den
Lauf ab (check_abort), sonst laeuft er weiter und das Gesamtergebnis am
Ende zaehlt die Fehlschlaege.

Aufbau nach dem Muster von condition_dialog.ConditionDialog (params-Dict
rein, params() raus); anders als dort gibt es hier eine echte Validierung:
Minimum > Maximum sperrt den OK-Button, statt den Fehler stillschweigend
zu uebernehmen.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from i18n import Translator, tr
from step_spinbox import SteppedDoubleSpinBox
from testcase_model import COND_FIELD_LABELS, COND_FIELD_UNITS, TestStep, check_summary
from theme import current as current_palette


def _params_to_step(params: dict) -> TestStep:
    return TestStep(
        check_enabled=params["enabled"],
        check_field=params["field"],
        check_min=params["min"],
        check_max=params["max"],
        check_abort=params["abort"],
    )


class CheckDialog(QDialog):
    def __init__(self, params: dict, is_arb: bool, parent=None) -> None:
        """params: dict mit enabled/field/min/max/abort (siehe
        testcase_tab._build_action_row). is_arb steuert nur den Hinweis, dass
        bei einem Arbiträrsignal-Schritt nach dem Signalende gemessen wird."""
        super().__init__(parent)

        layout = QVBoxLayout(self)
        self._form = QFormLayout()

        self._enabled_check = QCheckBox()
        self._form.addRow(" ", self._enabled_check)

        self._field_combo = QComboBox()
        self._populate_field_combo()
        self._form.addRow(" ", self._field_combo)

        self._min_spin = SteppedDoubleSpinBox()
        self._min_spin.setDecimals(3)
        self._min_spin.setRange(-100000, 100000)
        self._form.addRow(" ", self._min_spin)

        self._max_spin = SteppedDoubleSpinBox()
        self._max_spin.setDecimals(3)
        self._max_spin.setRange(-100000, 100000)
        self._form.addRow(" ", self._max_spin)

        self._abort_check = QCheckBox()
        self._form.addRow(" ", self._abort_check)
        layout.addLayout(self._form)

        self._arb_hint = QLabel()
        self._arb_hint.setWordWrap(True)
        self._arb_hint.setStyleSheet(f"color: {current_palette().text_muted}; font-style: italic;")
        self._arb_hint.setVisible(is_arb)
        layout.addWidget(self._arb_hint)

        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(f"color: {current_palette().text_muted}; font-style: italic;")
        layout.addWidget(self._summary_label)

        self._warning_label = QLabel()
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet(f"color: {current_palette().warning};")
        layout.addWidget(self._warning_label)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._enabled_check.toggled.connect(self._update_state)
        self._field_combo.currentIndexChanged.connect(self._on_field_changed)
        self._min_spin.valueChanged.connect(self._update_state)
        self._max_spin.valueChanged.connect(self._update_state)
        self._abort_check.toggled.connect(self._update_state)

        self._load_params(params)
        self._on_field_changed()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _populate_field_combo(self) -> None:
        current = self._field_combo.currentData() if self._field_combo.count() else None
        self._field_combo.blockSignals(True)
        self._field_combo.clear()
        for code, base in COND_FIELD_LABELS.items():
            unit = COND_FIELD_UNITS.get(code, "")
            self._field_combo.addItem(f"{tr(base)} ({unit})" if unit else tr(base), code)
        index = self._field_combo.findData(current) if current else 0
        self._field_combo.setCurrentIndex(max(index, 0))
        self._field_combo.blockSignals(False)

    def _retranslate(self) -> None:
        self.setWindowTitle(tr("Prüfung definieren"))
        self._populate_field_combo()
        self._enabled_check.setText(tr("Prüfung aktiv"))
        self._abort_check.setText(tr("Bei Verletzung abbrechen"))
        self._form.labelForField(self._field_combo).setText(tr("Messgröße:"))
        self._form.labelForField(self._min_spin).setText(tr("Minimum:"))
        self._form.labelForField(self._max_spin).setText(tr("Maximum:"))
        self._arb_hint.setText(tr("Bei Arbiträrsignal-Schritten wird nach dem Signalende gemessen."))
        self._update_state()

    def _load_params(self, params: dict) -> None:
        self._enabled_check.setChecked(bool(params.get("enabled", False)))
        field_index = self._field_combo.findData(params.get("field", "voltage"))
        self._field_combo.setCurrentIndex(max(field_index, 0))
        self._min_spin.setValue(params.get("min", 0.0))
        self._max_spin.setValue(params.get("max", 0.0))
        self._abort_check.setChecked(bool(params.get("abort", False)))

    def _on_field_changed(self) -> None:
        unit = COND_FIELD_UNITS.get(self._field_combo.currentData(), "")
        suffix = f" {unit}" if unit else ""
        self._min_spin.setSuffix(suffix)
        self._max_spin.setSuffix(suffix)
        self._update_state()

    def _update_state(self) -> None:
        enabled = self._enabled_check.isChecked()
        for widget in (self._field_combo, self._min_spin, self._max_spin, self._abort_check):
            widget.setEnabled(enabled)

        invalid = enabled and self._min_spin.value() > self._max_spin.value()
        self._warning_label.setText(tr("Minimum ist größer als Maximum.") if invalid else "")
        self._warning_label.setVisible(invalid)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not invalid)

        if enabled:
            summary = check_summary(_params_to_step(self.params()))
            if self._abort_check.isChecked():
                summary = f"{summary} {tr('(Abbruch)')}"
            self._summary_label.setText(summary)
        else:
            self._summary_label.setText(tr("Keine Prüfung"))

    def params(self) -> dict:
        return dict(
            enabled=self._enabled_check.isChecked(),
            field=self._field_combo.currentData() or "voltage",
            min=self._min_spin.value(),
            max=self._max_spin.value(),
            abort=self._abort_check.isChecked(),
        )

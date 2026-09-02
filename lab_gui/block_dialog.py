"""Popup-Dialog zum Speichern eines Zeilenbereichs des Testablauf-Editors als
wiederverwendbaren Baustein (siehe FEATURES.md Punkt 3: "Wiederverwendbare
Testcase-Bausteine").

Waehlt einen zusammenhaengenden Bereich der aktuellen Tabelle (von/bis Zeile,
1-basiert wie die Zeilennummern-Spalte) sowie einen Namen. Der Bereich muss in
sich strukturell ausgeglichen sein (siehe validate_structure()) -- ein Baustein
mit z.B. einer Schleife ohne ihr zugehoeriges "Ende" wuerde beim spaeteren
Einfuegen (testcase_tab._insert_block_from_file) die Struktur des Ziel-
Testablaufs brechen, deshalb sperrt ein unausgeglichener Bereich den OK-Button.

Aufbau nach dem Muster von check_dialog.CheckDialog (Validierung sperrt OK),
das eigentliche Speichern der Datei uebernimmt testcase_tab (siehe dort:
QFileDialog analog zu _save_to_file)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
)

from i18n import Translator, tr
from step_spinbox import SteppedSpinBox
from testcase_model import (
    CONTROL_STEP_LABELS,
    STEP_TYPE_ACTION,
    VALUELESS_ACTIONS,
    TestStep,
    action_label,
    arb_shape_label,
    condition_summary,
    is_arb_action,
    kind_label,
    validate_structure,
)
from theme import current as current_palette


def _step_line(step: TestStep) -> str:
    """Kurze Ein-Zeilen-Beschreibung eines Schritts fuer die Vorschauliste --
    kennt (anders als testcase_tab._device_display) keine bekannten
    Geraete-Anzeigenamen, da der Dialog ohne Geraeteregistrierung auskommt."""
    if step.step_type != STEP_TYPE_ACTION:
        if step.step_type == "loop":
            return tr("Schleife ({n}×)", n=step.loop_count)
        if step.step_type in ("while", "if"):
            return f"{tr(CONTROL_STEP_LABELS[step.step_type])}: {condition_summary(step)}"
        if step.step_type in ("set_var", "inc_var"):
            op = "=" if step.step_type == "set_var" else "+="
            return f"{tr(CONTROL_STEP_LABELS[step.step_type])}: {step.var_name} {op} {step.value:g}"
        if step.step_type == "wait":
            return f"{tr(CONTROL_STEP_LABELS['wait'])} ({step.duration:g} s)"
        return tr(CONTROL_STEP_LABELS.get(step.step_type, step.step_type))
    device = step.device_id or tr("{kind} (automatisch)", kind=kind_label(step.device_kind))
    action = action_label(step.device_kind, step.action)
    if is_arb_action(step.action):
        return f"{device}: {action} ({arb_shape_label(step.arb_shape)})"
    if step.action in VALUELESS_ACTIONS:
        return f"{device}: {action}"
    return f"{device}: {action} = {step.value:g}"


class SaveBlockDialog(QDialog):
    def __init__(self, steps: list[TestStep], default_row: int, parent=None) -> None:
        """steps: alle Schritte des aktuellen Testablaufs (0-basiert, aus
        TestcaseTab.steps()). default_row: 0-basierter Index der aktuell
        markierten Zeile (oder -1 ohne Auswahl) -- Ausgangspunkt fuer den
        vorausgewaehlten Ein-Zeilen-Bereich."""
        super().__init__(parent)
        self._steps = steps

        layout = QVBoxLayout(self)
        self._form = QFormLayout()

        self._name_edit = QLineEdit()
        self._form.addRow(" ", self._name_edit)

        start_default = min(default_row + 1 if default_row >= 0 else 1, len(steps))
        self._from_spin = SteppedSpinBox()
        self._from_spin.setRange(1, len(steps))
        self._from_spin.setValue(start_default)
        self._to_spin = SteppedSpinBox()
        self._to_spin.setRange(1, len(steps))
        self._to_spin.setValue(start_default)
        self._form.addRow(" ", self._from_spin)
        self._form.addRow(" ", self._to_spin)
        layout.addLayout(self._form)

        self._count_label = QLabel()
        self._count_label.setStyleSheet(f"color: {current_palette().text_muted}; font-style: italic;")
        layout.addWidget(self._count_label)

        self._preview_list = QListWidget()
        self._preview_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._preview_list)

        self._warning_label = QLabel()
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet(f"color: {current_palette().danger};")
        layout.addWidget(self._warning_label)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._name_edit.textChanged.connect(self._update_state)
        self._from_spin.valueChanged.connect(lambda _=None: self._on_range_changed(self._from_spin))
        self._to_spin.valueChanged.connect(lambda _=None: self._on_range_changed(self._to_spin))

        self._update_preview()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _on_range_changed(self, changed: SteppedSpinBox) -> None:
        # "von" darf "bis" nicht ueberholen und umgekehrt -- statt eines
        # Fehlers einfach den jeweils anderen Wert mitziehen.
        if self._from_spin.value() > self._to_spin.value():
            other = self._to_spin if changed is self._from_spin else self._from_spin
            other.blockSignals(True)
            other.setValue(changed.value())
            other.blockSignals(False)
        self._update_preview()

    def _selected_steps(self) -> list[TestStep]:
        start = self._from_spin.value() - 1
        end = self._to_spin.value()
        return self._steps[start:end]

    def _update_preview(self) -> None:
        self._preview_list.clear()
        for step in self._selected_steps():
            self._preview_list.addItem(_step_line(step))
        self._update_state()

    def _update_state(self) -> None:
        selected = self._selected_steps()
        _matching, _depths, errors = validate_structure(selected)
        name_ok = bool(self._name_edit.text().strip())
        self._count_label.setText(tr("{n} Schritt(e) ausgewählt", n=len(selected)))
        if errors:
            self._warning_label.setText(
                tr("Bereich ist strukturell unausgeglichen (z.B. Schleife ohne „Ende“) -- Auswahl anpassen.")
            )
        else:
            self._warning_label.setText("")
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(name_ok and not errors)

    def _retranslate(self) -> None:
        self.setWindowTitle(tr("Baustein speichern"))
        self._name_edit.setPlaceholderText(tr("Name des Bausteins"))
        self._form.labelForField(self._name_edit).setText(tr("Name:"))
        self._form.labelForField(self._from_spin).setText(tr("Von Zeile:"))
        self._form.labelForField(self._to_spin).setText(tr("Bis Zeile:"))
        self._update_state()

    def block_name(self) -> str:
        return self._name_edit.text().strip()

    def selected_steps(self) -> list[TestStep]:
        return self._selected_steps()

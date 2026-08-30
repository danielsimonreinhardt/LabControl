"""Popup-Dialog zur Definition einer While/If-Bedingung im Testcase-Editor.

Eine Bedingung vergleicht entweder einen Live-Messwert eines Geraets, die
verstrichene Zeit oder eine Laufvariable gegen einen festen Wert (siehe
testcase_model.COND_*). Bei einer While-Schleife kommt zusaetzlich eine
Obergrenze fuer die Anzahl Durchlaeufe hinzu (Endlosschleifen-Schutz gegen
eine Bedingung, die nie falsch wird -- siehe
testcase_runner.TestRunner._advance, Zweig "end"/"while").
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from i18n import Translator, tr
from testcase_model import COND_FIELD_LABELS, COND_FIELD_UNITS, COND_OPS, TestStep, condition_summary
from theme import current as current_palette

_SOURCE_BASE_LABELS = {"measurement": "Messwert", "time": "Zeit", "variable": "Variable"}
_TIME_REF_BASE_LABELS = {"block": "seit Blockstart", "run": "seit Teststart"}
_SOURCE_PAGE_INDEX = {"measurement": 0, "time": 1, "variable": 2}

# Trennzeichen fuer den itemData-Schluessel des Geraete-Combos -- dieselbe
# Notwendigkeit wie in testcase_tab._device_key: QComboBox.findData() ist bei
# zusammengesetzten Python-Objekten (Tupeln) als userData in der Praxis nicht
# zuverlaessig, ein String-Schluessel schon.
_DEVICE_KEY_SEP = "\x1f"


def _encode_device_key(kind: str, device_id: str) -> str:
    return f"{kind}{_DEVICE_KEY_SEP}{device_id}"


def _decode_device_key(key: str | None) -> tuple[str, str]:
    if not key:
        return "load", ""
    kind, _, device_id = key.partition(_DEVICE_KEY_SEP)
    return kind, device_id


class ConditionDialog(QDialog):
    def __init__(
        self,
        params: dict,
        device_items: list[tuple[str, str, str]],
        is_while: bool,
        parent=None,
    ) -> None:
        """device_items: Liste (Anzeigetext, device_kind, device_id) fuer die
        Geraeteauswahl der Messwert-Seite -- device_id=="" bedeutet
        "automatisch" (einziges verbundenes Geraet dieser Art zur Laufzeit),
        siehe TestRunner._eval_condition."""
        super().__init__(parent)
        self._device_items = device_items
        self._is_while = is_while
        self._device_combo_pending: str | None = None

        layout = QVBoxLayout(self)
        self._form = QFormLayout()

        self._source_combo = QComboBox()
        self._populate_source_combo()
        self._form.addRow(" ", self._source_combo)
        layout.addLayout(self._form)

        self._pages = QStackedWidget()
        layout.addWidget(self._pages)

        # -- Seite "Messwert" --
        self._measurement_page = QWidget()
        m_form = QFormLayout(self._measurement_page)
        self._device_combo = QComboBox()
        self._field_combo = QComboBox()
        self._populate_field_combo()
        self._m_op_combo = QComboBox()
        self._populate_op_combo(self._m_op_combo)
        self._m_value_spin = QDoubleSpinBox()
        self._m_value_spin.setDecimals(3)
        self._m_value_spin.setRange(-100000, 100000)
        m_form.addRow(" ", self._device_combo)
        m_form.addRow(" ", self._field_combo)
        m_form.addRow(" ", self._m_op_combo)
        m_form.addRow(" ", self._m_value_spin)
        self._pages.addWidget(self._measurement_page)

        # -- Seite "Zeit" --
        self._time_page = QWidget()
        t_form = QFormLayout(self._time_page)
        self._time_ref_combo = QComboBox()
        self._populate_time_ref_combo()
        self._t_op_combo = QComboBox()
        self._populate_op_combo(self._t_op_combo)
        self._t_value_spin = QDoubleSpinBox()
        self._t_value_spin.setDecimals(1)
        self._t_value_spin.setRange(0, 1e7)
        self._t_value_spin.setSuffix(" s")
        t_form.addRow(" ", self._time_ref_combo)
        t_form.addRow(" ", self._t_op_combo)
        t_form.addRow(" ", self._t_value_spin)
        self._pages.addWidget(self._time_page)

        # -- Seite "Variable" --
        self._variable_page = QWidget()
        v_form = QFormLayout(self._variable_page)
        self._var_edit = QLineEdit()
        self._v_op_combo = QComboBox()
        self._populate_op_combo(self._v_op_combo)
        self._v_value_spin = QDoubleSpinBox()
        self._v_value_spin.setDecimals(3)
        self._v_value_spin.setRange(-1e9, 1e9)
        v_form.addRow(" ", self._var_edit)
        v_form.addRow(" ", self._v_op_combo)
        v_form.addRow(" ", self._v_value_spin)
        self._pages.addWidget(self._variable_page)

        self._max_iter_label = QLabel()
        self._max_iter_spin = QSpinBox()
        self._max_iter_spin.setRange(0, 1_000_000)
        if is_while:
            self._form.addRow(self._max_iter_label, self._max_iter_spin)
        else:
            self._max_iter_label.hide()
            self._max_iter_spin.hide()

        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet(f"color: {current_palette().text_muted}; font-style: italic;")
        layout.addWidget(self._summary_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._device_combo.currentIndexChanged.connect(self._update_summary)
        self._field_combo.currentIndexChanged.connect(self._update_summary)
        self._m_op_combo.currentIndexChanged.connect(self._update_summary)
        self._m_value_spin.valueChanged.connect(self._update_summary)
        self._time_ref_combo.currentIndexChanged.connect(self._update_summary)
        self._t_op_combo.currentIndexChanged.connect(self._update_summary)
        self._t_value_spin.valueChanged.connect(self._update_summary)
        self._var_edit.textChanged.connect(self._update_summary)
        self._v_op_combo.currentIndexChanged.connect(self._update_summary)
        self._v_value_spin.valueChanged.connect(self._update_summary)

        self._load_params(params)
        self._on_source_changed()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    # -- Combo-Befuellung (siehe signal_dialog.SignalDialog fuer das Muster) --

    def _populate_source_combo(self) -> None:
        current = self._source_combo.currentData() if self._source_combo.count() else None
        self._source_combo.blockSignals(True)
        self._source_combo.clear()
        for code, base in _SOURCE_BASE_LABELS.items():
            self._source_combo.addItem(tr(base), code)
        index = self._source_combo.findData(current) if current else 0
        self._source_combo.setCurrentIndex(max(index, 0))
        self._source_combo.blockSignals(False)

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

    def _populate_time_ref_combo(self) -> None:
        current = self._time_ref_combo.currentData() if self._time_ref_combo.count() else None
        self._time_ref_combo.blockSignals(True)
        self._time_ref_combo.clear()
        for code, base in _TIME_REF_BASE_LABELS.items():
            self._time_ref_combo.addItem(tr(base), code)
        index = self._time_ref_combo.findData(current) if current else 0
        self._time_ref_combo.setCurrentIndex(max(index, 0))
        self._time_ref_combo.blockSignals(False)

    def _populate_device_combo(self) -> None:
        current = self._device_combo.currentData() if self._device_combo.count() else self._device_combo_pending
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        for text, kind, device_id in self._device_items:
            self._device_combo.addItem(text, _encode_device_key(kind, device_id))
        index = self._device_combo.findData(current) if current else -1
        if index < 0 and current:
            # Aus einer gespeicherten Datei geladenes Geraet, das diese
            # Session noch nicht gesehen hat -- Eintrag beibehalten statt die
            # Auswahl beim Oeffnen des Dialogs stillschweigend auf
            # "automatisch" zurueckzusetzen (siehe testcase_tab._populate_device_combo).
            kind, device_id = _decode_device_key(current)
            if device_id:
                self._device_combo.addItem(tr("{device_id} (nicht verbunden)", device_id=device_id), current)
                index = self._device_combo.count() - 1
        self._device_combo.setCurrentIndex(max(index, 0))
        self._device_combo.blockSignals(False)

    def _populate_op_combo(self, combo: QComboBox) -> None:
        current = combo.currentData() if combo.count() else None
        combo.blockSignals(True)
        combo.clear()
        for code in COND_OPS:
            combo.addItem(code, code)
        index = combo.findData(current) if current else 0
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)

    def _retranslate(self) -> None:
        self.setWindowTitle(tr("Bedingung"))
        self._populate_source_combo()
        self._populate_field_combo()
        self._populate_time_ref_combo()
        self._populate_device_combo()
        for combo in (self._m_op_combo, self._t_op_combo, self._v_op_combo):
            self._populate_op_combo(combo)
        self._form.labelForField(self._source_combo).setText(tr("Quelle:"))
        self._measurement_page.layout().labelForField(self._device_combo).setText(tr("Gerät:"))
        self._measurement_page.layout().labelForField(self._field_combo).setText(tr("Messgröße:"))
        self._measurement_page.layout().labelForField(self._m_op_combo).setText(tr("Vergleich:"))
        self._measurement_page.layout().labelForField(self._m_value_spin).setText(tr("Wert:"))
        self._time_page.layout().labelForField(self._time_ref_combo).setText(tr("Referenz:"))
        self._time_page.layout().labelForField(self._t_op_combo).setText(tr("Vergleich:"))
        self._time_page.layout().labelForField(self._t_value_spin).setText(tr("Wert:"))
        self._variable_page.layout().labelForField(self._var_edit).setText(tr("Variable:"))
        self._variable_page.layout().labelForField(self._v_op_combo).setText(tr("Vergleich:"))
        self._variable_page.layout().labelForField(self._v_value_spin).setText(tr("Wert:"))
        self._var_edit.setPlaceholderText(tr("Variablenname"))
        self._max_iter_label.setText(tr("Max. Durchläufe:"))
        self._max_iter_spin.setSpecialValueText(tr("unbegrenzt"))
        self._max_iter_spin.setToolTip(
            tr("Sicherheitsabbruch gegen eine Endlosschleife (Bedingung, die nie falsch wird) -- 0 = unbegrenzt.")
        )
        self._update_summary()

    def _load_params(self, params: dict) -> None:
        source_index = self._source_combo.findData(params.get("cond_source", "measurement"))
        self._source_combo.setCurrentIndex(max(source_index, 0))
        self._device_combo_pending = _encode_device_key(
            params.get("cond_device_kind", "load"), params.get("cond_device_id", "")
        )
        self._populate_device_combo()
        field_index = self._field_combo.findData(params.get("cond_field", "voltage"))
        self._field_combo.setCurrentIndex(max(field_index, 0))
        self._m_op_combo.setCurrentIndex(max(self._m_op_combo.findData(params.get("cond_op", "<")), 0))
        self._m_value_spin.setValue(params.get("cond_value", 0.0))
        ref_index = self._time_ref_combo.findData(params.get("cond_time_ref", "block"))
        self._time_ref_combo.setCurrentIndex(max(ref_index, 0))
        self._t_op_combo.setCurrentIndex(max(self._t_op_combo.findData(params.get("cond_op", "<")), 0))
        self._t_value_spin.setValue(params.get("cond_value", 0.0))
        self._var_edit.setText(params.get("cond_var", ""))
        self._v_op_combo.setCurrentIndex(max(self._v_op_combo.findData(params.get("cond_op", "<")), 0))
        self._v_value_spin.setValue(params.get("cond_value", 0.0))
        self._max_iter_spin.setValue(int(params.get("max_iterations", 1000)))

    def _on_source_changed(self) -> None:
        source = self._source_combo.currentData()
        self._pages.setCurrentIndex(_SOURCE_PAGE_INDEX.get(source, 0))
        self._update_summary()

    def _current_step(self) -> TestStep:
        """Baut aus den aktuellen Dialog-Eingaben einen minimalen TestStep,
        nur um condition_summary()/params() aus denselben cond_*-Feldern
        speisen zu koennen wie der Runner sie spaeter liest."""
        source = self._source_combo.currentData()
        if source == "measurement":
            kind, device_id = _decode_device_key(self._device_combo.currentData())
            return TestStep(
                cond_source="measurement", cond_device_kind=kind, cond_device_id=device_id,
                cond_field=self._field_combo.currentData(), cond_op=self._m_op_combo.currentData(),
                cond_value=self._m_value_spin.value(),
            )
        if source == "time":
            return TestStep(
                cond_source="time", cond_time_ref=self._time_ref_combo.currentData(),
                cond_op=self._t_op_combo.currentData(), cond_value=self._t_value_spin.value(),
            )
        return TestStep(
            cond_source="variable", cond_var=self._var_edit.text().strip(),
            cond_op=self._v_op_combo.currentData(), cond_value=self._v_value_spin.value(),
        )

    def _update_summary(self) -> None:
        self._summary_label.setText(condition_summary(self._current_step()))

    def params(self) -> dict:
        step = self._current_step()
        result = dict(
            cond_source=step.cond_source, cond_device_kind=step.cond_device_kind,
            cond_device_id=step.cond_device_id, cond_field=step.cond_field,
            cond_op=step.cond_op, cond_value=step.cond_value,
            cond_time_ref=step.cond_time_ref, cond_var=step.cond_var,
        )
        if self._is_while:
            result["max_iterations"] = self._max_iter_spin.value()
        return result

"""Dialog eines Blocks "Ueberwachen … Ende" (0.63.0, FT-7): Quelle wie bei einer Messwert-Bedingung
(ConditionDialog), dazu Art und Grenzen der Zeitpruefung (timing_checks)."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLineEdit, QWidget

import timing_checks
from condition_dialog import ConditionDialog
from i18n import tr
from step_spinbox import SteppedDoubleSpinBox
from testcase_model import TestStep, monitor_summary


class MonitorDialog(ConditionDialog):
    def __init__(self, params: dict, device_items: list[tuple[str, str, str]], parent=None) -> None:
        super().__init__(dict(params, cond_source="measurement"), device_items, is_while=False, parent=parent)
        self._monitor_config = timing_checks.clean_config(params.get("monitor"))
        self._extra = QWidget()
        self._form.setRowVisible(self._source_combo, False)      # Quelle ist immer ein Messwert

        form = QFormLayout(self._extra)
        self._mode_combo = QComboBox()
        self._min_spin = self._spin()
        self._max_spin = self._spin()
        self._within_spin = self._spin(0.0, 1e5, " s")
        self._can_id_edit = QLineEdit()
        self._gap_spin = self._spin(1.0, 1e6, " ms")
        self._abort_check = QCheckBox()
        for widget in (self._mode_combo, self._min_spin, self._max_spin, self._within_spin, self._can_id_edit,
                       self._gap_spin, self._abort_check):
            form.addRow(" ", widget)
        self.layout().insertWidget(2, self._extra)

        c = self._monitor_config
        self._min_spin.setValue(c["min"])
        self._max_spin.setValue(c["max"])
        self._within_spin.setValue(c["within_s"])
        self._can_id_edit.setText(f"0x{c['can_id']:X}")
        self._gap_spin.setValue(c["max_gap_ms"])
        self._abort_check.setChecked(c["abort"])
        if "op" in (params.get("monitor") or {}):
            self._m_op_combo.setCurrentIndex(max(self._m_op_combo.findData(c["op"]), 0))
            self._m_value_spin.setValue(c["value"])
        self._populate_mode_combo(c["mode"])

        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        for spin in (self._min_spin, self._max_spin, self._within_spin, self._gap_spin):
            spin.valueChanged.connect(self._update_summary)
        self._can_id_edit.textChanged.connect(self._update_summary)
        self._abort_check.toggled.connect(self._update_summary)
        self._retranslate()
        self._on_mode_changed()

    @staticmethod
    def _spin(lo: float = -1e6, hi: float = 1e6, suffix: str = "") -> SteppedDoubleSpinBox:
        spin = SteppedDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(lo, hi)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def _populate_mode_combo(self, current: str) -> None:
        self._mode_combo.blockSignals(True)
        self._mode_combo.clear()
        for code in timing_checks.MODES:
            self._mode_combo.addItem(tr(timing_checks.MODE_LABELS[code]), code)
        self._mode_combo.setCurrentIndex(max(self._mode_combo.findData(current), 0))
        self._mode_combo.blockSignals(False)

    def _mode(self) -> str:
        return self._mode_combo.currentData() if hasattr(self, "_mode_combo") else "band"

    def _retranslate(self) -> None:
        super()._retranslate()
        if not hasattr(self, "_mode_combo"):
            return
        self.setWindowTitle(tr("Überwachen"))
        self._populate_mode_combo(self._mode())
        form = self._extra.layout()
        for widget, text in ((self._mode_combo, tr("Art:")), (self._min_spin, tr("Minimum:")),
                             (self._max_spin, tr("Maximum:")), (self._within_spin, tr("Innerhalb:")),
                             (self._can_id_edit, tr("CAN-ID:")), (self._gap_spin, tr("Größte Lücke:"))):
            form.labelForField(widget).setText(text)
        form.labelForField(self._abort_check).setText("")
        self._abort_check.setText(tr("Bei Verletzung abbrechen"))
        self._update_summary()

    def _on_mode_changed(self) -> None:
        self._update_measurement_visibility()
        self._update_summary()

    def _update_measurement_visibility(self) -> None:
        super()._update_measurement_visibility()
        if not hasattr(self, "_mode_combo"):
            return
        mode = self._mode()
        m_form = self._measurement_page.layout()
        gap = mode == "can_gap"
        kind = self._current_device_kind()
        m_form.setRowVisible(self._field_combo, not gap and kind != "can")
        m_form.setRowVisible(self._m_channel_spin, not gap and kind == "hil")
        m_form.setRowVisible(self._m_can_signal_edit, not gap and kind == "can")
        m_form.setRowVisible(self._m_op_combo, mode == "reach")
        m_form.setRowVisible(self._m_value_spin, mode == "reach")
        form = self._extra.layout()
        form.setRowVisible(self._min_spin, mode == "band")
        form.setRowVisible(self._max_spin, mode == "band")
        form.setRowVisible(self._within_spin, mode == "reach")
        form.setRowVisible(self._can_id_edit, gap)
        form.setRowVisible(self._gap_spin, gap)

    def _can_id(self) -> int:
        try:
            return int(self._can_id_edit.text().strip() or "0", 0) & 0x1FFFFFFF
        except ValueError:
            return 0

    def monitor_config(self) -> dict:
        return {"mode": self._mode(), "min": self._min_spin.value(), "max": self._max_spin.value(),
                "op": self._m_op_combo.currentData(), "value": self._m_value_spin.value(),
                "within_s": self._within_spin.value(), "can_id": self._can_id(),
                "max_gap_ms": self._gap_spin.value(), "abort": self._abort_check.isChecked()}

    def _update_summary(self) -> None:
        if not hasattr(self, "_abort_check"):
            super()._update_summary()
            return
        step = self._current_step()
        step.params = {"monitor": self.monitor_config()}
        self._summary_label.setText(monitor_summary(step))

    def params(self) -> dict:
        result = super().params()
        result["monitor"] = self.monitor_config()
        return result


def monitor_step(params: dict, enabled: bool = True) -> TestStep:
    """TestStep eines Blocks "Ueberwachen" aus den Dialog-/Zeilen-Parametern."""
    return TestStep(step_type="monitor", enabled=enabled, cond_source="measurement",
                    cond_device_kind=params.get("cond_device_kind", "psu"),
                    cond_device_id=params.get("cond_device_id", ""), cond_field=params.get("cond_field", "voltage"),
                    hil_channel=int(params.get("hil_channel", 1)),
                    params={"monitor": timing_checks.clean_config(params.get("monitor"))})

"""Popup-Dialog zur Definition eines CAN-Frame-Testschritts (Aktion "CAN_SEND").

Analog zu signal_dialog.py (Arbiträrsignal): haelt die Zusatz-Parameter, die
nicht ins normale Wert-Feld der Testschritt-Zeile passen (siehe
testcase_model.TestStep: can_id/can_data/can_extended).
"""
from __future__ import annotations

from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from i18n import Translator, tr
from step_spinbox import SteppedSpinBox

STANDARD_ID_MAX = 0x7FF
EXTENDED_ID_MAX = 0x1FFFFFFF
MAX_DATA_BYTES = 8

# Erlaubt Hex-Byte-Paare, durch Leerzeichen getrennt, z.B. "01 A2 FF" oder "".
_HEX_DATA_PATTERN = QRegularExpression(r"^([0-9A-Fa-f]{2}\s*)*$")


class CanFrameDialog(QDialog):
    def __init__(self, params: dict, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self._form = QFormLayout()

        self._id_spin = SteppedSpinBox()
        self._id_spin.setDisplayIntegerBase(16)
        self._id_spin.setPrefix("0x")

        self._extended_check = QCheckBox()
        self._extended_check.toggled.connect(self._on_extended_toggled)

        self._data_edit = QLineEdit()
        self._data_edit.setValidator(QRegularExpressionValidator(_HEX_DATA_PATTERN, self))
        self._data_edit.textChanged.connect(self._update_byte_count)

        self._byte_count_label = QLabel()
        self._byte_count_label.setStyleSheet("color: gray; font-style: italic;")

        self._form.addRow(" ", self._id_spin)
        self._form.addRow(" ", self._extended_check)
        self._form.addRow(" ", self._data_edit)
        self._form.addRow(" ", self._byte_count_label)
        layout.addLayout(self._form)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._load_params(params)

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _load_params(self, params: dict) -> None:
        self._extended_check.setChecked(bool(params.get("extended", False)))
        self._on_extended_toggled(self._extended_check.isChecked())
        self._id_spin.setValue(min(int(params.get("id", 0)), self._id_spin.maximum()))
        self._data_edit.setText(str(params.get("data", "")))

    def _on_extended_toggled(self, extended: bool) -> None:
        self._id_spin.setRange(0, EXTENDED_ID_MAX if extended else STANDARD_ID_MAX)

    def _update_byte_count(self) -> None:
        count = len(self._data_edit.text().split())
        self._byte_count_label.setText(tr("{count}/{max} Bytes", count=count, max=MAX_DATA_BYTES))
        too_long = count > MAX_DATA_BYTES
        self._byte_count_label.setStyleSheet(
            f"color: {'red' if too_long else 'gray'}; font-style: italic;"
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not too_long)

    def _retranslate(self) -> None:
        self.setWindowTitle(tr("CAN-Frame definieren"))
        self._form.labelForField(self._id_spin).setText(tr("Arbitration-ID:"))
        self._form.labelForField(self._extended_check).setText(tr("Extended-ID (29 Bit):"))
        self._form.labelForField(self._data_edit).setText(tr("Daten (Hex-Bytes, z.B. \"01 A2 FF\"):"))
        self._update_byte_count()

    def params(self) -> dict:
        return dict(
            id=self._id_spin.value(),
            data=self._data_edit.text().strip(),
            extended=self._extended_check.isChecked(),
        )

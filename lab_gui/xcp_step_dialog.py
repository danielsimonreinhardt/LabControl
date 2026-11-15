"""Dialog eines XCP-Ablaufschritts (0.64.0, FT-10): A2L-Objekt waehlen, bei XCP_READ den Index eines Felds,
bei XCP_WRITE die physikalischen Werte (Grenzen aus der A2L)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from i18n import tr
from step_spinbox import SteppedSpinBox


class XcpStepDialog(QDialog):
    def __init__(self, action: str, params: dict, a2l, parent=None) -> None:
        super().__init__(parent)
        self._action = action
        self._a2l = a2l
        self.setWindowTitle(tr("XCP-Schritt"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name = QComboBox()
        self._name.setEditable(True)
        self._name.setMinimumWidth(360)
        if a2l is not None:
            for name in a2l.names():
                obj = a2l.get(name)
                if action == "XCP_WRITE" and not obj.writable:
                    continue
                self._name.addItem(name, name)
        self._name.setCurrentText(str(params.get("name", "")))
        self._index = SteppedSpinBox()
        self._index.setRange(0, 4095)
        self._index.setValue(int(params.get("index", 0) or 0))
        self._values = QLineEdit(" ".join(f"{v:g}" for v in params.get("values", []) or []))
        self._values.setPlaceholderText("1.5   oder   10 20 30 40")
        self._info = QLabel()
        self._info.setWordWrap(True)
        form.addRow(tr("Objekt:"), self._name)
        form.addRow(tr("Index:"), self._index)
        form.addRow(tr("Werte:"), self._values)
        form.setRowVisible(self._index, action == "XCP_READ")
        form.setRowVisible(self._values, action == "XCP_WRITE")
        layout.addLayout(form)
        layout.addWidget(self._info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._name.currentTextChanged.connect(self._update_info)
        self._update_info()

    def _update_info(self) -> None:
        name = self._name.currentText().strip()
        if self._a2l is None or name not in self._a2l.objects:
            self._info.setText(tr("Keine A2L geladen") if self._a2l is None else "")
            return
        obj = self._a2l.get(name)
        kind = tr("Messgröße") if obj.kind == "measurement" else tr("Parameter")
        unit = f" {obj.unit}" if obj.unit else ""
        size = " × ".join(str(d) for d in obj.dims) if len(obj.dims) > 1 else str(obj.count)
        self._info.setText(f"{kind} · {obj.datatype} × {size} · {obj.lower:g}–{obj.upper:g}{unit}"
                           + ("" if obj.writable else " · " + tr("nur lesbar")))

    def params(self) -> dict:
        result = {"name": self._name.currentText().strip()}
        if self._action == "XCP_READ":
            result["index"] = self._index.value()
        else:
            result["values"] = [float(v.replace(",", ".")) for v in self._values.text().replace(";", " ").split()
                                if _is_number(v)]
        return result


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False

"""Parameter eines UDS-Ablaufschritts bearbeiten (FT-1, 0.60.1). Auswahllisten kommen aus dem ODX des
eingestellten Pruefings; ohne ODX bleiben freie Eingaben (Name oder Nummer). Siehe uds_steps.py fuer die
Bedeutung der Parameter."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

import uds_steps
from i18n import tr
from testcase_model import action_label


def _editable_combo(items: list[tuple[str, str]], current: str) -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    for text, value in items:
        combo.addItem(text, value)
    index = combo.findData(current)
    if index >= 0:
        combo.setCurrentIndex(index)
    else:
        combo.setEditText(str(current or ""))
    return combo


def _combo_value(combo: QComboBox) -> str:
    index = combo.findText(combo.currentText())
    if index >= 0 and combo.itemData(index) is not None:
        return str(combo.itemData(index))
    return combo.currentText().strip()


class UdsStepDialog(QDialog):
    def __init__(self, action: str, params: dict, odx=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("UDS: {action}", action=action_label("uds", action)))
        self._action = action
        self._params = dict(params or {})
        self._odx = odx
        layout = QVBoxLayout(self)
        self._form = QFormLayout()
        layout.addLayout(self._form)
        self._fields: dict[str, QWidget] = {}
        self._value_edits: dict[str, QLineEdit] = {}
        self._build()
        expect_nrc = uds_steps.parse_expect(self._params.get("expect"))
        self._expect = QComboBox()
        self._expect.addItem(tr("positive Antwort"), "positive")
        self._expect.addItem(tr("negative Antwort (NRC)"), "nrc")
        self._expect.setCurrentIndex(0 if expect_nrc is None else 1)
        self._nrc = QLineEdit("" if expect_nrc is None else f"0x{expect_nrc:02X}")
        self._nrc.setPlaceholderText("0x33")
        self._form.addRow(tr("Erwartung:"), self._expect)
        self._form.addRow(tr("NRC:"), self._nrc)
        self._expect.currentIndexChanged.connect(lambda _i: self._nrc.setEnabled(self._expect.currentData() == "nrc"))
        self._nrc.setEnabled(expect_nrc is not None)
        self._error = QLabel()
        layout.addWidget(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- Felder je Aktion ------------------------------------------------------------------

    def _build(self) -> None:
        a, p, odx = self._action, self._params, self._odx
        if a == "UDS_SESSION":
            items = [(f"{n} (0x{i:02X})", n) for n, i in (odx.sessions.items() if odx else [])]
            self._add("session", tr("Session:"), _editable_combo(items, p.get("session", "Extended")))
        elif a == "UDS_SECURITY":
            items = [(tr("automatisch"), "")] + [(f"{n} (0x{s.level:02X})", n)
                                                 for n, s in (odx.security.items() if odx else [])]
            self._add("level", tr("Stufe:"), _editable_combo(items, p.get("level", "")))
        elif a in ("UDS_READ_DID", "UDS_WRITE_DID"):
            dids = [(f"{d.name} (0x{d.did:04X})", d.name) for d in (odx.dids.values() if odx else [])
                    if (d.read if a == "UDS_READ_DID" else d.write)]
            did_combo = _editable_combo(dids, p.get("did", ""))
            self._add("did", tr("DID:"), did_combo)
            if a == "UDS_READ_DID":
                self._element = QComboBox()
                self._add("element", tr("Element:"), self._element)
                did_combo.currentTextChanged.connect(lambda _t: self._fill_elements())
                self._fill_elements()
            else:
                self._values_holder = QWidget()
                self._values_form = QFormLayout(self._values_holder)
                self._values_form.setContentsMargins(0, 0, 0, 0)
                self._form.addRow(tr("Werte:"), self._values_holder)
                self._hex = QLineEdit(str(p.get("hex", "")))
                self._hex.setPlaceholderText(tr("oder roh als Hex"))
                self._add("hex", tr("Roh (Hex):"), self._hex)
                did_combo.currentTextChanged.connect(lambda _t: self._fill_values())
                self._fill_values()
        elif a == "UDS_READ_DTC":
            self._add("mask", tr("Statusmaske:"), QLineEdit(str(p.get("mask", "0xFF"))))
            codes = ", ".join(f"0x{c:06X}" for c in (odx.dtcs if odx else {}))
            present = QLineEdit(", ".join(map(str, p.get("present", []))))
            present.setToolTip(codes)
            absent = QLineEdit(", ".join(map(str, p.get("absent", []))))
            absent.setToolTip(codes)
            self._add("present", tr("muss gesetzt sein:"), present)
            self._add("absent", tr("darf nicht gesetzt sein:"), absent)
            empty = QCheckBox(tr("Fehlerspeicher muss leer sein"))
            empty.setChecked(bool(p.get("empty")))
            self._add("empty", "", empty)
        elif a == "UDS_CLEAR_DTC":
            self._add("group", tr("Gruppe:"), QLineEdit(str(p.get("group", "0xFFFFFF"))))
        elif a == "UDS_ROUTINE":
            routines = [(f"{r.name} (0x{r.rid:04X})", r.name) for r in (odx.routines.values() if odx else [])]
            self._add("routine", tr("Routine:"), _editable_combo(routines, p.get("routine", "")))
            step = QComboBox()
            for code in ("start", "stop", "results"):
                step.addItem(tr({"start": "Start", "stop": "Stopp", "results": "Ergebnis"}[code]), code)
            step.setCurrentIndex(max(0, step.findData(p.get("step", "start"))))
            self._add("step", tr("Schritt:"), step)
            self._add("data", tr("Daten (Hex):"), QLineEdit(str(p.get("data", ""))))
        elif a == "UDS_RESET":
            reset = QComboBox()
            for value, name in uds_steps.RESET_TYPES.items():
                reset.addItem(f"{name} (0x{value:02X})", value)
            reset.setCurrentIndex(max(0, reset.findData(int(p.get("type", 1)))))
            self._add("type", tr("Art:"), reset)
        elif a == "UDS_RAW":
            raw = QLineEdit(str(p.get("request", "")))
            raw.setPlaceholderText("22 F1 86")
            self._add("request", tr("Anfrage (Hex):"), raw)

    def _add(self, key: str, label: str, widget: QWidget) -> None:
        self._fields[key] = widget
        self._form.addRow(label, widget)

    def _did(self):
        if self._odx is None:
            return None
        name = _combo_value(self._fields["did"])
        found = self._odx.did_by_name(name)
        if found is None:
            try:
                found = self._odx.dids.get(int(name, 0))
            except ValueError:
                found = None
        return found

    def _fill_elements(self) -> None:
        did = self._did()
        current = self._element.currentData() if self._element.count() else self._params.get("element", "")
        self._element.clear()
        self._element.addItem(tr("erster Zahlenwert"), "")
        for element in (did.elements if did else []):
            unit = f" [{element.unit}]" if element.unit else ""
            self._element.addItem(element.name + unit, element.name)
        self._element.setCurrentIndex(max(0, self._element.findData(current)))

    def _fill_values(self) -> None:
        while self._values_form.rowCount():
            self._values_form.removeRow(0)
        self._value_edits = {}
        did = self._did()
        values = self._params.get("values") or {}
        for element in (did.elements if did else []):
            edit = QLineEdit(str(values.get(element.name, "")))
            if element.texttable:
                edit.setToolTip(", ".join(f"{k}={v}" for k, v in element.texttable.items()))
            self._value_edits[element.name] = edit
            self._values_form.addRow(element.name + (f" [{element.unit}]" if element.unit else ""), edit)

    # -- Ergebnis --------------------------------------------------------------------------

    def _accept(self) -> None:
        try:
            self._result = self._collect()
            uds_steps.command_for(self._action, self._result)
            uds_steps.parse_expect(self._result.get("expect"))
        except (ValueError, TypeError) as exc:
            self._error.setText(str(exc))
            return
        self.accept()

    def _collect(self) -> dict:
        p: dict = {}
        for key, widget in self._fields.items():
            if isinstance(widget, QCheckBox):
                if widget.isChecked():
                    p[key] = True
            elif isinstance(widget, QComboBox):
                value = widget.currentData() if not widget.isEditable() else _combo_value(widget)
                if value not in (None, ""):
                    p[key] = value
            elif isinstance(widget, QLineEdit):
                text = widget.text().strip()
                if text:
                    p[key] = text
        for key in ("present", "absent"):
            if key in p:
                p[key] = [c.strip() for c in p[key].replace(";", ",").split(",") if c.strip()]
                for code in p[key]:
                    int(code, 0)
        for key in ("mask", "group"):
            if key in p:
                int(str(p[key]), 0)
        for key in ("request", "data", "hex"):
            if key in p:
                bytes.fromhex(p[key].replace(" ", ""))
        values = {}
        for name, edit in self._value_edits.items():
            text = edit.text().strip()
            if text:
                try:
                    values[name] = float(text) if "." in text else int(text, 0)
                except ValueError:
                    values[name] = text   # Wertetabelle
        if values:
            p["values"] = values
        if self._expect.currentData() == "nrc":
            nrc = int(self._nrc.text().strip() or "x", 0)
            p["expect"] = f"nrc:0x{nrc:02X}"
        return p

    def params(self) -> dict:
        return dict(getattr(self, "_result", self._params))

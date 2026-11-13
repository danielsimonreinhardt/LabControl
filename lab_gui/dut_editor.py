"""Einstellungsabschnitt "Pruefling" (FT-1 UDS, FP-12 a): CAN-Interface, ODX-Datei von neXtBSW diaggen,
Name der Schluesseldatei im gemeinsamen Ordner. Abläufe nennen nur UDS-Schritte, nicht diese Angaben."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit

from i18n import tr
from icons import IconButton


class DutEditor(QGroupBox):
    config_edited = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._can_ids: dict[str, str] = {}
        self._filling = False
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self._labels = {key: QLabel() for key in ("name", "can", "odx", "key")}
        self._name = QLineEdit()
        self._name.setMinimumWidth(220)
        self._name.editingFinished.connect(self._emit)
        form.addRow(self._labels["name"], self._name)
        self._can = QComboBox()
        self._can.setMinimumWidth(220)
        self._can.currentIndexChanged.connect(lambda _i: self._emit())
        form.addRow(self._labels["can"], self._can)
        odx_row = QHBoxLayout()
        self._odx = QLineEdit()
        self._odx.setMinimumWidth(320)
        self._odx.setPlaceholderText("…/build/evalboard_s32k144_demo.pdx")
        self._odx.editingFinished.connect(self._emit)
        self._odx_browse = IconButton("mdi.folder-open-outline", "")
        self._odx_browse.clicked.connect(self._browse)
        odx_row.addWidget(self._odx, 1)
        odx_row.addWidget(self._odx_browse)
        form.addRow(self._labels["odx"], odx_row)
        self._key = QLineEdit()
        self._key.setPlaceholderText("evalboard_s32k144")
        self._key.editingFinished.connect(self._emit)
        form.addRow(self._labels["key"], self._key)
        self._status = QLabel()
        self._status.setWordWrap(True)
        form.addRow("", self._status)
        self._form = form
        self.retranslate()

    def retranslate(self) -> None:
        self.setTitle(tr("Prüfling (UDS)"))
        for key, text in (("name", tr("Name:")), ("can", tr("CAN-Interface:")), ("odx", tr("ODX/PDX:")),
                          ("key", tr("Schlüssel:"))):
            self._labels[key].setText(text)
        self._odx.setToolTip(tr("Export von neXtBSW: python -m tools.diaggen pdx <projekt> -o <datei>.pdx; "
                                "leer im Simulationsmodus = nachgebildetes Steuergerät"))
        self._key.setToolTip(tr("Datei <gemeinsamer Ordner>/keys/<Name>.key (64 Hex-Zeichen)"))
        self._odx_browse.setToolTip(tr("ODX-Datei wählen…"))
        self._fill_can(self._can.currentData() or "")

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        if kind == "can" and not device_id.startswith("@"):
            self._can_ids[device_id] = label
            self._fill_can(self._can.currentData() or "")

    def set_config(self, config: dict) -> None:
        self._filling = True
        self._name.setText(config.get("name", ""))
        self._odx.setText(config.get("odx", ""))
        self._key.setText(config.get("key", ""))
        self._fill_can(config.get("can_device", ""))
        self._filling = False

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def _fill_can(self, current: str) -> None:
        filling, self._filling = self._filling, True
        self._can.clear()
        self._can.addItem(tr("automatisch"), "")
        ids = dict(self._can_ids)
        if current and current not in ids:
            ids[current] = current
        for device_id, label in sorted(ids.items(), key=lambda i: i[1].lower()):
            self._can.addItem(f"{label} ({device_id})", device_id)
        self._can.setCurrentIndex(max(0, self._can.findData(current)))
        self._filling = filling

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("ODX-Datei wählen…"), self._odx.text(),
                                              tr("ODX (*.pdx *.odx-d *.odx);;Alle Dateien (*)"))
        if path:
            self._odx.setText(path)
            self._emit()

    def _emit(self) -> None:
        if self._filling:
            return
        self.config_edited.emit({"name": self._name.text().strip(), "can_device": self._can.currentData() or "",
                                 "odx": self._odx.text().strip(), "key": self._key.text().strip()})

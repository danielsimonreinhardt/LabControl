"""Messmittel-Tabelle in den Einstellungen (FT-8, 0.59.1): Kennung je Geraet (vom Geraet gemeldet) und
Inventarnummer, Kalibrierdatum, Faelligkeit (vom Nutzer, gespeichert in messmittel.json im gemeinsamen
Ordner, damit PC und Pi denselben Stand sehen)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView, QTableWidget, \
    QTableWidgetItem, QVBoxLayout

from i18n import tr
from icons import IconButton
from instruments import Instruments, valid_date

COLUMNS = ("Gerät", "Typ", "Seriennr.", "Firmware", "Inventar-Nr.", "Kalibriert", "Fällig")
EDITABLE = {4: "inventory", 5: "calibrated", 6: "due"}
ROLE_ID = Qt.ItemDataRole.UserRole


class InstrumentsEditor(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._instruments: Instruments | None = None
        self._labels: dict[str, str] = {}
        self._filling = False
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.setMinimumHeight(140)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self._refresh_button = IconButton("mdi.refresh", "")
        self._refresh_button.clicked.connect(self.refresh)
        row.addWidget(self._refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.retranslate()

    def attach(self, instruments: Instruments) -> None:
        self._instruments = instruments
        self.refresh()

    def retranslate(self) -> None:
        self.setTitle(tr("Messmittel"))
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        self._refresh_button.setToolTip(tr("Kennungen und Kalibrierdaten neu lesen"))
        for column in EDITABLE:
            if column in (5, 6):
                self.table.horizontalHeaderItem(column).setToolTip(tr("Datum als JJJJ-MM-TT"))

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        if device_id.startswith("@") or kind in ("can", "jlink"):
            return   # Partnergeraete pflegt ihr Besitzer; CAN-Adapter und J-Link messen nicht
        self._labels[device_id] = label
        self.refresh()

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self.on_device_known(kind, device_id, label)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        if self._instruments is None:
            return
        self._instruments.reload()
        ids = sorted(set(self._labels) | {d for d in self._instruments.known_ids()
                                          if not d.startswith(("@", "can:", "jlink:"))},
                     key=lambda d: self._labels.get(d, d).lower())
        self._filling = True
        self.table.setRowCount(len(ids))
        for row, device_id in enumerate(ids):
            identity = self._instruments.identity(device_id)
            cal = self._instruments.calibration(device_id)
            values = (self._labels.get(device_id, device_id), identity.get("model", ""), identity.get("serial", ""),
                      identity.get("firmware", ""), cal.get("inventory", ""), cal.get("calibrated", ""),
                      cal.get("due", ""))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(ROLE_ID, device_id)
                if column not in EDITABLE:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setToolTip(device_id)
                self.table.setItem(row, column, item)
        self._filling = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or self._instruments is None or item.column() not in EDITABLE:
            return
        device_id = item.data(ROLE_ID)
        key = EDITABLE[item.column()]
        value = item.text().strip()
        if key in ("calibrated", "due") and not valid_date(value):
            self._filling = True
            item.setText(self._instruments.calibration(device_id).get(key, ""))
            item.setBackground(QBrush(QColor("#c0392b")))
            item.setToolTip(tr("Datum als JJJJ-MM-TT"))
            self._filling = False
            return
        try:
            self._instruments.set_calibration(device_id, **{key: value})
        except (OSError, ValueError) as exc:
            item.setToolTip(str(exc))
            return
        item.setBackground(QBrush())
        item.setToolTip("")

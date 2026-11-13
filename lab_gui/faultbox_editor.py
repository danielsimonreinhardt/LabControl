"""Einstellungsabschnitt "Fehlerbox" (FT-9, 0.61.0): Fehler als Tabelle (Name, Schaltfolge, Wartezeit, Beschreibung),
gespeichert in faultbox.json im gemeinsamen Ordner. "Uebernehmen" prueft die Schaltfolgen (faultbox.parse_sequence)
und speichert; ein Fehler bleibt mit seiner Meldung stehen."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import faultbox
from i18n import tr
from icons import IconButton
from theme import set_tone

COLUMNS = ("Name", "Schaltfolge", "Wartezeit (ms)", "Beschreibung")


class FaultBoxEditor(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._service = None
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.setMinimumHeight(120)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self._add = IconButton("mdi.plus", "")
        self._add.clicked.connect(self._add_row)
        self._remove = IconButton("mdi.minus", "")
        self._remove.clicked.connect(self._remove_row)
        self._apply = IconButton("mdi.check", "")
        self._apply.clicked.connect(self.apply)
        self._status = QLabel()
        self._status.setWordWrap(True)
        row.addWidget(self._add)
        row.addWidget(self._remove)
        row.addWidget(self._apply)
        row.addWidget(self._status, 1)
        layout.addLayout(row)
        self._hil_by_name: dict[str, str] = {}
        self.retranslate()

    def attach(self, service) -> None:
        self._service = service
        self.load()

    def retranslate(self) -> None:
        self.setTitle(tr("Fehlerbox (microHIL)"))
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        self.table.horizontalHeaderItem(1).setToolTip(
            tr("R<n>=0|1 Relais, O<n>=0|1 Digitalausgang, A<n>=<mV> Analogausgang; z. B. R1=1, O3=1"))
        self._add.setToolTip(tr("Fehler hinzufügen"))
        self._remove.setToolTip(tr("Markierten Fehler entfernen"))
        self._apply.setToolTip(tr("Prüfen und speichern"))

    def load(self) -> None:
        if self._service is None:
            return
        faults = self._service.faults()
        self._hil_by_name = {f.name: f.hil for f in faults}
        self.table.setRowCount(0)
        for fault in faults:
            self._append(fault.name, fault.sequence, str(fault.settle_ms), fault.description)
        error = self._service.load_error()
        self._status.setText(error or tr("{n} Fehler", n=len(faults)))
        set_tone(self._status, "danger" if error else "muted")

    def _append(self, *values: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(value))

    def _add_row(self) -> None:
        self._append(tr("Fehler {n}", n=self.table.rowCount() + 1), "R1=1", str(faultbox.DEFAULT_SETTLE_MS), "")
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 1))

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def faults(self) -> list[faultbox.Fault]:
        result = []
        for row in range(self.table.rowCount()):
            def cell(column: int) -> str:
                item = self.table.item(row, column)
                return item.text().strip() if item is not None else ""
            try:
                settle = int(cell(2) or faultbox.DEFAULT_SETTLE_MS)
            except ValueError:
                raise faultbox.FaultError(tr("Zeile {n}: Wartezeit ist keine Zahl", n=row + 1)) from None
            name = cell(0)
            result.append(faultbox.Fault(name=name, sequence=cell(1), hil=self._hil_by_name.get(name, ""),
                                         settle_ms=settle, description=cell(3)))
        return result

    def apply(self) -> bool:
        if self._service is None:
            return False
        try:
            self._service.save(self.faults())
        except (faultbox.FaultError, OSError, ValueError) as exc:
            self._status.setText(str(exc))
            set_tone(self._status, "danger")
            return False
        self.load()
        return True

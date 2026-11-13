"""Einstellung der Verriegelung Netzteil/Last (FP-7 b, run_session.Interlock): Paare aus Netzteil und
Last, die im Testablauf nicht gleichzeitig eingeschaltet sein duerfen. Nur Geraete dieses Rechners
(FP-8 a); ein Ablauf kann die Verriegelung mit "Quelle/Senke" aufheben (testcase_properties.py)."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QVBoxLayout, QWidget

from i18n import tr
from icons import IconButton


class InterlockEditor(QGroupBox):
    groups_edited = Signal(list)   # [[psu_id, load_id], ...]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._devices: dict[str, tuple[str, str]] = {}   # device_id -> (kind, label)
        self._groups: list[list[str]] = []
        layout = QVBoxLayout(self)
        self._rows_layout = QVBoxLayout()
        layout.addLayout(self._rows_layout)
        add_row = QHBoxLayout()
        self._add_button = IconButton("mdi.plus", "")
        self._add_button.clicked.connect(self._add_pair)
        add_row.addWidget(self._add_button)
        add_row.addStretch(1)
        layout.addLayout(add_row)
        self._rows: list[QWidget] = []
        self.retranslate()

    def retranslate(self) -> None:
        self.setTitle(tr("Verriegelung Netzteil/Last im Testablauf"))
        self._add_button.setToolTip(tr("Paar hinzufügen"))
        self._rebuild()

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        if kind not in ("psu", "load") or device_id.startswith("@"):
            return
        self._devices[device_id] = (kind, label)
        self._rebuild()

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self.on_device_known(kind, device_id, label)

    def set_groups(self, groups: list[list[str]]) -> None:
        self._groups = [list(g) for g in groups]
        self._rebuild()

    def groups(self) -> list[list[str]]:
        return [list(g) for g in self._groups]

    # -- intern ---------------------------------------------------------------

    def _ids(self, kind: str) -> list[str]:
        return sorted((d for d, (k, _l) in self._devices.items() if k == kind),
                      key=lambda d: self._devices[d][1].lower())

    def _combo(self, kind: str, current: str) -> QComboBox:
        combo = QComboBox()
        ids = self._ids(kind)
        if current and current not in ids:
            ids.append(current)   # gespeichert, aber (noch) nicht bekannt
        for device_id in ids:
            combo.addItem(self._devices.get(device_id, (kind, device_id))[1], device_id)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        return combo

    def _rebuild(self) -> None:
        for row in self._rows:
            row.deleteLater()
        self._rows = []
        for index, group in enumerate(self._groups):
            psu = next((d for d in group if d.startswith("psu:")), "")
            load = next((d for d in group if d.startswith("load:")), "")
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            psu_combo = self._combo("psu", psu)
            load_combo = self._combo("load", load)
            remove = IconButton("mdi.minus", "")
            remove.setToolTip(tr("Paar entfernen"))
            psu_combo.currentIndexChanged.connect(
                lambda _i, i=index, c=psu_combo, o=load_combo: self._set_pair(i, c.currentData(), o.currentData()))
            load_combo.currentIndexChanged.connect(
                lambda _i, i=index, c=psu_combo, o=load_combo: self._set_pair(i, c.currentData(), o.currentData()))
            remove.clicked.connect(lambda _c=False, i=index: self._remove_pair(i))
            row_layout.addWidget(psu_combo, 1)
            row_layout.addWidget(load_combo, 1)
            row_layout.addWidget(remove)
            self._rows_layout.addWidget(row)
            self._rows.append(row)
        self._add_button.setEnabled(bool(self._ids("psu")) and bool(self._ids("load")))

    def _set_pair(self, index: int, psu: str, load: str) -> None:
        if 0 <= index < len(self._groups) and psu and load:
            self._groups[index] = [psu, load]
            self.groups_edited.emit(self.groups())

    def _add_pair(self) -> None:
        psus, loads = self._ids("psu"), self._ids("load")
        if not psus or not loads:
            return
        self._groups.append([psus[0], loads[0]])
        self._rebuild()
        self.groups_edited.emit(self.groups())

    def _remove_pair(self, index: int) -> None:
        if 0 <= index < len(self._groups):
            del self._groups[index]
            self._rebuild()
            self.groups_edited.emit(self.groups())

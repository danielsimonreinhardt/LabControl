"""Varianten bearbeiten (FT-5, PI-55 a): Dateien im gemeinsamen Ordner "varianten" anlegen, aendern, loeschen.
Je Variante Rollen (Name -> Geraet) und Parameter (Name -> Zahl). Siehe variants.py."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import variants
from i18n import tr
from icons import IconButton


class VariantDialog(QDialog):
    def __init__(self, folder: Path, known_devices: dict[str, tuple[str, str]], parent=None) -> None:
        """known_devices: device_id -> (kind, label) fuer die Geraeteauswahl der Rollen."""
        super().__init__(parent)
        self.setWindowTitle(tr("Varianten"))
        self.resize(820, 520)
        self._folder = folder
        self._known = dict(known_devices)
        self._current: variants.Variant | None = None
        self._dirty = False
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        self._list = QListWidget()
        self._list.setMinimumWidth(200)
        self._list.currentItemChanged.connect(self._on_selected)
        left.addWidget(self._list, 1)
        list_buttons = QHBoxLayout()
        self._new_button = IconButton("mdi.plus", tr("Neue Variante"))
        self._copy_button = IconButton("mdi.content-copy", tr("Variante kopieren"))
        self._delete_button = IconButton("mdi.delete-outline", tr("Variante löschen"))
        self._new_button.clicked.connect(self._new_variant)
        self._copy_button.clicked.connect(self._copy_variant)
        self._delete_button.clicked.connect(self._delete_variant)
        for button in (self._new_button, self._copy_button, self._delete_button):
            list_buttons.addWidget(button)
        list_buttons.addStretch(1)
        left.addLayout(list_buttons)
        layout.addLayout(left)

        right = QVBoxLayout()
        form = QFormLayout()
        self._name = QLineEdit()
        self._description = QLineEdit()
        form.addRow(tr("Name:"), self._name)
        form.addRow(tr("Beschreibung:"), self._description)
        right.addLayout(form)

        right.addWidget(QLabel(tr("Rollen")))
        self._roles = QTableWidget(0, 2)
        self._roles.setHorizontalHeaderLabels([tr("Rolle"), tr("Gerät")])
        self._roles.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._roles.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        right.addWidget(self._roles, 1)
        right.addLayout(self._row_buttons(self._roles, self._add_role))

        right.addWidget(QLabel(tr("Parameter")))
        self._vars = QTableWidget(0, 2)
        self._vars.setHorizontalHeaderLabels([tr("Name"), tr("Wert")])
        self._vars.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._vars.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        right.addWidget(self._vars, 1)
        right.addLayout(self._row_buttons(self._vars, self._add_var))

        self._error = QLabel()
        self._error.setWordWrap(True)
        right.addWidget(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self._save)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        holder = QWidget()
        holder.setLayout(right)
        layout.addWidget(holder, 1)
        self._editor = holder
        self._reload()

    # -- Liste -------------------------------------------------------------------------------

    def _row_buttons(self, table: QTableWidget, add) -> QHBoxLayout:
        row = QHBoxLayout()
        plus = IconButton("mdi.plus", tr("Zeile hinzufügen"))
        minus = IconButton("mdi.minus", tr("Zeile entfernen"))
        plus.clicked.connect(lambda: add("", ""))
        minus.clicked.connect(lambda: table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None)
        row.addWidget(plus)
        row.addWidget(minus)
        row.addStretch(1)
        return row

    def _reload(self, select: str = "") -> None:
        found, problems = variants.list_variants(self._folder)
        self._list.blockSignals(True)
        self._list.clear()
        for variant in found:
            item = QListWidgetItem(variant.name)
            item.setData(Qt.ItemDataRole.UserRole, variant)
            item.setToolTip(variant.description or str(variant.path))
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._error.setText("\n".join(problems))
        target = next((i for i in range(self._list.count()) if self._list.item(i).text() == select), 0)
        if self._list.count():
            self._list.setCurrentRow(target)
        else:
            self._show(None)

    def _on_selected(self, item, _previous=None) -> None:
        self._show(item.data(Qt.ItemDataRole.UserRole) if item is not None else None)

    def _show(self, variant: variants.Variant | None) -> None:
        self._current = variant
        self._editor.setEnabled(variant is not None)
        self._copy_button.setEnabled(variant is not None)
        self._delete_button.setEnabled(variant is not None)
        self._name.setText(variant.name if variant else "")
        self._description.setText(variant.description if variant else "")
        self._roles.setRowCount(0)
        self._vars.setRowCount(0)
        for role, device in (variant.roles.items() if variant else []):
            self._add_role(role, device)
        for name, value in (variant.vars.items() if variant else []):
            self._add_var(name, f"{value:g}")

    def _add_role(self, role: str, device: str) -> None:
        row = self._roles.rowCount()
        self._roles.insertRow(row)
        self._roles.setItem(row, 0, QTableWidgetItem(role))
        combo = QComboBox()
        combo.setEditable(True)
        for device_id, (kind, label) in sorted(self._known.items(), key=lambda kv: kv[1][1].lower()):
            combo.addItem(f"{label} ({device_id})", device_id)
        index = combo.findData(device)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(device)
        self._roles.setCellWidget(row, 1, combo)

    def _add_var(self, name: str, value: str) -> None:
        row = self._vars.rowCount()
        self._vars.insertRow(row)
        self._vars.setItem(row, 0, QTableWidgetItem(name))
        self._vars.setItem(row, 1, QTableWidgetItem(value))

    # -- Bearbeiten --------------------------------------------------------------------------

    def _collect(self) -> variants.Variant:
        roles = {}
        for row in range(self._roles.rowCount()):
            role = (self._roles.item(row, 0).text() if self._roles.item(row, 0) else "").strip()
            combo = self._roles.cellWidget(row, 1)
            index = combo.findText(combo.currentText())
            device = (combo.itemData(index) if index >= 0 else combo.currentText()).strip()
            if role or device:
                roles[role] = device
        values = {}
        for row in range(self._vars.rowCount()):
            name = (self._vars.item(row, 0).text() if self._vars.item(row, 0) else "").strip()
            value = (self._vars.item(row, 1).text() if self._vars.item(row, 1) else "").strip()
            if name or value:
                values[name] = value
        data = {"name": self._name.text().strip(), "description": self._description.text().strip(),
                "roles": roles, "vars": values}
        variant = variants.parse_variant(data)
        variant.path = self._current.path if self._current is not None else None
        return variant

    def _save(self) -> None:
        try:
            variant = self._collect()
            if variant.path is not None and variant.path.stem != Path(variants.file_name(variant.name)).stem:
                old = variant.path
                variant.path = None
                path = variants.save_variant(variant, self._folder)
                if old != path:
                    old.unlink(missing_ok=True)       # umbenannt
            else:
                variants.save_variant(variant, self._folder)
        except (OSError, ValueError) as exc:
            self._error.setText(str(exc))
            return
        self._error.setText(tr("Gespeichert: {path}", path=str(variant.path)))
        self._reload(variant.name)

    def _new_variant(self) -> None:
        names = {self._list.item(i).text() for i in range(self._list.count())}
        name = next(f"{tr('Variante')} {n}" for n in range(1, 1000) if f"{tr('Variante')} {n}" not in names)
        try:
            variants.save_variant(variants.Variant(name), self._folder)
        except OSError as exc:
            self._error.setText(str(exc))
            return
        self._reload(name)

    def _copy_variant(self) -> None:
        if self._current is None:
            return
        try:
            variant = self._collect()
        except ValueError as exc:
            self._error.setText(str(exc))
            return
        variant.name, variant.path = f"{variant.name} ({tr('Kopie')})", None
        try:
            variants.save_variant(variant, self._folder)
        except OSError as exc:
            self._error.setText(str(exc))
            return
        self._reload(variant.name)

    def _delete_variant(self) -> None:
        if self._current is None or self._current.path is None:
            return
        if QMessageBox.question(self, tr("Variante löschen"),
                                tr("Variante „{name}“ löschen?", name=self._current.name)) \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self._current.path.unlink()
        except OSError as exc:
            self._error.setText(str(exc))
            return
        self._reload()


class RefsDialog(QDialog):
    """Zahlenfelder eines Schritts aus Parametern (variants.REF_FIELDS); leer = fester Wert der Zeile."""

    FIELDS = ("value", "duration", "check_min", "check_max")

    def __init__(self, refs: dict[str, str], names: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Werte aus Parametern"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._combos: dict[str, QComboBox] = {}
        for field in self.FIELDS:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItem("", "")
            for name in names:
                combo.addItem(name, name)
            combo.setEditText(refs.get(field, ""))
            combo.setMinimumWidth(200)
            self._combos[field] = combo
            form.addRow(tr(variants.REF_FIELDS[field]) + ":", combo)
        layout.addLayout(form)
        self._error = QLabel()
        layout.addWidget(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        for combo in self._combos.values():
            name = combo.currentText().strip()
            if name and not variants.valid_var(name):
                self._error.setText(tr("Ungültiger Parametername: {name}", name=name))
                return
        self.accept()

    def refs(self) -> dict[str, str]:
        return {field: combo.currentText().strip() for field, combo in self._combos.items()
                if combo.currentText().strip()}

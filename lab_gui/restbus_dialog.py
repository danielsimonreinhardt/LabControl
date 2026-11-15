"""Restbus eines CAN-Interfaces bearbeiten und bedienen (FT-6, PI-56 c): Botschaften aus der DBC waehlen, Zyklus,
Signalwerte (live, solange der Restbus laeuft), E2E je Botschaft, E2E-Angaben aus einem neXtBSW-Projekt uebernehmen.
Dazu der Dialog eines Ablaufschritts "Restbus-Signal setzen". Siehe device-driver/can_bus/restbus.py."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from i18n import tr
from icons import IconButton

E2E_PROFILES = ("", "P01", "P02", "P11", "COUNTER")


def load_db(path: str):
    from can_bus.dbc import load_dbc
    return load_dbc(path) if path else None


def rte_qualifiers(nextbsw_dir: str, project: str) -> dict:
    """E2E-Angaben aus <neXtBSW>/projects/<project>/rte.yaml, gelesen mit dem Python des neXtBSW-Ordners
    (dort gibt es PyYAML; LabControl braucht so keine weitere Abhaengigkeit)."""
    from can_bus.restbus import import_e2e_from_rte
    from canflash_service import nextbsw_python

    rte = Path(nextbsw_dir).expanduser() / "projects" / project / "rte.yaml"
    if not rte.is_file():
        raise ValueError(tr("rte.yaml nicht gefunden: {path}", path=str(rte)))
    code = "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))"
    proc = subprocess.run([str(nextbsw_python(nextbsw_dir)), "-c", code, str(rte)], capture_output=True, text=True,
                          encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "rte.yaml unlesbar")
    return import_e2e_from_rte(json.loads(proc.stdout))


class RestbusDialog(QDialog):
    """Nicht-modal: Aenderungen an Werten gehen sofort an den laufenden Restbus (signal_set), die Auswahl der
    Botschaften/E2E beim Speichern in die Einstellungen (config_saved)."""

    config_saved = Signal(str, dict)                  # can_id, Konfiguration
    signal_set = Signal(str, str, str, str)           # can_id, Botschaft, Signal, Wert (Text)
    running_requested = Signal(str, bool)             # can_id, an/aus

    def __init__(self, can_id: str, label: str, config: dict, interface_dbc: str, nextbsw: tuple[str, str],
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Restbus – {name}", name=label))
        self.resize(900, 600)
        self._can_id = can_id
        self._interface_dbc = interface_dbc
        self._nextbsw = nextbsw
        self._db = None
        self._config = {m["name"]: dict(m) for m in (config or {}).get("messages", []) if m.get("name")}
        self._state: dict = {}
        self._filling = False
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self._dbc = QLineEdit((config or {}).get("dbc", ""))
        self._dbc.setPlaceholderText(interface_dbc or tr("DBC des Interfaces"))
        self._dbc.editingFinished.connect(self._load)
        browse = IconButton("mdi.folder-open-outline", tr("DBC-Datei wählen…"))
        browse.clicked.connect(self._browse)
        self._run_button = IconButton("mdi.play", tr("Restbus starten"))
        self._run_button.setCheckable(True)
        self._run_button.toggled.connect(lambda on: self.running_requested.emit(self._can_id, on))
        self._import_button = IconButton("mdi.import", tr("E2E aus dem neXtBSW-Projekt übernehmen"))
        self._import_button.clicked.connect(self._import_e2e)
        top.addWidget(QLabel(tr("DBC:")))
        top.addWidget(self._dbc, 1)
        top.addWidget(browse)
        top.addWidget(self._import_button)
        top.addWidget(self._run_button)
        layout.addLayout(top)

        self._messages = QTableWidget(0, 5)
        self._messages.setHorizontalHeaderLabels([tr("Senden"), tr("Botschaft"), tr("ID"), tr("Zyklus (ms)"), tr("E2E")])
        self._messages.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._messages.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._messages.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._messages.currentCellChanged.connect(lambda row, *_: self._show_message(row))
        layout.addWidget(self._messages, 2)

        detail = QHBoxLayout()
        self._signals = QTableWidget(0, 4)
        self._signals.setHorizontalHeaderLabels([tr("Signal"), tr("Wert"), tr("Einheit"), tr("Bereich")])
        self._signals.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._signals.itemChanged.connect(self._on_value_edited)
        detail.addWidget(self._signals, 3)
        e2e_box = QWidget()
        form = QFormLayout(e2e_box)
        self._profile = QComboBox()
        for profile in E2E_PROFILES:
            self._profile.addItem(profile or tr("ohne"), profile)
        self._data_id = QLineEdit()
        self._data_id.setPlaceholderText("0x0210")
        self._mode = QComboBox()
        for mode in ("BOTH", "ALT", "LOW", "NIBBLE"):
            self._mode.addItem(mode, mode)
        self._crc = QComboBox()
        self._counter = QComboBox()
        self._nibble = QComboBox()
        self._id_list = QLineEdit()
        self._id_list.setPlaceholderText(tr("16 Werte, Komma"))
        form.addRow(tr("Profil:"), self._profile)
        form.addRow(tr("Data ID:"), self._data_id)
        form.addRow(tr("Data-ID-Modus:"), self._mode)
        form.addRow(tr("CRC:"), self._crc)
        form.addRow(tr("Zähler:"), self._counter)
        form.addRow(tr("ID-Nibble:"), self._nibble)
        form.addRow(tr("Data-ID-Liste:"), self._id_list)
        for widget in (self._profile, self._mode, self._crc, self._counter, self._nibble):
            widget.currentIndexChanged.connect(lambda _i: self._store_e2e())
        for widget in (self._data_id, self._id_list):
            widget.editingFinished.connect(self._store_e2e)
        detail.addWidget(e2e_box, 2)
        layout.addLayout(detail, 2)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self._save)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self._load()

    # -- DBC und Botschaften ---------------------------------------------------------------------

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("DBC-Datei wählen…"), self._dbc.text() or self._interface_dbc,
                                              tr("DBC (*.dbc);;Alle Dateien (*)"))
        if path:
            self._dbc.setText(path)
            self._load()

    def _load(self) -> None:
        from can_bus.restbus import messages_overview
        path = self._dbc.text().strip() or self._interface_dbc
        try:
            self._db = load_db(path)
        except Exception as exc:  # noqa: BLE001 -- jede kaputte Datei ist ein Bedienfehler
            self._db = None
            self._status.setText(str(exc))
        self._overview = messages_overview(self._db) if self._db is not None else []
        if self._db is None and not path:
            self._status.setText(tr("Keine DBC – hier oder beim CAN-Interface eintragen"))
        self._filling = True
        self._messages.setRowCount(0)
        for info in self._overview:
            row = self._messages.rowCount()
            self._messages.insertRow(row)
            stored = self._config.get(info["name"], {})
            box = QCheckBox()
            box.setChecked(bool(stored) and stored.get("enabled", True))
            box.toggled.connect(lambda on, name=info["name"]: self._set_enabled(name, on))
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(6, 0, 0, 0)
            hl.addWidget(box)
            self._messages.setCellWidget(row, 0, holder)
            name_item = QTableWidgetItem(info["name"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if info["senders"]:
                name_item.setToolTip(tr("Sender: {nodes}", nodes=", ".join(info["senders"])))
            self._messages.setItem(row, 1, name_item)
            id_item = QTableWidgetItem(f"0x{info['id']:X}")
            id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._messages.setItem(row, 2, id_item)
            cycle = QSpinBox()
            cycle.setRange(10, 60000)
            cycle.setValue(int(stored.get("cycle_ms") or info["cycle_ms"]))
            cycle.valueChanged.connect(lambda v, name=info["name"]: self._set_cycle(name, v))
            self._messages.setCellWidget(row, 3, cycle)
            e2e_item = QTableWidgetItem((stored.get("e2e") or {}).get("profile", ""))
            e2e_item.setFlags(e2e_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._messages.setItem(row, 4, e2e_item)
        self._filling = False
        if self._overview:
            self._messages.setCurrentCell(0, 1)
            self._show_message(0)

    def _entry(self, name: str) -> dict:
        info = next(i for i in self._overview if i["name"] == name)
        return self._config.setdefault(name, {"name": name, "enabled": False, "cycle_ms": info["cycle_ms"],
                                              "values": {}})

    def _set_enabled(self, name: str, on: bool) -> None:
        self._entry(name)["enabled"] = on

    def _set_cycle(self, name: str, value: int) -> None:
        self._entry(name)["cycle_ms"] = value

    def _current(self) -> dict | None:
        row = self._messages.currentRow()
        return self._overview[row] if 0 <= row < len(self._overview) else None

    def _show_message(self, row: int) -> None:
        if not 0 <= row < len(self._overview):
            return
        info = self._overview[row]
        stored = self._config.get(info["name"], {})
        live = (self._state.get("messages") or {}).get(info["name"], {}).get("values", {})
        self._filling = True
        self._signals.setRowCount(0)
        for sig in info["signals"]:
            r = self._signals.rowCount()
            self._signals.insertRow(r)
            name = QTableWidgetItem(sig["name"])
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value = live.get(sig["name"], (stored.get("values") or {}).get(sig["name"], sig["initial"]))
            if sig["choices"] and isinstance(value, (int, float)) and int(value) in sig["choices"]:
                text = sig["choices"][int(value)]
            else:
                text = f"{value:g}" if isinstance(value, (int, float)) else str(value)
            self._signals.setItem(r, 0, name)
            self._signals.setItem(r, 1, QTableWidgetItem(text))
            unit = QTableWidgetItem(sig["unit"])
            unit.setFlags(unit.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._signals.setItem(r, 2, unit)
            limits = f"{sig['minimum']:g} … {sig['maximum']:g}" if sig["minimum"] is not None and \
                sig["maximum"] is not None else ""
            span = QTableWidgetItem(limits)
            span.setFlags(span.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if sig["choices"]:
                span.setToolTip(", ".join(f"{k}={v}" for k, v in sig["choices"].items()))
            self._signals.setItem(r, 3, span)
        names = [""] + [s["name"] for s in info["signals"]]
        e2e = stored.get("e2e") or {}
        for combo, key in ((self._crc, "crc"), (self._counter, "counter"), (self._nibble, "data_id_nibble")):
            combo.clear()
            for n in names:
                combo.addItem(n or "–", n)
            combo.setCurrentIndex(max(0, combo.findData(e2e.get(key, ""))))
        self._profile.setCurrentIndex(max(0, self._profile.findData(e2e.get("profile", ""))))
        self._mode.setCurrentIndex(max(0, self._mode.findData(e2e.get("data_id_mode", "BOTH"))))
        self._data_id.setText(f"0x{int(e2e['data_id']):04X}" if "data_id" in e2e else "")
        self._id_list.setText(", ".join(str(v) for v in e2e.get("data_id_list", [])))
        self._filling = False

    def _on_value_edited(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 1:
            return
        info = self._current()
        if info is None:
            return
        signal = self._signals.item(item.row(), 0).text()
        text = item.text().strip()
        self._entry(info["name"]).setdefault("values", {})[signal] = text
        self.signal_set.emit(self._can_id, info["name"], signal, text)

    def _store_e2e(self) -> None:
        if self._filling:
            return
        info = self._current()
        if info is None:
            return
        entry = self._entry(info["name"])
        profile = self._profile.currentData() or ""
        if not profile:
            entry.pop("e2e", None)
        else:
            e2e = {"profile": profile, "counter": self._counter.currentData() or ""}
            if profile != "COUNTER":
                e2e["crc"] = self._crc.currentData() or ""
                try:
                    e2e["data_id"] = int(self._data_id.text().strip() or "0", 0)
                except ValueError:
                    self._status.setText(tr("Data ID ist keine Zahl"))
                    return
            if profile in ("P01", "P11"):
                e2e["data_id_mode"] = self._mode.currentData()
                if e2e["data_id_mode"] == "NIBBLE":
                    e2e["data_id_nibble"] = self._nibble.currentData() or ""
            if profile == "P02":
                try:
                    e2e["data_id_list"] = [int(v.strip(), 0) for v in self._id_list.text().split(",") if v.strip()]
                except ValueError:
                    self._status.setText(tr("Data-ID-Liste: nur Zahlen"))
                    return
            entry["e2e"] = e2e
        row = self._messages.currentRow()
        self._messages.item(row, 4).setText(profile)

    def _import_e2e(self) -> None:
        nextbsw, project = self._nextbsw
        if not nextbsw or not project:
            self._status.setText(tr("neXtBSW-Ordner und Projekt in Einstellungen → Prüfling eintragen"))
            return
        try:
            found = rte_qualifiers(nextbsw, project)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self._status.setText(str(exc))
            return
        names = {i["name"] for i in self._overview}
        used = [name for name in found if name in names]
        for name in used:
            self._entry(name)["e2e"] = found[name]
        self._load()
        self._status.setText(tr("E2E übernommen: {names}", names=", ".join(used) or "–"))

    # -- Speichern und Zustand --------------------------------------------------------------------

    def config(self) -> dict:
        messages = [m for m in self._config.values() if m.get("enabled") or m.get("e2e") or m.get("values")]
        return {"dbc": self._dbc.text().strip(), "messages": sorted(messages, key=lambda m: m["name"])}

    def _save(self) -> None:
        self._store_e2e()
        self.config_saved.emit(self._can_id, self.config())
        self._status.setText(tr("Gespeichert"))

    def set_state(self, state: dict) -> None:
        """Zustand vom Worker (laeuft, Zaehler, Fehler)."""
        self._state = dict(state or {})
        running = bool(self._state.get("running"))
        self._run_button.blockSignals(True)
        self._run_button.setChecked(running)
        self._run_button.blockSignals(False)
        self._run_button.setToolTip(tr("Restbus stoppen") if running else tr("Restbus starten"))
        errors = self._state.get("errors") or []
        if errors:
            self._status.setText("; ".join(errors))


class RestbusStepDialog(QDialog):
    """Ablaufschritt "Restbus-Signal setzen": Botschaft, Signal, Wert."""

    def __init__(self, params: dict, db, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Restbus-Signal setzen"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._db = db
        self._message = QComboBox()
        self._message.setEditable(True)
        for message in sorted(db.messages, key=lambda m: m.frame_id) if db is not None else []:
            self._message.addItem(message.name, message.name)
        self._message.setCurrentText(str(params.get("message", "")))
        self._signal = QComboBox()
        self._signal.setEditable(True)
        self._value = QLineEdit(str(params.get("value", "")))
        form.addRow(tr("Botschaft:"), self._message)
        form.addRow(tr("Signal:"), self._signal)
        form.addRow(tr("Wert:"), self._value)
        layout.addLayout(form)
        self._info = QLabel()
        layout.addWidget(self._info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._message.currentTextChanged.connect(lambda _t: self._fill_signals())
        self._fill_signals(str(params.get("signal", "")))

    def _fill_signals(self, current: str = "") -> None:
        current = current or self._signal.currentText()
        self._signal.clear()
        try:
            message = self._db.get_message_by_name(self._message.currentText()) if self._db is not None else None
        except KeyError:
            message = None
        for sig in message.signals if message is not None else []:
            self._signal.addItem(sig.name, sig.name)
        self._signal.setCurrentText(current)

    def _accept(self) -> None:
        params = self.params()
        if not params.get("message") or not params.get("signal"):
            self._info.setText(tr("Botschaft und Signal angeben"))
            return
        self.accept()

    def params(self) -> dict:
        result = {"message": self._message.currentText().strip(), "signal": self._signal.currentText().strip()}
        text = self._value.text().strip()
        if text:
            try:
                result["value"] = float(text.replace(",", "."))
            except ValueError:
                result["value"] = text     # Name aus der Wertetabelle
        return result

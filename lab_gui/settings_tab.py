"""Settings-Reiter: Simulationsmodus fuer Debugging ohne Hardware, Dark Mode,
Sprache, geraete-individuelle Sicherheits-Grenzwerte (Watchdog, siehe
safety.py). Jedes verbundene/bekannte Geraet bekommt eine eigene
Grenzwert-Sektion (analog zu control_tab.py: eine Sektion pro Geraete-ID),
statt einer gemeinsamen Einstellung je Geraeteart.

Intern in vier Unterreiter gegliedert (Allgemein/Geraete/CAN-Bus/Sicherheit,
siehe SettingsTab._build_*_page) statt einer einzigen langen Liste -- die
oeffentliche Schnittstelle (Signale, on_device_known()/set_*()-Methoden)
bleibt dabei unveraendert, main_window.py kennt die Unterreiter nicht."""
from __future__ import annotations

from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from can_bus.dbc import DbcError, load_dbc
from device_worker import CAN_NODE_DEVICES
from can_bus.driver import (
    INTERFACE_LIST as CAN_INTERFACE_LIST,
    CanBus,
    DEFAULT_BITRATE as CAN_DEFAULT_BITRATE,
    DEFAULT_SLCAN_SERIAL_BAUDRATE as CAN_DEFAULT_SLCAN_SERIAL_BAUDRATE,
)
from help_dialog import HelpDialog
from i18n import AVAILABLE_LANGUAGES, Translator, tr
from icons import ICON_SIZE, IconButton
from paths import IS_FROZEN
from remote_actions import CONTROL_KINDS, SCOPE_KINDS
from safety import SAFETY_LIMIT_FIELDS
from settings import (
    SHARE_BIND_LAN,
    SHARE_BIND_LOCAL,
    SHARE_CONTROL_TIMEOUT_MAX,
    SHARE_CONTROL_TIMEOUT_MIN,
    SHARE_PORT_MAX,
    SHARE_PORT_MIN,
)
from step_spinbox import SteppedDoubleSpinBox, SteppedSpinBox
from theme import ThemeManager
from theme import current as current_palette
from theme import set_tone

# field -> deutscher Basis-Anzeigename (Uebersetzungsschluessel), analog zu
# testcase_model.COND_FIELD_LABELS.
_FIELD_LABELS = {
    "max_voltage": "max. Spannung",
    "max_current": "max. Strom",
    "max_power": "max. Leistung",
}


def _separator() -> QFrame:
    """Duenne horizontale Trennlinie zwischen den thematischen Gruppen im
    Einstellungen-Tab (Darstellung / Hilfe / Geraeteverwaltung / Sicherheit,
    siehe SettingsTab.__init__). Farbe ueber die globale Theme-Regel
    QFrame[role="separator"] (theme.stylesheet) statt eines nativen
    QFrame-Rahmens -- wechselt dadurch beim Live-Umschalten Hell/Dunkel mit,
    genau wie die Hinweistexte (QLabel[role="hint"])."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setProperty("role", "separator")
    return line


def _scrollable(content: QWidget) -> QScrollArea:
    """Wrappt eine Unterreiter-Seite in eine QScrollArea (setWidgetResizable),
    analog zu control_tab.py -- einzelne Reiter (v.a. Sicherheit/Geraete-Info
    mit ihren dynamisch je Geraet hinzukommenden Sektionen) koennen die
    verfuegbare Hoehe des 1024x600-Kiosk-Displays leicht ueberschreiten."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(content)
    return scroll


def _button_row(*buttons: QPushButton) -> QHBoxLayout:
    """Haelt QPushButtons auf ihrer natuerlichen Inhaltsbreite statt sie
    (QPushButton-Standardverhalten in einem QVBoxLayout, horizontale
    SizePolicy "Minimum" laesst sie wachsen) auf die volle Tab-Breite zu
    strecken -- analog zur bereits bestehenden language_row."""
    row = QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    row.addStretch()
    return row


class _DeviceSafetyGroup(QGroupBox):
    """Grenzwert-Sektion fuer EIN Geraet (siehe SettingsTab.on_device_known)."""

    limit_changed = Signal(str, bool, float)  # field, enabled, value

    def __init__(self, kind: str, label: str) -> None:
        super().__init__()
        self._kind = kind
        self.setTitle(label)
        form = QFormLayout(self)
        # Default-Policy (AllNonFixedFieldsGrow) laesst die Feld-Spalte
        # (Checkbox+Spinbox) auf die volle verfuegbare Breite wachsen --
        # dadurch spannte sich das ganze Panel unnoetig ueber die komplette
        # Tab-Breite auf (BUGS.md #11), obwohl der Inhalt viel schmaler waere.
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        # field -> (Checkbox, Spinbox, Zeilen-Label) fuer
        # set_limits()/_on_field_changed().
        self._widgets: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}
        self._row_labels: dict[str, QLabel] = {}
        for field, unit, lo, hi, _default in SAFETY_LIMIT_FIELDS.get(kind, []):
            checkbox = QCheckBox()
            spin = SteppedDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(2)
            spin.setSuffix(f" {unit}" if unit else "")
            spin.setEnabled(False)
            checkbox.toggled.connect(spin.setEnabled)
            checkbox.toggled.connect(lambda _enabled, f=field: self._on_field_changed(f))
            spin.valueChanged.connect(lambda _value, f=field: self._on_field_changed(f))
            row = QHBoxLayout()
            row.addWidget(checkbox)
            row.addWidget(spin)
            row_label = QLabel()
            form.addRow(row_label, row)
            self._widgets[field] = (checkbox, spin)
            self._row_labels[field] = row_label

        self.retranslate()

    def retranslate(self) -> None:
        for field, row_label in self._row_labels.items():
            row_label.setText(tr(_FIELD_LABELS.get(field, field)))

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_rating_ranges(self, maxima: dict[str, float]) -> None:
        """Begrenzt die Spinboxen auf die Nennwerte des Geraets (Feld -> Maximum).

        Ohne Signalsperre: liegt ein gespeicherter Grenzwert ueber dem neuen
        Maximum, klemmt ihn die Spinbox darauf und meldet das per limit_changed --
        der gespeicherte Wert folgt damit der Anzeige statt ihr zu widersprechen.
        Ein Grenzwert oberhalb dessen, was das Geraet ueberhaupt liefern kann, ist
        ohnehin wirkungslos; die Klemmung geht also in die sichere Richtung.
        """
        for field, maximum in maxima.items():
            widgets = self._widgets.get(field)
            if widgets is None or maximum <= 0:
                continue
            spin = widgets[1]
            spin.setRange(spin.minimum(), maximum)

    def set_limits(self, limits: dict) -> None:
        for field, (checkbox, spin) in self._widgets.items():
            entry = limits.get(field, {"enabled": False, "value": spin.value()})
            checkbox.blockSignals(True)
            spin.blockSignals(True)
            checkbox.setChecked(entry["enabled"])
            spin.setValue(entry["value"])
            spin.setEnabled(entry["enabled"])
            checkbox.blockSignals(False)
            spin.blockSignals(False)

    def _on_field_changed(self, field: str) -> None:
        checkbox, spin = self._widgets[field]
        self.limit_changed.emit(field, checkbox.isChecked(), spin.value())


class _DeviceInfoGroup(QGroupBox):
    """Geraete-Info-Sektion fuer EIN Geraet (aktuell nur microHIL, siehe
    SettingsTab.on_device_known) -- zeigt die per `*IDN?` gemeldete
    Firmwareversion (device_worker.DeviceWorker.hil_info ->
    set_firmware_version())."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.setTitle(label)
        self._version: str | None = None

        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self._firmware_row_label = QLabel()
        self._firmware_value = QLabel()
        form.addRow(self._firmware_row_label, self._firmware_value)

        self.retranslate()

    def retranslate(self) -> None:
        self._firmware_row_label.setText(tr("Firmware-Version:"))
        self._refresh_value()

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_firmware_version(self, version: str | None) -> None:
        self._version = version or None
        self._refresh_value()

    def _refresh_value(self) -> None:
        self._firmware_value.setText(self._version if self._version else tr("unbekannt"))


class _DbcFileCell(QWidget):
    """Zelle fuer die optionale DBC-Datei eines CAN-Interfaces (siehe
    settings.py::can_configs, Schluessel "dbc_path") -- FEATURES.md Punkt 3.

    Zeigt nur den Dateinamen an (voller Pfad als Tooltip, sonst sprengt ein
    langer Pfad die Spaltenbreite), ein "..."-Button oeffnet einen
    Dateiauswahl-Dialog, ein "x"-Button entfernt die Zuordnung wieder
    (deaktiviert, solange keine Datei hinterlegt ist). Die gewaehlte Datei
    wird beim Auswaehlen sofort probeweise geladen (can_bus.dbc.load_dbc) --
    eine kaputte/falsche Datei wird so schon hier abgefangen statt erst
    beim naechsten empfangenen CAN-Frame lautlos im DeviceWorker-Log zu
    verschwinden (siehe device_worker.DeviceWorker._reload_can_dbcs)."""

    changed = Signal()

    def __init__(self, path: str = "") -> None:
        super().__init__()
        self._path = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLineEdit()
        self._label.setReadOnly(True)
        layout.addWidget(self._label, 1)
        self._browse_button = IconButton("mdi.file-search-outline", "")
        self._browse_button.clicked.connect(self._on_browse_clicked)
        layout.addWidget(self._browse_button)
        self._clear_button = IconButton("mdi.close", "")
        self._clear_button.clicked.connect(self._on_clear_clicked)
        layout.addWidget(self._clear_button)
        self.set_path(path)

    def retranslate(self) -> None:
        self._browse_button.setToolTip(tr("DBC-Datei wählen…"))
        self._clear_button.setToolTip(tr("DBC-Zuordnung entfernen"))
        self._refresh_label()

    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path or ""
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self._path:
            self._label.setText(Path(self._path).name)
            self._label.setToolTip(self._path)
        else:
            self._label.setText("")
            self._label.setToolTip(
                tr("Keine DBC-Datei hinterlegt -- Frames werden nur als Rohdaten angezeigt.")
            )
        self._clear_button.setEnabled(bool(self._path))

    def _on_browse_clicked(self) -> None:
        start_dir = str(Path(self._path).parent) if self._path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("DBC-Datei wählen"), start_dir, tr("DBC-Dateien (*.dbc);;Alle Dateien (*)"),
        )
        if not path:
            return
        try:
            load_dbc(path)
        except DbcError as exc:
            QMessageBox.warning(self, tr("DBC-Datei ungültig"), str(exc))
            return
        self.set_path(path)
        self.changed.emit()

    def _on_clear_clicked(self) -> None:
        if not self._path:
            return
        self.set_path("")
        self.changed.emit()


class _CanConfigTable(QWidget):
    """Liste konfigurierter CAN-Interfaces (settings.py: can_configs), eine Karte je Interface.

    Anders als Last/Netzteil keine Hotplug-Erkennung (can_bus/README.md): Interface-Typ, Kanal und
    Bitrate traegt der Nutzer ein; die Lupe bietet die ueber CanBus.discover_configs() gefundenen
    Kanaele zur Auswahl an. Bis 0.50 eine Tabelle mit zehn Spalten -- passte im Touch-Profil am Pi
    (1024 px) nicht ins Fenster (Nutzerfeedback 12.11.2026). Jetzt zwei Zeilen je Karte:
      oben:  Interface | Kanal (+ Lupe, Warnsymbol bei Verbindungsfehler) | Geraet am Bus | Entfernen
      unten: Bitrate | Serial-Baudrate (nur slcan) | Bezeichnung | DBC-Datei
    Schnittstelle wie vorher (changed, add_row, configs, set_configs, set/clear_connect_error).
    Der Kanal haelt ein Token in der Property "_channel_token", wenn er den sprechenden Namen aus der
    Auswahl zeigt (BUGS_GESCHLOSSEN.md #34); Tippen von Hand verwirft es (textEdited)."""

    changed = Signal()  # irgendeine Karte wurde hinzugefuegt/entfernt/bearbeitet

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[dict] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        # Ein interaktiv bearbeitetes Feld macht den zuletzt angezeigten Verbindungsfehler ungueltig
        # (neuer Verbindungsversuch steht bevor); set_configs blockiert Signale beim Neuladen.
        self.changed.connect(self._clear_all_connect_errors)

    def rowCount(self) -> int:  # noqa: N802 (Qt-Namensstil wie vorher QTableWidget)
        return len(self._rows)

    def retranslate(self) -> None:
        for row in self._rows:
            for key, text in (("cap_interface", "Interface"), ("cap_channel", "Kanal"),
                              ("cap_device", "Gerät am Bus"), ("cap_bitrate", "Bitrate"),
                              ("cap_serial", "Serial-Baudrate"), ("cap_label", "Bezeichnung"),
                              ("cap_dbc", "DBC-Datei")):
                row[key].setText(tr(text))
            row["dbc"].retranslate()

    def _caption(self, text: str) -> QLabel:
        label = QLabel(tr(text))
        label.setProperty("role", "hint")   # gedaempfte Farbe (theme.stylesheet)
        return label

    def add_row(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        card = QFrame()
        card.setObjectName("canCard")
        card.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(card)
        grid.setContentsMargins(10, 6, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        row: dict = {"card": card}

        interface_combo = QComboBox()
        for code in CAN_INTERFACE_LIST:
            interface_combo.addItem(code, code)
        index = interface_combo.findData(cfg.get("interface", CAN_INTERFACE_LIST[0]))
        interface_combo.setCurrentIndex(max(index, 0))
        interface_combo.currentIndexChanged.connect(lambda _=None: self.changed.emit())

        # editingFinished statt textChanged: sonst stoesst jeder Tastendruck einen Reconnect an
        channel_edit = QLineEdit(str(cfg.get("channel", "")))
        channel_edit.editingFinished.connect(lambda: self.changed.emit())

        def _clear_channel_token(_text=None, e=channel_edit) -> None:
            e.setProperty("_channel_token", None)
            e.setToolTip("")

        channel_edit.textEdited.connect(_clear_channel_token)
        pick_button = IconButton("mdi.magnify", tr("Verfügbare Kanäle suchen…"))
        pick_button.clicked.connect(lambda _=None, r=row: self._pick_channel(r))
        status_label = QLabel()     # Warnsymbol bei can_connect_error, Fehlertext als Tooltip
        status_label.hide()
        channel_box = QHBoxLayout()
        channel_box.setSpacing(4)
        channel_box.addWidget(channel_edit, 1)
        channel_box.addWidget(status_label)
        channel_box.addWidget(pick_button)

        device_combo = QComboBox()
        for code, label in CAN_NODE_DEVICES:
            device_combo.addItem(tr(label), code)
        index = device_combo.findData(cfg.get("device", ""))
        device_combo.setCurrentIndex(max(index, 0))
        device_combo.currentIndexChanged.connect(lambda _=None: self.changed.emit())

        remove_button = IconButton("mdi.trash-can-outline", tr("Entfernen"))
        remove_button.clicked.connect(lambda _=None, r=row: self._remove(r))

        bitrate_spin = SteppedSpinBox(small_step=1000, large_step=100_000)
        bitrate_spin.setRange(10_000, 1_000_000)
        bitrate_spin.setSuffix(" bit/s")
        bitrate_spin.setValue(int(cfg.get("bitrate", CAN_DEFAULT_BITRATE)))
        bitrate_spin.valueChanged.connect(lambda _=None: self.changed.emit())

        serial_baud_spin = SteppedSpinBox(small_step=1200, large_step=57_600)
        serial_baud_spin.setRange(1200, 2_000_000)
        serial_baud_spin.setSuffix(" Bd")
        serial_baud_spin.setValue(int(cfg.get("serial_baudrate", CAN_DEFAULT_SLCAN_SERIAL_BAUDRATE)))
        serial_baud_spin.valueChanged.connect(lambda _=None: self.changed.emit())

        label_edit = QLineEdit(str(cfg.get("label", "")))
        label_edit.editingFinished.connect(lambda: self.changed.emit())

        dbc_cell = _DbcFileCell(str(cfg.get("dbc_path", "")))
        dbc_cell.changed.connect(lambda: self.changed.emit())
        dbc_cell.retranslate()

        caps = {key: self._caption(text) for key, text in (
            ("cap_interface", "Interface"), ("cap_channel", "Kanal"), ("cap_device", "Gerät am Bus"),
            ("cap_bitrate", "Bitrate"), ("cap_serial", "Serial-Baudrate"), ("cap_label", "Bezeichnung"),
            ("cap_dbc", "DBC-Datei"))}
        grid.addWidget(caps["cap_interface"], 0, 0)
        grid.addWidget(caps["cap_channel"], 0, 1)
        grid.addWidget(caps["cap_device"], 0, 2)
        grid.addWidget(interface_combo, 1, 0)
        grid.addLayout(channel_box, 1, 1)
        grid.addWidget(device_combo, 1, 2)
        grid.addWidget(remove_button, 1, 3, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(caps["cap_bitrate"], 2, 0)
        grid.addWidget(caps["cap_label"], 2, 1)
        grid.addWidget(caps["cap_dbc"], 2, 2, 1, 2)
        grid.addWidget(bitrate_spin, 3, 0)
        grid.addWidget(label_edit, 3, 1)
        grid.addWidget(dbc_cell, 3, 2, 1, 2)
        # Serial-Baudrate nur bei slcan (Baudrate der Verbindung ZUM Adapter): eigene Zeile, sonst verborgen
        grid.addWidget(caps["cap_serial"], 4, 0)
        grid.addWidget(serial_baud_spin, 5, 0)
        for col, stretch in ((0, 0), (1, 1), (2, 1), (3, 0)):
            grid.setColumnStretch(col, stretch)

        def _update_serial_baud(_index=None, spin=serial_baud_spin, cap=caps["cap_serial"],
                                combo=interface_combo) -> None:
            slcan = combo.currentData() == "slcan"
            spin.setVisible(slcan)
            cap.setVisible(slcan)

        interface_combo.currentIndexChanged.connect(_update_serial_baud)
        _update_serial_baud()

        row.update(caps)
        row.update(interface=interface_combo, channel=channel_edit, pick=pick_button, status=status_label,
                   bitrate=bitrate_spin, serial=serial_baud_spin, label=label_edit, dbc=dbc_cell,
                   device=device_combo, remove=remove_button)
        self._rows.append(row)
        self._layout.addWidget(card)

    def _remove(self, row: dict) -> None:
        if row in self._rows:
            self._rows.remove(row)
            row["card"].setParent(None)
            row["card"].deleteLater()
            self.changed.emit()

    def _pick_channel(self, row: dict) -> None:
        channel_edit: QLineEdit = row["channel"]
        interface = row["interface"].currentData()
        errors: dict[str, str] = {}
        try:
            configs = CanBus.discover_configs(errors)
        except Exception as exc:
            configs = []
            errors[interface] = str(exc)
        matching = [c for c in configs if c.get("interface") == interface]
        if not matching:
            # Konkreten Grund mitliefern: ein nicht ladbares Backend sieht sonst aus wie
            # "Geraet nicht angeschlossen" (can_bus/driver.py::backend_problem)
            reason = errors.get(interface, "")
            message = tr(
                "Für Interface-Typ '{interface}' wurden keine Kanäle gefunden -- "
                "ist der zugehörige Vendor-Treiber installiert und das Gerät "
                "angeschlossen?", interface=interface,
            )
            if reason:
                message += "\n\n" + tr("Grund: {reason}", reason=reason)
            QMessageBox.information(self, tr("Keine Kanäle gefunden"), message)
            return
        # Angezeigt wird der sprechende Name, gespeichert das eindeutige Kanal-Token
        by_name: dict[str, str] = {}
        for config in matching:
            name = str(config.get("display_name") or config["channel"])
            if name in by_name:
                name = f"{name} [{config['channel']}]"
            by_name[name] = str(config["channel"])
        name, ok = QInputDialog.getItem(
            self, tr("Kanal wählen"), tr("Verfügbare Kanäle:"),
            list(by_name), editable=False,
        )
        if ok:
            token = by_name[name]
            channel_edit.setText(name)
            channel_edit.setProperty("_channel_token", token)
            channel_edit.setToolTip(token)
            self.changed.emit()

    @staticmethod
    def _channel(row: dict) -> str:
        token = row["channel"].property("_channel_token")
        return str(token).strip() if token else row["channel"].text().strip()

    def configs(self) -> list[dict]:
        result = []
        for row in self._rows:
            channel = self._channel(row)
            if not channel:
                continue
            interface = row["interface"].currentData()
            cfg = dict(
                interface=interface,
                channel=channel,
                bitrate=row["bitrate"].value(),
                label=row["label"].text().strip(),
                dbc_path=row["dbc"].path(),
            )
            if row["device"].currentData():
                cfg["device"] = row["device"].currentData()
            if interface == "slcan":   # bei vector/pcan bedeutungslos
                cfg["serial_baudrate"] = row["serial"].value()
            result.append(cfg)
        return result

    def _row_device_id(self, row: dict) -> str:
        """Wie device_worker.can_device_id(cfg), aber aus den aktuellen Feldern einer Karte."""
        return f"can:{row['interface'].currentData()}:{self._channel(row)}"

    def set_connect_error(self, device_id: str, message: str) -> None:
        for row in self._rows:
            if self._row_device_id(row) == device_id:
                status_label: QLabel = row["status"]
                status_label.setToolTip(message)
                status_label.setPixmap(
                    qta.icon("mdi.alert-circle-outline", color=current_palette().danger).pixmap(ICON_SIZE))
                status_label.show()
                return

    def clear_connect_error(self, device_id: str) -> None:
        for row in self._rows:
            if self._row_device_id(row) == device_id:
                row["status"].hide()
                row["status"].setToolTip("")
                return

    def _clear_all_connect_errors(self) -> None:
        for row in self._rows:
            row["status"].hide()

    def set_configs(self, configs: list[dict]) -> None:
        for row in self._rows:
            row["card"].setParent(None)
            row["card"].deleteLater()
        self._rows = []
        for cfg in configs:
            self.add_row(cfg)


class _ShareTable(QTableWidget):
    """Freigabe je Geraet: welche Kachel darf nach aussen gelesen bzw.
    gesteuert werden.

    Tabelle statt je einer QGroupBox pro Geraet (wie bei den Sicherheits-
    Grenzwerten): es geht um zwei Haken pro Zeile, und 3-10 Geraete sind als
    Raster deutlich besser zu ueberblicken als als Sektionsliste. Der
    Zeilen-Lebenszyklus folgt trotzdem dem Muster von _safety_sections
    (on_device_known/forget_device in SettingsTab).
    """

    share_changed = Signal(str, bool, bool, bool)  # device_id, read, control, measure

    COL_LABEL, COL_ID, COL_READ, COL_CONTROL, COL_MEASURE = range(5)

    # Geraetearten mit einer Zeile in dieser Tabelle. Steuern gibt es nur fuer
    # remote_actions.CONTROL_KINDS (CAN ist lesbar, aber nie fernsteuerbar),
    # Messen nur fuer remote_actions.SCOPE_KINDS (Erfassungen ueber
    # Netzwerk/MCP, seit 0.16.0 -- vorher blieb das Oszilloskop ganz draussen).
    SUPPORTED_KINDS = ("load", "psu", "can", "hil", "fg", "picoscope", "scpiscope", "battsim")

    def __init__(self) -> None:
        super().__init__(0, 5)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.horizontalHeader().setStretchLastSection(False)
        self._rows: dict[str, int] = {}
        self._kinds: dict[str, str] = {}

    def retranslate(self) -> None:
        self.setHorizontalHeaderLabels(
            [tr("Gerät"), tr("Geräte-ID"), tr("Lesen"), tr("Steuern"), tr("Messen")]
        )
        for device_id, row in self._rows.items():
            widget = self.cellWidget(row, self.COL_CONTROL)
            if widget is not None:
                widget.setToolTip(self._control_tooltip(self._kinds.get(device_id, "")))
            widget = self.cellWidget(row, self.COL_MEASURE)
            if widget is not None:
                widget.setToolTip(self._measure_tooltip(self._kinds.get(device_id, "")))

    @staticmethod
    def _control_tooltip(kind: str) -> str:
        if kind in CONTROL_KINDS:
            return tr("Fernsteuerung dieses Geräts erlauben (wirkt nur bei aktivem Hauptschalter)")
        return tr("Dieses Gerät lässt sich nicht fernsteuern")

    @staticmethod
    def _measure_tooltip(kind: str) -> str:
        if kind in SCOPE_KINDS:
            return tr("Erfassungen über Netzwerk/MCP erlauben (braucht den Hauptschalter nicht)")
        return tr("Nur für Oszilloskope")

    def _checkbox(self, device_id: str, column: int, enabled: bool) -> QWidget:
        box = QCheckBox()
        box.setEnabled(enabled)
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        layout.setAlignment(box, Qt.AlignmentFlag.AlignCenter)
        box.toggled.connect(lambda _c, d=device_id, c=column: self._on_toggled(d, c))
        setattr(holder, "checkbox", box)
        return holder

    def _box(self, device_id: str, column: int) -> QCheckBox | None:
        holder = self.cellWidget(self._rows[device_id], column)
        return getattr(holder, "checkbox", None) if holder is not None else None

    def _on_toggled(self, device_id: str, column: int) -> None:
        """Steuern setzt Lesen voraus (ohne Sicht auf die Kachel gibt es kein
        Steuern), und Lesen abwaehlen nimmt Steuern zurueck. Settings
        normalisiert dasselbe noch einmal -- das hier haelt nur die Anzeige
        ehrlich."""
        if device_id not in self._rows:
            return
        read = self._box(device_id, self.COL_READ)
        if read is not None:
            for dependent in (self.COL_CONTROL, self.COL_MEASURE):
                box = self._box(device_id, dependent)
                if box is None:
                    continue
                if column == dependent and box.isChecked() and not read.isChecked():
                    read.blockSignals(True)
                    read.setChecked(True)
                    read.blockSignals(False)
                elif column == self.COL_READ and not read.isChecked() and box.isChecked():
                    box.blockSignals(True)
                    box.setChecked(False)
                    box.blockSignals(False)
        self._emit(device_id)

    def _emit(self, device_id: str) -> None:
        if device_id not in self._rows:
            return
        read = self._box(device_id, self.COL_READ)
        control = self._box(device_id, self.COL_CONTROL)
        measure = self._box(device_id, self.COL_MEASURE)
        self.share_changed.emit(
            device_id,
            bool(read.isChecked()) if read else False,
            bool(control.isChecked()) if control else False,
            bool(measure.isChecked()) if measure else False,
        )

    def add_device(self, kind: str, device_id: str, label: str) -> None:
        if kind not in self.SUPPORTED_KINDS:
            return
        if device_id in self._rows:
            self.set_label(device_id, label)
            return
        row = self.rowCount()
        self.insertRow(row)
        self._rows[device_id] = row
        self.setItem(row, self.COL_LABEL, QTableWidgetItem(label))
        self.setItem(row, self.COL_ID, QTableWidgetItem(device_id))
        self._kinds[device_id] = kind
        self.setCellWidget(row, self.COL_READ, self._checkbox(device_id, self.COL_READ, True))
        control_cell = self._checkbox(device_id, self.COL_CONTROL, kind in CONTROL_KINDS)
        control_cell.setToolTip(self._control_tooltip(kind))
        self.setCellWidget(row, self.COL_CONTROL, control_cell)
        measure_cell = self._checkbox(device_id, self.COL_MEASURE, kind in SCOPE_KINDS)
        measure_cell.setToolTip(self._measure_tooltip(kind))
        self.setCellWidget(row, self.COL_MEASURE, measure_cell)
        self.resizeColumnsToContents()

    def set_label(self, device_id: str, label: str) -> None:
        row = self._rows.get(device_id)
        if row is not None:
            self.setItem(row, self.COL_LABEL, QTableWidgetItem(label))

    def remove_device(self, device_id: str) -> None:
        row = self._rows.pop(device_id, None)
        self._kinds.pop(device_id, None)
        if row is None:
            return
        self.removeRow(row)
        # Nachfolgende Zeilen ruecken auf -- Index-Zuordnung nachziehen.
        for other, other_row in self._rows.items():
            if other_row > row:
                self._rows[other] = other_row - 1

    def set_share(self, devices: dict) -> None:
        for device_id in self._rows:
            entry = devices.get(device_id, {})
            for column, key in ((self.COL_READ, "read"), (self.COL_CONTROL, "control"),
                                (self.COL_MEASURE, "measure")):
                box = self._box(device_id, column)
                if box is None:
                    continue
                box.blockSignals(True)
                box.setChecked(bool(entry.get(key)))
                box.blockSignals(False)


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    touch_mode_toggled = Signal(bool)
    language_selected = Signal(str)
    network_dir_changed = Signal(str)   # Netzlaufwerk fuer Log und Betriebsdaten, leer = nur lokal
    safety_limit_changed = Signal(str, str, bool, float)  # device_id, field, enabled, value
    notifications_toggled = Signal(bool)
    panel_colors_toggled = Signal(bool)
    can_configs_changed = Signal(list)  # list[dict]: interface/channel/bitrate/serial_baudrate/label/dbc_path
    scpi_scopes_changed = Signal(list)  # list[str]: Adressen der SCPI-Oszilloskope
    # Nutzer hat die Rueckfrage in _on_reset_devices_clicked bereits mit Ja
    # bestaetigt -- main_window._on_reset_devices_requested fuehrt den
    # eigentlichen Reset aus (DeviceRegistry/Settings kennt dieses Widget
    # nicht direkt).
    reset_devices_requested = Signal()
    # Wie reset_devices_requested, aber nur fuer Geraete, die gerade NICHT
    # verbunden sind (main_window._on_remove_offline_devices_requested).
    remove_offline_devices_requested = Signal()

    # Netzwerk-Freigabe (siehe share_server.py). Wie ueberall im
    # Einstellungen-Tab: nur melden, Settings fasst dieser Tab nie an.
    share_enabled_toggled = Signal(bool)
    share_bind_selected = Signal(str)
    share_port_changed = Signal(int)
    share_token_regenerate_requested = Signal()
    share_read_token_toggled = Signal(bool)
    share_access_log_toggled = Signal(bool)
    share_device_changed = Signal(str, bool, bool, bool)  # device_id, read, control, measure
    share_control_toggled = Signal(bool)            # Hauptschalter Fernsteuerung
    share_local_bypass_toggled = Signal(bool)       # lokal ohne Hauptschalter
    share_control_timeout_changed = Signal(int)     # Minuten
    # Verbund-Partner (PI-23 a): {"enabled", "address", "token", "name"}
    partner_config_edited = Signal(dict)
    # Kopplung starten (pairing.py): eingetragene Adresse
    partner_pair_requested = Signal(str)
    # J-Link (PI-31 a): {"enabled", "device", "interface", "speed", "serial", "commander"}
    jlink_config_edited = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self._subtabs = QTabWidget()
        outer_layout.addWidget(self._subtabs)

        self._subtabs.addTab(_scrollable(self._build_general_page()), "")
        self._subtabs.addTab(_scrollable(self._build_devices_page()), "")
        self._subtabs.addTab(_scrollable(self._build_can_page()), "")
        self._subtabs.addTab(_scrollable(self._build_safety_page()), "")
        self._subtabs.addTab(_scrollable(self._build_network_page()), "")

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _build_general_page(self) -> QWidget:
        """Reiter "Allgemein": Simulationsmodus, Darstellung/Verhalten,
        Sprache, Hilfe -- alles ohne Geraetebezug."""
        page = QWidget()
        layout = QVBoxLayout(page)

        self._sim_checkbox = QCheckBox()
        self._sim_checkbox.toggled.connect(self.simulation_mode_toggled)
        layout.addWidget(self._sim_checkbox)


        # Simulationsmodus nur im Dev-Betrieb anbieten, in Release-.exe
        # komplett ausgeblendet (nicht nur deaktiviert) -- siehe FEATURES.md
        # Punkt 4. Der eigentliche Schutz liegt in Settings.simulation_mode
        # (paths.IS_FROZEN); das Ausblenden hier verhindert nur, dass die
        # nicht wirksame Option im Release-Build ueberhaupt sichtbar ist.
        # Die Trennlinie darunter wird aus demselben Grund mit ausgeblendet,
        # sonst stuende in der Release-.exe eine Linie ohne jeden Inhalt
        # darueber ganz oben im Tab.
        self._sim_separator = _separator()
        if IS_FROZEN:
            self._sim_checkbox.setVisible(False)
            self._sim_separator.setVisible(False)
        layout.addWidget(self._sim_separator)

        # -- Darstellung/Verhalten -------------------------------------------
        self._dark_checkbox = QCheckBox()
        self._dark_checkbox.toggled.connect(self.dark_mode_toggled)
        layout.addWidget(self._dark_checkbox)

        # Touch-Bedienung (touch_ui.py): grosse Bedienelemente, Navigation links, Uebersicht als Reiter
        self._touch_checkbox = QCheckBox()
        self._touch_checkbox.toggled.connect(self.touch_mode_toggled)
        layout.addWidget(self._touch_checkbox)
        self._touch_auto = False

        self._notify_checkbox = QCheckBox()
        self._notify_checkbox.toggled.connect(self.notifications_toggled)
        layout.addWidget(self._notify_checkbox)

        self._panel_colors_checkbox = QCheckBox()
        self._panel_colors_checkbox.toggled.connect(self.panel_colors_toggled)
        layout.addWidget(self._panel_colors_checkbox)

        language_row = QHBoxLayout()
        self._language_label = QLabel()
        language_row.addWidget(self._language_label)
        self._language_combo = QComboBox()
        for code, native_name in AVAILABLE_LANGUAGES.items():
            self._language_combo.addItem(native_name, code)
        self._language_combo.currentIndexChanged.connect(
            lambda index: self.language_selected.emit(self._language_combo.itemData(index))
        )
        language_row.addWidget(self._language_combo)
        language_row.addStretch()
        layout.addLayout(language_row)

        layout.addWidget(_separator())

        # -- Netzlaufwerk fuer Log und Betriebsdaten (app_logging, data_store) --
        log_row = QHBoxLayout()
        self._network_dir_label = QLabel()
        log_row.addWidget(self._network_dir_label)
        self._network_dir_edit = QLineEdit()
        self._network_dir_edit.setMinimumWidth(320)
        # editingFinished (Enter/Fokusverlust) statt textChanged: sonst wuerde jeder
        # Tastendruck ein neues Ziel pruefen lassen.
        self._network_dir_edit.editingFinished.connect(
            lambda: self.network_dir_changed.emit(self._network_dir_edit.text().strip())
        )
        log_row.addWidget(self._network_dir_edit, 1)
        layout.addLayout(log_row)
        self._log_status_label = QLabel()
        self._log_status_label.setWordWrap(True)
        self._log_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._log_status_label)
        self._data_status_label = QLabel()
        self._data_status_label.setWordWrap(True)
        self._data_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._data_status_label)
        self._log_status: dict = {}
        self._data_status: dict = {}

        layout.addWidget(_separator())

        # -- Hilfe -------------------------------------------------------------
        self._help_button = IconButton("mdi.help-circle-outline", "")   # nur Symbol (Review KN-7 a)
        self._help_button.clicked.connect(self._on_help_clicked)
        layout.addLayout(_button_row(self._help_button))

        layout.addStretch()
        return page

    def _build_devices_page(self) -> QWidget:
        """Reiter "Geräte": Geraeteverwaltung (Reset) und Geraete-Info
        (aktuell nur microHIL-Firmwareversion)."""
        page = QWidget()
        layout = QVBoxLayout(page)

        # -- Geraeteverwaltung ---------------------------------------------
        # Zwei Stufen (Nutzerwunsch 2026-09-28): nur die gerade nicht
        # verbundenen Geraete vergessen, oder wie bisher alle.
        # Symbol davor, Text bleibt (Review KN-14 a)
        self._remove_offline_button = IconButton("mdi.delete-sweep-outline", "", text=" ")
        self._remove_offline_button.clicked.connect(self._on_remove_offline_clicked)
        self._reset_devices_button = IconButton("mdi.delete-forever-outline", "", text=" ")
        self._reset_devices_button.clicked.connect(self._on_reset_devices_clicked)
        layout.addLayout(_button_row(self._remove_offline_button, self._reset_devices_button))

        layout.addWidget(_separator())

        # -- SCPI-Oszilloskope ueber LAN (seit 0.21.0) -------------------------
        # Keine Autodiscovery im Netz: Adressen von Hand, durch Komma getrennt.
        # Uebernommen wird beim Verlassen des Felds bzw. Enter.
        self._scpi_title = QLabel()
        self._scpi_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._scpi_title)
        self._scpi_edit = QLineEdit()
        self._scpi_edit.setPlaceholderText("192.168.1.50")
        self._scpi_edit.editingFinished.connect(self._on_scpi_edited)
        layout.addWidget(self._scpi_edit)

        layout.addWidget(_separator())
        self._build_jlink_section(layout)
        layout.addWidget(_separator())

        # -- Geraete-Info (aktuell nur microHIL-Firmwareversion) --------------

        self._info_sections_layout = QVBoxLayout()
        layout.addLayout(self._info_sections_layout)
        self._info_sections: dict[str, _DeviceInfoGroup] = {}
        self._info_section_rows: dict[str, QWidget] = {}

        layout.addStretch()
        return page

    # -- J-Link (PI-31 a, jlink_service.py) -----------------------------------------

    def _build_jlink_section(self, layout: QVBoxLayout) -> None:
        self._jlink_title = QLabel()
        self._jlink_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._jlink_title)
        self._jlink_enabled = QCheckBox()
        self._jlink_enabled.toggled.connect(lambda _c: self._emit_jlink_config())
        layout.addWidget(self._jlink_enabled)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self._jlink_device = QLineEdit()
        self._jlink_device.setPlaceholderText("S32K144")
        self._jlink_device.setMinimumWidth(200)
        self._jlink_device.editingFinished.connect(self._emit_jlink_config)
        self._jlink_device_label = QLabel()
        form.addRow(self._jlink_device_label, self._jlink_device)
        self._jlink_interface = QComboBox()
        self._jlink_interface.addItems(["SWD", "JTAG"])
        self._jlink_interface.currentIndexChanged.connect(lambda _i: self._emit_jlink_config())
        self._jlink_interface_label = QLabel()
        form.addRow(self._jlink_interface_label, self._jlink_interface)
        self._jlink_speed = SteppedSpinBox()
        self._jlink_speed.setRange(5, 50000)
        self._jlink_speed.setSuffix(" kHz")
        self._jlink_speed.valueChanged.connect(lambda _v: self._emit_jlink_config())
        self._jlink_speed_label = QLabel()
        form.addRow(self._jlink_speed_label, self._jlink_speed)
        self._jlink_serial = QLineEdit()
        self._jlink_serial.setMaxLength(20)
        self._jlink_serial.editingFinished.connect(self._emit_jlink_config)
        self._jlink_serial_label = QLabel()
        form.addRow(self._jlink_serial_label, self._jlink_serial)
        self._jlink_commander = QLineEdit()
        self._jlink_commander.setMinimumWidth(320)
        self._jlink_commander.editingFinished.connect(self._emit_jlink_config)
        self._jlink_commander_label = QLabel()
        form.addRow(self._jlink_commander_label, self._jlink_commander)
        layout.addLayout(form)
        self._jlink_status = QLabel()
        self._jlink_status.setWordWrap(True)
        layout.addWidget(self._jlink_status)

    def _retranslate_jlink(self) -> None:
        self._jlink_title.setText(tr("J-Link"))
        self._jlink_enabled.setText(tr("J-Link verwenden"))
        self._jlink_device_label.setText(tr("Zielbaustein:"))
        self._jlink_interface_label.setText(tr("Schnittstelle:"))
        self._jlink_speed_label.setText(tr("Takt:"))
        self._jlink_serial_label.setText(tr("Seriennummer:"))
        self._jlink_serial.setPlaceholderText(tr("einziger angeschlossener J-Link"))
        self._jlink_commander_label.setText(tr("J-Link Commander:"))
        self._jlink_commander.setPlaceholderText(tr("automatisch suchen"))

    def _emit_jlink_config(self) -> None:
        self.jlink_config_edited.emit({
            "enabled": self._jlink_enabled.isChecked(),
            "device": self._jlink_device.text().strip(),
            "interface": self._jlink_interface.currentText(),
            "speed": self._jlink_speed.value(),
            "serial": self._jlink_serial.text().strip(),
            "commander": self._jlink_commander.text().strip(),
        })

    def set_jlink_config(self, config: dict, commander_found: str) -> None:
        widgets = (self._jlink_enabled, self._jlink_device, self._jlink_interface, self._jlink_speed,
                   self._jlink_serial, self._jlink_commander)
        for widget in widgets:
            widget.blockSignals(True)
        self._jlink_enabled.setChecked(bool(config.get("enabled")))
        self._jlink_device.setText(config.get("device", ""))
        self._jlink_interface.setCurrentText(config.get("interface", "SWD"))
        self._jlink_speed.setValue(int(config.get("speed", 4000)))
        self._jlink_serial.setText(config.get("serial", ""))
        self._jlink_commander.setText(config.get("commander", ""))
        for widget in widgets:
            widget.blockSignals(False)
        if commander_found:
            self._jlink_status.setText(tr("J-Link Commander: {path}", path=commander_found))
            set_tone(self._jlink_status, "muted")
        else:
            self._jlink_status.setText(tr("J-Link Commander nicht gefunden"))
            set_tone(self._jlink_status, "warning")

    def set_scpi_scopes(self, addresses: list[str]) -> None:
        self._scpi_edit.blockSignals(True)
        self._scpi_edit.setText(", ".join(addresses))
        self._scpi_edit.blockSignals(False)

    def _on_scpi_edited(self) -> None:
        from unit_mso1000hd.driver import is_valid_address
        parts = [p.strip() for p in self._scpi_edit.text().replace(";", ",").split(",")]
        valid = [p for p in parts if p and is_valid_address(p)]
        invalid = [p for p in parts if p and not is_valid_address(p)]
        self._scpi_edit.setToolTip(tr("Ungültig und ignoriert: {items}", items=", ".join(invalid)) if invalid else "")
        self.scpi_scopes_changed.emit(valid)

    def _build_can_page(self) -> QWidget:
        """Reiter "CAN-Bus": explizit konfigurierte CAN-Interfaces (siehe
        can_bus/README.md, keine Hotplug-Autodiscovery wie bei Last/Netzteil)."""
        page = QWidget()
        layout = QVBoxLayout(page)

        self._can_table = _CanConfigTable()
        self._can_table.changed.connect(self._on_can_table_changed)
        layout.addWidget(self._can_table)

        self._can_add_button = IconButton("mdi.plus", "")   # nur Symbol (Review KN-7 a)
        self._can_add_button.clicked.connect(self._on_can_add_clicked)
        layout.addLayout(_button_row(self._can_add_button))
        layout.addStretch(1)

        return page

    def _build_safety_page(self) -> QWidget:
        """Reiter "Sicherheit": geraete-individuelle Grenzwerte (Watchdog,
        siehe safety.py)."""
        page = QWidget()
        layout = QVBoxLayout(page)


        # Ein eigenes Layout fuer die dynamisch je Geraet erzeugten
        # _DeviceSafetyGroup-Sektionen (siehe on_device_known), damit sie sich
        # gemeinsam vor dem abschliessenden addStretch() einreihen.
        self._safety_sections_layout = QVBoxLayout()
        layout.addLayout(self._safety_sections_layout)
        self._safety_sections: dict[str, _DeviceSafetyGroup] = {}
        # device_id -> (max. Spannung, max. Strom) laut GMAX, siehe set_psu_ratings
        self._psu_ratings: dict[str, tuple[float, float]] = {}
        # Umschliessendes Zeilen-Widget je Sektion (siehe on_device_known) --
        # ermoeglicht forget_device(), die komplette Zeile (Sektion + den
        # Stretch daneben) mit einem einzigen deleteLater() zu entfernen,
        # statt zusaetzlich das QHBoxLayout-Zeilenobjekt selbst verwalten zu
        # muessen.
        self._safety_section_rows: dict[str, QWidget] = {}

        layout.addStretch()
        return page

    def _retranslate_network(self) -> None:
        self._share_enabled_checkbox.setText(tr("Freigabe im lokalen Netzwerk aktivieren"))
        self._share_bind_label.setText(tr("Erreichbar für"))
        self._share_bind_combo.setItemText(0, tr("Nur diesen PC"))
        self._share_bind_combo.setItemText(1, tr("Alle Geräte im lokalen Netzwerk"))
        self._share_port_label.setText(tr("Port"))
        self._share_url_label.setText(tr("Adresse"))
        self._share_url_edit.setToolTip(tr("Diese Adresse im ESP32-Sketch oder Browser verwenden"))
        self._share_token_label.setText(tr("Token"))
        self._share_token_new.setToolTip(tr("Neuen Token erzeugen (macht den alten ungültig)"))
        self._share_token_copy.setToolTip(tr("Token in die Zwischenablage kopieren"))
        self._share_read_token_checkbox.setText(tr("Lesezugriff ohne Token erlauben"))
        self._share_access_log_checkbox.setText(
            tr("Zugriffe protokollieren")
        )
        self._share_control_checkbox.setText(tr("Fernsteuerung für andere Rechner"))
        self._share_control_checkbox.setToolTip(tr(
            "Zeitlich begrenzt, für Programme auf anderen Rechnern. Der gekoppelte Verbund-Partner "
            "braucht das nicht."))
        self._share_local_bypass_checkbox.setText(tr("Claude (MCP) darf steuern"))
        self._share_local_bypass_checkbox.setToolTip(
            tr("Gilt für Programme auf diesem Rechner, z.B. den MCP-Server. Token, "
               "„Steuern“ und die Sperren bei Testlauf und Sicherheitsabschaltung bleiben."))
        self._share_control_timeout_label.setText(tr("Schaltet sich ab nach"))
        self._share_table.retranslate()
        self._retranslate_partner()

    def _build_network_page(self) -> QWidget:
        """Reiter "Netzwerk": ausgewaehlte Kacheln im lokalen Netz bereitstellen
        (siehe share_server.py). Aufbau bewusst wie _build_can_page(): Hinweis,
        Bedienelemente, dynamische Geraeteliste."""
        page = QWidget()
        layout = QVBoxLayout(page)


        self._share_enabled_checkbox = QCheckBox()
        self._share_enabled_checkbox.toggled.connect(self.share_enabled_toggled)
        layout.addWidget(self._share_enabled_checkbox)

        # Schalter fuer Claude (MCP) und andere Programme auf DIESEM Rechner (PI-39 b, Einstellung
        # local_bypass): ohne Hauptschalter steuern. Token, "Steuern" und die Sperren bleiben.
        self._share_local_bypass_checkbox = QCheckBox()
        self._share_local_bypass_checkbox.toggled.connect(self.share_local_bypass_toggled)
        layout.addWidget(self._share_local_bypass_checkbox)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        # Auswahlliste statt Freitextfeld: die Entscheidung "nur dieser PC"
        # gegen "ganzes Netz" ist sicherheitsrelevant und soll weder
        # vertippbar noch versehentlich auf eine dritte Adresse setzbar sein.
        self._share_bind_combo = QComboBox()
        self._share_bind_combo.addItem("", SHARE_BIND_LOCAL)
        self._share_bind_combo.addItem("", SHARE_BIND_LAN)
        self._share_bind_combo.currentIndexChanged.connect(
            lambda _i: self.share_bind_selected.emit(self._share_bind_combo.currentData())
        )
        self._share_bind_label = QLabel()
        form.addRow(self._share_bind_label, self._share_bind_combo)

        self._share_port_spin = SteppedSpinBox()
        self._share_port_spin.setRange(SHARE_PORT_MIN, SHARE_PORT_MAX)
        self._share_port_spin.valueChanged.connect(self.share_port_changed)
        self._share_port_label = QLabel()
        form.addRow(self._share_port_label, self._share_port_spin)

        # Fertige Adresse zum Abtippen in den ESP32-Sketch -- schreibgeschuetzt,
        # aber markier- und kopierbar.
        self._share_url_edit = QLineEdit()
        self._share_url_edit.setReadOnly(True)
        self._share_url_label = QLabel()
        form.addRow(self._share_url_label, self._share_url_edit)

        self._share_token_edit = QLineEdit()
        self._share_token_edit.setReadOnly(True)
        self._share_token_new = IconButton("mdi.refresh", "")
        self._share_token_new.clicked.connect(self.share_token_regenerate_requested)
        self._share_token_copy = IconButton("mdi.content-copy", "")
        self._share_token_copy.clicked.connect(self._on_share_token_copy)
        token_row = QWidget()
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.addWidget(self._share_token_edit, 1)
        token_layout.addWidget(self._share_token_new)
        token_layout.addWidget(self._share_token_copy)
        self._share_token_label = QLabel()
        form.addRow(self._share_token_label, token_row)
        layout.addLayout(form)

        self._share_read_token_checkbox = QCheckBox()
        # Invertiert zur Einstellung: gefragt wird "ohne Token erlauben",
        # gespeichert wird "Token erforderlich" -- der Haken soll die
        # Lockerung sein, nicht die Absicherung.
        self._share_read_token_checkbox.toggled.connect(
            lambda checked: self.share_read_token_toggled.emit(not checked)
        )
        layout.addWidget(self._share_read_token_checkbox)

        self._share_access_log_checkbox = QCheckBox()
        self._share_access_log_checkbox.toggled.connect(self.share_access_log_toggled)
        layout.addWidget(self._share_access_log_checkbox)

        self._share_status = QLabel()
        self._share_status.setWordWrap(True)
        layout.addWidget(self._share_status)

        layout.addWidget(_separator())

        # -- Fernsteuerung: Hauptschalter mit Zeitlimit ----------------------

        # Nicht gespeichert: nach jedem App-Start ist die Fernsteuerung aus.
        # Wer sie einschaltet, gibt bewusst ein Zeitfenster her.
        self._share_control_checkbox = QCheckBox()
        self._share_control_checkbox.toggled.connect(self.share_control_toggled)
        layout.addWidget(self._share_control_checkbox)

        control_form = QFormLayout()
        control_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self._share_control_timeout_spin = SteppedSpinBox()
        self._share_control_timeout_spin.setRange(SHARE_CONTROL_TIMEOUT_MIN, SHARE_CONTROL_TIMEOUT_MAX)
        self._share_control_timeout_spin.setSuffix(" min")
        self._share_control_timeout_spin.valueChanged.connect(self.share_control_timeout_changed)
        self._share_control_timeout_label = QLabel()
        control_form.addRow(self._share_control_timeout_label, self._share_control_timeout_spin)
        layout.addLayout(control_form)

        self._share_control_status = QLabel()
        layout.addWidget(self._share_control_status)

        layout.addWidget(_separator())


        self._share_table = _ShareTable()
        self._share_table.share_changed.connect(self.share_device_changed)
        layout.addWidget(self._share_table)

        layout.addWidget(_separator())
        self._build_partner_section(layout)

        layout.addStretch()
        return page

    # -- Verbund mit einer zweiten LabControl-Instanz (PI-23 a, partner_link.py) --

    def _build_partner_section(self, layout: QVBoxLayout) -> None:
        self._partner_title = QLabel()
        self._partner_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._partner_title)

        self._partner_enabled_checkbox = QCheckBox()
        self._partner_enabled_checkbox.toggled.connect(lambda _c: self._emit_partner_config())
        layout.addWidget(self._partner_enabled_checkbox)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self._partner_address_edit = QLineEdit()
        self._partner_address_edit.setMinimumWidth(260)
        self._partner_address_edit.setPlaceholderText("192.168.178.181:8420")
        self._partner_address_edit.editingFinished.connect(self._emit_partner_config)
        self._partner_address_label = QLabel()
        # Kopplung (pairing.py): Token austauschen statt abtippen
        self._partner_pair_button = IconButton("mdi.link-variant", "", text=" ")   # Symbol davor (Review KN-14 a)
        self._partner_pair_button.clicked.connect(
            lambda: self.partner_pair_requested.emit(self._partner_address_edit.text().strip()))
        self._partner_address_edit.textChanged.connect(
            lambda text: self._partner_pair_button.setEnabled(bool(text.strip())))
        self._partner_pair_button.setEnabled(False)
        address_row = QWidget()
        address_layout = QHBoxLayout(address_row)
        address_layout.setContentsMargins(0, 0, 0, 0)
        address_layout.addWidget(self._partner_address_edit)
        address_layout.addWidget(self._partner_pair_button)
        form.addRow(self._partner_address_label, address_row)
        self._partner_token_edit = QLineEdit()
        self._partner_token_edit.setMinimumWidth(260)
        self._partner_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._partner_token_edit.editingFinished.connect(self._emit_partner_config)
        self._partner_token_label = QLabel()
        form.addRow(self._partner_token_label, self._partner_token_edit)
        self._partner_name_edit = QLineEdit()
        self._partner_name_edit.setMaxLength(24)
        self._partner_name_edit.editingFinished.connect(self._emit_partner_config)
        self._partner_name_label = QLabel()
        form.addRow(self._partner_name_label, self._partner_name_edit)
        layout.addLayout(form)

        self._partner_status = QLabel()
        self._partner_status.setWordWrap(True)
        layout.addWidget(self._partner_status)

    def _retranslate_partner(self) -> None:
        self._partner_title.setText(tr("Verbund"))
        self._partner_enabled_checkbox.setText(tr("Verbund aktivieren"))
        self._partner_address_label.setText(tr("Partner-Adresse:"))
        self._partner_pair_button.setText(tr("Koppeln…"))
        self._partner_pair_button.setToolTip(tr(
            "Fragt beim anderen Rechner an. Beide zeigen denselben Code; dort „Koppeln“ tippen, dann "
            "tauschen beide ihre Token aus und schalten den Verbund ein."))
        self._partner_token_label.setText(tr("Partner-Token:"))
        self._partner_name_label.setText(tr("Anzeigename:"))
        self._partner_name_edit.setPlaceholderText(tr("Rechnername"))

    def _emit_partner_config(self) -> None:
        self.partner_config_edited.emit({
            "enabled": self._partner_enabled_checkbox.isChecked(),
            "address": self._partner_address_edit.text().strip(),
            "token": self._partner_token_edit.text().strip(),
            "name": self._partner_name_edit.text().strip(),
        })

    def set_partner_config(self, config: dict) -> None:
        widgets = (self._partner_enabled_checkbox, self._partner_address_edit,
                   self._partner_token_edit, self._partner_name_edit)
        for widget in widgets:
            widget.blockSignals(True)
        self._partner_enabled_checkbox.setChecked(bool(config.get("enabled")))
        self._partner_address_edit.setText(config.get("address", ""))
        self._partner_token_edit.setText(config.get("token", ""))
        self._partner_name_edit.setText(config.get("name", ""))
        for widget in widgets:
            widget.blockSignals(False)
        self._partner_pair_button.setEnabled(bool(self._partner_address_edit.text().strip()))

    def set_partner_status(self, text: str, tone: str) -> None:
        """tone: "success", "danger" oder "muted" (theme.set_tone)."""
        self._partner_status.setText(text)
        set_tone(self._partner_status, tone)

    def _on_share_token_copy(self) -> None:
        QApplication.clipboard().setText(self._share_token_edit.text())

    # -- Netzwerk-Freigabe: Zustand von MainWindow hereinreichen -------------

    def set_share_config(self, config: dict) -> None:
        """Schiebt den persistierten Stand in die Bedienelemente.

        blockSignals rundherum, damit das Befuellen nicht als Nutzeraktion
        zurueckgemeldet wird (gleiches Muster wie set_dark_mode etc.).
        """
        for widget in (self._share_enabled_checkbox, self._share_bind_combo,
                       self._share_port_spin, self._share_read_token_checkbox,
                       self._share_access_log_checkbox, self._share_control_timeout_spin,
                       self._share_local_bypass_checkbox):
            widget.blockSignals(True)
        self._share_enabled_checkbox.setChecked(bool(config.get("enabled")))
        index = self._share_bind_combo.findData(config.get("bind", SHARE_BIND_LOCAL))
        if index >= 0:
            self._share_bind_combo.setCurrentIndex(index)
        self._share_port_spin.setValue(int(config.get("port", SHARE_PORT_MIN)))
        self._share_read_token_checkbox.setChecked(not config.get("read_requires_token", True))
        self._share_access_log_checkbox.setChecked(bool(config.get("access_log")))
        self._share_control_timeout_spin.setValue(
            int(config.get("control_timeout_min", SHARE_CONTROL_TIMEOUT_MIN)))
        self._share_local_bypass_checkbox.setChecked(bool(config.get("local_bypass", True)))
        for widget in (self._share_enabled_checkbox, self._share_bind_combo,
                       self._share_port_spin, self._share_read_token_checkbox,
                       self._share_access_log_checkbox, self._share_control_timeout_spin,
                       self._share_local_bypass_checkbox):
            widget.blockSignals(False)
        self._share_token_edit.setText(config.get("token", ""))
        self._share_table.set_share(config.get("devices", {}))

    def set_share_status(self, text: str, is_error: bool) -> None:
        self._share_status.setText(text)
        set_tone(self._share_status, "danger" if is_error else "muted")

    def set_share_control_state(self, active: bool, text: str) -> None:
        """Zeigt den Zustand des Hauptschalters an (von MainWindow, sekuendlich
        waehrend er an ist). Setzt den Haken ohne Signal -- die Anzeige folgt
        dem Zustand, sie loest ihn nicht aus. Nach einem Ablauf steht der Haken
        so wieder auf aus."""
        self._share_control_checkbox.blockSignals(True)
        self._share_control_checkbox.setChecked(active)
        self._share_control_checkbox.blockSignals(False)
        set_tone(self._share_control_status, "warning" if active else "muted", strong=active)
        self._share_control_status.setText(text)

    def set_share_url(self, url: str) -> None:
        self._share_url_edit.setText(url)

    def _retranslate(self) -> None:
        self._sim_checkbox.setText(tr("Simulationsmodus"))
        self._dark_checkbox.setText(tr("Dark Mode"))
        touch_text = tr("Touch-Bedienung")
        if self._touch_auto:
            touch_text += " " + tr("(automatisch)")
        self._touch_checkbox.setText(touch_text)
        self._notify_checkbox.setText(tr("Desktop-Benachrichtigungen"))
        self._panel_colors_checkbox.setText(tr("Kachelfarben"))
        self._language_label.setText(tr("Sprache:"))
        self._network_dir_label.setText(tr("Netzlaufwerk:"))
        self._network_dir_edit.setPlaceholderText(tr("leer = nur lokal"))
        self._network_dir_edit.setToolTip(
            tr(
                "LabControl legt Log, Oszilloskop-Erfassungen, Berichte und Aufzeichnungen in einem Unterordner "
                "je Rechner ab, Testabläufe und Bausteine gemeinsam. Ist das Netzlaufwerk nicht erreichbar, "
                "bleibt alles lokal und wird später nachgeschoben."
            )
        )
        self._render_log_status()
        self._render_data_status()
        self._help_button.setToolTip(tr("Öffnet das Benutzerhandbuch"))
        self._remove_offline_button.setText(tr("Nicht verbundene Geräte löschen"))
        self._remove_offline_button.setToolTip(
            tr(
                "Vergisst alle Geräte, die gerade nicht verbunden sind, samt Namen, "
                "Sicherheits-Grenzwerten, Panel-Farben und Netzwerk-Freigabe. Verbundene "
                "Geräte bleiben unverändert."
            )
        )
        self._reset_devices_button.setText(tr("Alle Geräte löschen"))
        self._reset_devices_button.setToolTip(
            tr(
                "Löscht alle gespeicherten Geräte-Namen, Sicherheits-Grenzwerte und "
                "Panel-Farben und setzt sie auf die Standardwerte zurück."
            )
        )
        self._scpi_title.setText(tr("Oszilloskope im Netzwerk (SCPI)"))
        self._retranslate_jlink()
        for section in self._info_sections.values():
            section.retranslate()
        for section in self._safety_sections.values():
            section.retranslate()
        self._can_table.retranslate()
        self._can_add_button.setToolTip(tr("Interface hinzufügen"))

        self._subtabs.setTabText(0, tr("Allgemein"))
        self._subtabs.setTabText(1, tr("Geräte"))
        self._subtabs.setTabText(2, tr("CAN-Bus"))
        self._subtabs.setTabText(3, tr("Sicherheit"))
        self._subtabs.setTabText(4, tr("Netzwerk"))
        self._retranslate_network()

    def set_simulation_mode(self, enabled: bool) -> None:
        self._sim_checkbox.blockSignals(True)
        self._sim_checkbox.setChecked(enabled)
        self._sim_checkbox.blockSignals(False)

    def set_touch_mode(self, enabled: bool, automatic: bool = False) -> None:
        """automatic: noch nie selbst gewaehlt, nach Bildschirmgroesse entschieden (Hinweis im Text)."""
        self._touch_checkbox.blockSignals(True)
        self._touch_checkbox.setChecked(enabled)
        self._touch_checkbox.blockSignals(False)
        if automatic != self._touch_auto:
            self._touch_auto = automatic
            self._retranslate()

    def set_dark_mode(self, enabled: bool) -> None:
        self._dark_checkbox.blockSignals(True)
        self._dark_checkbox.setChecked(enabled)
        self._dark_checkbox.blockSignals(False)

    def set_notifications_enabled(self, enabled: bool) -> None:
        self._notify_checkbox.blockSignals(True)
        self._notify_checkbox.setChecked(enabled)
        self._notify_checkbox.blockSignals(False)

    def set_panel_colors_enabled(self, enabled: bool) -> None:
        self._panel_colors_checkbox.blockSignals(True)
        self._panel_colors_checkbox.setChecked(enabled)
        self._panel_colors_checkbox.blockSignals(False)

    def set_language(self, language: str) -> None:
        index = self._language_combo.findData(language)
        if index < 0:
            return
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(index)
        self._language_combo.blockSignals(False)

    def set_network_dir(self, path: str, env_override: str = "") -> None:
        """Feld fuellen. Ist LABCONTROL_NETWORK_DIR gesetzt, gilt die Variable: Feld gesperrt."""
        self._network_dir_edit.blockSignals(True)
        self._network_dir_edit.setText(env_override or path)
        self._network_dir_edit.blockSignals(False)
        self._network_dir_edit.setEnabled(not env_override)
        self._log_env_override = env_override

    def set_data_status(self, status: dict) -> None:
        """Zustand der Datenablage (data_store.DataStore.status())."""
        status = {k: v for k, v in status.items() if k != "last_sync"}
        if status == self._data_status:
            return
        self._data_status = status
        self._render_data_status()

    def _render_data_status(self) -> None:
        status = self._data_status
        if not status or status.get("target") == "off":
            self._data_status_label.setText("")
            return
        files, size = status.get("pending_files", 0), status.get("pending_bytes", 0)
        waiting = tr("{files} Datei(en), {mb} MB warten lokal", files=files, mb=f"{size / 1_048_576:.1f}") if files else ""
        if status.get("target") == "network":
            text = tr("Daten gehen ins Netzlaufwerk: {path}", path=status.get("path", ""))
            text += f" ({waiting})" if waiting else ""
            tone = "success"
        else:
            text = tr("Netzlaufwerk nicht nutzbar ({reason}), Daten bleiben lokal und werden nachgeschoben",
                      reason=status.get("reason") or tr("wird geprüft"))
            text += f": {waiting}" if waiting else ""
            tone = "danger" if status.get("reason") else "muted"
        self._data_status_label.setText(text)
        set_tone(self._data_status_label, tone)

    def set_log_status(self, status: dict) -> None:
        """Wohin das Log gerade schreibt (app_logging.status())."""
        if status == self._log_status:
            return
        self._log_status = dict(status)
        self._render_log_status()

    def _render_log_status(self) -> None:
        status = self._log_status
        if not status:
            self._log_status_label.setText("")
            return
        if status.get("target") == "network":
            text = tr("Log schreibt ins Netzlaufwerk: {path}", path=status.get("path", ""))
            tone = "success"
        elif status.get("reason"):
            text = tr("Netzlaufwerk nicht nutzbar ({reason}), Log schreibt lokal: {path}",
                      reason=status.get("reason", ""), path=status.get("path", ""))
            tone = "danger"
        else:
            text = tr("Log schreibt lokal: {path}", path=status.get("path", ""))
            tone = "muted"
        if getattr(self, "_log_env_override", ""):
            text += " " + tr("(festgelegt über LABCONTROL_NETWORK_DIR)")
        self._log_status_label.setText(text)
        set_tone(self._log_status_label, tone)

    # -- Geraete-Info (aktuell nur microHIL-Firmwareversion) -------------------

    def _ensure_info_section(self, device_id: str, label: str) -> None:
        section = self._info_sections.get(device_id)
        if section is not None:
            section.set_label(label)
            return
        section = _DeviceInfoGroup(label)
        # Zeile mit Stretch statt direktem addWidget(), analog zu
        # on_device_known/_safety_section_rows (siehe dort, BUGS.md #11).
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(section)
        row.addStretch(1)
        self._info_sections_layout.addWidget(row_widget)
        self._info_sections[device_id] = section
        self._info_section_rows[device_id] = row_widget

    def set_hil_firmware_version(self, device_id: str, version: str) -> None:
        """Verbunden mit device_worker.DeviceWorker.hil_info (siehe
        main_window.py) -- zeigt die per `*IDN?` gemeldete Firmwareversion in
        der Geraete-Info-Sektion des zugehoerigen microHIL an."""
        section = self._info_sections.get(device_id)
        if section is not None:
            section.set_firmware_version(version)

    # -- geraete-individuelle Sicherheits-Grenzwerte -------------------------

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        if kind == "hil":
            self._ensure_info_section(device_id, label)
        self._share_table.add_device(kind, device_id, label)
        if not SAFETY_LIMIT_FIELDS.get(kind):
            # CAN/Oszilloskop/HIL sind nicht sicherheitsrelevant (keine
            # Watchdog-Grenzwerte, siehe SAFETY_LIMIT_FIELDS) -- keine leere
            # Sektion im Sicherheit-Reiter anlegen (siehe BUGS_OFFEN.md #24).
            return
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_label(label)
            return
        section = _DeviceSafetyGroup(kind, label)
        section.limit_changed.connect(
            lambda field, enabled, value, d=device_id: self.safety_limit_changed.emit(d, field, enabled, value)
        )
        # Zeile mit Stretch statt direktem addWidget() (siehe BUGS.md #11):
        # ein QGroupBox-Kind einer QVBoxLayout wird sonst auf die volle
        # verfuegbare Breite gestreckt, auch wenn form.setFieldGrowthPolicy
        # oben die Feld-Spalte selbst schon kompakt haelt. Als Wrapper-WIDGET
        # (nicht nur -Layout) angelegt, siehe _safety_section_rows.
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(section)
        row.addStretch(1)
        self._safety_sections_layout.addWidget(row_widget)
        self._safety_sections[device_id] = section
        if device_id in self._psu_ratings:
            section.set_rating_ranges(self._rating_maxima(self._psu_ratings[device_id]))
        self._safety_section_rows[device_id] = row_widget

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self._share_table.set_label(device_id, label)
        info_section = self._info_sections.get(device_id)
        if info_section is not None:
            info_section.set_label(label)
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_label(label)

    def forget_device(self, device_id: str) -> None:
        """Entfernt die Geraete-Info- und Sicherheits-Grenzwert-Sektion eines
        Geraets vollstaendig -- fuer den "Geraetezuordnung loeschen"-Button
        (main_window._on_reset_devices_requested) und fuer ein einzelnes, in
        den Einstellungen geloeschtes CAN-Interface
        (main_window._on_can_configs_changed), siehe
        dashboard.DashboardWidget.forget_device fuer die Begruendung."""
        self._share_table.remove_device(device_id)
        self._info_sections.pop(device_id, None)
        info_row_widget = self._info_section_rows.pop(device_id, None)
        if info_row_widget is not None:
            info_row_widget.deleteLater()
        self._safety_sections.pop(device_id, None)
        row_widget = self._safety_section_rows.pop(device_id, None)
        if row_widget is not None:
            row_widget.deleteLater()

    @staticmethod
    def _rating_maxima(ratings: tuple[float, float]) -> dict[str, float]:
        return {"max_voltage": ratings[0], "max_current": ratings[1]}

    def set_psu_ratings(self, device_id: str, max_voltage: float, max_current: float) -> None:
        """Nennwerte (GMAX) eines Netzteils -> Bereiche seiner Grenzwert-Felder."""
        self._psu_ratings[device_id] = (max_voltage, max_current)
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_rating_ranges(self._rating_maxima((max_voltage, max_current)))

    def set_device_safety_limits(self, device_id: str, limits: dict) -> None:
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_limits(limits)

    # -- CAN-Interfaces -------------------------------------------------------

    def set_can_configs(self, configs: list[dict]) -> None:
        self._can_table.blockSignals(True)
        self._can_table.set_configs(configs)
        self._can_table.blockSignals(False)

    def _on_can_add_clicked(self) -> None:
        self._can_table.add_row()

    def _on_can_table_changed(self) -> None:
        self.can_configs_changed.emit(self._can_table.configs())

    @Slot(str, str)
    def on_can_connect_error(self, device_id: str, message: str) -> None:
        self._can_table.set_connect_error(device_id, message)

    @Slot(str, bool)
    def on_can_connected(self, device_id: str, online: bool) -> None:
        if online:
            self._can_table.clear_connect_error(device_id)

    def _on_help_clicked(self) -> None:
        HelpDialog(self).exec()

    def _on_remove_offline_clicked(self) -> None:
        if QMessageBox.question(
            self,
            tr("Nicht verbundene Geräte löschen"),
            tr(
                "Alle Geräte, die gerade nicht verbunden sind, samt Namen, Sicherheits-"
                "Grenzwerten, Panel-Farben und Netzwerk-Freigabe löschen? Verbundene Geräte "
                "bleiben unverändert. Das lässt sich nicht rückgängig machen."
            ),
        ) != QMessageBox.StandardButton.Yes:
            return
        self.remove_offline_devices_requested.emit()

    def _on_reset_devices_clicked(self) -> None:
        if QMessageBox.question(
            self,
            tr("Alle Geräte löschen"),
            tr(
                "Alle gespeicherten Geräte-Namen, Sicherheits-Grenzwerte und Panel-Farben "
                "wirklich löschen und auf die Standardwerte zurücksetzen? Das lässt sich "
                "nicht rückgängig machen."
            ),
        ) != QMessageBox.StandardButton.Yes:
            return
        self.reset_devices_requested.emit()

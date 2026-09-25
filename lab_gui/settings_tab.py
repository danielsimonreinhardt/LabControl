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
from remote_actions import CONTROL_KINDS
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
    siehe SettingsTab.__init__). Fixe Farbe aus der beim Erzeugen aktuellen
    Palette statt eines nativen QFrame-Rahmens -- analog zu _hint/
    _safety_hint (siehe dort) faerbt SettingsTab bislang nichts bei einem
    spaeteren Theme-Wechsel nach, das gilt fuer diese Linien ebenso."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {current_palette().border};")
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


def _button_row(button: QPushButton) -> QHBoxLayout:
    """Haelt einen QPushButton auf seiner natuerlichen Inhaltsbreite statt
    ihn (QPushButton-Standardverhalten in einem QVBoxLayout, horizontale
    SizePolicy "Minimum" laesst ihn wachsen) auf die volle Tab-Breite zu
    strecken -- analog zur bereits bestehenden language_row."""
    row = QHBoxLayout()
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


_CAN_TABLE_COLUMNS = (
    "interface", "channel", "pick", "status", "bitrate", "serial_baudrate", "label", "dbc", "remove",
)


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


class _CanConfigTable(QTableWidget):
    """Editierbare Liste konfigurierter CAN-Interfaces (siehe settings.py:
    can_configs). Anders als Last/Netzteil keine Hotplug-Autodiscovery (siehe
    can_bus/README.md) -- der Nutzer traegt Interface-Typ/Kanal/Bitrate hier
    explizit ein, ein "..."-Button pro Zeile bietet ueber CanBus.
    discover_configs() gefundene Kanaele als Auswahl an.

    Die Spalte "Serial-Baudrate" ist nur fuer Interface-Typ "slcan" relevant
    (Baudrate der seriellen/USB-Verbindung ZUM Adapter, siehe
    can_bus/driver.py::DEFAULT_SLCAN_SERIAL_BAUDRATE) -- bei Vector/PCAN
    bedeutungslos, daher fuer diese Zeilen deaktiviert statt versteckt (eine
    verschwindende Spalte je Zeile waere verwirrender als ein blosses
    deaktiviertes Feld). Optional laesst sich pro Zeile zusaetzlich eine
    DBC-Datei hinterlegen (siehe _DbcFileCell), fuer die Signal-Decodierung
    empfangener CAN-Frames in control_tab.CanControlGroup (FEATURES.md
    Punkt 3).

    Spalte "Status": zeigt, wenn device_worker.DeviceWorker fuer diese Zeile
    can_connect_error gemeldet hat, ein Warnsymbol mit dem Fehlertext als
    Tooltip (siehe set_connect_error/clear_connect_error) -- vorher landete
    ein gescheiterter Verbindungsversuch AUSSCHLIESSLICH im Log, die Zeile
    sah unveraendert "normal konfiguriert" aus und das Interface tauchte
    ohne jede erkennbare Ursache nirgends in der App auf (Nutzerfeedback,
    genau das bereits aus den Vector-app_name-Bugs bekannte Muster, siehe
    can_bus/driver.py::_vector_bus_kwargs-Docstring)."""

    changed = Signal()  # irgendeine Zeile wurde hinzugefuegt/entfernt/bearbeitet

    def __init__(self) -> None:
        super().__init__(0, len(_CAN_TABLE_COLUMNS))
        self.verticalHeader().setVisible(False)
        for col in (1, 6, 7):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3, 4, 5, 8):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Ein interaktiv bearbeitetes Feld macht den zuletzt angezeigten
        # Verbindungsfehler dieser Zeile ungueltig (neuer Verbindungsversuch
        # steht unmittelbar bevor, siehe set_can_configs) -- blockSignals()
        # in set_configs() unterdrueckt das beim reinen Neuladen aus
        # settings.json (siehe dort), betrifft also wirklich nur echte
        # Nutzer-Edits.
        self.changed.connect(self._clear_all_connect_errors)

    def retranslate(self) -> None:
        self.setHorizontalHeaderLabels(
            [
                tr("Interface"), tr("Kanal"), "", "", tr("Bitrate"), tr("Serial-Baudrate"),
                tr("Bezeichnung"), tr("DBC-Datei"), "",
            ]
        )
        for row in range(self.rowCount()):
            dbc_cell: _DbcFileCell = self.cellWidget(row, 7)
            dbc_cell.retranslate()

    def add_row(self, cfg: dict | None = None) -> None:
        cfg = cfg or {}
        row = self.rowCount()
        self.insertRow(row)

        interface_combo = QComboBox()
        for code in CAN_INTERFACE_LIST:
            interface_combo.addItem(code, code)
        index = interface_combo.findData(cfg.get("interface", CAN_INTERFACE_LIST[0]))
        interface_combo.setCurrentIndex(max(index, 0))
        interface_combo.currentIndexChanged.connect(lambda _=None: self.changed.emit())
        self.setCellWidget(row, 0, interface_combo)

        # editingFinished (Fokusverlust/Enter) statt textChanged: sonst wuerde
        # jeder einzelne Tastendruck sofort einen Rekonfigurations-/Reconnect-
        # Versuch im DeviceWorker anstossen (siehe set_can_configs), waehrend
        # der Nutzer den Kanalnamen noch tippt.
        channel_edit = QLineEdit(str(cfg.get("channel", "")))
        channel_edit.editingFinished.connect(lambda: self.changed.emit())
        # Property "_channel_token" (siehe _pick_channel/configs(), BUGS_
        # GESCHLOSSEN.md #34): haelt das tatsaechliche Kanal-Token fest, wenn
        # das Feld gerade den sprechenden Namen aus dem Auswahl-Popup zeigt
        # statt des Tokens selbst. textEdited (nur bei TASTATUR-Eingabe, im
        # Gegensatz zu textChanged/setText()) verwirft die Property wieder,
        # sobald der Nutzer den Text von Hand aendert -- ab dann gilt wieder
        # der reine Feldinhalt als Kanalwert, wie schon immer bei
        # manuell eingetragenen Kanaelen (z.B. slcan-Portnamen ohne
        # sprechenden Namen).
        def _clear_channel_token(_text=None, e=channel_edit) -> None:
            e.setProperty("_channel_token", None)
            e.setToolTip("")

        channel_edit.textEdited.connect(_clear_channel_token)
        self.setCellWidget(row, 1, channel_edit)

        pick_button = IconButton("mdi.magnify", tr("Verfügbare Kanäle suchen…"))
        pick_button.clicked.connect(lambda _=None, r=row: self._pick_channel(r))
        self.setCellWidget(row, 2, pick_button)

        # Verbindungsfehler-Anzeige (siehe Klassendoc) -- leer/versteckt,
        # solange kein can_connect_error fuer diese Zeile gemeldet wurde.
        status_label = QLabel()
        status_label.hide()
        self.setCellWidget(row, 3, status_label)

        bitrate_spin = SteppedSpinBox(small_step=1000, large_step=100_000)
        bitrate_spin.setRange(10_000, 1_000_000)
        bitrate_spin.setSuffix(" bit/s")
        bitrate_spin.setValue(int(cfg.get("bitrate", CAN_DEFAULT_BITRATE)))
        bitrate_spin.valueChanged.connect(lambda _=None: self.changed.emit())
        self.setCellWidget(row, 4, bitrate_spin)

        serial_baud_spin = SteppedSpinBox(small_step=1200, large_step=57_600)
        serial_baud_spin.setRange(1200, 2_000_000)
        serial_baud_spin.setSuffix(" Bd")
        serial_baud_spin.setValue(int(cfg.get("serial_baudrate", CAN_DEFAULT_SLCAN_SERIAL_BAUDRATE)))
        serial_baud_spin.valueChanged.connect(lambda _=None: self.changed.emit())
        self.setCellWidget(row, 5, serial_baud_spin)

        def _update_serial_baud_enabled(_index=None, spin=serial_baud_spin, combo=interface_combo) -> None:
            spin.setEnabled(combo.currentData() == "slcan")

        interface_combo.currentIndexChanged.connect(_update_serial_baud_enabled)
        _update_serial_baud_enabled()

        label_edit = QLineEdit(str(cfg.get("label", "")))
        label_edit.editingFinished.connect(lambda: self.changed.emit())
        self.setCellWidget(row, 6, label_edit)

        dbc_cell = _DbcFileCell(str(cfg.get("dbc_path", "")))
        dbc_cell.changed.connect(lambda: self.changed.emit())
        dbc_cell.retranslate()
        self.setCellWidget(row, 7, dbc_cell)

        remove_button = IconButton("mdi.trash-can-outline", tr("Entfernen"))
        remove_button.clicked.connect(lambda _=None, w=remove_button: self._remove_row_of(w))
        self.setCellWidget(row, 8, remove_button)

    def _remove_row_of(self, widget: QWidget) -> None:
        for row in range(self.rowCount()):
            if self.cellWidget(row, 8) is widget:
                self.removeRow(row)
                self.changed.emit()
                return

    def _pick_channel(self, row: int) -> None:
        interface_combo: QComboBox = self.cellWidget(row, 0)
        channel_edit: QLineEdit = self.cellWidget(row, 1)
        interface = interface_combo.currentData()
        errors: dict[str, str] = {}
        try:
            configs = CanBus.discover_configs(errors)
        except Exception as exc:
            configs = []
            errors[interface] = str(exc)
        matching = [c for c in configs if c.get("interface") == interface]
        if not matching:
            # Den konkreten Grund mitliefern, falls es einen gibt: ein nicht
            # ladbares Backend (z.B. fehlender Vendor-Treiber, oder ein Build
            # ohne die dynamisch nachgeladenen python-can-Backends) sieht
            # sonst exakt aus wie "Geraet nicht angeschlossen" -- genau diese
            # Ununterscheidbarkeit hat die Ursachensuche lange blockiert,
            # siehe can_bus/driver.py::backend_problem.
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
        # Angezeigt wird der sprechende Name (z.B. "VN1610 Channel 1
        # (S/N 75816)"), gespeichert das eindeutige Kanal-Token aus
        # discover_configs() -- die rohen Kanalnummern doppeln sich zwischen
        # echter Hardware und den virtuellen Vector-Kanaelen und waeren als
        # Auswahl nicht unterscheidbar (siehe can_bus/driver.py).
        by_name: dict[str, str] = {}
        for config in matching:
            name = str(config.get("display_name") or config["channel"])
            # Gleichnamige Eintraege (theoretisch bei zwei baugleichen
            # Geraeten ohne Seriennummer) bleiben durch das angehaengte Token
            # unterscheidbar, statt sich gegenseitig zu ueberschreiben.
            if name in by_name:
                name = f"{name} [{config['channel']}]"
            by_name[name] = str(config["channel"])
        name, ok = QInputDialog.getItem(
            self, tr("Kanal wählen"), tr("Verfügbare Kanäle:"),
            list(by_name), editable=False,
        )
        if ok:
            # Anzeige: sprechender Name wie im Popup (z.B. "CANcaseXL Ch1
            # (S/N 59177)") statt des rohen Tokens (BUGS_GESCHLOSSEN.md
            # #34) -- das tatsaechlich zu verwendende Token bleibt ueber die
            # Property erhalten (siehe add_row/configs()), der Tooltip zeigt
            # es zusaetzlich fuer alle, die z.B. beim Nachschauen in
            # settings.json den rohen Wert sehen wollen.
            token = by_name[name]
            channel_edit.setText(name)
            channel_edit.setProperty("_channel_token", token)
            channel_edit.setToolTip(token)
            self.changed.emit()

    def configs(self) -> list[dict]:
        result = []
        for row in range(self.rowCount()):
            interface_combo: QComboBox = self.cellWidget(row, 0)
            channel_edit: QLineEdit = self.cellWidget(row, 1)
            bitrate_spin: QSpinBox = self.cellWidget(row, 4)
            serial_baud_spin: QSpinBox = self.cellWidget(row, 5)
            label_edit: QLineEdit = self.cellWidget(row, 6)
            dbc_cell: _DbcFileCell = self.cellWidget(row, 7)
            # Zeigt das Feld gerade den sprechenden Namen aus dem
            # Auswahl-Popup an (siehe _pick_channel/add_row, BUGS_
            # GESCHLOSSEN.md #34), gilt weiterhin das dort hinterlegte
            # tatsaechliche Kanal-Token -- NICHT der angezeigte Text.
            token = channel_edit.property("_channel_token")
            channel = str(token).strip() if token else channel_edit.text().strip()
            if not channel:
                continue
            interface = interface_combo.currentData()
            cfg = dict(
                interface=interface,
                channel=channel,
                bitrate=bitrate_spin.value(),
                label=label_edit.text().strip(),
                dbc_path=dbc_cell.path(),
            )
            # serial_baudrate nur bei "slcan" mit abspeichern -- fuer
            # vector/pcan bedeutungslos (siehe can_bus/README.md), wuerde
            # settings.json sonst mit einem fuer diese Zeile irrelevanten
            # Feld fuellen.
            if interface == "slcan":
                cfg["serial_baudrate"] = serial_baud_spin.value()
            result.append(cfg)
        return result

    def _row_device_id(self, row: int) -> str:
        """Wie device_worker.can_device_id(cfg), aber direkt aus den
        aktuellen Zelleninhalten einer Zeile -- fuer den Abgleich mit
        can_connect_error/can_connected, die beide nur die device_id kennen,
        nicht den Zeilenindex (Zeilen koennen sich beim Hinzufuegen/Entfernen
        verschieben)."""
        interface_combo: QComboBox = self.cellWidget(row, 0)
        channel_edit: QLineEdit = self.cellWidget(row, 1)
        token = channel_edit.property("_channel_token")
        channel = str(token).strip() if token else channel_edit.text().strip()
        return f"can:{interface_combo.currentData()}:{channel}"

    def set_connect_error(self, device_id: str, message: str) -> None:
        for row in range(self.rowCount()):
            if self._row_device_id(row) == device_id:
                status_label: QLabel = self.cellWidget(row, 3)
                status_label.setToolTip(message)
                status_label.setPixmap(
                    qta.icon("mdi.alert-circle-outline", color=current_palette().danger)
                    .pixmap(ICON_SIZE)
                )
                status_label.show()
                return

    def clear_connect_error(self, device_id: str) -> None:
        for row in range(self.rowCount()):
            if self._row_device_id(row) == device_id:
                status_label: QLabel = self.cellWidget(row, 3)
                status_label.hide()
                status_label.setToolTip("")
                return

    def _clear_all_connect_errors(self) -> None:
        for row in range(self.rowCount()):
            status_label: QLabel = self.cellWidget(row, 3)
            if status_label is not None:
                status_label.hide()

    def set_configs(self, configs: list[dict]) -> None:
        self.setRowCount(0)
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

    share_changed = Signal(str, bool, bool)  # device_id, read, control

    COL_LABEL, COL_ID, COL_READ, COL_CONTROL = range(4)

    # Geraetearten mit einer Zeile in dieser Tabelle. Das Oszilloskop bleibt
    # draussen (eigenes Sonderpanel, exklusives Handle, keine Werte fuer eine
    # Anzeige). Steuern gibt es nur fuer remote_actions.CONTROL_KINDS: CAN
    # ist lesbar, aber nie fernsteuerbar.
    SUPPORTED_KINDS = ("load", "psu", "can", "hil", "fg")

    def __init__(self) -> None:
        super().__init__(0, 4)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.horizontalHeader().setStretchLastSection(False)
        self._rows: dict[str, int] = {}
        self._kinds: dict[str, str] = {}

    def retranslate(self) -> None:
        self.setHorizontalHeaderLabels(
            [tr("Gerät"), tr("Geräte-ID"), tr("Lesen"), tr("Steuern")]
        )
        for device_id, row in self._rows.items():
            widget = self.cellWidget(row, self.COL_CONTROL)
            if widget is not None:
                widget.setToolTip(self._control_tooltip(self._kinds.get(device_id, "")))

    @staticmethod
    def _control_tooltip(kind: str) -> str:
        if kind in CONTROL_KINDS:
            return tr("Fernsteuerung dieses Geräts erlauben (wirkt nur bei aktivem Hauptschalter)")
        return tr("Dieses Gerät lässt sich nicht fernsteuern")

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
        control = self._box(device_id, self.COL_CONTROL)
        if read is not None and control is not None:
            if column == self.COL_CONTROL and control.isChecked() and not read.isChecked():
                read.blockSignals(True)
                read.setChecked(True)
                read.blockSignals(False)
            elif column == self.COL_READ and not read.isChecked() and control.isChecked():
                control.blockSignals(True)
                control.setChecked(False)
                control.blockSignals(False)
        self._emit(device_id)

    def _emit(self, device_id: str) -> None:
        if device_id not in self._rows:
            return
        read = self._box(device_id, self.COL_READ)
        control = self._box(device_id, self.COL_CONTROL)
        self.share_changed.emit(
            device_id,
            bool(read.isChecked()) if read else False,
            bool(control.isChecked()) if control else False,
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
            for column, key in ((self.COL_READ, "read"), (self.COL_CONTROL, "control")):
                box = self._box(device_id, column)
                if box is None:
                    continue
                box.blockSignals(True)
                box.setChecked(bool(entry.get(key)))
                box.blockSignals(False)


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    language_selected = Signal(str)
    safety_limit_changed = Signal(str, str, bool, float)  # device_id, field, enabled, value
    notifications_toggled = Signal(bool)
    panel_colors_toggled = Signal(bool)
    can_configs_changed = Signal(list)  # list[dict]: interface/channel/bitrate/serial_baudrate/label/dbc_path
    # Nutzer hat die Rueckfrage in _on_reset_devices_clicked bereits mit Ja
    # bestaetigt -- main_window._on_reset_devices_requested fuehrt den
    # eigentlichen Reset aus (DeviceRegistry/Settings kennt dieses Widget
    # nicht direkt).
    reset_devices_requested = Signal()

    # Netzwerk-Freigabe (siehe share_server.py). Wie ueberall im
    # Einstellungen-Tab: nur melden, Settings fasst dieser Tab nie an.
    share_enabled_toggled = Signal(bool)
    share_bind_selected = Signal(str)
    share_port_changed = Signal(int)
    share_token_regenerate_requested = Signal()
    share_read_token_toggled = Signal(bool)
    share_access_log_toggled = Signal(bool)
    share_device_changed = Signal(str, bool, bool)  # device_id, read, control
    share_control_toggled = Signal(bool)            # Hauptschalter Fernsteuerung
    share_local_bypass_toggled = Signal(bool)       # lokal ohne Hauptschalter
    share_control_timeout_changed = Signal(int)     # Minuten

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

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._hint)

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
            self._hint.setVisible(False)
            self._sim_separator.setVisible(False)
        layout.addWidget(self._sim_separator)

        # -- Darstellung/Verhalten -------------------------------------------
        self._dark_checkbox = QCheckBox()
        self._dark_checkbox.toggled.connect(self.dark_mode_toggled)
        layout.addWidget(self._dark_checkbox)

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

        # -- Hilfe -------------------------------------------------------------
        self._help_button = IconButton("mdi.help-circle-outline", "", text=tr("Hilfe"))
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
        self._reset_devices_button = QPushButton()
        self._reset_devices_button.clicked.connect(self._on_reset_devices_clicked)
        layout.addLayout(_button_row(self._reset_devices_button))

        layout.addWidget(_separator())

        # -- Geraete-Info (aktuell nur microHIL-Firmwareversion) --------------
        self._info_hint = QLabel()
        self._info_hint.setWordWrap(True)
        self._info_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._info_hint)

        self._info_sections_layout = QVBoxLayout()
        layout.addLayout(self._info_sections_layout)
        self._info_sections: dict[str, _DeviceInfoGroup] = {}
        self._info_section_rows: dict[str, QWidget] = {}

        layout.addStretch()
        return page

    def _build_can_page(self) -> QWidget:
        """Reiter "CAN-Bus": explizit konfigurierte CAN-Interfaces (siehe
        can_bus/README.md, keine Hotplug-Autodiscovery wie bei Last/Netzteil)."""
        page = QWidget()
        layout = QVBoxLayout(page)

        self._can_hint = QLabel()
        self._can_hint.setWordWrap(True)
        self._can_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._can_hint)

        self._can_table = _CanConfigTable()
        self._can_table.changed.connect(self._on_can_table_changed)
        layout.addWidget(self._can_table)

        self._can_add_button = IconButton("mdi.plus", "", text=tr("Interface hinzufügen"))
        self._can_add_button.clicked.connect(self._on_can_add_clicked)
        layout.addLayout(_button_row(self._can_add_button))

        return page

    def _build_safety_page(self) -> QWidget:
        """Reiter "Sicherheit": geraete-individuelle Grenzwerte (Watchdog,
        siehe safety.py)."""
        page = QWidget()
        layout = QVBoxLayout(page)

        self._safety_hint = QLabel()
        self._safety_hint.setWordWrap(True)
        self._safety_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._safety_hint)

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
        self._share_hint.setText(
            tr(
                "Stellt ausgewählte Kacheln im lokalen Netzwerk bereit: zum Anzeigen, z.B.\n"
                "für ein ESP32-Display oder einen Browser auf dem Handy, und — nur wenn\n"
                "ausdrücklich erlaubt — zum Fernsteuern. Es wird nichts freigegeben, solange\n"
                "unten kein Haken bei „Lesen“ gesetzt ist. Der Zugriff ist unverschlüsselt\n"
                "und nur für ein vertrauenswürdiges Heim- oder Labornetz gedacht — den Port\n"
                "niemals aus dem Internet erreichbar machen (keine Portfreigabe, kein UPnP)."
            )
        )
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
            tr("Zugriffe protokollieren (eigene Datei share_access.log)")
        )
        self._share_control_hint.setText(
            tr(
                "Fernsteuerung: Über das Netzwerk lassen sich Sollwerte setzen und Ausgänge\n"
                "schalten (z.B. durch einen KI-Assistenten über den MCP-Server). Sie gilt nur\n"
                "für Geräte mit Haken bei „Steuern“, nie während eines Testlaufs oder nach\n"
                "einer Sicherheitsabschaltung und immer nur mit Token — von anderen Geräten\n"
                "außerdem nur bei aktivem Hauptschalter. „ALLE AUS“ geht jederzeit."
            )
        )
        self._share_control_checkbox.setText(tr("Fernsteuerung aktiv"))
        self._share_local_bypass_checkbox.setText(
            tr("Zugriffe von diesem PC brauchen den Hauptschalter nicht"))
        self._share_local_bypass_checkbox.setToolTip(
            tr("Gilt für Programme auf diesem Rechner, z.B. den MCP-Server. Token, "
               "„Steuern“ und die Sperren bei Testlauf und Sicherheitsabschaltung bleiben."))
        self._share_control_timeout_label.setText(tr("Schaltet sich ab nach"))
        self._share_devices_hint.setText(
            tr(
                "Freigabe je Gerät: „Lesen“ zeigt die Kachel im Netzwerk, „Steuern“ erlaubt\n"
                "zusätzlich, sie zu bedienen. Steuern setzt Lesen voraus."
            )
        )
        self._share_table.retranslate()

    def _build_network_page(self) -> QWidget:
        """Reiter "Netzwerk": ausgewaehlte Kacheln im lokalen Netz bereitstellen
        (siehe share_server.py). Aufbau bewusst wie _build_can_page(): Hinweis,
        Bedienelemente, dynamische Geraeteliste."""
        page = QWidget()
        layout = QVBoxLayout(page)

        self._share_hint = QLabel()
        self._share_hint.setWordWrap(True)
        self._share_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._share_hint)

        self._share_enabled_checkbox = QCheckBox()
        self._share_enabled_checkbox.toggled.connect(self.share_enabled_toggled)
        layout.addWidget(self._share_enabled_checkbox)

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
        self._share_control_hint = QLabel()
        self._share_control_hint.setWordWrap(True)
        self._share_control_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._share_control_hint)

        # Nicht gespeichert: nach jedem App-Start ist die Fernsteuerung aus.
        # Wer sie einschaltet, gibt bewusst ein Zeitfenster her.
        self._share_control_checkbox = QCheckBox()
        self._share_control_checkbox.toggled.connect(self.share_control_toggled)
        layout.addWidget(self._share_control_checkbox)

        # Ausnahme fuer Programme auf DIESEM Rechner (z.B. den MCP-Server): sie
        # brauchen den Hauptschalter nicht. Token, "Steuern" und die Sperren bleiben.
        self._share_local_bypass_checkbox = QCheckBox()
        self._share_local_bypass_checkbox.toggled.connect(self.share_local_bypass_toggled)
        layout.addWidget(self._share_local_bypass_checkbox)

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

        self._share_devices_hint = QLabel()
        self._share_devices_hint.setWordWrap(True)
        self._share_devices_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._share_devices_hint)

        self._share_table = _ShareTable()
        self._share_table.share_changed.connect(self.share_device_changed)
        layout.addWidget(self._share_table)

        layout.addStretch()
        return page

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
        color = current_palette().danger if is_error else current_palette().text_muted
        self._share_status.setStyleSheet(f"color: {color};")

    def set_share_control_state(self, active: bool, text: str) -> None:
        """Zeigt den Zustand des Hauptschalters an (von MainWindow, sekuendlich
        waehrend er an ist). Setzt den Haken ohne Signal -- die Anzeige folgt
        dem Zustand, sie loest ihn nicht aus. Nach einem Ablauf steht der Haken
        so wieder auf aus."""
        self._share_control_checkbox.blockSignals(True)
        self._share_control_checkbox.setChecked(active)
        self._share_control_checkbox.blockSignals(False)
        color = current_palette().warning if active else current_palette().text_muted
        self._share_control_status.setStyleSheet(f"color: {color}; font-weight: bold;" if active
                                                 else f"color: {color};")
        self._share_control_status.setText(text)

    def set_share_url(self, url: str) -> None:
        self._share_url_edit.setText(url)

    def _retranslate(self) -> None:
        self._sim_checkbox.setText(tr("Simulationsmodus (simulierte Geräte statt Hardware)"))
        self._hint.setText(
            tr(
                "Im Simulationsmodus stehen ein virtuelles Labornetzteil und eine virtuelle\n"
                "elektronische Last im Dashboard/Control-Tab zur Verfuegung, um die GUI ohne\n"
                "angeschlossene Hardware zu testen."
            )
        )
        self._dark_checkbox.setText(tr("Dark Mode (Amber Industrial statt Modern Light)"))
        self._notify_checkbox.setText(tr("Desktop-Benachrichtigung bei Lauf-Ende/Fehler"))
        self._panel_colors_checkbox.setText(tr("Individuelle Panel-Hintergrundfarben (Dashboard/Control)"))
        self._language_label.setText(tr("Sprache:"))
        self._help_button.setText(tr("Hilfe"))
        self._help_button.setToolTip(tr("Öffnet das Benutzerhandbuch"))
        self._reset_devices_button.setText(tr("Gerätezuordnung löschen"))
        self._reset_devices_button.setToolTip(
            tr(
                "Löscht alle gespeicherten Geräte-Namen, Sicherheits-Grenzwerte und "
                "Panel-Farben und setzt sie auf die Standardwerte zurück."
            )
        )
        self._info_hint.setText(tr("Geräte-Info -- z. B. die aktuell geflashte Firmware-Version."))
        for section in self._info_sections.values():
            section.retranslate()
        self._safety_hint.setText(
            tr(
                "Grenzwerte (Sicherheitsabschaltung) je Gerät -- bei Überschreitung werden "
                "alle Ausgänge sofort abgeschaltet (Netzteil: Strom auf 0 A)."
            )
        )
        for section in self._safety_sections.values():
            section.retranslate()
        self._can_hint.setText(
            tr(
                "CAN-Interfaces (Vector, PEAK/PCAN, SLCAN -- z.B. der CAN1-Port des microHIL) "
                "-- werden hier explizit konfiguriert, da anders als bei Last/Netzteil keine "
                "automatische Erkennung möglich ist. \"Serial-Baudrate\" gilt nur für SLCAN "
                "(Baudrate der seriellen/USB-Verbindung zum Adapter, nicht die CAN-Bitrate). "
                "Optional lässt sich je Interface eine DBC-Datei hinterlegen: empfangene "
                "Frames werden dann im Control-Tab zusätzlich zu den Rohdaten als benannte, "
                "skalierte Signale angezeigt."
            )
        )
        self._can_table.retranslate()
        self._can_add_button.setText(tr("Interface hinzufügen"))

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

    def _on_reset_devices_clicked(self) -> None:
        if QMessageBox.question(
            self,
            tr("Gerätezuordnung löschen"),
            tr(
                "Alle gespeicherten Geräte-Namen, Sicherheits-Grenzwerte und Panel-Farben "
                "wirklich löschen und auf die Standardwerte zurücksetzen? Das lässt sich "
                "nicht rückgängig machen."
            ),
        ) != QMessageBox.StandardButton.Yes:
            return
        self.reset_devices_requested.emit()

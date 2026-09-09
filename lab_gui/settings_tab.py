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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
from can_bus.driver import INTERFACE_LIST as CAN_INTERFACE_LIST, CanBus, DEFAULT_BITRATE as CAN_DEFAULT_BITRATE
from help_dialog import HelpDialog
from i18n import AVAILABLE_LANGUAGES, Translator, tr
from icons import IconButton
from paths import IS_FROZEN
from safety import SAFETY_LIMIT_FIELDS
from step_spinbox import SteppedDoubleSpinBox, SteppedSpinBox
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


_CAN_TABLE_COLUMNS = ("interface", "channel", "pick", "bitrate", "label", "dbc", "remove")


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
    discover_configs() gefundene Kanaele als Auswahl an. Optional laesst
    sich pro Zeile zusaetzlich eine DBC-Datei hinterlegen (siehe
    _DbcFileCell), fuer die Signal-Decodierung empfangener CAN-Frames in
    control_tab.CanControlGroup (FEATURES.md Punkt 3)."""

    changed = Signal()  # irgendeine Zeile wurde hinzugefuegt/entfernt/bearbeitet

    def __init__(self) -> None:
        super().__init__(0, len(_CAN_TABLE_COLUMNS))
        self.verticalHeader().setVisible(False)
        for col in (1, 4, 5):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3, 6):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

    def retranslate(self) -> None:
        self.setHorizontalHeaderLabels(
            [tr("Interface"), tr("Kanal"), "", tr("Bitrate"), tr("Bezeichnung"), tr("DBC-Datei"), ""]
        )
        for row in range(self.rowCount()):
            dbc_cell: _DbcFileCell = self.cellWidget(row, 5)
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
        self.setCellWidget(row, 1, channel_edit)

        pick_button = IconButton("mdi.magnify", tr("Verfügbare Kanäle suchen…"))
        pick_button.clicked.connect(lambda _=None, r=row: self._pick_channel(r))
        self.setCellWidget(row, 2, pick_button)

        bitrate_spin = SteppedSpinBox(small_step=1000, large_step=100_000)
        bitrate_spin.setRange(10_000, 1_000_000)
        bitrate_spin.setSuffix(" bit/s")
        bitrate_spin.setValue(int(cfg.get("bitrate", CAN_DEFAULT_BITRATE)))
        bitrate_spin.valueChanged.connect(lambda _=None: self.changed.emit())
        self.setCellWidget(row, 3, bitrate_spin)

        label_edit = QLineEdit(str(cfg.get("label", "")))
        label_edit.editingFinished.connect(lambda: self.changed.emit())
        self.setCellWidget(row, 4, label_edit)

        dbc_cell = _DbcFileCell(str(cfg.get("dbc_path", "")))
        dbc_cell.changed.connect(lambda: self.changed.emit())
        dbc_cell.retranslate()
        self.setCellWidget(row, 5, dbc_cell)

        remove_button = IconButton("mdi.trash-can-outline", tr("Entfernen"))
        remove_button.clicked.connect(lambda _=None, w=remove_button: self._remove_row_of(w))
        self.setCellWidget(row, 6, remove_button)

    def _remove_row_of(self, widget: QWidget) -> None:
        for row in range(self.rowCount()):
            if self.cellWidget(row, 6) is widget:
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
            channel_edit.setText(by_name[name])
            self.changed.emit()

    def configs(self) -> list[dict]:
        result = []
        for row in range(self.rowCount()):
            interface_combo: QComboBox = self.cellWidget(row, 0)
            channel_edit: QLineEdit = self.cellWidget(row, 1)
            bitrate_spin: QSpinBox = self.cellWidget(row, 3)
            label_edit: QLineEdit = self.cellWidget(row, 4)
            dbc_cell: _DbcFileCell = self.cellWidget(row, 5)
            channel = channel_edit.text().strip()
            if not channel:
                continue
            result.append(dict(
                interface=interface_combo.currentData(),
                channel=channel,
                bitrate=bitrate_spin.value(),
                label=label_edit.text().strip(),
                dbc_path=dbc_cell.path(),
            ))
        return result

    def set_configs(self, configs: list[dict]) -> None:
        self.setRowCount(0)
        for cfg in configs:
            self.add_row(cfg)


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    language_selected = Signal(str)
    safety_limit_changed = Signal(str, str, bool, float)  # device_id, field, enabled, value
    notifications_toggled = Signal(bool)
    panel_colors_toggled = Signal(bool)
    can_configs_changed = Signal(list)  # list[dict]: interface/channel/bitrate/label/dbc_path
    # Nutzer hat die Rueckfrage in _on_reset_devices_clicked bereits mit Ja
    # bestaetigt -- main_window._on_reset_devices_requested fuehrt den
    # eigentlichen Reset aus (DeviceRegistry/Settings kennt dieses Widget
    # nicht direkt).
    reset_devices_requested = Signal()

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
        # Umschliessendes Zeilen-Widget je Sektion (siehe on_device_known) --
        # ermoeglicht forget_device(), die komplette Zeile (Sektion + den
        # Stretch daneben) mit einem einzigen deleteLater() zu entfernen,
        # statt zusaetzlich das QHBoxLayout-Zeilenobjekt selbst verwalten zu
        # muessen.
        self._safety_section_rows: dict[str, QWidget] = {}

        layout.addStretch()
        return page

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
                "CAN-Interfaces (Vector, PEAK/PCAN) -- werden hier explizit konfiguriert, "
                "da anders als bei Last/Netzteil keine automatische Erkennung möglich ist. "
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
        self._safety_section_rows[device_id] = row_widget

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        info_section = self._info_sections.get(device_id)
        if info_section is not None:
            info_section.set_label(label)
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_label(label)

    def forget_device(self, device_id: str) -> None:
        """Entfernt die Geraete-Info- und Sicherheits-Grenzwert-Sektion eines
        Geraets vollstaendig -- nur fuer den "Geraetezuordnung loeschen"-Button
        (main_window._on_reset_devices_requested) gedacht, siehe
        dashboard.DashboardWidget.forget_device fuer die Begruendung."""
        self._info_sections.pop(device_id, None)
        info_row_widget = self._info_section_rows.pop(device_id, None)
        if info_row_widget is not None:
            info_row_widget.deleteLater()
        self._safety_sections.pop(device_id, None)
        row_widget = self._safety_section_rows.pop(device_id, None)
        if row_widget is not None:
            row_widget.deleteLater()

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

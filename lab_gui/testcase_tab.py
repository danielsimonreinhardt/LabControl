"""Testcase-Tab: zeilenbasierter Editor + Ausfuehrung fuer Testablauf-Schritte.

Jede Zeile beschreibt einen Schritt: Geraet, Aktion, Wert, Dauer (Wartezeit
nach dem Schritt) und ob der Schritt aktiv ist. Die eigentliche Ausfuehrung
uebernimmt ein TestRunner (siehe testcase_runner.py), der ueber
run_requested/stop_requested angesteuert wird.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from check_dialog import CheckDialog
from condition_dialog import ConditionDialog
from i18n import Translator, tr
from icons import IconButton
from paths import app_dir
from signal_dialog import SignalDialog
from theme import Palette, ThemeManager
from theme import current as current_palette
from testcase_model import (
    ACTION_VALUE_RANGE,
    ARB_TARGETS,
    CONTROL_STEP_LABELS,
    DEVICE_ACTIONS,
    DEVICE_KIND_LABELS,
    STEP_TYPE_ACTION,
    VALUELESS_ACTIONS,
    TestStep,
    action_label,
    check_summary,
    condition_summary,
    is_arb_action,
    kind_label,
    load_steps,
    save_steps,
    validate_structure,
)

# Spaltenindizes der Tabelle (siehe BASE_COLUMNS) -- als benannte Konstanten,
# damit ein spaeteres Einfuegen/Umsortieren von Spalten nicht wieder alle
# verstreuten Literalindizes brechen kann.
COL_NUM, COL_DEVICE, COL_ACTION, COL_VALUE, COL_DURATION, COL_CHECK, COL_ENABLED = range(7)

# step_type -> welche Spalten eine Kontrollfluss-Zeile tatsaechlich mit einem
# Widget belegt (COL_NUM = Zeilennummer ist immer vorhanden, COL_DEVICE immer
# ein Label; COL_CHECK bleibt bei Kontrollfluss-Zeilen leer -- Pruefungen gibt
# es nur an Aktionsschritten). Nur zur Dokumentation der Zeilenlayouts, siehe
# _build_control_row:
#   loop:            COL_VALUE = Durchlaufzahl-Spinbox,        COL_ENABLED = Aktiv
#   while/if:        COL_VALUE = Bedingungs-Zusammenfassung,   COL_ENABLED = Aktiv (nur "while"/"if"-Zeile selbst)
#   else/end:        keine weiteren Spalten
#   set_var/inc_var: COL_ACTION = Variablenname, COL_VALUE = Wert, COL_DURATION = Dauer, COL_ENABLED = Aktiv
CONTROL_ROW_WITH_CHECKBOX = {"loop", "while", "if", "set_var", "inc_var"}


def _cond_params_to_step(params: dict) -> TestStep:
    return TestStep(
        cond_source=params["cond_source"],
        cond_device_kind=params["cond_device_kind"],
        cond_device_id=params["cond_device_id"],
        cond_field=params["cond_field"],
        cond_op=params["cond_op"],
        cond_value=params["cond_value"],
        cond_time_ref=params["cond_time_ref"],
        cond_var=params["cond_var"],
    )


def _check_params_to_step(params: dict) -> TestStep:
    return TestStep(
        check_enabled=params["enabled"],
        check_field=params["field"],
        check_min=params["min"],
        check_max=params["max"],
        check_abort=params["abort"],
    )

# Deutsche Basis-Anzeigenamen (Uebersetzungsschluessel) der Spaltenkoepfe.
# "#" ist sprachunabhaengig.
BASE_COLUMNS = ["#", "Gerät", "Aktion", "Wert", "Dauer (s)", "Prüfung", "Aktiv"]
# Default-Spaltenbreiten (Pixel) fuer die Standard-Fenstergroesse (1000x700
# Hauptfenster -> ca. 958px Tabellenbreite). Aus einem vom Nutzer vorgegebenen
# Referenz-Screenshot als Anteile ermittelt und auf diese Breite umgerechnet --
# absolute Pixelwerte 1:1 aus dem (viel breiteren) Screenshot zu uebernehmen
# liess der Aktion-Spalte bei der Standardgroesse kaum Platz. Aktion (Index 2)
# bleibt Stretch und nimmt sich den Rest; alle Spalten bleiben per Drag&Drop
# veraenderbar (Interactive-Resize).
DEFAULT_COLUMN_WIDTHS = {0: 49, 1: 167, 3: 119, 4: 94, 5: 150, 6: 77}

DEFAULT_DIR = app_dir() / "testcases"

BLINK_INTERVAL_MS = 400

# Trennzeichen fuer den itemData-Schluessel des Geraete-Combos (siehe
# _device_key/_parse_device_key). QComboBox.findData() vergleicht userData
# ueber Qt/PySide-QVariant-Konvertierung; bei zusammengesetzten Python-Objekten
# (Tupel) wie zuvor verwendet ist dieser Vergleich in der Praxis nicht
# zuverlaessig (abhaengig davon, wie das Tupel konstruiert wurde, liefert
# findData() teils -1 fuer ein eigentlich vorhandenes, gleiches Tupel). Ein
# einfacher String-Schluessel umgeht das zuverlaessig.
_DEVICE_KEY_SEP = "\x1f"


def _device_key(kind: str, device_id: str) -> str:
    return f"{kind}{_DEVICE_KEY_SEP}{device_id}"


def _parse_device_key(key: str | None) -> tuple[str, str]:
    if not key:
        return "load", ""
    kind, _, device_id = key.partition(_DEVICE_KEY_SEP)
    return kind, device_id


class TestcaseTab(QWidget):
    run_requested = Signal()
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        # device_id -> (kind, label) fuer alle in dieser Session bekannten Geraete;
        # gespeist von DeviceRegistry ueber on_device_known()/on_label_changed().
        self._known_devices: dict[str, tuple[str, str]] = {}

        # psu device_id -> (OVP, OCP), gespeist von DeviceWorker.psu_limits ueber
        # on_psu_limits(). Nur fuer Zeilen mit konkret ausgewaehltem Geraet nutzbar
        # (bei "automatisch" steht das Zielgeraet erst zur Laufzeit fest).
        self._psu_limits: dict[str, tuple[float, float]] = {}

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_row = -1
        self._blink_on = False
        self._error_row = -1
        self._selected_row = -1

        # Ergebnisse der Pass/Fail-Pruefungen des aktuellen/letzten Laufs:
        # Zeile -> bestanden. "Sticky fail": schlaegt ein Schritt in EINER
        # Schleifeniteration fehl, bleibt die Zeile rot, auch wenn spaetere
        # Iterationen bestehen. Die Zaehler zaehlen dagegen jede Ausfuehrung
        # (ein Pruefschritt in einer 10er-Schleife = 10 Pruefungen).
        self._check_results: dict[int, bool] = {}
        self._checks_total = 0
        self._checks_failed = 0

        # "Bereit"/"Läuft…"/"Fertig"/"Gestoppt" (siehe _set_status) statt
        # eines Fehlertexts -- fuer die Retranslate braucht der aktuelle
        # Status einen stabilen Schluessel statt des schon uebersetzten Texts.
        self._status_key = "Bereit"
        self._status_key_kwargs: dict = {}
        # Anzeige des aktuell aktiven Durchlaufs (innerste laufende
        # Schleife/While, siehe on_iteration_changed) -- bereits uebersetzter
        # Text, der an die "Schritt x/y..."-Statuszeile angehaengt wird.
        self._iteration_text = ""

        # Aus validate_structure() (siehe testcase_model.py), von
        # _revalidate_structure() nach jeder Strukturaenderung neu berechnet;
        # Runner.start() validiert zusaetzlich als Backstop.
        self._structure_ok = True
        self._is_running = False

        button_row = QHBoxLayout()
        self._add_button = IconButton("mdi.plus", "")
        self._remove_button = IconButton("mdi.minus", "")
        self._up_button = IconButton("mdi.arrow-up", "")
        self._down_button = IconButton("mdi.arrow-down", "")
        self._load_button = IconButton("mdi.folder-open-outline", "")
        self._save_button = IconButton("mdi.content-save-outline", "")
        self._add_menu = QMenu(self._add_button)
        self._action_add_action = self._add_menu.addAction("")
        self._action_add_loop = self._add_menu.addAction("")
        self._action_add_while = self._add_menu.addAction("")
        self._action_add_if = self._add_menu.addAction("")
        self._action_add_else = self._add_menu.addAction("")
        self._action_add_end = self._add_menu.addAction("")
        self._action_add_set_var = self._add_menu.addAction("")
        self._action_add_inc_var = self._add_menu.addAction("")
        self._action_add_action.triggered.connect(lambda: self._insert_new_step(TestStep()))
        self._action_add_loop.triggered.connect(lambda: self._insert_block("loop"))
        self._action_add_while.triggered.connect(lambda: self._insert_block("while"))
        self._action_add_if.triggered.connect(lambda: self._insert_block("if"))
        self._action_add_else.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="else")))
        self._action_add_end.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="end")))
        self._action_add_set_var.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="set_var")))
        self._action_add_inc_var.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="inc_var")))
        self._add_button.setMenu(self._add_menu)
        self._remove_button.clicked.connect(self._remove_selected_row)
        self._up_button.clicked.connect(lambda: self._move_selected_row(-1))
        self._down_button.clicked.connect(lambda: self._move_selected_row(1))
        self._load_button.clicked.connect(self._load_from_file)
        self._save_button.clicked.connect(self._save_to_file)
        for button in (self._add_button, self._remove_button, self._up_button, self._down_button):
            button_row.addWidget(button)
        button_row.addStretch()
        button_row.addWidget(self._load_button)
        button_row.addWidget(self._save_button)
        layout.addLayout(button_row)

        self._table = QTableWidget(0, len(BASE_COLUMNS))
        header = self._table.horizontalHeader()
        for col in range(len(BASE_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        for col, width in DEFAULT_COLUMN_WIDTHS.items():
            header.resizeSection(col, width)
        header.setSectionResizeMode(COL_ACTION, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

        run_row = QHBoxLayout()
        self._run_button = IconButton("mdi.play", "", text=tr("Start"))
        self._stop_button = IconButton("mdi.stop", "", text=tr("Stop"))
        self._stop_button.setEnabled(False)
        self._run_button.clicked.connect(self.run_requested.emit)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._status_label = QLabel()
        self._status_kind = "info"  # "info" | "success" | "error", siehe _set_status
        self._status_label.setStyleSheet(f"color: {current_palette().text_muted};")
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._stop_button)
        run_row.addWidget(self._status_label)
        run_row.addStretch()
        layout.addLayout(run_row)

        ThemeManager.instance().changed.connect(self._on_theme_changed)

        self._add_row_clicked()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._table.setHorizontalHeaderLabels([tr(c) if c != "#" else c for c in BASE_COLUMNS])
        self._add_button.setToolTip(tr("Zeile hinzufügen"))
        self._remove_button.setToolTip(tr("Zeile entfernen"))
        self._up_button.setToolTip(tr("Nach oben"))
        self._down_button.setToolTip(tr("Nach unten"))
        self._load_button.setToolTip(tr("Laden…"))
        self._save_button.setToolTip(tr("Speichern…"))
        self._action_add_action.setText(tr("Aktionsschritt"))
        self._action_add_loop.setText(tr("Schleife (n×) … Ende"))
        self._action_add_while.setText(tr("Solange … Ende"))
        self._action_add_if.setText(tr("Wenn … Ende"))
        self._action_add_else.setText(tr("Sonst"))
        self._action_add_end.setText(tr("Ende"))
        self._action_add_set_var.setText(tr("Variable setzen"))
        self._action_add_inc_var.setText(tr("Variable erhöhen"))
        self._run_button.setToolTip(tr("Start"))
        self._run_button.setText(tr("Start"))
        self._stop_button.setToolTip(tr("Stop"))
        self._stop_button.setText(tr("Stop"))
        self._set_status(self._status_key, kind=self._status_kind, **self._status_key_kwargs)
        for row in range(self._table.rowCount()):
            self._retranslate_row(row)
        self._revalidate_structure()

    def _retranslate_row(self, row: int) -> None:
        number_item = self._table.item(row, COL_NUM)
        step_type = number_item.data(Qt.ItemDataRole.UserRole) or STEP_TYPE_ACTION
        if step_type != STEP_TYPE_ACTION:
            self._retranslate_control_row(row, step_type)
            return

        device_combo: QComboBox = self._table.cellWidget(row, COL_DEVICE)
        action_combo: QComboBox = self._table.cellWidget(row, COL_ACTION)
        value_stack: QStackedWidget = self._table.cellWidget(row, COL_VALUE)
        arb_page: QWidget = value_stack.widget(1)

        # _populate_device_combo() emits currentIndexChanged unconditionally
        # (siehe dort), was ueber die verkabelten Row-Closures die
        # Aktions-Combo mit ihrem urspruenglichen (bei Zeilenerstellung
        # gesetzten) Default neu befuellen wuerde -- die aktuell ausgewaehlte
        # Aktion vorher sichern und danach explizit wiederherstellen.
        current_action = action_combo.currentData()
        self._populate_device_combo(device_combo, _parse_device_key(device_combo.currentData()))
        self._refresh_actions_for_row(action_combo, device_combo, current_action)
        arb_page._refresh_summary()
        arb_page._arb_button.setToolTip(tr("Signal definieren…"))
        check_page = self._table.cellWidget(row, COL_CHECK)
        check_page._refresh_summary()
        check_page._check_button.setToolTip(tr("Prüfung…"))

    def _retranslate_control_row(self, row: int, step_type: str) -> None:
        label: QLabel = self._table.cellWidget(row, COL_DEVICE)
        if label is not None and step_type != "end":
            label.setText(tr(CONTROL_STEP_LABELS.get(step_type, step_type)))
        elif label is not None and not label.text():
            label.setText(tr("Ende"))  # vorlaeufig, _revalidate_structure ergaenzt den Blocktyp
        if step_type in ("while", "if"):
            container = self._table.cellWidget(row, COL_VALUE)
            cond_button = container.findChild(IconButton)
            if cond_button is not None:
                cond_button.setToolTip(tr("Bedingung…"))
            container._refresh_summary()
        if step_type in ("set_var", "inc_var"):
            name_edit: QLineEdit = self._table.cellWidget(row, COL_ACTION)
            if name_edit is not None:
                name_edit.setPlaceholderText(tr("Variablenname"))

    def _on_theme_changed(self, palette: Palette) -> None:
        self._apply_status_style()
        # Arbiträrsignal-Zusammenfassung je Zeile (arb_page, Index 1 im
        # value_stack) haengt nicht am ThemeManager-Signal -- Zeilen kommen
        # und gehen (Zeile hinzufuegen/entfernen/verschieben), ein direktes
        # Signal-Connect pro Label wuerde beim Entfernen einer Zeile nicht
        # sauber wieder abgehaengt. Stattdessen hier zentral ueber die aktuell
        # vorhandenen Zeilen iterieren.
        for row in range(self._table.rowCount()):
            value_stack = self._table.cellWidget(row, COL_VALUE)
            if isinstance(value_stack, QStackedWidget):
                arb_page = value_stack.widget(1)
                arb_summary_label = arb_page.findChild(QLabel)
                arb_summary_label.setStyleSheet(f"color: {palette.text_muted}; font-style: italic;")
                refresh = getattr(value_stack.widget(0), "_refresh_limit_warning", None)
                if refresh is not None:
                    refresh()
                check_page = self._table.cellWidget(row, COL_CHECK)
                check_label = getattr(check_page, "_summary_label", None)
                if check_label is not None:
                    check_label.setStyleSheet(f"color: {palette.text_muted}; font-style: italic;")
            elif value_stack is not None:
                # Kontrollfluss-Zeile (while/if-Bedingungscontainer oder
                # loop-Spinbox, siehe _build_control_row) -- kein
                # QStackedWidget, hoechstens eine Zusammenfassungs-Label
                # nachzufaerben.
                summary_label = value_stack.findChild(QLabel)
                if summary_label is not None:
                    summary_label.setStyleSheet(f"color: {palette.text_muted}; font-style: italic;")
            self._apply_row_style(row)

    # -- Statusanzeige ----------------------------------------------------------

    def _set_status(self, key: str, kind: str = "info", **kwargs) -> None:
        self._status_key = key
        self._status_key_kwargs = kwargs
        self._status_kind = kind
        self._apply_status_style()
        self._status_label.setText(tr(key, **kwargs))

    def _apply_status_style(self) -> None:
        pal = current_palette()
        if self._status_kind == "error":
            self._status_label.setStyleSheet(f"color: {pal.danger}; font-weight: bold;")
        elif self._status_kind == "success":
            self._status_label.setStyleSheet(f"color: {pal.success}; font-weight: bold;")
        else:
            self._status_label.setStyleSheet(f"color: {pal.text_muted};")

    # -- Geraeteregistrierung (von MainWindow/DeviceRegistry gespeist) --------

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        self._known_devices[device_id] = (kind, label)
        self._refresh_device_combos()

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self._known_devices[device_id] = (kind, label)
        self._refresh_device_combos()

    def on_psu_limits(self, device_id: str, ovp: float, ocp: float) -> None:
        self._psu_limits[device_id] = (ovp, ocp)
        for row in range(self._table.rowCount()):
            value_stack = self._table.cellWidget(row, COL_VALUE)
            if not isinstance(value_stack, QStackedWidget):
                continue  # Kontrollfluss-Zeile -- kein Wert-Feld mit OVP/OCP-Warnung
            value_spin = value_stack.widget(0)
            refresh = getattr(value_spin, "_refresh_limit_warning", None)
            if refresh is not None:
                refresh()

    def _refresh_device_combos(self) -> None:
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, COL_DEVICE)
            if isinstance(combo, QComboBox):
                self._populate_device_combo(combo, _parse_device_key(combo.currentData()))

    def _condition_device_items(self) -> list[tuple[str, str, str]]:
        """Wie _device_combo_items(), aber als (Anzeigetext, kind, device_id)
        statt eines kodierten Combo-Schluessels -- fuer ConditionDialog, das
        (anders als die Zeilen-Closures hier) keinen Zugriff auf
        testcase_tab._device_key/_parse_device_key haben soll."""
        items = [
            (tr("{kind} (automatisch)", kind=kind_label(kind)), kind, "")
            for kind in DEVICE_KIND_LABELS
        ]
        for device_id, (kind, label) in sorted(self._known_devices.items(), key=lambda kv: kv[1][1]):
            items.append((f"{label} ({kind_label(kind)})", kind, device_id))
        return items

    def _device_combo_items(self) -> list[tuple[str, str]]:
        items = [
            (tr("{kind} (automatisch)", kind=kind_label(kind)), _device_key(kind, ""))
            for kind in DEVICE_KIND_LABELS
        ]
        for device_id, (kind, label) in sorted(self._known_devices.items(), key=lambda kv: kv[1][1]):
            items.append((f"{label} ({kind_label(kind)})", _device_key(kind, device_id)))
        return items

    def _populate_device_combo(
        self, combo: QComboBox, selected: tuple[str, str] | None
    ) -> None:
        if selected is None:
            selected = ("load", "")
        selected_key = _device_key(*selected)
        combo.blockSignals(True)
        combo.clear()
        for text, key in self._device_combo_items():
            combo.addItem(text, key)
        index = combo.findData(selected_key)
        if index < 0:
            kind, device_id = selected
            if device_id:
                # Aus einer gespeicherten Datei geladenes Geraet, das diese
                # Session noch nicht gesehen hat -- Eintrag beibehalten statt
                # die Auswahl stillschweigend zu verwerfen.
                combo.addItem(tr("{device_id} (nicht verbunden)", device_id=device_id), selected_key)
                index = combo.count() - 1
            else:
                index = max(combo.findData(_device_key(kind, "")), 0)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.currentIndexChanged.emit(index)

    def _device_display(self, kind: str, device_id: str) -> str:
        if device_id in self._known_devices:
            return self._known_devices[device_id][1]
        base = kind_label(kind)
        if not device_id:
            return tr("{kind} (automatisch)", kind=base)
        return tr("{kind} ({device_id}, nicht verbunden)", kind=base, device_id=device_id)

    # -- Zeilenverwaltung -----------------------------------------------------

    def _add_row_clicked(self) -> None:
        self._insert_new_step(TestStep())

    def _insert_at_selection(self) -> int:
        """Einfuegeposition fuer einen neuen Schritt: direkt nach der
        markierten Zeile, sonst ans Ende -- so landen neu eingefuegte
        Kontrollfluss-Bloecke da, wo der Nutzer gerade arbeitet, statt immer
        am Tabellenende."""
        if self._selected_row >= 0:
            return self._selected_row + 1
        return self._table.rowCount()

    def _insert_new_step(self, step: TestStep) -> None:
        index = self._insert_at_selection()
        self._insert_row(index, step)
        self._table.selectRow(index)
        self._revalidate_structure()

    def _insert_block(self, kind: str) -> None:
        """Fuegt einen Block-Start (loop/while/if) zusammen mit seinem
        passenden "Ende" als Paar ein, mit leerem Rumpf dazwischen."""
        index = self._insert_at_selection()
        self._insert_row(index, TestStep(step_type=kind))
        self._insert_row(index + 1, TestStep(step_type="end"))
        self._table.selectRow(index)
        self._revalidate_structure()

    def _refresh_actions_for_row(
        self, action_combo: QComboBox, device_combo: QComboBox, current_action: str
    ) -> None:
        kind, _device_id = _parse_device_key(device_combo.currentData())
        action_combo.blockSignals(True)
        action_combo.clear()
        for code in DEVICE_ACTIONS[kind]:
            action_combo.addItem(action_label(kind, code), code)
        index = action_combo.findData(current_action)
        action_combo.setCurrentIndex(max(index, 0))
        action_combo.blockSignals(False)
        action_combo.currentIndexChanged.emit(action_combo.currentIndex())

    def _insert_row(self, row_index: int, step: TestStep) -> None:
        self._table.insertRow(row_index)

        number_item = QTableWidgetItem()
        number_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        number_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        number_item.setData(Qt.ItemDataRole.UserRole, step.step_type)
        self._table.setItem(row_index, COL_NUM, number_item)

        if step.step_type == STEP_TYPE_ACTION:
            self._build_action_row(row_index, step)
        else:
            self._build_control_row(row_index, step)

        self._renumber_rows()

    def _build_action_row(self, row_index: int, step: TestStep) -> None:
        device_combo = QComboBox()
        self._table.setCellWidget(row_index, COL_DEVICE, device_combo)

        action_combo = QComboBox()
        self._table.setCellWidget(row_index, COL_ACTION, action_combo)

        value_spin = QDoubleSpinBox()
        value_spin.setDecimals(3)

        arb_page = QWidget()
        arb_layout = QHBoxLayout(arb_page)
        arb_layout.setContentsMargins(2, 0, 2, 0)
        arb_summary_label = QLabel()
        arb_summary_label.setStyleSheet(f"color: {current_palette().text_muted}; font-style: italic;")
        arb_button = IconButton("mdi.sine-wave", tr("Signal definieren…"))
        arb_page._arb_button = arb_button
        arb_layout.addWidget(arb_summary_label, 1)
        arb_layout.addWidget(arb_button)
        arb_page._params = dict(
            shape=step.arb_shape,
            target=step.arb_target or ARB_TARGETS[step.device_kind][0],
            amplitude=step.arb_amplitude,
            offset=step.arb_offset,
            frequency=step.arb_frequency,
            interval_ms=step.arb_interval_ms,
        )

        value_stack = QStackedWidget()
        value_stack.addWidget(value_spin)  # Index 0: normaler Zahlenwert
        value_stack.addWidget(arb_page)    # Index 1: Arbiträrsignal-Zusammenfassung + Button
        self._table.setCellWidget(row_index, COL_VALUE, value_stack)

        duration_spin = QDoubleSpinBox()
        duration_spin.setRange(0, 36000)
        duration_spin.setDecimals(1)
        duration_spin.setSuffix(" s")
        duration_spin.setValue(step.duration)
        self._table.setCellWidget(row_index, COL_DURATION, duration_spin)

        # Pass/Fail-Pruefung: Zusammenfassung + Dialog-Button, gleiches Muster
        # wie die Bedingungszelle einer while/if-Zeile (siehe _build_control_row).
        check_page = QWidget()
        check_page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        check_page_layout = QHBoxLayout(check_page)
        check_page_layout.setContentsMargins(2, 0, 2, 0)
        check_summary_label = QLabel()
        check_summary_label.setStyleSheet(f"color: {current_palette().text_muted}; font-style: italic;")
        check_button = IconButton("mdi.checkbox-marked-circle-outline", tr("Prüfung…"))
        check_page_layout.addWidget(check_summary_label, 1)
        check_page_layout.addWidget(check_button)
        check_page._summary_label = check_summary_label
        check_page._check_button = check_button
        check_page._check_params = dict(
            enabled=step.check_enabled,
            field=step.check_field,
            min=step.check_min,
            max=step.check_max,
            abort=step.check_abort,
        )

        def refresh_check_summary() -> None:
            params = check_page._check_params
            if not params["enabled"]:
                check_summary_label.setText("–")
                return
            summary = check_summary(_check_params_to_step(params))
            if params["abort"]:
                summary = f"{summary} {tr('(Abbruch)')}"
            check_summary_label.setText(summary)

        check_page._refresh_summary = refresh_check_summary
        refresh_check_summary()

        def open_check_dialog() -> None:
            dialog = CheckDialog(
                check_page._check_params,
                is_arb=is_arb_action(action_combo.currentData() or ""),
                parent=self,
            )
            if dialog.exec() == CheckDialog.DialogCode.Accepted:
                check_page._check_params = dialog.params()
                refresh_check_summary()

        check_button.clicked.connect(open_check_dialog)
        self._table.setCellWidget(row_index, COL_CHECK, check_page)

        enabled_check = QCheckBox()
        enabled_check.setChecked(step.enabled)
        enabled_container = QWidget()
        enabled_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        enabled_layout = QHBoxLayout(enabled_container)
        enabled_layout.addWidget(enabled_check)
        enabled_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        enabled_layout.setContentsMargins(0, 0, 0, 0)
        self._table.setCellWidget(row_index, COL_ENABLED, enabled_container)

        def current_kind() -> str:
            return _parse_device_key(device_combo.currentData())[0]

        def refresh_actions(current_action: str = step.action) -> None:
            self._refresh_actions_for_row(action_combo, device_combo, current_action)

        def refresh_value_warning() -> None:
            kind = current_kind()
            code = action_combo.currentData() or ""
            device_id = _parse_device_key(device_combo.currentData())[1]
            limits = self._psu_limits.get(device_id) if kind == "psu" and device_id else None
            threshold = None
            if limits is not None:
                if code == "PSU_VOLT":
                    threshold, limit_label = limits[0], "OVP"
                elif code == "PSU_CURR":
                    threshold, limit_label = limits[1], "OCP"
            if threshold is not None and value_spin.value() > threshold:
                value_spin.setStyleSheet(f"border: 1px solid {current_palette().warning};")
                value_spin.setToolTip(
                    tr(
                        "{value:g} liegt über der aktuellen {limit_label}-Schwelle ({threshold:g}) "
                        "-- wird vom Netzteil kommentarlos abgelehnt.",
                        value=value_spin.value(), limit_label=limit_label, threshold=threshold,
                    )
                )
            else:
                value_spin.setStyleSheet("")
                value_spin.setToolTip("")

        value_spin._refresh_limit_warning = refresh_value_warning
        value_spin.valueChanged.connect(refresh_value_warning)

        def refresh_arb_summary() -> None:
            kind = current_kind()
            params = arb_page._params
            shape_label = tr("Sinus") if params["shape"] != "square" else tr("Rechteck")
            target_label = action_label(kind, params["target"]) if params["target"] else "?"
            arb_summary_label.setText(
                f"{shape_label}: {target_label}, {params['offset']:g}±{params['amplitude']:g}, "
                f"{params['frequency']:g} Hz"
            )

        arb_page._refresh_summary = refresh_arb_summary

        def open_signal_dialog() -> None:
            kind = current_kind()
            device_id = _parse_device_key(device_combo.currentData())[1]
            limits = self._psu_limits.get(device_id) if kind == "psu" and device_id else None
            dialog = SignalDialog(kind, duration_spin.value(), arb_page._params, self, limits=limits)
            if dialog.exec() == SignalDialog.DialogCode.Accepted:
                arb_page._params = dialog.params()
                refresh_arb_summary()

        arb_button.clicked.connect(open_signal_dialog)

        def on_action_changed(index: int) -> None:
            kind = current_kind()
            code = action_combo.itemData(index) or ""
            unit, lo, hi = ACTION_VALUE_RANGE.get(code, ("", -1000, 10000))
            value_spin.setSuffix(f" {unit}" if unit else "")
            value_spin.setRange(lo, hi)
            value_spin.setEnabled(code not in VALUELESS_ACTIONS)
            if is_arb_action(code):
                if arb_page._params.get("target") not in ARB_TARGETS.get(kind, []):
                    arb_page._params["target"] = ARB_TARGETS[kind][0]
                refresh_arb_summary()
                value_stack.setCurrentIndex(1)
            else:
                value_stack.setCurrentIndex(0)
            refresh_value_warning()

        device_combo.currentIndexChanged.connect(lambda _=None: refresh_value_warning())
        device_combo.currentIndexChanged.connect(lambda _=None: refresh_actions())
        action_combo.currentIndexChanged.connect(on_action_changed)
        self._populate_device_combo(device_combo, (step.device_kind, step.device_id))
        value_spin.setValue(step.value)

    def _build_control_row(self, row_index: int, step: TestStep) -> None:
        t = step.step_type

        label = QLabel()
        label.setStyleSheet("font-weight: bold;")
        self._table.setCellWidget(row_index, COL_DEVICE, label)

        if t in CONTROL_ROW_WITH_CHECKBOX:
            enabled_check = QCheckBox()
            enabled_check.setChecked(step.enabled)
            enabled_container = QWidget()
            enabled_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            enabled_layout = QHBoxLayout(enabled_container)
            enabled_layout.addWidget(enabled_check)
            enabled_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            enabled_layout.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(row_index, COL_ENABLED, enabled_container)

        if t == "loop":
            count_spin = QSpinBox()
            count_spin.setRange(1, 100000)
            count_spin.setSuffix(" ×")
            count_spin.setValue(max(step.loop_count, 1))
            count_spin.valueChanged.connect(lambda _=None: self._revalidate_structure())
            self._table.setCellWidget(row_index, COL_VALUE, count_spin)

        elif t in ("while", "if"):
            container = QWidget()
            h_layout = QHBoxLayout(container)
            h_layout.setContentsMargins(2, 0, 2, 0)
            summary_label = QLabel()
            summary_label.setStyleSheet(f"color: {current_palette().text_muted}; font-style: italic;")
            cond_button = IconButton("mdi.help-rhombus-outline", tr("Bedingung…"))
            h_layout.addWidget(summary_label, 1)
            h_layout.addWidget(cond_button)
            container._cond_params = dict(
                cond_source=step.cond_source,
                cond_device_kind=step.cond_device_kind,
                cond_device_id=step.cond_device_id,
                cond_field=step.cond_field,
                cond_op=step.cond_op,
                cond_value=step.cond_value,
                cond_time_ref=step.cond_time_ref,
                cond_var=step.cond_var,
            )
            if t == "while":
                container._cond_params["max_iterations"] = step.max_iterations

            def refresh_summary() -> None:
                summary_label.setText(condition_summary(_cond_params_to_step(container._cond_params)))

            container._refresh_summary = refresh_summary
            refresh_summary()

            def open_condition_dialog() -> None:
                dialog = ConditionDialog(
                    container._cond_params, self._condition_device_items(), is_while=(t == "while"), parent=self
                )
                if dialog.exec() == ConditionDialog.DialogCode.Accepted:
                    container._cond_params = dialog.params()
                    refresh_summary()
                    self._revalidate_structure()

            cond_button.clicked.connect(open_condition_dialog)
            self._table.setCellWidget(row_index, COL_VALUE, container)

        elif t in ("set_var", "inc_var"):
            name_edit = QLineEdit(step.var_name)
            name_edit.setPlaceholderText(tr("Variablenname"))
            name_edit.textChanged.connect(lambda _=None: self._revalidate_structure())
            self._table.setCellWidget(row_index, COL_ACTION, name_edit)

            value_spin = QDoubleSpinBox()
            value_spin.setDecimals(3)
            value_spin.setRange(-1e9, 1e9)
            value_spin.setValue(step.value)
            self._table.setCellWidget(row_index, COL_VALUE, value_spin)

            duration_spin = QDoubleSpinBox()
            duration_spin.setRange(0, 36000)
            duration_spin.setDecimals(1)
            duration_spin.setSuffix(" s")
            duration_spin.setValue(step.duration)
            self._table.setCellWidget(row_index, COL_DURATION, duration_spin)

        self._retranslate_control_row(row_index, t)

    def _remove_selected_row(self) -> None:
        # Bewusst self._selected_row (die tatsaechlich markierte Zeile) statt
        # currentRow() (Tastatur-/Eingabefokus) -- sonst entfernt/verschiebt
        # der Button eine andere Zeile als die farblich hervorgehobene, sobald
        # der Fokus z.B. durch einen Klick in ein Wert-/Dauer-Feld einer
        # anderen Zeile abgewandert ist, ohne die Markierung zu aendern.
        row = self._selected_row
        if row < 0:
            return
        # Qt feuert waehrend removeRow() ein itemSelectionChanged mit einer
        # selbst gewaehlten (nicht immer sinnvollen, z.B. auf die letzte
        # Zeile springenden) Zwischenauswahl. Reagiert unser Handler darauf,
        # faerbt er eine Zeile ein, die durch die Index-Verschiebung danach
        # nicht mehr dieselben Widgets referenziert -- die Farbe blieb dann
        # an der falschen (verschobenen) Zeile haengen. Deshalb waehrend der
        # Tabellenmutation die Signale blocken und den Endzustand danach
        # einmalig sauber neu berechnen, statt auf Zwischenereignisse zu
        # reagieren (siehe auch _move_selected_row).
        self._table.blockSignals(True)
        self._table.removeRow(row)
        self._table.blockSignals(False)
        self._renumber_rows()
        self._resync_selection()
        self._revalidate_structure()

    def _move_selected_row(self, offset: int) -> None:
        row = self._selected_row
        target = row + offset
        if row < 0 or not (0 <= target < self._table.rowCount()):
            return
        step = self._row_to_step(row)
        self._table.blockSignals(True)
        self._table.removeRow(row)
        self._insert_row(target, step)
        self._table.selectRow(target)
        self._table.blockSignals(False)
        self._resync_selection()
        self._revalidate_structure()

    def _resync_selection(self) -> None:
        """Liest die tatsaechliche Tabellenauswahl neu ein und faerbt alle
        Zeilen einmalig konsistent neu -- als Abschluss einer Zeilenmutation
        (Verschieben/Entfernen), waehrend der Tabellensignale blockiert waren
        (siehe _move_selected_row/_remove_selected_row)."""
        rows = self._table.selectionModel().selectedRows()
        self._selected_row = rows[0].row() if rows else -1
        for row in range(self._table.rowCount()):
            self._apply_row_style(row)

    def _renumber_rows(self) -> None:
        for row in range(self._table.rowCount()):
            self._table.item(row, COL_NUM).setText(str(row + 1))

    # -- Lesen/Schreiben der Zeilen -------------------------------------------

    def _row_to_step(self, row: int) -> TestStep:
        number_item = self._table.item(row, COL_NUM)
        step_type = number_item.data(Qt.ItemDataRole.UserRole) or STEP_TYPE_ACTION
        if step_type == STEP_TYPE_ACTION:
            return self._action_row_to_step(row)
        return self._control_row_to_step(row, step_type)

    def _action_row_to_step(self, row: int) -> TestStep:
        device_combo: QComboBox = self._table.cellWidget(row, COL_DEVICE)
        action_combo: QComboBox = self._table.cellWidget(row, COL_ACTION)
        value_stack: QStackedWidget = self._table.cellWidget(row, COL_VALUE)
        value_spin: QDoubleSpinBox = value_stack.widget(0)
        arb_page: QWidget = value_stack.widget(1)
        duration_spin: QDoubleSpinBox = self._table.cellWidget(row, COL_DURATION)
        check_page = self._table.cellWidget(row, COL_CHECK)
        enabled_container = self._table.cellWidget(row, COL_ENABLED)
        enabled_check: QCheckBox = enabled_container.findChild(QCheckBox)

        kind, device_id = _parse_device_key(device_combo.currentData())
        action_code = action_combo.currentData() or ""
        params = arb_page._params
        check_params = check_page._check_params
        return TestStep(
            device_kind=kind,
            device_id=device_id,
            action=action_code,
            value=value_spin.value(),
            duration=duration_spin.value(),
            enabled=enabled_check.isChecked(),
            arb_shape=params["shape"],
            arb_target=params["target"],
            arb_amplitude=params["amplitude"],
            arb_offset=params["offset"],
            arb_frequency=params["frequency"],
            arb_interval_ms=params["interval_ms"],
            check_enabled=check_params["enabled"],
            check_field=check_params["field"],
            check_min=check_params["min"],
            check_max=check_params["max"],
            check_abort=check_params["abort"],
        )

    def _control_row_to_step(self, row: int, step_type: str) -> TestStep:
        enabled = True
        enabled_container = self._table.cellWidget(row, COL_ENABLED)
        if enabled_container is not None:
            enabled_check = enabled_container.findChild(QCheckBox)
            if enabled_check is not None:
                enabled = enabled_check.isChecked()

        if step_type == "loop":
            count_spin: QSpinBox = self._table.cellWidget(row, COL_VALUE)
            return TestStep(step_type="loop", loop_count=count_spin.value(), enabled=enabled)

        if step_type in ("while", "if"):
            container = self._table.cellWidget(row, COL_VALUE)
            params = container._cond_params
            return TestStep(
                step_type=step_type,
                enabled=enabled,
                max_iterations=params.get("max_iterations", 1000) if step_type == "while" else 1000,
                cond_source=params["cond_source"],
                cond_device_kind=params["cond_device_kind"],
                cond_device_id=params["cond_device_id"],
                cond_field=params["cond_field"],
                cond_op=params["cond_op"],
                cond_value=params["cond_value"],
                cond_time_ref=params["cond_time_ref"],
                cond_var=params["cond_var"],
            )

        if step_type in ("set_var", "inc_var"):
            name_edit: QLineEdit = self._table.cellWidget(row, COL_ACTION)
            value_spin: QDoubleSpinBox = self._table.cellWidget(row, COL_VALUE)
            duration_spin: QDoubleSpinBox = self._table.cellWidget(row, COL_DURATION)
            return TestStep(
                step_type=step_type,
                enabled=enabled,
                var_name=name_edit.text().strip(),
                value=value_spin.value(),
                duration=duration_spin.value(),
            )

        # "else"/"end": keine weiteren Felder.
        return TestStep(step_type=step_type)

    def steps(self) -> list[TestStep]:
        return [self._row_to_step(row) for row in range(self._table.rowCount())]

    # -- Speichern/Laden -----------------------------------------------------

    def _save_to_file(self) -> None:
        DEFAULT_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Testablauf speichern"), str(DEFAULT_DIR), tr("Testablauf (*.json)")
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix != ".json":
            path = path.with_suffix(".json")
        try:
            save_steps(self.steps(), path)
        except OSError as exc:
            QMessageBox.critical(self, tr("Fehler beim Speichern"), str(exc))

    def _load_from_file(self) -> None:
        DEFAULT_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Testablauf laden"), str(DEFAULT_DIR), tr("Testablauf (*.json)")
        )
        if not path_str:
            return
        try:
            steps = load_steps(Path(path_str))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, tr("Fehler beim Laden"), str(exc))
            return

        self._table.setRowCount(0)
        for step in steps:
            self._insert_row(self._table.rowCount(), step)
        if not steps:
            self._add_row_clicked()
        self._revalidate_structure()

    # -- Struktur-Validierung (Schleifen/If/While-Verschachtelung) --------------
    #
    # Nach jeder Zeilenmutation neu berechnet: Einrueckungstiefe je Zeile
    # (Anzeige ueber padding-left am Label/Combo in Spalte 1, siehe
    # _apply_row_style), "Ende (Schleife/Solange/Wenn)"-Beschriftung, und ob
    # der Start-Button ueberhaupt gedrueckt werden darf. testcase_runner.start()
    # validiert zusaetzlich als Backstop (Dateien koennen von Hand bearbeitet
    # oder aus einer aelteren Programmversion geladen worden sein).

    def _revalidate_structure(self) -> None:
        steps = self.steps()
        matching, depths, errors = validate_structure(steps)
        error_by_row = dict(errors)
        end_kind_labels = {"loop": tr("Schleife"), "while": tr("Solange"), "if": tr("Wenn")}

        for row in range(self._table.rowCount()):
            step = steps[row]
            col1 = self._table.cellWidget(row, COL_DEVICE)
            if col1 is None:
                continue
            col1._indent_depth = depths[row] if row < len(depths) else 0
            col1._structure_error = row in error_by_row
            if row in error_by_row:
                col1.setToolTip(error_by_row[row])
            elif step.step_type == "end" and row in matching:
                suffix = end_kind_labels.get(steps[matching[row].start_index].step_type, "")
                col1.setText(tr("Ende ({kind})", kind=suffix) if suffix else tr("Ende"))
                col1.setToolTip("")
            elif step.step_type in ("while", "if"):
                col1.setToolTip("")
            else:
                col1.setToolTip("")

        self._structure_ok = not errors
        if errors:
            self._run_button.setToolTip(
                tr("Struktur unvollständig: {message}", message=error_by_row[errors[0][0]])
            )
        else:
            self._run_button.setToolTip(tr("Start"))
        self._update_run_enabled()

        for row in range(self._table.rowCount()):
            self._apply_row_style(row)

    def _update_run_enabled(self) -> None:
        self._run_button.setEnabled(not self._is_running and self._structure_ok)

    # -- Ausfuehrung -----------------------------------------------------------

    def set_running(self, running: bool) -> None:
        self._is_running = running
        self._stop_button.setEnabled(running)
        self._table.setEnabled(not running)
        for button in (
            self._add_button,
            self._remove_button,
            self._up_button,
            self._down_button,
            self._load_button,
            self._save_button,
        ):
            button.setEnabled(not running)
        self._update_run_enabled()

    def on_run_started(self) -> None:
        self._clear_all_row_colors()
        self._iteration_text = ""
        self.set_running(True)
        self._set_status("Läuft…")

    def on_iteration_changed(self, _row: int, iteration: int, total: int) -> None:
        # Nur die innerste gerade aktive Schleife/While wird angezeigt (siehe
        # testcase_runner.iteration_changed) -- bei verschachtelten Schleifen
        # ein bewusster Kompromiss statt einer vollen Stack-Anzeige.
        if total:
            self._iteration_text = tr("(Durchlauf {i}/{n})", i=iteration, n=total)
        else:
            self._iteration_text = tr("(Durchlauf {i})", i=iteration)

    def on_step_started(self, index: int, step: TestStep) -> None:
        self._start_blink(index)
        total = self._table.rowCount()
        if step.step_type in ("set_var", "inc_var"):
            op = "=" if step.step_type == "set_var" else "+="
            detail = f"{step.var_name} {op} {step.value:g}"
            self._set_status(
                "Schritt {index}/{total}: {detail} {iter}",
                index=index + 1, total=total, detail=detail, iter=self._iteration_text,
            )
            return
        label = action_label(step.device_kind, step.action)
        device_display = self._device_display(step.device_kind, step.device_id)
        if is_arb_action(step.action):
            shape = tr("Sinus") if step.arb_shape != "square" else tr("Rechteck")
            target_label = action_label(step.device_kind, step.arb_target)
            detail = tr(
                "{shape} auf {target}, {offset:g}±{amplitude:g}, {frequency:g} Hz, {duration:g} s",
                shape=shape,
                target=target_label,
                offset=step.arb_offset,
                amplitude=step.arb_amplitude,
                frequency=step.arb_frequency,
                duration=step.duration,
            )
        else:
            detail = f"{step.value}"
        self._set_status(
            "Schritt {index}/{total}: {device} – {action} ({detail}) {iter}",
            index=index + 1,
            total=total,
            device=device_display,
            action=label,
            detail=detail,
            iter=self._iteration_text,
        )

    def on_step_result(self, index: int, passed: bool, value: float) -> None:
        self._checks_total += 1
        if not passed:
            self._checks_failed += 1
        self._check_results[index] = self._check_results.get(index, True) and passed
        self._apply_row_style(index)
        check_page = self._table.cellWidget(index, COL_CHECK)
        summary_label = getattr(check_page, "_summary_label", None)
        if summary_label is not None:
            summary_label.setToolTip(tr("Gemessen: {value:g}", value=value))

    def on_run_finished(self) -> None:
        self._stop_blink()
        self._iteration_text = ""
        self.set_running(False)
        if self._checks_total:
            if self._checks_failed:
                self._set_status(
                    "Fertig – NICHT bestanden ({failed}/{total} Prüfungen fehlgeschlagen)",
                    kind="error",
                    failed=self._checks_failed,
                    total=self._checks_total,
                )
            else:
                self._set_status(
                    "Fertig – BESTANDEN ({total} Prüfungen)",
                    kind="success",
                    total=self._checks_total,
                )
        else:
            self._set_status("Fertig")

    def on_run_stopped(self) -> None:
        self._stop_blink()
        self._iteration_text = ""
        self.set_running(False)
        self._set_status("Gestoppt")

    def on_step_failed(self, index: int, message: str) -> None:
        self._stop_blink()
        self._error_row = index
        self._apply_row_style(index)
        # Ablauf ist intern zwar schon gestoppt, aber der Fehler bleibt sichtbar
        # (rote Zeile) und die Bedienelemente gesperrt, bis er ueber "Stop"
        # quittiert wird -- set_running(True) haelt dafuer den Stop-Button aktiv.
        self.set_running(True)
        total = self._table.rowCount()
        self._set_status(
            "Fehler bei Schritt {index}/{total}: {message} — mit Stop quittieren",
            kind="error",
            index=index + 1,
            total=total,
            message=message,
        )

    def _on_stop_clicked(self) -> None:
        self.stop_requested.emit()
        self._stop_blink()
        self._iteration_text = ""
        self._clear_all_row_colors()
        self.set_running(False)
        self._set_status("Gestoppt")

    # -- Auswahl-/Blink-/Fehleranzeige -----------------------------------------
    #
    # Eine Zeile kann gleichzeitig "ausgewaehlt" (Klick des Nutzers), "aktiv"
    # (blinkt gruen waehrend der Ausfuehrung) und/oder "fehlgeschlagen" (rot,
    # bis per Stop quittiert) sein. _row_style_color() loest das nach fester
    # Prioritaet auf (Fehler > Blinken > Auswahl), damit sich die Zustaende
    # nicht gegenseitig unbeabsichtigt ueberschreiben -- z.B. darf ein Klick
    # auf die gerade blinkende Zeile das Gruen nicht durch die (dezentere)
    # Auswahlfarbe ersetzen. Farben kommen aus der aktiven Palette (siehe
    # theme.py), damit die Hervorhebung zu beiden Farb-Themes passt.

    def _row_style_color(self, row: int) -> str | None:
        pal = current_palette()
        if row == self._error_row:
            return pal.danger
        if row == self._blink_row and self._blink_on:
            return pal.success
        # Dauerhaftes Pass/Fail-Ergebnis einer Pruefung (siehe on_step_result):
        # unter Fehler/Blinken (die gerade laufende Zeile soll sichtbar
        # blinken, auch wenn sie in einer frueheren Schleifeniteration schon
        # ein Ergebnis hat), aber ueber der dezenteren Auswahlfarbe.
        if row in self._check_results:
            return pal.success if self._check_results[row] else pal.danger
        if row == self._selected_row:
            return pal.selection
        return None

    def _apply_row_style(self, row: int) -> None:
        if row < 0 or row >= self._table.rowCount():
            return
        color = self._row_style_color(row)
        style = f"background-color: {color};" if color else ""
        combo_style = self._combo_row_style(color)
        for col in range(1, self._table.columnCount()):
            widget = self._table.cellWidget(row, col)
            if widget is None:
                continue
            if col == COL_DEVICE:
                # Spalte 1 traegt zusaetzlich die Einrueckung (Verschachtelung
                # von Schleifen/If) und ggf. eine Fehlermarkierung, siehe
                # _revalidate_structure() -- beides an _indent_depth/
                # _structure_error am Widget selbst abgelegt, weil dieselbe
                # Zeile beim Verschieben/Neuladen komplett neu aufgebaut wird.
                depth = getattr(widget, "_indent_depth", 0)
                has_error = getattr(widget, "_structure_error", False)
                border = f"border: 1px solid {current_palette().danger};" if has_error else ""
                if isinstance(widget, QComboBox):
                    widget.setStyleSheet(self._combo_row_style(color, depth * 18, border))
                else:
                    base = f"background-color: {color};" if color else ""
                    widget.setStyleSheet(f"{base} padding-left: {depth * 18}px; {border}")
            else:
                widget.setStyleSheet(combo_style if isinstance(widget, QComboBox) else style)
        number_item = self._table.item(row, COL_NUM)
        if number_item is not None:
            number_item.setBackground(QBrush(QColor(color)) if color else QBrush())

    def _combo_row_style(self, color: str | None, indent_px: int = 0, border: str = "") -> str:
        # QComboBox braucht einen eigenen Stylesheet-Zweig: eine einfache
        # "background-color: ...;"-Deklaration ohne Selektor faerbt zwar die
        # Box selbst, wird aber zusaetzlich an das Popup (QAbstractItemView)
        # vererbt, das intern als Kind-Widget an der Combobox haengt -- ohne
        # den expliziten Gegen-Selektor waere beim Aufklappen die GESAMTE
        # Liste eingefaerbt statt nur der Zeile im Hintergrund. Sobald das
        # Popup ueberhaupt ein eigenes Stylesheet bekommt, schaltet Qt fuer
        # dessen Eintraege von der palettenbasierten Standarddarstellung auf
        # reines CSS-Rendering um -- die Standard-Hoverhervorhebung faellt
        # dann ersatzlos weg, wenn man sie nicht explizit ueber
        # "::item:hover"/"::item:selected" nachbaut (die globale
        # "selection-background-color"-Regel aus theme.py greift hier NICHT
        # mehr, weil dieses spezifischere Stylesheet sie ueberschreibt).
        pal = current_palette()
        combo_bg = f"background-color: {color};" if color else ""
        indent = f"padding-left: {indent_px}px;" if indent_px else ""
        return (
            f"QComboBox {{ {combo_bg} {indent} {border} }}"
            f"QComboBox QAbstractItemView {{"
            f" background-color: {pal.surface}; color: {pal.text}; }}"
            f"QComboBox QAbstractItemView::item {{"
            f" background-color: {pal.surface}; color: {pal.text}; padding: 3px 6px; }}"
            f"QComboBox QAbstractItemView::item:hover {{"
            f" background-color: {pal.selection}; color: {pal.text}; }}"
            f"QComboBox QAbstractItemView::item:selected {{"
            f" background-color: {pal.selection}; color: {pal.text}; }}"
        )

    def _on_selection_changed(self) -> None:
        # Bewusst ueber das Selection-Model statt currentRow() -- currentRow()
        # ist der Fokus/die "current item", die beim Entfernen/Einfuegen von
        # Zeilen (siehe _move_selected_row: removeRow + _insert_row +
        # selectRow) kurzzeitig von der tatsaechlich markierten Zeile
        # abweichen kann. Das fuehrte dazu, dass nach einem Verschieben eine
        # andere als die verschobene Zeile farblich hervorgehoben blieb.
        rows = self._table.selectionModel().selectedRows()
        row = rows[0].row() if rows else -1
        if row == self._selected_row:
            return
        old_row = self._selected_row
        self._selected_row = row
        if old_row >= 0:
            self._apply_row_style(old_row)
        if row >= 0:
            self._apply_row_style(row)

    def _start_blink(self, row: int) -> None:
        self._stop_blink()
        self._blink_row = row
        self._blink_on = False
        self._toggle_blink()
        self._blink_timer.start(BLINK_INTERVAL_MS)

    def _stop_blink(self) -> None:
        self._blink_timer.stop()
        row = self._blink_row
        self._blink_row = -1
        if row >= 0:
            self._apply_row_style(row)

    def _toggle_blink(self) -> None:
        self._blink_on = not self._blink_on
        self._apply_row_style(self._blink_row)

    def _clear_all_row_colors(self) -> None:
        self._error_row = -1
        self._check_results.clear()
        self._checks_total = 0
        self._checks_failed = 0
        for row in range(self._table.rowCount()):
            self._apply_row_style(row)
            # Auch den "Gemessen: ..."-Tooltip des letzten Laufs entfernen.
            check_page = self._table.cellWidget(row, COL_CHECK)
            summary_label = getattr(check_page, "_summary_label", None)
            if summary_label is not None:
                summary_label.setToolTip("")

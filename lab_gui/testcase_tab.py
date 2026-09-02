"""Testcase-Tab: zeilenbasierter Editor + Ausfuehrung fuer Testablauf-Schritte.

Jede Zeile beschreibt einen Schritt: Geraet, Aktion, Wert, Dauer (Wartezeit
nach dem Schritt) und ob der Schritt aktiv ist. Die eigentliche Ausfuehrung
uebernimmt ein TestRunner (siehe testcase_runner.py), der ueber
run_requested/stop_requested angesteuert wird.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon
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
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from block_dialog import SaveBlockDialog
from check_dialog import CheckDialog
from condition_dialog import ConditionDialog
from i18n import Translator, tr
from icons import IconButton, SplitIconButton
from paths import app_dir
from signal_dialog import SignalDialog
from step_spinbox import SteppedDoubleSpinBox, SteppedSpinBox
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
    arb_shape_label,
    check_summary,
    condition_summary,
    is_arb_action,
    kind_label,
    load_block,
    load_steps,
    save_block,
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
#   wait:            COL_DURATION = Wartezeit,                 COL_ENABLED = Aktiv
CONTROL_ROW_WITH_CHECKBOX = {"loop", "while", "if", "set_var", "inc_var", "wait"}


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
# Default-Spaltenbreiten (Pixel) fuer die drei Spalten mit fester Breite
# (siehe FIXED_COLUMNS) bei der Standard-Fenstergroesse (1000x700 Hauptfenster
# -> ca. 958px Tabellenbreite). Alle uebrigen Spalten (Geraet/Aktion/Wert/
# Pruefung) teilen sich den verbleibenden Platz gleichmaessig per Stretch-
# Resize-Modus (siehe __init__) -- kein fester Wert noetig/sinnvoll.
# Spalte 0 etwas breiter als eine reine "9"-Ziffer + Klapp-Icon braeuchte --
# Baustein-Unternummern ("4.1", "4.2", ...) sind laenger als einstellige
# Hauptnummern (siehe _renumber_rows).
DEFAULT_COLUMN_WIDTHS = {0: 58, 4: 94, 6: 77}

# Spalten mit fester Breite (Zeilennummer/Dauer/Aktiv) -- bleiben beim
# Skalieren des Hauptfensters unveraendert. Alle anderen Spalten (inkl.
# "Aktion") sind Stretch und teilen sich den Rest gleichmaessig, statt dass
# "Aktion" allein den kompletten uebrigen Platz einnimmt.
FIXED_COLUMNS = {COL_NUM, COL_DURATION, COL_ENABLED}

# Rechtsversatz (Pixel), um den eine Baustein-Mitgliederzeile ALS GANZES
# (die komplette Zeile als grafisches Element, nicht ihr Inhalt innerhalb
# der Zellen) nach rechts verschoben wird -- sichtbare Leerflaeche am
# Zeilenanfang, wie bei hierarchisch untergeordneten Eintraegen. Umgesetzt
# ueber echte Widget-Geometrie (_apply_member_row_shifts) plus einen
# versetzt zeichnenden Delegate fuer die Item-Spalte "#"
# (_NumColumnDelegate) -- Stylesheet-Padding scheidet aus, weil es je nach
# Widget-Typ nur den Inhalt einrueckt statt das Element zu verschieben.
BLOCK_MEMBER_INDENT_PX = 16


class _NumColumnDelegate(QStyledItemDelegate):
    """Zeichnet die Zelle der Spalte "#" einer Baustein-Mitgliederzeile um
    BLOCK_MEMBER_INDENT_PX nach rechts versetzt (Hintergrund UND Nummer) --
    die Spalte ist ein QTableWidgetItem statt eines Zellen-Widgets, ihre
    Darstellung laesst sich daher nur ueber den Delegate verschieben
    (Stylesheet-Padding und fuehrende Leerzeichen im Text bleiben dort
    wirkungslos, per Screenshot-Vergleich verifiziert). Das Gegenstueck fuer
    die Zellen-WIDGETS der uebrigen Spalten ist
    TestcaseTab._apply_member_row_shifts."""

    def __init__(self, indent_for_row, parent=None) -> None:
        super().__init__(parent)
        # Callback statt direkter TestcaseTab-Referenz, damit der Delegate
        # nichts ueber Gruppen/Kopfzeilen wissen muss.
        self._indent_for_row = indent_for_row

    def paint(self, painter, option, index) -> None:  # noqa: N802 (Qt-Override)
        indent = self._indent_for_row(index.row())
        if indent:
            option = QStyleOptionViewItem(option)
            # translated() statt adjusted(): adjusted(indent, 0, 0, 0) zieht
            # nur den linken Rand nach innen und VERKLEINERT die Zelle -- der
            # zentrierte Text wandert dadurch nur um den halben Betrag (per
            # Pixelmessung bestaetigt: 8 statt 16 px) und der Versatz wirkte
            # wie gar keiner. translated() verschiebt die ganze Zelle; was
            # rechts hinausragt, klippt die Tabelle ohnehin an der Zellgrenze.
            option.rect = option.rect.translated(indent, 0)
        super().paint(painter, option, index)


DEFAULT_DIR = app_dir() / "testcases"
# Ablage fuer wiederverwendbare Bausteine (siehe block_dialog.SaveBlockDialog,
# save_block/load_block in testcase_model.py) -- eigenes Verzeichnis statt
# DEFAULT_DIR, da ein Baustein kein vollstaendiger Testablauf ist und beim
# Laden ueber "Testablauf laden…" nicht versehentlich als solcher geoeffnet
# werden soll.
BLOCKS_DIR = app_dir() / "blocks"

BLINK_INTERVAL_MS = 400

# Zeichen, die in Windows-Dateinamen ungueltig sind -- fuer den Default-
# Dateinamen im Speichern-Dialog eines Bausteins (siehe _save_block), dessen
# Name frei eingegeben wird. Der Nutzer kann den vorgeschlagenen Dateinamen im
# QFileDialog jederzeit noch anpassen, dies ist nur eine sinnvolle Vorbelegung.
_INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def _sanitize_filename(name: str) -> str:
    cleaned = "".join(" " if c in _INVALID_FILENAME_CHARS else c for c in name).strip()
    return cleaned or "baustein"

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


@dataclass
class _BlockGroup:
    """Rein editorseitige Gruppierung eines ueber "Baustein einfügen"
    hinzugefuegten Zeilenbereichs (siehe TestcaseTab._insert_block_from_file)
    -- erlaubt, den Bereich im Editor auf die erste (Kopf-)Zeile einzuklappen.

    Wirkt sich NICHT auf die Testschritt-Daten aus: steps()/Speichern/
    Ausfuehrung kennen keine Gruppen und iterieren immer ueber alle Zeilen,
    auch eingeklappt verborgene. Wird beim Speichern/Laden eines Testablaufs
    nicht mit persistiert -- nur eine Anzeige-Erleichterung fuer die aktuelle
    Editier-Sitzung, siehe TestcaseTab._on_row_inserted/_on_row_removed fuer
    die Nachfuehrung bei Zeilenmutationen.

    start: Zeilenindex der Kopfzeile (immer sichtbar, zeigt den ersten
        Baustein-Schritt normal an -- "nur eine Testschrittzeile" im
        eingeklappten Zustand).
    count: Gesamtzahl Zeilen inkl. Kopfzeile.
    collapsed: True = Zeilen start+1..start+count-1 sind ausgeblendet.
    """

    start: int
    count: int
    name: str
    collapsed: bool = True
    # True, sobald sich die Zeilenzahl durch Hinzufuegen/Entfernen einzelner
    # Zeilen INNERHALB des aufgeklappten Bausteins veraendert hat (BUGS.md
    # #22c) -- gesetzt von TestcaseTab._on_row_inserted/_on_row_removed,
    # ausgewertet von _sync_header_overlay ("(modifiziert)"-Zusatz zum Namen).
    modified: bool = False
    # Schwebender Zusammenfassungs-Overlay ueber der Kopfzeile im
    # eingeklappten Zustand (siehe _BlockHeaderOverlay/TestcaseTab.
    # _sync_header_overlay) -- nur waehrend collapsed=True erzeugt/sichtbar,
    # lazy statt beim Einfuegen, siehe dort.
    overlay: _BlockHeaderOverlay | None = None
    # Nicht-interaktive Ueberdeckungen der Spalten "Dauer" (Gesamtdauer des
    # Bausteins) und "Pruefung" (Aggregat-Faerbung, siehe
    # TestcaseTab._sync_duration_overlay/_sync_check_overlay) -- anders als
    # `overlay` oben IMMER sichtbar, solange die Kopfzeile existiert (auch
    # aufgeklappt), da die Kopfzeile diese beiden Spalten dauerhaft als
    # Baustein-Zusammenfassung zeigt statt als editierbares Feld des ersten
    # Baustein-Schritts. Ebenfalls lazy erzeugt.
    duration_overlay: _ReadonlyCellOverlay | None = None
    check_overlay: _ReadonlyCellOverlay | None = None


class _BlockHeaderOverlay(QWidget):
    """Rein visuelle Zusammenfassungsflaeche ueber der Kopfzeile eines
    eingeklappten Bausteins (BUGS.md #18: "Gerät" -> "Baustein", "Aktion" ->
    Bausteinname, "Wert" -> Schrittzahl). Schwebt als Kind von
    QTableWidget.viewport() UEBER den echten Zellen-Widgets in COL_DEVICE/
    COL_ACTION/COL_VALUE der Kopfzeile, ohne sie zu ersetzen -- die echten
    Widgets bleiben unveraendert vorhanden und die alleinige Datenquelle fuer
    steps()/_row_to_step, nur optisch verdeckt. Das haelt diesen rein
    kosmetischen Bug von der Testschritt-Datenhaltung fern, die pro Zeile
    direkt an die Zellen-Widgets gekoppelt ist (siehe _action_row_to_step).

    Ein Klick auf die Flaeche waehlt trotzdem die Kopfzeile aus (wie ein
    Klick auf eine normale Zelle), statt stumm zu verpuffen oder an die
    verdeckten Widgets durchgereicht zu werden (was z.B. ungewollt ein
    Dropdown darunter oeffnen wuerde)."""

    def __init__(self, on_click) -> None:
        super().__init__()
        self._on_click = on_click
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        self.device_label = QLabel()
        self.action_label = QLabel()
        self.value_label = QLabel()
        for label in (self.device_label, self.action_label, self.value_label):
            layout.addWidget(label, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt-Override)
        self._on_click()
        super().mousePressEvent(event)


class _ReadonlyCellOverlay(QWidget):
    """Nicht-interaktive Ueberdeckung EINER Zelle der Kopfzeile eines
    Bausteins (Spalte "Dauer" bzw. "Pruefung", siehe TestcaseTab.
    _sync_duration_overlay/_sync_check_overlay). Anders als
    _BlockHeaderOverlay bleibt diese Flaeche auch im aufgeklappten Zustand
    sichtbar -- die Kopfzeile zeigt in diesen beiden Spalten dauerhaft eine
    Baustein-Zusammenfassung (Gesamtdauer bzw. Pruefergebnis-Farbe) statt des
    editierbaren Felds des ersten Baustein-Schritts, das sie verdeckt.

    Ein Klick waehlt wie bei _BlockHeaderOverlay die Kopfzeile aus, statt an
    das verdeckte Widget (Dauer-Spinbox/Pruefungs-Button) durchgereicht zu
    werden."""

    def __init__(self, on_click) -> None:
        super().__init__()
        self._on_click = on_click
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt-Override)
        self._on_click()
        super().mousePressEvent(event)


class TestcaseTab(QWidget):
    run_requested = Signal()
    stop_requested = Signal()
    open_report_requested = Signal()
    export_report_pdf_to = Signal(object)  # Path
    notify_requested = Signal(str, str)  # title, message -- siehe main_window._show_notification

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        # Zuletzt geladene/gespeicherte Testablauf-Datei, fuer den Testablauf-
        # Namen im Nachlauf-Report (siehe run_report.py) -- ohne Datei
        # (neu erstellter, ungespeicherter Ablauf) zeigt der Report "Unbenannt".
        self._current_path: Path | None = None
        self._report_available = False

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

        # Ein-/ausklappbare Bausteine (siehe _BlockGroup) -- rein editorseitig,
        # unabhaengig von den Testschritt-Daten.
        self._block_groups: list[_BlockGroup] = []

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
        self._add_button = SplitIconButton("mdi.plus", "")
        self._remove_button = IconButton("mdi.minus", "")
        self._up_button = IconButton("mdi.arrow-up", "")
        self._down_button = IconButton("mdi.arrow-down", "")
        self._clear_all_button = IconButton("mdi.delete-sweep-outline", "")
        self._load_button = IconButton("mdi.folder-open-outline", "")
        self._save_button = IconButton("mdi.content-save-outline", "")
        # Getauscht (BUGS.md #19): das Plus-Icon liest sich intuitiv als
        # "Baustein hinzufuegen" (einfuegen), nicht als "speichern" -- der
        # reine Puzzleteil-Umriss ohne Plus passt besser zu "speichern".
        self._block_save_button = IconButton("mdi.puzzle-outline", "")
        self._block_insert_button = IconButton("mdi.puzzle-plus-outline", "")
        self._add_menu = QMenu(self._add_button)
        self._action_add_action = self._add_menu.addAction("")
        self._action_add_loop = self._add_menu.addAction("")
        self._action_add_while = self._add_menu.addAction("")
        self._action_add_if = self._add_menu.addAction("")
        self._action_add_else = self._add_menu.addAction("")
        self._action_add_end = self._add_menu.addAction("")
        self._action_add_set_var = self._add_menu.addAction("")
        self._action_add_inc_var = self._add_menu.addAction("")
        self._action_add_wait = self._add_menu.addAction("")
        self._action_add_action.triggered.connect(lambda: self._insert_new_step(TestStep()))
        self._action_add_loop.triggered.connect(lambda: self._insert_block("loop"))
        self._action_add_while.triggered.connect(lambda: self._insert_block("while"))
        self._action_add_if.triggered.connect(lambda: self._insert_block("if"))
        self._action_add_else.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="else")))
        self._action_add_end.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="end")))
        self._action_add_set_var.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="set_var")))
        self._action_add_inc_var.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="inc_var")))
        self._action_add_wait.triggered.connect(lambda: self._insert_new_step(TestStep(step_type="wait")))
        self._add_button.setMenu(self._add_menu)
        self._add_button.clicked.connect(self._add_row_clicked)
        self._remove_button.clicked.connect(self._remove_selected_row)
        self._up_button.clicked.connect(lambda: self._move_selected_row(-1))
        self._down_button.clicked.connect(lambda: self._move_selected_row(1))
        self._clear_all_button.clicked.connect(self._clear_all_rows)
        self._load_button.clicked.connect(self._load_from_file)
        self._save_button.clicked.connect(self._save_to_file)
        self._block_save_button.clicked.connect(self._save_block)
        self._block_insert_button.clicked.connect(self._insert_block_from_file)
        for button in (
            self._add_button, self._remove_button, self._up_button, self._down_button,
            self._clear_all_button,
        ):
            button_row.addWidget(button)
        button_row.addWidget(self._block_save_button)
        button_row.addWidget(self._block_insert_button)
        button_row.addStretch()
        button_row.addWidget(self._load_button)
        button_row.addWidget(self._save_button)
        layout.addLayout(button_row)

        self._table = QTableWidget(0, len(BASE_COLUMNS))
        header = self._table.horizontalHeader()
        # Feste Spalten (FIXED_COLUMNS) bekommen ihre Default-Breite und bleiben
        # beim Skalieren des Fensters unveraendert (Fixed); alle uebrigen
        # Spalten (inkl. "Aktion") sind Stretch und teilen sich den
        # verbleibenden Platz automatisch zu gleichen Teilen -- die initiale
        # Interactive-Breite vor dem Umschalten auf Fixed/Stretch wird
        # zunaechst gesetzt, damit resizeSection() ueberhaupt greift.
        for col in range(len(BASE_COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        for col, width in DEFAULT_COLUMN_WIDTHS.items():
            header.resizeSection(col, width)
        for col in range(len(BASE_COLUMNS)):
            mode = QHeaderView.ResizeMode.Fixed if col in FIXED_COLUMNS else QHeaderView.ResizeMode.Stretch
            header.setSectionResizeMode(col, mode)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        # Versetzt gezeichnete Spalte "#" fuer Baustein-Mitgliederzeilen
        # (siehe _NumColumnDelegate/BLOCK_MEMBER_INDENT_PX) -- die uebrigen
        # Spalten dieser Zeilen verschiebt _apply_member_row_shifts als echte
        # Widget-Geometrie um denselben Betrag.
        self._num_delegate = _NumColumnDelegate(self._member_indent_for_row, self._table)
        self._table.setItemDelegateForColumn(COL_NUM, self._num_delegate)
        # Baustein-Kopfzeilen-Overlay (siehe _sync_header_overlay) muss bei
        # Spaltenbreiten-Aenderung (Stretch-Spalten beim Fenster-Resize) und
        # beim vertikalen Scrollen neu positioniert werden, da es als freies
        # Kind von viewport() haengt statt als Zellen-Widget mitzuwandern.
        header.sectionResized.connect(lambda *_: self._sync_all_header_overlays())
        self._table.verticalScrollBar().valueChanged.connect(lambda *_: self._sync_all_header_overlays())
        # Beim Scrollen/Spalten-Resize legt QTableWidget die Zellen-Widgets neu
        # aus und verwirft dabei den Rechtsversatz der Mitgliederzeilen -- der
        # Event-Filter faengt das pro Widget ab, diese Sammel-Aufrufe holen
        # zusaetzlich die Zeilen nach, die dabei kein eigenes Move-Event
        # bekommen haben (z.B. gerade erst wieder eingeblendete).
        header.sectionResized.connect(lambda *_: self._apply_member_row_shifts())
        self._table.verticalScrollBar().valueChanged.connect(lambda *_: self._apply_member_row_shifts())
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

        self._report_button = IconButton("mdi.file-chart-outline", "")
        self._report_button.setEnabled(False)
        self._report_menu = QMenu(self._report_button)
        self._action_report_html = self._report_menu.addAction("")
        self._action_report_pdf = self._report_menu.addAction("")
        self._action_report_html.triggered.connect(self.open_report_requested.emit)
        self._action_report_pdf.triggered.connect(self._pick_pdf_path)
        self._report_button.setMenu(self._report_menu)
        run_row.addWidget(self._report_button)
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
        self._clear_all_button.setToolTip(tr("Alle Zeilen löschen"))
        self._load_button.setToolTip(tr("Laden…"))
        self._save_button.setToolTip(tr("Speichern…"))
        self._block_save_button.setToolTip(tr("Baustein speichern…"))
        self._block_insert_button.setToolTip(tr("Baustein einfügen…"))
        self._action_add_action.setText(tr("Aktionsschritt"))
        self._action_add_loop.setText(tr("Schleife (n×) … Ende"))
        self._action_add_while.setText(tr("Solange … Ende"))
        self._action_add_if.setText(tr("Wenn … Ende"))
        self._action_add_else.setText(tr("Sonst"))
        self._action_add_end.setText(tr("Ende"))
        self._action_add_set_var.setText(tr("Variable setzen"))
        self._action_add_inc_var.setText(tr("Variable erhöhen"))
        self._action_add_wait.setText(tr("Warten"))
        self._run_button.setToolTip(tr("Start"))
        self._run_button.setText(tr("Start"))
        self._stop_button.setToolTip(tr("Stop"))
        self._stop_button.setText(tr("Stop"))
        self._report_button.setToolTip(tr("Report…"))
        self._action_report_html.setText(tr("Report öffnen (HTML)"))
        self._action_report_pdf.setText(tr("Als PDF exportieren…"))
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

    def forget_device(self, device_id: str) -> None:
        """Entfernt ein Geraet aus der Geraete-Auswahl der Testablauf-Zeilen
        -- nur fuer den "Geraetezuordnung loeschen"-Button (main_window.
        _on_reset_devices_requested) gedacht, siehe dashboard.
        DashboardWidget.forget_device fuer die Begruendung. Bereits in
        Testablauf-Zeilen ausgewaehlte device_ids bleiben unveraendert
        gespeichert -- die Combo faellt fuer sie auf den schon bestehenden
        "nicht verbunden"-Fallback zurueck (siehe _populate_device_combo)."""
        if self._known_devices.pop(device_id, None) is not None:
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
        am Tabellenende. Ist die markierte Zeile die Kopfzeile eines
        Bausteins (BUGS.md #20), landet die neue Zeile hinter dem GESAMTEN
        Baustein statt zwischen Kopfzeile und erster Mitgliederzeile -- ein
        Einfuegen "in den Baustein hinein" waere fuer den Nutzer nicht von
        einer regulaeren Folgezeile zu unterscheiden."""
        if self._selected_row >= 0:
            group = self._group_at_header(self._selected_row)
            if group is not None:
                return group.start + group.count
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
        self._on_row_inserted(row_index)
        # Landet die neue Zeile innerhalb einer eingeklappten Gruppe (z.B.
        # "Zeile hinzufügen" bei noch markierter, inzwischen verborgener
        # Zeile eines kollabierten Bausteins), muss sie sofort mit verborgen
        # werden -- sonst waere sie trotz "eingeklappt" sichtbar.
        group = self._group_containing(row_index)
        if group is not None and group.collapsed and row_index != group.start:
            self._table.setRowHidden(row_index, True)

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

        value_spin = SteppedDoubleSpinBox()
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
            duty=step.arb_duty,
        )

        value_stack = QStackedWidget()
        value_stack.addWidget(value_spin)  # Index 0: normaler Zahlenwert
        value_stack.addWidget(arb_page)    # Index 1: Arbiträrsignal-Zusammenfassung + Button
        self._table.setCellWidget(row_index, COL_VALUE, value_stack)

        duration_spin = SteppedDoubleSpinBox()
        duration_spin.setRange(0, 36000)
        duration_spin.setDecimals(1)
        duration_spin.setSuffix(" s")
        duration_spin.setValue(step.duration)
        self._table.setCellWidget(row_index, COL_DURATION, duration_spin)
        duration_spin.valueChanged.connect(lambda _=None, s=duration_spin: self._notify_duration_changed(s))

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
            shape_label = arb_shape_label(params["shape"])
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
            count_spin = SteppedSpinBox()
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

            value_spin = SteppedDoubleSpinBox()
            value_spin.setDecimals(3)
            value_spin.setRange(-1e9, 1e9)
            value_spin.setValue(step.value)
            self._table.setCellWidget(row_index, COL_VALUE, value_spin)

            duration_spin = SteppedDoubleSpinBox()
            duration_spin.setRange(0, 36000)
            duration_spin.setDecimals(1)
            duration_spin.setSuffix(" s")
            duration_spin.setValue(step.duration)
            self._table.setCellWidget(row_index, COL_DURATION, duration_spin)
            duration_spin.valueChanged.connect(lambda _=None, s=duration_spin: self._notify_duration_changed(s))

        elif t == "wait":
            duration_spin = SteppedDoubleSpinBox()
            duration_spin.setRange(0, 36000)
            duration_spin.setDecimals(1)
            duration_spin.setSuffix(" s")
            duration_spin.setValue(step.duration)
            self._table.setCellWidget(row_index, COL_DURATION, duration_spin)
            duration_spin.valueChanged.connect(lambda _=None, s=duration_spin: self._notify_duration_changed(s))

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
        # Ist die markierte Zeile die Kopfzeile eines Bausteins (BUGS.md
        # #22a/b), soll "Zeile entfernen" den gesamten Baustein loeschen --
        # im eingeklappten Zustand ist ohnehin nur die Kopfzeile markierbar,
        # im aufgeklappten Zustand bleibt das Entfernen einzelner
        # Mitgliederzeilen ueber den else-Zweig unveraendert moeglich.
        group = self._group_at_header(row)
        count = group.count if group is not None else 1
        self._table.blockSignals(True)
        for _ in range(count):
            self._on_row_removed(row)
            self._table.removeRow(row)
        self._table.blockSignals(False)
        self._renumber_rows()
        self._resync_selection()
        self._revalidate_structure()

    def _move_selected_row(self, offset: int) -> None:
        row = self._selected_row
        if row < 0:
            return
        # Ist die markierte Zeile die Kopfzeile eines Bausteins, muss der
        # GESAMTE Baustein als Einheit verschoben werden -- siehe _move_block.
        group = self._group_at_header(row)
        if group is not None:
            self._move_block(group, offset)
            return

        target = row + offset
        if not (0 <= target < self._table.rowCount()):
            return
        own_group = self._group_containing(row)
        foreign_group = self._group_containing(target)
        if foreign_group is not None and foreign_group is not own_group:
            # Zielposition liegt innerhalb eines FREMDEN (ggf. eingeklappten)
            # Bausteins -- als Ganzes ueberspringen statt mittendrin
            # einzufuegen, sonst wuerde eine normale Zeile faelschlich zur
            # Mitgliederzeile dieses Bausteins (Zeilenzahl+Grenzen liefen
            # auseinander, siehe Bugreport). Beim Abwaertsverschieben (offset>0)
            # liegt `row` VOR dem fremden Baustein -- ihr Entfernen zieht
            # dessen Zeilen um eins nach vorn, "dahinter" landet daher bei
            # start+count-1, nicht start+count (sonst eine Zeile zu weit).
            # Beim Aufwaertsverschieben liegt `row` dahinter, dessen Entfernen
            # veraendert die (davorliegenden) Indizes des Bausteins nicht.
            target = foreign_group.start if offset < 0 else foreign_group.start + foreign_group.count - 1
            if not (0 <= target < self._table.rowCount()):
                return
        if own_group is not None and not (own_group.start < target < own_group.start + own_group.count):
            # Eine Mitgliederzeile darf ihren eigenen Baustein durch
            # Verschieben nicht verlassen (weder zur Kopfzeile hoch noch nach
            # unten heraus) -- dafuer gibt es "Baustein speichern"/manuelles
            # Neuanlegen, nicht die Pfeil-Buttons.
            return

        step = self._row_to_step(row)
        self._table.blockSignals(True)
        self._on_row_removed(row)
        self._table.removeRow(row)
        self._insert_row(target, step)
        self._table.selectRow(target)
        self._table.blockSignals(False)
        self._resync_selection()
        self._revalidate_structure()

    def _move_block(self, group: _BlockGroup, offset: int) -> None:
        """Verschiebt einen kompletten Baustein (Kopf- plus Mitgliederzeilen)
        als Einheit um eine Position nach oben/unten. Steht dabei eine
        einzelne fremde Zeile im Weg, wird nur mit ihr getauscht; steht ein
        ganzer anderer Baustein im Weg, wird komplett mit ihm getauscht,
        statt Kopf-/Mitgliederzeilen der beiden Bausteine zu vermischen."""
        start, count = group.start, group.count
        if offset < 0:
            if start == 0:
                return
            neighbor = self._group_containing(start - 1)
            span = neighbor.count if neighbor is not None else 1
            neighbor_start = start - span
        else:
            end = start + count
            if end >= self._table.rowCount():
                return
            neighbor = self._group_containing(end)
            span = neighbor.count if neighbor is not None else 1
            neighbor_start = end

        own_steps = [self._row_to_step(r) for r in range(start, start + count)]
        neighbor_steps = [self._row_to_step(r) for r in range(neighbor_start, neighbor_start + span)]
        lo = min(start, neighbor_start)

        # Aus der Gruppenliste nehmen, BEVOR Zeilen entfernt werden: die
        # generische Nachfuehrung in _on_row_removed soll fuer group/neighbor
        # selbst nicht laufen (sie werden unten an der neuen Position komplett
        # neu angelegt), nur fuer alle UEBRIGEN (unbeteiligten) Bausteine.
        self._dissolve_group_overlay(group)
        if neighbor is not None:
            self._dissolve_group_overlay(neighbor)
        self._block_groups = [g for g in self._block_groups if g is not group and g is not neighbor]

        self._table.blockSignals(True)
        for r in reversed(range(lo, lo + count + span)):
            self._on_row_removed(r)
            self._table.removeRow(r)

        if offset < 0:
            for i, step in enumerate(own_steps):
                self._insert_row(lo + i, step)
            for i, step in enumerate(neighbor_steps):
                self._insert_row(lo + count + i, step)
            new_start, neighbor_new_start = lo, lo + count
        else:
            for i, step in enumerate(neighbor_steps):
                self._insert_row(lo + i, step)
            for i, step in enumerate(own_steps):
                self._insert_row(lo + span + i, step)
            neighbor_new_start, new_start = lo, lo + span

        new_group = _BlockGroup(start=new_start, count=count, name=group.name, collapsed=group.collapsed)
        self._block_groups.append(new_group)
        self._apply_group_visibility(new_group)
        if neighbor is not None:
            new_neighbor = _BlockGroup(
                start=neighbor_new_start, count=neighbor.count,
                name=neighbor.name, collapsed=neighbor.collapsed,
            )
            self._block_groups.append(new_neighbor)
            self._apply_group_visibility(new_neighbor)

        self._table.selectRow(new_start)
        self._table.blockSignals(False)
        self._resync_selection()
        self._revalidate_structure()

    def _clear_all_rows(self) -> None:
        if self._table.rowCount() == 0:
            return
        if QMessageBox.question(
            self,
            tr("Alle Zeilen löschen"),
            tr("Wirklich alle Zeilen dieses Testablaufs löschen? Das lässt sich nicht rückgängig machen."),
        ) != QMessageBox.StandardButton.Yes:
            return
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.blockSignals(False)
        self._clear_block_groups()
        self._clear_all_row_colors()
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
        """Nummeriert alle Zeilen neu. Zeilen innerhalb eines Bausteins (siehe
        _BlockGroup) bekommen statt der fortlaufenden Hauptnummer eine eigene
        Unternummerierung "<Kopfzeilennummer>.<Position>" (z.B. "4.1", "4.2"),
        die Kopfzeile selbst zaehlt normal in der Hauptsequenz mit -- die
        Zeile danach setzt die Hauptsequenz nahtlos fort (z.B. "5"), als waere
        der Baustein nur eine einzelne Zeile. Bausteine sind nicht
        verschachtelbar (siehe _insert_block_from_file), daher genuegt eine
        flache Zuordnung Zeile -> Position innerhalb ihres Bausteins."""
        member_position = {
            row: position
            for group in self._block_groups
            for position, row in enumerate(range(group.start + 1, group.start + group.count), start=1)
        }
        main_number = 0
        current_header_number = ""
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_NUM)
            if row in member_position:
                # Der Rechtsversatz dieser Zelle kommt vom _NumColumnDelegate
                # (ganze Zelle inkl. Hintergrund versetzt gezeichnet), die
                # Ausrichtung innerhalb der Zelle bleibt wie bei jeder
                # anderen Zeile zentriert.
                item.setText(f"{current_header_number}.{member_position[row]}")
                continue
            main_number += 1
            current_header_number = str(main_number)
            item.setText(current_header_number)

    # -- Ein-/ausklappbare Bausteine -------------------------------------------
    #
    # Ein per "Baustein einfügen" hinzugefuegter Zeilenbereich wird als
    # _BlockGroup nachverfolgt und kann ueber die Kopfzeile ein-/ausgeklappt
    # werden (siehe _on_table_cell_clicked). Rein editorseitig -- die
    # Gruppen-Liste wird bei jeder Zeilenmutation ueber _on_row_inserted/
    # _on_row_removed nachgefuehrt, damit start/count auch nach Verschieben/
    # Entfernen anderer Zeilen stimmen; die Kopfzeile selbst zu entfernen oder
    # zu verschieben loest die Gruppierung wieder auf (Zeilen werden dabei
    # eingeblendet, statt als verwaiste versteckte Zeilen zurueckzubleiben).

    def _group_at_header(self, row: int) -> _BlockGroup | None:
        for group in self._block_groups:
            if group.start == row:
                return group
        return None

    def _group_containing(self, row: int) -> _BlockGroup | None:
        for group in self._block_groups:
            if group.start <= row < group.start + group.count:
                return group
        return None

    # -- Ganze Mitgliederzeilen nach rechts verschieben ------------------------
    #
    # Der Zeilenanfang einer Baustein-Mitgliederzeile wird ALS GANZES um
    # BLOCK_MEMBER_INDENT_PX nach rechts verschoben (Leerflaeche am
    # Zeilenanfang, hierarchische Unterordnung), nicht nur der Inhalt
    # innerhalb der Zellen: die Zelle "#" zeichnet der _NumColumnDelegate
    # versetzt, das Geraet-Feld bekommt tatsaechlich versetzte Geometrie.
    # Den Versatz nimmt die Geraet-Spalte in ihrer Breite auf (sie wird um
    # denselben Betrag schmaler) -- dadurch bleibt ihr rechter Rand und damit
    # jede folgende Spalte an der normalen Position, die Datenspalten bleiben
    # ueber alle Zeilen hinweg buendig vergleichbar und nichts laeuft rechts
    # aus der Tabelle hinaus. QTableWidget setzt die Widget-Geometrie bei
    # jedem Layout (Scrollen, Spalten-Resize, Zeilenmutation) selbst auf die
    # unversetzte Zellposition zurueck -- ein Event-Filter (siehe
    # eventFilter) korrigiert das sofort wieder, _apply_member_row_shifts
    # uebernimmt die (De-)Markierung nach Strukturaenderungen.

    def _member_indent_for_row(self, row: int) -> int:
        """Rechtsversatz der Zeile: BLOCK_MEMBER_INDENT_PX fuer Zeilen
        INNERHALB eines Bausteins (die Kopfzeile selbst bleibt
        unverschoben), sonst 0. Auch vom Spalten-"#"-Delegate abgefragt."""
        group = self._group_containing(row)
        if group is not None and row != group.start:
            return BLOCK_MEMBER_INDENT_PX
        return 0

    def _cell_indent(self, row: int, col: int) -> int:
        """Versatz eines einzelnen Zellen-Widgets: nur die Geraet-Spalte
        traegt ihn (und wird dafuer schmaler, siehe
        _set_cell_widget_geometry) -- alle Spalten rechts davon behalten ihre
        normale Position."""
        return self._member_indent_for_row(row) if col == COL_DEVICE else 0

    def _set_cell_widget_geometry(self, widget: QWidget, row: int, col: int, indent: int) -> None:
        # Basis ist Qts eigenes Zellrechteck (visualRect) statt
        # columnViewportPosition/columnWidth: genau diese Geometrie gibt
        # QTableView einem Zellen-Widget normalerweise, inklusive der
        # 1px-Korrektur fuer die Gitterlinie. So ist eine unversetzte Zeile
        # pixelgenau wie zuvor und die versetzte weicht ausschliesslich um
        # den Einzug ab.
        rect = self._table.visualRect(self._table.model().index(row, col))
        target = QRect(
            rect.x() + indent, rect.y(), rect.width() - indent, rect.height()
        )
        if widget.geometry() != target:
            widget.setGeometry(target)

    def _apply_member_row_shifts(self) -> None:
        """Markiert die Zellen-Widgets aller Baustein-Mitgliederzeilen fuer
        den Rechtsversatz (Event-Filter installieren, Geometrie setzen) bzw.
        hebt die Markierung wieder auf, wenn eine Zeile keine Mitgliederzeile
        mehr ist (Gruppe aufgeloest/verschoben). Nach jeder
        Strukturaenderung von _revalidate_structure aufgerufen."""
        for row in range(self._table.rowCount()):
            for col in range(1, self._table.columnCount()):
                widget = self._table.cellWidget(row, col)
                if widget is None:
                    continue
                indent = self._cell_indent(row, col)
                if indent:
                    if not getattr(widget, "_member_shift_col", None) == col:
                        widget._member_shift_col = col
                        widget.installEventFilter(self)
                    self._set_cell_widget_geometry(widget, row, col, indent)
                elif getattr(widget, "_member_shift_col", None) is not None:
                    widget._member_shift_col = None
                    widget.removeEventFilter(self)
                    self._set_cell_widget_geometry(widget, row, col, 0)
        # Spalte "#" wird vom Delegate versetzt gezeichnet -- Neuzeichnen
        # anstossen, damit ein geaenderter Versatz sofort sichtbar wird.
        self._table.viewport().update()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt-Override)
        # Korrigiert die Geometrie eines fuer den Rechtsversatz markierten
        # Zellen-Widgets, sobald QTableWidget sie beim eigenen Layouten
        # (Scrollen/Resize/Zeilenmutation) auf die unversetzte Zellposition
        # zurueckgesetzt hat. Kein Endlos-Pingpong: unsere Korrektur setzt
        # die Geometrie nur bei Abweichung (siehe _set_cell_widget_geometry),
        # das darauf folgende Move-Event trifft dann bereits die Zielposition.
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            col = getattr(obj, "_member_shift_col", None)
            if col is not None:
                row = self._row_of_widget(obj, col)
                if row >= 0:
                    indent = self._cell_indent(row, col)
                    if indent:
                        self._set_cell_widget_geometry(obj, row, col, indent)
        return super().eventFilter(obj, event)

    def _apply_group_visibility(self, group: _BlockGroup) -> None:
        for row in range(group.start + 1, group.start + group.count):
            self._table.setRowHidden(row, group.collapsed)
        self._sync_header_overlay(group)
        self._sync_duration_overlay(group)
        self._sync_check_overlay(group)

    def _unhide_group_rows(self, group: _BlockGroup) -> None:
        for row in range(group.start, group.start + group.count):
            if 0 <= row < self._table.rowCount():
                self._table.setRowHidden(row, False)

    def _sync_header_overlay(self, group: _BlockGroup) -> None:
        """Erzeugt/aktualisiert/positioniert den Kopfzeilen-Overlay (siehe
        _BlockHeaderOverlay) -- zeigt "Baustein"/Bausteinname/Schrittzahl an,
        UNABHAENGIG vom Ein-/Ausklapp-Zustand (die Kopfzeile bleibt so immer
        als Baustein-Zusammenfassung erkennbar, statt beim Aufklappen wieder
        die rohen Geraet-/Aktion/Wert-Felder des ersten Baustein-Schritts
        freizugeben). Muss nach jeder Aenderung, die Text (group.name/count),
        Position (Zeile verschoben/Spalten resized) oder Faerbung betreffen
        koennte, erneut aufgerufen werden -- siehe _sync_all_header_overlays
        fuer den zentralen Sammel-Aufruf."""
        row = group.start
        if not (0 <= row < self._table.rowCount()):
            if group.overlay is not None:
                group.overlay.hide()
            return
        if group.overlay is None:
            group.overlay = _BlockHeaderOverlay(lambda g=group: self._table.selectRow(g.start))
            group.overlay.setParent(self._table.viewport())
        overlay = group.overlay
        overlay.device_label.setText(tr("Baustein"))
        name = f"{group.name} {tr('(modifiziert)')}" if group.modified else group.name
        overlay.action_label.setText(name)
        overlay.value_label.setText(tr("{n} Schritte", n=group.count))
        pal = current_palette()
        color = self._row_style_color(row) or pal.surface
        label_style = f"color: {pal.text}; font-style: italic;"
        for label in (overlay.device_label, overlay.action_label, overlay.value_label):
            label.setStyleSheet(label_style)
        overlay.setStyleSheet(f"background-color: {color};")
        # Der Overlay verdeckt das eigentliche Widget in Spalte COL_DEVICE
        # komplett -- ohne diese Uebernahme des dort hinterlegten
        # _indent_depth (siehe _revalidate_structure) wuerde die Kopfzeile im
        # eingeklappten Zustand IMMER unabhaengig von ihrer tatsaechlichen
        # Verschachtelungstiefe (z.B. innerhalb einer Schleife/eines
        # Wenn-Blocks) ohne Einrueckung erscheinen, obwohl das darunterliegende
        # Widget korrekt eingerueckt waere.
        col1 = self._table.cellWidget(row, COL_DEVICE)
        depth = getattr(col1, "_indent_depth", 0)
        overlay.layout().setContentsMargins(6 + depth * 18, 0, 6, 0)
        self._position_header_overlay(group)
        overlay.show()
        overlay.raise_()

    def _position_header_overlay(self, group: _BlockGroup) -> None:
        if group.overlay is None:
            return
        row = group.start
        x = self._table.columnViewportPosition(COL_DEVICE)
        width = sum(self._table.columnWidth(c) for c in (COL_DEVICE, COL_ACTION, COL_VALUE))
        y = self._table.rowViewportPosition(row)
        height = self._table.rowHeight(row)
        group.overlay.setGeometry(x, y, width, height)

    def _row_of_widget(self, widget: QWidget, column: int) -> int:
        """Ermittelt die aktuelle Zeile eines Zellen-Widgets ueber Identitaet
        statt eines beim Erzeugen erfassten Zeilenindex -- letzterer wuerde
        durch spaeteres Einfuegen/Entfernen/Verschieben ANDERER Zeilen
        veralten. Fuer die kleinen Testablauf-Tabellen dieser App (typisch
        wenige Dutzend Zeilen) ist die lineare Suche unproblematisch."""
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, column) is widget:
                return row
        return -1

    def _notify_duration_changed(self, spin: QDoubleSpinBox) -> None:
        """Haengt an jeder Dauer-Spinbox (siehe _build_action_row/
        _build_control_row) -- aktualisiert die Gesamtdauer-Anzeige der
        Kopfzeile, falls die geaenderte Zeile Teil eines (auf- oder
        eingeklappten) Bausteins ist."""
        row = self._row_of_widget(spin, COL_DURATION)
        if row < 0:
            return
        group = self._group_containing(row)
        if group is not None:
            self._sync_duration_overlay(group)

    def _block_total_duration(self, group: _BlockGroup) -> float:
        """Aufsummierte Dauer aller Schritte des Bausteins (Kopfzeile
        inklusive) -- fuer die Anzeige in der Dauer-Spalte der Kopfzeile
        (siehe _sync_duration_overlay). Bewusst eine einfache Summe der
        einzelnen `duration`-Felder ohne Beruecksichtigung von
        Schleifen-Wiederholungen o.ae. -- das entspricht der Dauer EINES
        Durchlaufs des Baustein-Rumpfs, wie er im Editor sichtbar ist."""
        return sum(
            self._row_to_step(row).duration
            for row in range(group.start, group.start + group.count)
        )

    def _sync_duration_overlay(self, group: _BlockGroup) -> None:
        """Zeigt in der Dauer-Spalte der Kopfzeile die aufsummierte
        Gesamtdauer des Bausteins an, statt der (editierbaren) Dauer des
        ersten Baustein-Schritts, die sie verdeckt. Anders als der
        Geraet/Aktion/Wert-Overlay (_sync_header_overlay) unabhaengig vom
        collapsed-Zustand immer sichtbar -- die Kopfzeile bleibt auch
        aufgeklappt die Baustein-Zusammenfassung fuer diese Spalte."""
        row = group.start
        if not (0 <= row < self._table.rowCount()):
            if group.duration_overlay is not None:
                group.duration_overlay.hide()
            return
        if group.duration_overlay is None:
            group.duration_overlay = _ReadonlyCellOverlay(lambda g=group: self._table.selectRow(g.start))
            group.duration_overlay.setParent(self._table.viewport())
        overlay = group.duration_overlay
        overlay.label.setText(f"{self._block_total_duration(group):g} s")
        pal = current_palette()
        color = self._row_style_color(row) or pal.surface
        overlay.label.setStyleSheet(f"color: {pal.text};")
        overlay.setStyleSheet(f"background-color: {color};")
        x = self._table.columnViewportPosition(COL_DURATION)
        width = self._table.columnWidth(COL_DURATION)
        y = self._table.rowViewportPosition(row)
        height = self._table.rowHeight(row)
        overlay.setGeometry(x, y, width, height)
        overlay.show()
        overlay.raise_()

    def _sync_check_overlay(self, group: _BlockGroup) -> None:
        """Blockt die Pruefungs-Spalte der Kopfzeile gegen Bearbeitung (kein
        eigener Pruefungs-Dialog fuer den Baustein als Ganzes). Die
        Faerbung nach Pruefergebnis passiert NICHT hier, sondern ueber die
        normale Zeilenfarbe (siehe _update_block_check_aggregate/
        _row_style_color) -- die Kopfzeile traegt dafuer selbst einen
        Eintrag in _check_results, genau wie jede andere geprueft Zeile,
        die Ueberdeckung uebernimmt nur denselben Hintergrund."""
        row = group.start
        if not (0 <= row < self._table.rowCount()):
            if group.check_overlay is not None:
                group.check_overlay.hide()
            return
        if group.check_overlay is None:
            group.check_overlay = _ReadonlyCellOverlay(lambda g=group: self._table.selectRow(g.start))
            group.check_overlay.setParent(self._table.viewport())
        overlay = group.check_overlay
        pal = current_palette()
        color = self._row_style_color(row) or pal.surface
        overlay.setStyleSheet(f"background-color: {color};")
        x = self._table.columnViewportPosition(COL_CHECK)
        width = self._table.columnWidth(COL_CHECK)
        y = self._table.rowViewportPosition(row)
        height = self._table.rowHeight(row)
        overlay.setGeometry(x, y, width, height)
        overlay.show()
        overlay.raise_()

    def _sync_all_header_overlays(self) -> None:
        for group in self._block_groups:
            self._sync_header_overlay(group)
            self._sync_duration_overlay(group)
            self._sync_check_overlay(group)

    def _dissolve_group_overlay(self, group: _BlockGroup) -> None:
        if group.overlay is not None:
            group.overlay.deleteLater()
            group.overlay = None
        if group.duration_overlay is not None:
            group.duration_overlay.deleteLater()
            group.duration_overlay = None
        if group.check_overlay is not None:
            group.check_overlay.deleteLater()
            group.check_overlay = None

    def _clear_block_groups(self) -> None:
        for group in self._block_groups:
            self._dissolve_group_overlay(group)
        self._block_groups = []

    def _toggle_group(self, group: _BlockGroup) -> None:
        group.collapsed = not group.collapsed
        self._apply_group_visibility(group)
        if group.collapsed and group.start < self._selected_row < group.start + group.count:
            # Die markierte Zeile wuerde sonst unsichtbar markiert bleiben.
            self._table.selectRow(group.start)
        self._revalidate_structure()

    def _reveal_row(self, row: int) -> None:
        """Klappt die Gruppe auf, falls `row` gerade darin verborgen ist --
        z.B. wenn der Runner (siehe on_step_started) eine Zeile innerhalb
        eines eingeklappten Bausteins erreicht, damit der Fortschritt
        sichtbar bleibt statt in einer verborgenen Zeile zu blinken."""
        group = self._group_containing(row)
        if group is not None and group.collapsed and row != group.start:
            group.collapsed = False
            self._apply_group_visibility(group)
            self._revalidate_structure()

    def _on_table_cell_clicked(self, row: int, column: int) -> None:
        if column != COL_NUM:
            return
        group = self._group_at_header(row)
        if group is not None:
            self._toggle_group(group)

    def _on_row_inserted(self, row_index: int) -> None:
        for group in self._block_groups:
            if row_index <= group.start:
                group.start += 1
            elif group.start < row_index < group.start + group.count:
                group.count += 1
                group.modified = True

    def _on_row_removed(self, row_index: int) -> None:
        remaining: list[_BlockGroup] = []
        for group in self._block_groups:
            if row_index < group.start:
                group.start -= 1
                remaining.append(group)
            elif row_index == group.start:
                # Kopfzeile entfernt -- Gruppierung loest sich auf.
                self._unhide_group_rows(group)
                self._dissolve_group_overlay(group)
            elif group.start < row_index < group.start + group.count:
                group.count -= 1
                if group.count > 1:
                    group.modified = True
                    remaining.append(group)
                else:
                    self._unhide_group_rows(group)
                    self._dissolve_group_overlay(group)
            else:
                remaining.append(group)
        self._block_groups = remaining

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
            arb_duty=params.get("duty", 0.5),
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

        if step_type == "wait":
            duration_spin: QDoubleSpinBox = self._table.cellWidget(row, COL_DURATION)
            return TestStep(step_type="wait", enabled=enabled, duration=duration_spin.value())

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
            return
        self._current_path = path

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
        self._current_path = Path(path_str)

        self._table.setRowCount(0)
        self._clear_block_groups()
        for step in steps:
            self._insert_row(self._table.rowCount(), step)
        if not steps:
            self._add_row_clicked()
        self._revalidate_structure()

    # -- Wiederverwendbare Bausteine ------------------------------------------
    #
    # Ein Baustein ist ein benannter, in sich strukturell ausgeglichener
    # Ausschnitt eines Testablaufs (siehe SaveBlockDialog), der in einem
    # eigenen Verzeichnis (BLOCKS_DIR) abgelegt und spaeter an beliebiger
    # Stelle -- auch in einem ganz anderen Testablauf -- wieder eingefuegt
    # werden kann, statt haeufige Schrittfolgen (z.B. ein Entladeprofil)
    # jedes Mal neu aufzubauen.

    def _save_block(self) -> None:
        steps = self.steps()
        if not steps:
            return
        dialog = SaveBlockDialog(steps, self._selected_row, parent=self)
        if dialog.exec() != SaveBlockDialog.DialogCode.Accepted:
            return
        name = dialog.block_name()
        block_steps = dialog.selected_steps()

        BLOCKS_DIR.mkdir(exist_ok=True)
        default_path = BLOCKS_DIR / f"{_sanitize_filename(name)}.json"
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Baustein speichern"), str(default_path), tr("Baustein (*.json)")
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix != ".json":
            path = path.with_suffix(".json")
        try:
            save_block(block_steps, name, path)
        except OSError as exc:
            QMessageBox.critical(self, tr("Fehler beim Speichern"), str(exc))

    def _insert_block_from_file(self) -> None:
        BLOCKS_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getOpenFileName(
            self, tr("Baustein einfügen"), str(BLOCKS_DIR), tr("Baustein (*.json)")
        )
        if not path_str:
            return
        try:
            name, block_steps = load_block(Path(path_str))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            QMessageBox.critical(self, tr("Fehler beim Laden"), str(exc))
            return
        if not block_steps:
            return

        index = self._insert_at_selection()
        for offset, step in enumerate(block_steps):
            self._insert_row(index + offset, step)

        # Ab zwei Zeilen als einklappbare Gruppe nachverfolgen (siehe
        # _BlockGroup) -- ein einzelner Schritt braucht keine Klapp-Funktion.
        if len(block_steps) > 1:
            group = _BlockGroup(start=index, count=len(block_steps), name=name)
            self._block_groups.append(group)
            self._apply_group_visibility(group)

        self._table.selectRow(index)
        self._revalidate_structure()

    def current_testcase_name(self) -> str:
        return self._current_path.stem if self._current_path else tr("Unbenannt")

    def _pick_pdf_path(self) -> None:
        from run_report import REPORTS_DIR  # lokal: vermeidet Modul-Ladezeit-Kopplung beim Tab-Import

        REPORTS_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Report als PDF exportieren"), str(REPORTS_DIR), tr("PDF (*.pdf)")
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        self.export_report_pdf_to.emit(path)

    def set_report_available(self, available: bool) -> None:
        self._report_available = available
        self._report_button.setEnabled(available and not self._is_running)

    # -- Struktur-Validierung (Schleifen/If/While-Verschachtelung) --------------
    #
    # Nach jeder Zeilenmutation neu berechnet: Einrueckungstiefe je Zeile
    # (Anzeige ueber padding-left am Label/Combo in Spalte 1, siehe
    # _apply_row_style), "Ende (Schleife/Solange/Wenn)"-Beschriftung, und ob
    # der Start-Button ueberhaupt gedrueckt werden darf. testcase_runner.start()
    # validiert zusaetzlich als Backstop (Dateien koennen von Hand bearbeitet
    # oder aus einer aelteren Programmversion geladen worden sein).

    def _revalidate_structure(self) -> None:
        self._renumber_rows()
        steps = self.steps()
        matching, depths, errors = validate_structure(steps)
        error_by_row = dict(errors)
        end_kind_labels = {"loop": tr("Schleife"), "while": tr("Solange"), "if": tr("Wenn")}

        # group_member_rows markiert die Zeilen INNERHALB eines (auf- oder
        # eingeklappten) Bausteins (ohne die Kopfzeile selbst) -- sie werden
        # als ganze Zeile nach rechts verschoben (siehe
        # _apply_member_row_shifts), unabhaengig von der hier berechneten
        # Schleifen/If-Verschachtelungstiefe.
        group_by_header = {group.start: group for group in self._block_groups}
        group_member_rows: set[int] = set()
        for group in self._block_groups:
            group_member_rows.update(range(group.start + 1, group.start + group.count))

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

        # Klapp-Pfeil (Kopfzeile einer Baustein-Gruppe) bzw. keine Markierung
        # in Spalte "#" -- siehe _on_table_cell_clicked fuer den Klick-Handler.
        pal = current_palette()
        for row in range(self._table.rowCount()):
            number_item = self._table.item(row, COL_NUM)
            if number_item is None:
                continue
            group = group_by_header.get(row)
            if group is not None:
                icon_name = "mdi.chevron-right" if group.collapsed else "mdi.chevron-down"
                number_item.setIcon(qta.icon(icon_name, color=pal.text))
                number_item.setToolTip(
                    tr(
                        "Baustein „{name}“ ({n} Schritte) -- zum Ein-/Ausklappen klicken",
                        name=group.name, n=group.count,
                    )
                )
            elif row in group_member_rows:
                number_item.setIcon(QIcon())
                number_item.setToolTip(
                    tr("Teil des Bausteins „{name}“", name=self._group_containing(row).name)
                )
            else:
                number_item.setIcon(QIcon())
                number_item.setToolTip("")

        self._sync_all_header_overlays()

        for row in range(self._table.rowCount()):
            self._apply_row_style(row)

        # Rechtsversatz der kompletten Mitgliederzeilen zuletzt -- die
        # Zellen-Widgets muessen dafuer bereits ihre endgueltige Zeilen-/
        # Spaltenzuordnung haben (siehe _apply_member_row_shifts).
        self._apply_member_row_shifts()

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
            self._block_save_button,
            self._block_insert_button,
        ):
            button.setEnabled(not running)
        self._report_button.setEnabled(self._report_available and not running)
        self._update_run_enabled()

    def on_run_started(self) -> None:
        self._clear_all_row_colors()
        self._iteration_text = ""
        self._report_available = False
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
        self._reveal_row(index)
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
        if step.step_type == "wait":
            self._set_status(
                "Schritt {index}/{total}: Warten ({duration:g} s) {iter}",
                index=index + 1, total=total, duration=step.duration, iter=self._iteration_text,
            )
            return
        label = action_label(step.device_kind, step.action)
        device_display = self._device_display(step.device_kind, step.device_id)
        if is_arb_action(step.action):
            shape = arb_shape_label(step.arb_shape)
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
        group = self._group_containing(index)
        if group is not None:
            self._update_block_check_aggregate(group)
            self._apply_row_style(group.start)

    def _update_block_check_aggregate(self, group: _BlockGroup) -> None:
        """Fasst die Pruefergebnisse aller Schritte eines Bausteins zu einem
        Gesamtergebnis fuer dessen Kopfzeile zusammen (gruen nur, wenn ALLE
        bislang ausgewerteten Unter-Pruefungen bestanden haben) -- dieses
        Aggregat wird wie ein normales Zeilenergebnis in _check_results
        abgelegt, damit die vorhandene Faerbe-/Overlay-Logik
        (_row_style_color/_apply_row_style) es ohne Sonderfall fuer die
        Kopfzeile mitbehandelt. Schritte ohne aktive Pruefung liefern nie
        einen Eintrag in _check_results und fliessen daher nicht ein --
        ohne jede Pruefung im Baustein bleibt die Kopfzeile ungefaerbt."""
        rows_with_result = [
            row for row in range(group.start, group.start + group.count)
            if row in self._check_results
        ]
        if not rows_with_result:
            return
        self._check_results[group.start] = all(self._check_results[row] for row in rows_with_result)

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
        self.notify_requested.emit(tr("Testlauf beendet"), self._status_label.text())

    def on_run_stopped(self) -> None:
        self._stop_blink()
        self._iteration_text = ""
        self.set_running(False)
        self._set_status("Gestoppt")

    def on_step_failed(self, index: int, message: str) -> None:
        self._reveal_row(index)
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
        self.notify_requested.emit(tr("Testlauf-Fehler"), self._status_label.text())

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
        # check_pass statt success, damit "bestanden" auch im Dark-Theme
        # gruen ist (dort ist success amber, siehe theme.Palette.check_pass).
        if row in self._check_results:
            return pal.check_pass if self._check_results[row] else pal.danger
        if row == self._selected_row:
            return pal.selection
        return None

    def _apply_row_style(self, row: int) -> None:
        if row < 0 or row >= self._table.rowCount():
            return
        color = self._row_style_color(row)
        style = f"background-color: {color};" if color else ""
        # Der Baustein-Rechtsversatz einer Zeile steckt NICHT im Stylesheet,
        # sondern in der Widget-Geometrie bzw. im Delegate der Spalte "#"
        # (siehe _apply_member_row_shifts/_NumColumnDelegate): Padding wuerde
        # je nach Widget-Typ nur den Inhalt einruecken statt das Element zu
        # verschieben. Hier bleibt nur die (davon unabhaengige)
        # Schleifen/If-Verschachtelungstiefe _indent_depth, die wie zuvor als
        # Texteinzug in Spalte COL_DEVICE dargestellt wird.
        combo_style = self._combo_row_style(color)
        field_style = self._field_row_style(color)
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
                left = depth * 18
                if isinstance(widget, QComboBox):
                    widget.setStyleSheet(self._combo_row_style(color, left, border))
                else:
                    base = f"background-color: {color};" if color else ""
                    widget.setStyleSheet(f"{base} padding-left: {left}px; {border}")
            elif isinstance(widget, QComboBox):
                widget.setStyleSheet(combo_style)
            elif isinstance(widget, (QDoubleSpinBox, QSpinBox, QLineEdit)):
                # Diese "nativen" Eingabefelder teilen sich mit QComboBox
                # dieselbe globale Regel in theme.py::form_control_qss() --
                # ein simples "padding-left" reicht hier aus demselben Grund
                # nicht (siehe _combo_row_style), daher ebenfalls die
                # vollstaendig selbst-deklarierte Variante verwenden.
                widget.setStyleSheet(field_style)
            else:
                widget.setStyleSheet(style)
        number_item = self._table.item(row, COL_NUM)
        if number_item is not None:
            number_item.setBackground(QBrush(QColor(color)) if color else QBrush())
        # Baustein-Kopfzeilen-Overlay traegt seinen eigenen Hintergrund
        # (haengt nicht als Zellen-Widget an der Schleife oben, siehe
        # _BlockHeaderOverlay) -- separat nachziehen, damit Auswahl-/Blink-/
        # Fehlerfarbe auch im eingeklappten Zustand sichtbar bleiben.
        group = self._group_at_header(row)
        if group is not None:
            self._sync_header_overlay(group)
            self._sync_duration_overlay(group)
            self._sync_check_overlay(group)

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
        #
        # Border/Hintergrund/Padding werden hier VOLLSTAENDIG neu deklariert
        # (nicht nur "padding-left") statt sich auf eine Kaskade mit der
        # globalen QComboBox-Regel aus theme.py::form_control_qss() zu
        # verlassen -- ein bloßes "padding-left" ergab dort im echten Theme
        # (mit bereits gesetztem "padding: 3px 6px;") nur einen kaum
        # sichtbaren Bruchteil des gewuenschten Einzugs statt der vollen
        # Pixelzahl. Mit vollstaendig eigener Deklaration "gehoert" dieses
        # Stylesheet der ComboBox allein, keine Kaskade noetig.
        pal = current_palette()
        combo_bg = color or pal.surface
        box_border = border or f"border: 1px solid {pal.border};"
        return (
            f"QComboBox {{"
            f" background-color: {combo_bg}; color: {pal.text};"
            f" {box_border} border-radius: 4px;"
            f" padding: 3px 6px 3px {6 + indent_px}px; }}"
            f"QComboBox QAbstractItemView {{"
            f" background-color: {pal.surface}; color: {pal.text}; }}"
            f"QComboBox QAbstractItemView::item {{"
            f" background-color: {pal.surface}; color: {pal.text}; padding: 3px 6px; }}"
            f"QComboBox QAbstractItemView::item:hover {{"
            f" background-color: {pal.selection}; color: {pal.text}; }}"
            f"QComboBox QAbstractItemView::item:selected {{"
            f" background-color: {pal.selection}; color: {pal.text}; }}"
        )

    def _field_row_style(self, color: str | None, indent_px: int = 0, border: str = "") -> str:
        """Wie _combo_row_style, aber fuer die uebrigen "nativen" Eingabefelder
        (QLineEdit/QDoubleSpinBox/QSpinBox), die dieselbe globale Regel aus
        theme.py::form_control_qss() teilen und daher denselben
        Kaskaden-Effekt zeigen -- ebenfalls vollstaendig selbst deklariert
        statt nur "padding-left" (siehe dortiger Kommentar)."""
        pal = current_palette()
        bg = color or pal.surface
        box_border = border or f"border: 1px solid {pal.border};"
        return (
            f"background-color: {bg}; color: {pal.text}; {box_border}"
            f" border-radius: 4px; padding: 3px 6px 3px {6 + indent_px}px;"
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

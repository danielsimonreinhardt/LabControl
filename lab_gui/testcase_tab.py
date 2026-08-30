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
    QMessageBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import Translator, tr
from icons import IconButton
from paths import app_dir
from signal_dialog import SignalDialog
from theme import Palette, ThemeManager
from theme import current as current_palette
from testcase_model import (
    ACTION_VALUE_RANGE,
    ARB_TARGETS,
    DEVICE_ACTIONS,
    DEVICE_KIND_LABELS,
    VALUELESS_ACTIONS,
    TestStep,
    action_label,
    is_arb_action,
    kind_label,
    load_steps,
    save_steps,
)

# Deutsche Basis-Anzeigenamen (Uebersetzungsschluessel) der Spaltenkoepfe.
# "#" ist sprachunabhaengig.
BASE_COLUMNS = ["#", "Gerät", "Aktion", "Wert", "Dauer (s)", "Aktiv"]
# Default-Spaltenbreiten (Pixel) fuer die Standard-Fenstergroesse (1000x700
# Hauptfenster -> ca. 958px Tabellenbreite). Aus einem vom Nutzer vorgegebenen
# Referenz-Screenshot als Anteile ermittelt und auf diese Breite umgerechnet --
# absolute Pixelwerte 1:1 aus dem (viel breiteren) Screenshot zu uebernehmen
# liess der Aktion-Spalte bei der Standardgroesse kaum Platz. Aktion (Index 2)
# bleibt Stretch und nimmt sich den Rest; alle Spalten bleiben per Drag&Drop
# veraenderbar (Interactive-Resize).
DEFAULT_COLUMN_WIDTHS = {0: 49, 1: 167, 3: 119, 4: 94, 5: 77}

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

        # "Bereit"/"Läuft…"/"Fertig"/"Gestoppt" (siehe _set_status) statt
        # eines Fehlertexts -- fuer die Retranslate braucht der aktuelle
        # Status einen stabilen Schluessel statt des schon uebersetzten Texts.
        self._status_key = "Bereit"
        self._status_key_kwargs: dict = {}

        button_row = QHBoxLayout()
        self._add_button = IconButton("mdi.plus", "")
        self._remove_button = IconButton("mdi.minus", "")
        self._up_button = IconButton("mdi.arrow-up", "")
        self._down_button = IconButton("mdi.arrow-down", "")
        self._load_button = IconButton("mdi.folder-open-outline", "")
        self._save_button = IconButton("mdi.content-save-outline", "")
        self._add_button.clicked.connect(self._add_row_clicked)
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
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
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
        self._status_is_error = False
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
        self._run_button.setToolTip(tr("Start"))
        self._run_button.setText(tr("Start"))
        self._stop_button.setToolTip(tr("Stop"))
        self._stop_button.setText(tr("Stop"))
        self._set_status(self._status_key, error=self._status_is_error, **self._status_key_kwargs)
        for row in range(self._table.rowCount()):
            self._retranslate_row(row)

    def _retranslate_row(self, row: int) -> None:
        device_combo: QComboBox = self._table.cellWidget(row, 1)
        action_combo: QComboBox = self._table.cellWidget(row, 2)
        value_stack: QStackedWidget = self._table.cellWidget(row, 3)
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

    def _on_theme_changed(self, palette: Palette) -> None:
        if self._status_is_error:
            self._status_label.setStyleSheet(f"color: {palette.danger}; font-weight: bold;")
        else:
            self._status_label.setStyleSheet(f"color: {palette.text_muted};")
        # Arbiträrsignal-Zusammenfassung je Zeile (arb_page, Index 1 im
        # value_stack) haengt nicht am ThemeManager-Signal -- Zeilen kommen
        # und gehen (Zeile hinzufuegen/entfernen/verschieben), ein direktes
        # Signal-Connect pro Label wuerde beim Entfernen einer Zeile nicht
        # sauber wieder abgehaengt. Stattdessen hier zentral ueber die aktuell
        # vorhandenen Zeilen iterieren.
        for row in range(self._table.rowCount()):
            value_stack: QStackedWidget = self._table.cellWidget(row, 3)
            arb_page = value_stack.widget(1)
            arb_summary_label = arb_page.findChild(QLabel)
            arb_summary_label.setStyleSheet(f"color: {palette.text_muted}; font-style: italic;")
            refresh = getattr(value_stack.widget(0), "_refresh_limit_warning", None)
            if refresh is not None:
                refresh()
            self._apply_row_style(row)

    # -- Statusanzeige ----------------------------------------------------------

    def _set_status(self, key: str, error: bool = False, **kwargs) -> None:
        self._status_key = key
        self._status_key_kwargs = kwargs
        self._status_is_error = error
        pal = current_palette()
        if error:
            self._status_label.setStyleSheet(f"color: {pal.danger}; font-weight: bold;")
        else:
            self._status_label.setStyleSheet(f"color: {pal.text_muted};")
        self._status_label.setText(tr(key, **kwargs))

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
            value_stack: QStackedWidget = self._table.cellWidget(row, 3)
            value_spin = value_stack.widget(0)
            refresh = getattr(value_spin, "_refresh_limit_warning", None)
            if refresh is not None:
                refresh()

    def _refresh_device_combos(self) -> None:
        for row in range(self._table.rowCount()):
            combo: QComboBox = self._table.cellWidget(row, 1)
            self._populate_device_combo(combo, _parse_device_key(combo.currentData()))

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
        self._insert_row(self._table.rowCount(), TestStep())

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
        self._table.setItem(row_index, 0, number_item)

        device_combo = QComboBox()
        self._table.setCellWidget(row_index, 1, device_combo)

        action_combo = QComboBox()
        self._table.setCellWidget(row_index, 2, action_combo)

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
        self._table.setCellWidget(row_index, 3, value_stack)

        duration_spin = QDoubleSpinBox()
        duration_spin.setRange(0, 36000)
        duration_spin.setDecimals(1)
        duration_spin.setSuffix(" s")
        duration_spin.setValue(step.duration)
        self._table.setCellWidget(row_index, 4, duration_spin)

        enabled_check = QCheckBox()
        enabled_check.setChecked(step.enabled)
        check_container = QWidget()
        check_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        check_layout = QHBoxLayout(check_container)
        check_layout.addWidget(enabled_check)
        check_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        check_layout.setContentsMargins(0, 0, 0, 0)
        self._table.setCellWidget(row_index, 5, check_container)

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

        self._renumber_rows()

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
            self._table.item(row, 0).setText(str(row + 1))

    # -- Lesen/Schreiben der Zeilen -------------------------------------------

    def _row_to_step(self, row: int) -> TestStep:
        device_combo: QComboBox = self._table.cellWidget(row, 1)
        action_combo: QComboBox = self._table.cellWidget(row, 2)
        value_stack: QStackedWidget = self._table.cellWidget(row, 3)
        value_spin: QDoubleSpinBox = value_stack.widget(0)
        arb_page: QWidget = value_stack.widget(1)
        duration_spin: QDoubleSpinBox = self._table.cellWidget(row, 4)
        check_container = self._table.cellWidget(row, 5)
        enabled_check: QCheckBox = check_container.findChild(QCheckBox)

        kind, device_id = _parse_device_key(device_combo.currentData())
        action_code = action_combo.currentData() or ""
        params = arb_page._params
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
        )

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

    # -- Ausfuehrung -----------------------------------------------------------

    def set_running(self, running: bool) -> None:
        self._run_button.setEnabled(not running)
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

    def on_run_started(self) -> None:
        self._clear_all_row_colors()
        self.set_running(True)
        self._set_status("Läuft…")

    def on_step_started(self, index: int, step: TestStep) -> None:
        self._start_blink(index)
        total = self._table.rowCount()
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
            "Schritt {index}/{total}: {device} – {action} ({detail})",
            index=index + 1,
            total=total,
            device=device_display,
            action=label,
            detail=detail,
        )

    def on_run_finished(self) -> None:
        self._stop_blink()
        self.set_running(False)
        self._set_status("Fertig")

    def on_run_stopped(self) -> None:
        self._stop_blink()
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
            error=True,
            index=index + 1,
            total=total,
            message=message,
        )

    def _on_stop_clicked(self) -> None:
        self.stop_requested.emit()
        self._stop_blink()
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
            widget.setStyleSheet(combo_style if isinstance(widget, QComboBox) else style)
        number_item = self._table.item(row, 0)
        if number_item is not None:
            number_item.setBackground(QBrush(QColor(color)) if color else QBrush())

    def _combo_row_style(self, color: str | None) -> str:
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
        return (
            f"QComboBox {{ {combo_bg} }}"
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
        for row in range(self._table.rowCount()):
            self._apply_row_style(row)

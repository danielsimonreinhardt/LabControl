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
    load_steps,
    save_steps,
)

COLUMNS = ["#", "Gerät", "Aktion", "Wert", "Dauer (s)", "Aktiv"]
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


class TestcaseTab(QWidget):
    run_requested = Signal()
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        # device_id -> (kind, label) fuer alle in dieser Session bekannten Geraete;
        # gespeist von DeviceRegistry ueber on_device_known()/on_label_changed().
        self._known_devices: dict[str, tuple[str, str]] = {}

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_row = -1
        self._blink_on = False

        button_row = QHBoxLayout()
        self._add_button = IconButton("mdi.plus", "Zeile hinzufügen")
        self._remove_button = IconButton("mdi.minus", "Zeile entfernen")
        self._up_button = IconButton("mdi.arrow-up", "Nach oben")
        self._down_button = IconButton("mdi.arrow-down", "Nach unten")
        self._load_button = IconButton("mdi.folder-open-outline", "Laden…")
        self._save_button = IconButton("mdi.content-save-outline", "Speichern…")
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

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        header = self._table.horizontalHeader()
        for col in range(len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        for col, width in DEFAULT_COLUMN_WIDTHS.items():
            header.resizeSection(col, width)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self._table)

        run_row = QHBoxLayout()
        self._run_button = IconButton("mdi.play", "Start", text="Start")
        self._stop_button = IconButton("mdi.stop", "Stop", text="Stop")
        self._stop_button.setEnabled(False)
        self._run_button.clicked.connect(self.run_requested.emit)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._status_label = QLabel("Bereit")
        self._status_is_error = False
        self._status_label.setStyleSheet(f"color: {current_palette().text_muted};")
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._stop_button)
        run_row.addWidget(self._status_label)
        run_row.addStretch()
        layout.addLayout(run_row)

        ThemeManager.instance().changed.connect(self._on_theme_changed)

        self._add_row_clicked()

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

    # -- Geraeteregistrierung (von MainWindow/DeviceRegistry gespeist) --------

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        self._known_devices[device_id] = (kind, label)
        self._refresh_device_combos()

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self._known_devices[device_id] = (kind, label)
        self._refresh_device_combos()

    def _refresh_device_combos(self) -> None:
        for row in range(self._table.rowCount()):
            combo: QComboBox = self._table.cellWidget(row, 1)
            self._populate_device_combo(combo, combo.currentData())

    def _device_combo_items(self) -> list[tuple[str, tuple[str, str]]]:
        items = [
            (f"{display} (automatisch)", (kind, ""))
            for kind, display in DEVICE_KIND_LABELS.items()
        ]
        for device_id, (kind, label) in sorted(self._known_devices.items(), key=lambda kv: kv[1][1]):
            items.append((f"{label} ({DEVICE_KIND_LABELS.get(kind, kind)})", (kind, device_id)))
        return items

    def _populate_device_combo(
        self, combo: QComboBox, selected: tuple[str, str] | None
    ) -> None:
        if selected is None:
            selected = ("load", "")
        combo.blockSignals(True)
        combo.clear()
        for text, data in self._device_combo_items():
            combo.addItem(text, data)
        index = combo.findData(selected)
        if index < 0:
            kind, device_id = selected
            if device_id:
                # Aus einer gespeicherten Datei geladenes Geraet, das diese
                # Session noch nicht gesehen hat -- Eintrag beibehalten statt
                # die Auswahl stillschweigend zu verwerfen.
                combo.addItem(f"{device_id} (nicht verbunden)", selected)
                index = combo.count() - 1
            else:
                index = max(combo.findData((kind, "")), 0)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)
        combo.currentIndexChanged.emit(index)

    def _device_display(self, kind: str, device_id: str) -> str:
        if device_id in self._known_devices:
            return self._known_devices[device_id][1]
        base = DEVICE_KIND_LABELS.get(kind, kind)
        return f"{base} (automatisch)" if not device_id else f"{base} ({device_id}, nicht verbunden)"

    # -- Zeilenverwaltung -----------------------------------------------------

    def _add_row_clicked(self) -> None:
        self._insert_row(self._table.rowCount(), TestStep())

    def _insert_row(self, row_index: int, step: TestStep) -> None:
        self._table.insertRow(row_index)

        number_item = QTableWidgetItem()
        number_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
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
        arb_button = IconButton("mdi.sine-wave", "Signal definieren…")
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
            data = device_combo.currentData()
            return data[0] if data else "load"

        def refresh_actions(current_action: str = step.action) -> None:
            kind = current_kind()
            action_combo.blockSignals(True)
            action_combo.clear()
            action_combo.addItems(DEVICE_ACTIONS[kind].keys())
            code_to_label = {v: k for k, v in DEVICE_ACTIONS[kind].items()}
            if current_action in code_to_label:
                action_combo.setCurrentText(code_to_label[current_action])
            action_combo.blockSignals(False)
            action_combo.currentTextChanged.emit(action_combo.currentText())

        def refresh_arb_summary() -> None:
            kind = current_kind()
            params = arb_page._params
            shape_label = "Sinus" if params["shape"] != "square" else "Rechteck"
            target_label = action_label(kind, params["target"]) if params["target"] else "?"
            arb_summary_label.setText(
                f"{shape_label}: {target_label}, {params['offset']:g}±{params['amplitude']:g}, "
                f"{params['frequency']:g} Hz"
            )

        def open_signal_dialog() -> None:
            kind = current_kind()
            dialog = SignalDialog(kind, duration_spin.value(), arb_page._params, self)
            if dialog.exec() == SignalDialog.DialogCode.Accepted:
                arb_page._params = dialog.params()
                refresh_arb_summary()

        arb_button.clicked.connect(open_signal_dialog)

        def on_action_changed(label: str) -> None:
            kind = current_kind()
            code = DEVICE_ACTIONS[kind].get(label, "")
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

        device_combo.currentIndexChanged.connect(lambda _=None: refresh_actions())
        action_combo.currentTextChanged.connect(on_action_changed)
        self._populate_device_combo(device_combo, (step.device_kind, step.device_id))
        value_spin.setValue(step.value)

        self._renumber_rows()

    def _remove_selected_row(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
            self._renumber_rows()

    def _move_selected_row(self, offset: int) -> None:
        row = self._table.currentRow()
        target = row + offset
        if row < 0 or not (0 <= target < self._table.rowCount()):
            return
        step = self._row_to_step(row)
        self._table.removeRow(row)
        self._insert_row(target, step)
        self._table.selectRow(target)

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

        kind, device_id = device_combo.currentData() or ("load", "")
        action_code = DEVICE_ACTIONS[kind].get(action_combo.currentText(), "")
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
            self, "Testablauf speichern", str(DEFAULT_DIR), "Testablauf (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix != ".json":
            path = path.with_suffix(".json")
        try:
            save_steps(self.steps(), path)
        except OSError as exc:
            QMessageBox.critical(self, "Fehler beim Speichern", str(exc))

    def _load_from_file(self) -> None:
        DEFAULT_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Testablauf laden", str(DEFAULT_DIR), "Testablauf (*.json)"
        )
        if not path_str:
            return
        try:
            steps = load_steps(Path(path_str))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Fehler beim Laden", str(exc))
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
        self._status_is_error = False
        self._status_label.setStyleSheet(f"color: {current_palette().text_muted};")
        self._status_label.setText("Läuft…")

    def on_step_started(self, index: int, step: TestStep) -> None:
        self._start_blink(index)
        total = self._table.rowCount()
        label = action_label(step.device_kind, step.action)
        device_display = self._device_display(step.device_kind, step.device_id)
        if is_arb_action(step.action):
            shape = "Sinus" if step.arb_shape != "square" else "Rechteck"
            target_label = action_label(step.device_kind, step.arb_target)
            detail = (
                f"{shape} auf {target_label}, {step.arb_offset:g}±{step.arb_amplitude:g}, "
                f"{step.arb_frequency:g} Hz, {step.duration:g} s"
            )
        else:
            detail = f"{step.value}"
        self._status_label.setText(
            f"Schritt {index + 1}/{total}: {device_display} – {label} ({detail})"
        )

    def on_run_finished(self) -> None:
        self._stop_blink()
        self.set_running(False)
        self._status_is_error = False
        self._status_label.setStyleSheet(f"color: {current_palette().text_muted};")
        self._status_label.setText("Fertig")

    def on_run_stopped(self) -> None:
        self._stop_blink()
        self.set_running(False)
        self._status_is_error = False
        self._status_label.setStyleSheet(f"color: {current_palette().text_muted};")
        self._status_label.setText("Gestoppt")

    def on_step_failed(self, index: int, message: str) -> None:
        self._stop_blink()
        self._set_row_color(index, current_palette().danger)
        # Ablauf ist intern zwar schon gestoppt, aber der Fehler bleibt sichtbar
        # (rote Zeile) und die Bedienelemente gesperrt, bis er ueber "Stop"
        # quittiert wird -- set_running(True) haelt dafuer den Stop-Button aktiv.
        self.set_running(True)
        total = self._table.rowCount()
        self._status_is_error = True
        self._status_label.setStyleSheet(f"color: {current_palette().danger}; font-weight: bold;")
        self._status_label.setText(
            f"Fehler bei Schritt {index + 1}/{total}: {message} — mit Stop quittieren"
        )

    def _on_stop_clicked(self) -> None:
        self.stop_requested.emit()
        self._stop_blink()
        self._clear_all_row_colors()
        self.set_running(False)
        self._status_is_error = False
        self._status_label.setStyleSheet(f"color: {current_palette().text_muted};")
        self._status_label.setText("Gestoppt")

    # -- Blink-/Fehleranzeige -------------------------------------------------

    def _start_blink(self, row: int) -> None:
        self._stop_blink()
        self._blink_row = row
        self._blink_on = False
        self._toggle_blink()
        self._blink_timer.start(BLINK_INTERVAL_MS)

    def _stop_blink(self) -> None:
        self._blink_timer.stop()
        if self._blink_row >= 0:
            self._set_row_color(self._blink_row, None)
        self._blink_row = -1

    def _toggle_blink(self) -> None:
        self._blink_on = not self._blink_on
        self._set_row_color(self._blink_row, current_palette().success if self._blink_on else None)

    def _set_row_color(self, row: int, color: str | None) -> None:
        if row < 0 or row >= self._table.rowCount():
            return
        style = f"background-color: {color};" if color else ""
        for col in range(1, self._table.columnCount()):
            widget = self._table.cellWidget(row, col)
            if widget is not None:
                widget.setStyleSheet(style)
        number_item = self._table.item(row, 0)
        if number_item is not None:
            number_item.setBackground(QBrush(QColor(color)) if color else QBrush())

    def _clear_all_row_colors(self) -> None:
        for row in range(self._table.rowCount()):
            self._set_row_color(row, None)

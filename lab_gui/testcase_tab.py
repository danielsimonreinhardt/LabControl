"""Testcase-Tab: zeilenbasierter Editor + Ausfuehrung fuer Testablauf-Schritte.

Jede Zeile beschreibt einen Schritt: Geraet, Aktion, Wert, Dauer (Wartezeit
nach dem Schritt) und ob der Schritt aktiv ist. Die eigentliche Ausfuehrung
uebernimmt ein TestRunner (siehe testcase_runner.py), der ueber
run_requested/stop_requested angesteuert wird.
"""
from __future__ import annotations

import sys
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
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from testcase_model import (
    ACTION_VALUE_RANGE,
    DEVICE_ACTIONS,
    VALUELESS_ACTIONS,
    TestStep,
    action_label,
    load_steps,
    save_steps,
)

COLUMNS = ["#", "Gerät", "Aktion", "Wert", "Dauer (s)", "Aktiv"]

# Als PyInstaller-.exe liegt __file__ im ephemeren Temp-Extraktionsordner;
# dort gespeicherte Testablaeufe wuerden beim Beenden verloren gehen. Daher
# im gefrorenen Fall neben der .exe speichern, sonst neben dem Skript.
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = Path(__file__).resolve().parent
DEFAULT_DIR = _APP_DIR / "testcases"

BLINK_COLOR = "#43a047"   # gruen: aktiver Schritt
ERROR_COLOR = "#e53935"   # rot: Schritt mit Problem abgebrochen
BLINK_INTERVAL_MS = 400


class TestcaseTab(QWidget):
    run_requested = Signal()
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_row = -1
        self._blink_on = False

        button_row = QHBoxLayout()
        self._add_button = QPushButton("Zeile hinzufügen")
        self._remove_button = QPushButton("Zeile entfernen")
        self._up_button = QPushButton("Nach oben")
        self._down_button = QPushButton("Nach unten")
        self._load_button = QPushButton("Laden…")
        self._save_button = QPushButton("Speichern…")
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
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self._table)

        run_row = QHBoxLayout()
        self._run_button = QPushButton("Start")
        self._stop_button = QPushButton("Stop")
        self._stop_button.setEnabled(False)
        self._run_button.clicked.connect(self.run_requested.emit)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._status_label = QLabel("Bereit")
        self._status_label.setStyleSheet("color: gray;")
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._stop_button)
        run_row.addWidget(self._status_label)
        run_row.addStretch()
        layout.addLayout(run_row)

        self._add_row_clicked()

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
        device_combo.addItems(DEVICE_ACTIONS.keys())
        device_combo.setCurrentText(step.device)
        self._table.setCellWidget(row_index, 1, device_combo)

        action_combo = QComboBox()
        self._table.setCellWidget(row_index, 2, action_combo)

        value_spin = QDoubleSpinBox()
        value_spin.setDecimals(3)
        self._table.setCellWidget(row_index, 3, value_spin)

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

        def refresh_actions(current_action: str = step.action) -> None:
            action_combo.blockSignals(True)
            action_combo.clear()
            action_combo.addItems(DEVICE_ACTIONS[device_combo.currentText()].keys())
            code_to_label = {v: k for k, v in DEVICE_ACTIONS[device_combo.currentText()].items()}
            if current_action in code_to_label:
                action_combo.setCurrentText(code_to_label[current_action])
            action_combo.blockSignals(False)
            action_combo.currentTextChanged.emit(action_combo.currentText())

        def on_action_changed(label: str) -> None:
            code = DEVICE_ACTIONS[device_combo.currentText()].get(label, "")
            unit, lo, hi = ACTION_VALUE_RANGE.get(code, ("", -1000, 10000))
            value_spin.setSuffix(f" {unit}" if unit else "")
            value_spin.setRange(lo, hi)
            value_spin.setEnabled(code not in VALUELESS_ACTIONS)

        device_combo.currentTextChanged.connect(lambda _=None: refresh_actions())
        action_combo.currentTextChanged.connect(on_action_changed)
        refresh_actions()
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
        value_spin: QDoubleSpinBox = self._table.cellWidget(row, 3)
        duration_spin: QDoubleSpinBox = self._table.cellWidget(row, 4)
        check_container = self._table.cellWidget(row, 5)
        enabled_check: QCheckBox = check_container.findChild(QCheckBox)

        device = device_combo.currentText()
        action_code = DEVICE_ACTIONS[device].get(action_combo.currentText(), "")
        return TestStep(
            device=device,
            action=action_code,
            value=value_spin.value(),
            duration=duration_spin.value(),
            enabled=enabled_check.isChecked(),
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
        self._status_label.setStyleSheet("color: gray;")
        self._status_label.setText("Läuft…")

    def on_step_started(self, index: int, step: TestStep) -> None:
        self._start_blink(index)
        total = self._table.rowCount()
        label = action_label(step.device, step.action)
        self._status_label.setText(
            f"Schritt {index + 1}/{total}: {step.device} – {label} ({step.value})"
        )

    def on_run_finished(self) -> None:
        self._stop_blink()
        self.set_running(False)
        self._status_label.setStyleSheet("color: gray;")
        self._status_label.setText("Fertig")

    def on_run_stopped(self) -> None:
        self._stop_blink()
        self.set_running(False)
        self._status_label.setStyleSheet("color: gray;")
        self._status_label.setText("Gestoppt")

    def on_step_failed(self, index: int, message: str) -> None:
        self._stop_blink()
        self._set_row_color(index, ERROR_COLOR)
        # Ablauf ist intern zwar schon gestoppt, aber der Fehler bleibt sichtbar
        # (rote Zeile) und die Bedienelemente gesperrt, bis er ueber "Stop"
        # quittiert wird -- set_running(True) haelt dafuer den Stop-Button aktiv.
        self.set_running(True)
        total = self._table.rowCount()
        self._status_label.setStyleSheet("color: #e53935; font-weight: bold;")
        self._status_label.setText(
            f"Fehler bei Schritt {index + 1}/{total}: {message} — mit Stop quittieren"
        )

    def _on_stop_clicked(self) -> None:
        self.stop_requested.emit()
        self._stop_blink()
        self._clear_all_row_colors()
        self.set_running(False)
        self._status_label.setStyleSheet("color: gray;")
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
        self._set_row_color(self._blink_row, BLINK_COLOR if self._blink_on else None)

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

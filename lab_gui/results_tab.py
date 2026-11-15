"""Reiter "Ergebnisse" (FT-11, PI-57 a): Laeufe von PC und Pi aus den Berichtsordnern, je Ablauf Historie,
wackelnde Pruefungen, Messwert-Trend und Vergleich zweier Laeufe. Daten: run_history.py."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

import run_history
from i18n import Translator, tr
from icons import IconButton
from theme import ThemeManager, current as current_palette, set_tone

RELOAD_INTERVAL_MS = 60_000


class TrendChart(QWidget):
    """Messwerte einer Pruefung ueber die Laeufe: Linie, Punkte gruen/rot, Grenzen gestrichelt."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: list[dict] = []
        self.setMinimumHeight(180)
        ThemeManager.instance().changed.connect(lambda _p: self.update())

    def set_points(self, points: list[dict]) -> None:
        self._points = list(points)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 -- Qt
        pal = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(64, 12, -12, -24)
        painter.setPen(QPen(QColor(pal.border)))
        painter.drawRect(rect)
        if not self._points:
            painter.setPen(QColor(pal.text_muted))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("Keine Messwerte"))
            return
        values = [p["value"] for p in self._points]
        limits = [v for p in self._points for v in (p["min"], p["max"]) if isinstance(v, (int, float))]
        low, high = min(values + limits), max(values + limits)
        if high == low:
            low, high = low - 1, high + 1
        span = high - low
        low, high = low - span * 0.05, high + span * 0.05

        def pos(i: int, v: float) -> QPointF:
            x = rect.left() + (i / max(1, len(values) - 1)) * rect.width()
            return QPointF(x, rect.bottom() - (v - low) / (high - low) * rect.height())

        painter.setPen(QColor(pal.text_muted))
        for v in (low, high):
            painter.drawText(QRectF(0, pos(0, v).y() - 8, 58, 16), Qt.AlignmentFlag.AlignRight, _axis_text(v))
        limit_pen = QPen(QColor(pal.text_muted))
        limit_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(limit_pen)
        for key in ("min", "max"):
            limit = self._points[-1][key]
            if isinstance(limit, (int, float)):
                y = pos(0, limit).y()
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.setPen(QPen(QColor(pal.accent), 1.6))
        for i in range(1, len(values)):
            painter.drawLine(pos(i - 1, values[i - 1]), pos(i, values[i]))
        for i, point in enumerate(self._points):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(pal.success if point["verdict"] == "passed" else pal.danger))
            painter.drawEllipse(pos(i, point["value"]), 3.5, 3.5)
        painter.setPen(QColor(pal.text_muted))
        painter.drawText(QRectF(rect.left(), rect.bottom() + 4, rect.width(), 16), Qt.AlignmentFlag.AlignLeft,
                         self._points[0]["started"][:16].replace("T", " "))
        painter.drawText(QRectF(rect.left(), rect.bottom() + 4, rect.width(), 16), Qt.AlignmentFlag.AlignRight,
                         self._points[-1]["started"][:16].replace("T", " "))


def _axis_text(value: float) -> str:
    """Achsenbeschriftung ohne Exponent: 11000 statt 1.1e+04, 0.125 statt 1.25e-01."""
    magnitude = abs(value)
    if magnitude >= 100:
        return f"{value:.0f}"
    if magnitude >= 1:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _item(text: str, tone: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if tone:
        pal = current_palette()
        item.setForeground(QColor(pal.success if tone == "ok" else pal.danger))
    return item


def _verdict_text(verdict: str) -> str:
    return {"passed": tr("bestanden"), "failed": tr("nicht bestanden"), "not_run": tr("nicht ausgeführt")}.get(
        verdict, verdict)


class ResultsTab(QWidget):
    open_report_requested = Signal(str)     # Pfad des HTML-Berichts eines Laufs

    def __init__(self, folders: Callable[[], list[Path]], parent=None) -> None:
        super().__init__(parent)
        self._folders = folders
        self._cache: dict = {}
        self._runs: list[run_history.RunSummary] = []
        self._shown: list[run_history.RunSummary] = []
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self._testcase = QComboBox()
        self._testcase.setMinimumWidth(260)
        self._testcase.currentIndexChanged.connect(lambda _i: self._show_testcase())
        self._reload_button = IconButton("mdi.refresh", "")
        self._reload_button.clicked.connect(self.reload)
        self._report_button = IconButton("mdi.file-document-outline", "")
        self._report_button.clicked.connect(self._open_report)
        self._summary = QLabel()
        top.addWidget(self._testcase)
        top.addWidget(self._reload_button)
        top.addWidget(self._summary, 1)
        top.addWidget(self._report_button)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._runs_table = QTableWidget(0, 5)
        self._runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._runs_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._runs_table.verticalHeader().setVisible(False)
        self._runs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._runs_table.itemSelectionChanged.connect(self._on_selection)
        splitter.addWidget(self._runs_table)

        self._tabs = QTabWidget()
        self._flaky_table = QTableWidget(0, 4)
        self._flaky_table.verticalHeader().setVisible(False)
        self._flaky_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        trend_page = QWidget()
        trend_layout = QVBoxLayout(trend_page)
        self._check = QComboBox()
        self._check.currentIndexChanged.connect(lambda _i: self._show_trend())
        self._chart = TrendChart()
        trend_layout.addWidget(self._check)
        trend_layout.addWidget(self._chart, 1)
        self._compare_table = QTableWidget(0, 5)
        self._compare_table.verticalHeader().setVisible(False)
        self._compare_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._compare_hint = QLabel()
        compare_page = QWidget()
        compare_layout = QVBoxLayout(compare_page)
        compare_layout.addWidget(self._compare_hint)
        compare_layout.addWidget(self._compare_table, 1)
        self._tabs.addTab(self._flaky_table, "")
        self._tabs.addTab(trend_page, "")
        self._tabs.addTab(compare_page, "")
        splitter.addWidget(self._tabs)
        splitter.setSizes([420, 600])
        layout.addWidget(splitter, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(RELOAD_INTERVAL_MS)
        self._timer.timeout.connect(self.reload)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._reload_button.setToolTip(tr("Neu einlesen"))
        self._report_button.setToolTip(tr("Bericht des gewählten Laufs öffnen"))
        self._runs_table.setHorizontalHeaderLabels([tr("Start"), tr("Rechner"), tr("Variante"), tr("Ergebnis"),
                                                    tr("Prüfungen")])
        self._flaky_table.setHorizontalHeaderLabels([tr("Prüfung"), tr("Wechsel"), tr("bestanden"),
                                                     tr("nicht bestanden")])
        self._compare_table.setHorizontalHeaderLabels([tr("Prüfung"), tr("Lauf A"), tr("Wert A"), tr("Lauf B"),
                                                       tr("Wert B")])
        self._tabs.setTabText(0, tr("Wackelnde Prüfungen"))
        self._tabs.setTabText(1, tr("Trend"))
        self._tabs.setTabText(2, tr("Vergleich"))
        self._compare_hint.setText(tr("Zwei Läufe links markieren (Strg+Klick)"))

    # -- Daten -----------------------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 -- Qt
        super().showEvent(event)
        self.reload()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 -- Qt
        super().hideEvent(event)
        self._timer.stop()

    def reload(self) -> None:
        self._runs = run_history.load_runs(self._folders(), self._cache)
        current = self._testcase.currentData()
        self._testcase.blockSignals(True)
        self._testcase.clear()
        names = run_history.testcases(self._runs)
        for name, count in names:
            self._testcase.addItem(f"{name} ({count})", name)
        if names:
            latest = self._runs[-1].testcase
            self._testcase.setCurrentIndex(max(0, self._testcase.findData(current or latest)))
        self._testcase.blockSignals(False)
        self._show_testcase()

    def _show_testcase(self) -> None:
        name = self._testcase.currentData()
        self._shown = list(reversed(run_history.for_testcase(self._runs, name))) if name else []
        table = self._runs_table
        table.setRowCount(0)
        for run in self._shown:
            row = table.rowCount()
            table.insertRow(row)
            failed = sum(c.verdict == "failed" for c in run.checks)
            outcome = tr(run_history.OUTCOME_DE.get(run.outcome, run.outcome))
            table.setItem(row, 0, _item(run.started[:16].replace("T", " ")))
            table.setItem(row, 1, _item(run.host))
            table.setItem(row, 2, _item(run.variant))
            table.setItem(row, 3, _item(outcome, "ok" if run.passed else "bad"))
            item = _item(f"{len(run.checks) - failed}/{len(run.checks)}")
            item.setToolTip(run.error)
            table.setItem(row, 4, item)
        table.resizeColumnsToContents()
        rate = run_history.pass_rate(self._shown)
        self._summary.setText("" if rate is None else
                              tr("{n} Läufe, {rate:.0f} % bestanden", n=len(self._shown), rate=rate * 100))
        set_tone(self._summary, "muted")
        flaky = run_history.flaky_checks(list(reversed(self._shown)))
        self._flaky_table.setRowCount(0)
        for entry in flaky:
            row = self._flaky_table.rowCount()
            self._flaky_table.insertRow(row)
            self._flaky_table.setItem(row, 0, _item(entry["label"]))
            self._flaky_table.setItem(row, 1, _item(str(entry["flips"])))
            self._flaky_table.setItem(row, 2, _item(str(entry["passed"]), "ok"))
            self._flaky_table.setItem(row, 3, _item(str(entry["failed"]), "bad"))
        current = self._check.currentData()
        self._check.blockSignals(True)
        self._check.clear()
        for key, label in run_history.check_keys(self._shown):
            self._check.addItem(label, key)
        self._check.setCurrentIndex(max(0, self._check.findData(current)))
        self._check.blockSignals(False)
        self._show_trend()
        self._on_selection()

    def _show_trend(self) -> None:
        key = self._check.currentData()
        self._chart.set_points(run_history.trend(list(reversed(self._shown)), key) if key else [])

    def _selected_runs(self) -> list[run_history.RunSummary]:
        rows = sorted({i.row() for i in self._runs_table.selectedIndexes()})
        return [self._shown[r] for r in rows if 0 <= r < len(self._shown)]

    def _on_selection(self) -> None:
        selected = self._selected_runs()
        self._report_button.setEnabled(len(selected) == 1 and Path(selected[0].file).with_suffix(".html").exists())
        self._compare_table.setRowCount(0)
        if len(selected) != 2:
            self._compare_hint.setVisible(True)
            return
        self._compare_hint.setVisible(False)
        newer, older = selected[0], selected[1]
        self._compare_table.setHorizontalHeaderLabels(
            [tr("Prüfung"), older.started[:16].replace("T", " "), tr("Wert"), newer.started[:16].replace("T", " "),
             tr("Wert")])
        for row_data in run_history.compare(older, newer):
            row = self._compare_table.rowCount()
            self._compare_table.insertRow(row)
            unit = f" {row_data['unit']}" if row_data["unit"] else ""
            tone_a = "ok" if row_data["a_verdict"] == "passed" else "bad"
            tone_b = "ok" if row_data["b_verdict"] == "passed" else "bad"
            label = _item(row_data["label"])
            if row_data["changed"]:
                font = label.font()
                font.setBold(True)
                label.setFont(font)
            self._compare_table.setItem(row, 0, label)
            self._compare_table.setItem(row, 1, _item(_verdict_text(row_data["a_verdict"]), tone_a))
            self._compare_table.setItem(row, 2, _item("" if row_data["a_value"] is None
                                                      else f"{row_data['a_value']:g}{unit}"))
            self._compare_table.setItem(row, 3, _item(_verdict_text(row_data["b_verdict"]), tone_b))
            self._compare_table.setItem(row, 4, _item("" if row_data["b_value"] is None
                                                      else f"{row_data['b_value']:g}{unit}"))
        self._tabs.setCurrentIndex(2)

    def _open_report(self) -> None:
        selected = self._selected_runs()
        if len(selected) == 1:
            self.open_report_requested.emit(str(Path(selected[0].file).with_suffix(".html")))

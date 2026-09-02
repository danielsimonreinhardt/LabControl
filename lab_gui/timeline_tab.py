"""Timeline-Reiter: zeitliche Verlaufsdarstellung (Oszilloskop) aller Messwerte.

Sammelt fortlaufend die Messwerte aller bekannten Geraete in Ringpuffern
(unabhaengig davon, ob der Tab gerade sichtbar ist). Angezeigt werden sie in
einem oder mehreren -- per Button hinzufuegbaren bzw. entfernbaren --
Diagrammen, die untereinander gestapelt werden. Jedes Diagramm hat im Header
einen Button, der ein Menue mit den noch nicht zugeordneten Signalen oeffnet;
die Auswahl fuegt das Signal als Chip mit Entfernen-Button hinzu und macht es
in keinem anderen Diagramm mehr waehlbar, bis es dort wieder entfernt wird --
ein Signal gehoert also nie zu mehr als einem Diagramm gleichzeitig.
Gezeichnet wird per Custom QPainter statt einer externen Plot-Bibliothek,
analog zur Oszilloskop-Vorschau in signal_dialog.py (keine zusaetzliche
Abhaengigkeit fuer den PyInstaller-Build auf dem Pi-Kiosk).

Innerhalb eines Diagramms teilen sich Signale gleicher Einheit eine y-Achse;
sind dort mehrere Einheiten vertreten, bekommt die erste (nach
AXIS_UNIT_PRIORITY) die linke, die zweite die rechte Achse. Weitere (seltene)
Einheiten landen ebenfalls auf der rechten Achse und teilen sich deren Skala.
Zeitfenster/Pause/Zurücksetzen wirken einheitlich auf alle Diagramme.

Diagramme lassen sich per Stift-Icon umbenennen. Sobald ein Diagramm einen
individuellen Namen traegt, werden Name und zugeordnete Signale (Geraet +
Feld) automatisch nach TIMELINE_LAYOUT_PATH gespeichert und beim naechsten
Start wiederhergestellt -- unbenannte Diagramme gelten als temporaer und
werden nicht persistiert. Da die Geraete beim Start noch nicht verbunden
sind, existieren ihre SignalSeries zu dem Zeitpunkt noch nicht; die
Zuordnung wird deshalb als "pending" gemerkt und erst aufgeloest, sobald
on_device_known fuer das jeweilige Geraet feuert (siehe _resolve_pending).

Unterhalb der Diagramme (aber ausserhalb von deren QScrollArea, siehe unten)
sitzt der Aufzeichnung-Bereich (Start/Stop/Export): bewusst hier statt in
einem eigenen Reiter, weil Aufzeichnung und Live-Ansicht dieselben Messwerte
betreffen und der Nutzer sie meist gemeinsam im Blick hat. Ausserhalb der
Scroll-Area platziert, damit er bei mehreren Diagrammen (Scrollen noetig)
trotzdem immer sichtbar bleibt. Die eigentliche Sammlung uebernimmt Recorder
(recording.py), an den dieser Tab nur Start/Stop/Zuruecksetzen und die
Statusanzeige anbindet (siehe main_window.py: _wire_recording) -- getrennt
von den hiesigen Ringpuffern (SignalSeries.data), die weiterhin nur der
Live-Oszilloskop-Anzeige dienen und unabhaengig von einer laufenden
Aufzeichnung ueber das Zeitfenster
gedeckelt bleiben.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from i18n import Translator, tr
from icons import IconButton
from paths import app_dir
from step_spinbox import SteppedDoubleSpinBox
from theme import Palette, ThemeManager, no_own_background
from theme import current as current_palette

# Diagramme, die der Nutzer umbenannt hat, werden hier persistiert (Name +
# zugeordnete Signale) -- siehe TimelineTab._save_layout/_load_layout_data.
# Unbenannte ("Diagramm 1", ...) Diagramme sind bewusst nur fuer die laufende
# Session gedacht und werden nicht gespeichert.
TIMELINE_LAYOUT_PATH = app_dir() / "timeline_layout.json"

# field_key -> (deutscher Basis-Anzeigename, Einheit); Einheit ist
# sprachunabhaengig und wird nicht ueber i18n.tr uebersetzt.
LOAD_SIGNAL_FIELDS = {
    "voltage": ("Spannung", "V"),
    "current": ("Strom", "A"),
    "power": ("Leistung", "W"),
}
PSU_SIGNAL_FIELDS = {
    "voltage": ("Spannung", "V"),
    "current": ("Strom", "A"),
}
KIND_FIELDS = {"load": LOAD_SIGNAL_FIELDS, "psu": PSU_SIGNAL_FIELDS}

# Bevorzugte Achsen-Reihenfolge: die linke Achse nimmt die erste hier
# vorhandene Einheit unter den einem Diagramm zugeordneten Signalen, die
# rechte Achse die zweite. Weitere Einheiten teilen sich ebenfalls die rechte
# Achse.
AXIS_UNIT_PRIORITY = ["V", "A", "W"]

SERIES_COLORS = [
    "#4f46e5", "#16a34a", "#dc2626", "#d97706", "#0891b2",
    "#c026d3", "#65a30d", "#e11d48", "#0284c7", "#7c3aed",
]

WINDOW_CHOICES = [
    ("30 s", 30.0),
    ("1 min", 60.0),
    ("2 min", 120.0),
    ("5 min", 300.0),
    ("15 min", 900.0),
    ("30 min", 1800.0),
]
DEFAULT_WINDOW_INDEX = 1
MAX_WINDOW_S = WINDOW_CHOICES[-1][1]
# _ScopeChart.paintEvent() legt das Zeitfenster bei JEDEM Aufruf live ueber
# time.time() (t_hi = jetzt) fest -- das Diagramm scrollt also bereits bei
# unveraenderten Messwerten weiter, sobald neu gezeichnet wird. Bei 500ms
# (2Hz) wirkte das sichtbar ruckelig; 33ms (~30Hz) liefert eine fluessige
# Darstellung, unabhaengig vom (langsameren) Poll-Intervall der Messwerte
# selbst (siehe device_worker.POLL_INTERVAL_MS).
REPAINT_INTERVAL_MS = 33

# Aufzeichnung-Bereich (siehe Klassen-Docstring): eigener Export-Zielordner,
# eigenes Timer-Intervall fuer die Dauer-Anzeige -- unabhaengig vom
# REPAINT_INTERVAL_MS der Diagramme.
RECORDING_DEFAULT_DIR = app_dir() / "recordings"
RECORDING_DURATION_REFRESH_MS = 500
# Blink-Takt des zusammengefuehrten Aufnahme-Buttons waehrend eine Aufnahme
# laeuft (FEATURES.md Punkt 3) -- eigener Timer, unabhaengig vom
# Dauer-Anzeige-Timer oben, da beide unterschiedliche Zwecke haben.
RECORDING_BLINK_INTERVAL_MS = 500


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def signal_key(device_id: str, field: str) -> str:
    return f"{device_id}:{field}"


def _field_name(kind: str, field: str) -> str:
    return tr(KIND_FIELDS[kind][field][0])


@dataclass
class SignalSeries:
    device_id: str
    kind: str
    field: str
    device_label: str
    unit: str
    color: QColor
    chart_id: int | None = None  # None = keinem Diagramm zugeordnet
    data: deque = dataclass_field(default_factory=deque)  # deque[(t: float, value: float)]

    @property
    def label(self) -> str:
        return f"{self.device_label} – {_field_name(self.kind, self.field)}"


class _ScopeChart(QWidget):
    """Custom-gezeichnetes Liniendiagramm mit optionaler zweiter Y-Achse."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._series: list[SignalSeries] = []
        self._window_s = WINDOW_CHOICES[DEFAULT_WINDOW_INDEX][1]
        # Y-Achsen-Skalierung (FEATURES.md Punkt 11): "auto" berechnet den
        # sichtbaren Wertebereich laufend aus den aktuell sichtbaren Werten
        # (siehe _value_range, bisheriges Verhalten); "fixed" nutzt stattdessen
        # die hier gespeicherten, vom Nutzer gesetzten Grenzen. Nur fuer die
        # laufende Session gueltig (keine Persistenz, siehe Modul-Docstring-
        # Erweiterung unten).
        self._y_mode = "auto"  # "auto" | "fixed"
        self._fixed_left = (0.0, 1.0)
        self._fixed_right = (0.0, 1.0)
        ThemeManager.instance().changed.connect(lambda _pal: self.update())
        Translator.instance().language_changed.connect(lambda _lang: self.update())

    def set_series(self, series: list[SignalSeries]) -> None:
        self._series = series
        self.update()

    def set_window(self, seconds: float) -> None:
        self._window_s = seconds
        self.update()

    def set_y_mode(self, mode: str) -> None:
        self._y_mode = mode
        self.update()

    def y_mode(self) -> str:
        return self._y_mode

    def set_fixed_range(self, axis: str, lo: float, hi: float) -> None:
        if axis == "left":
            self._fixed_left = (lo, hi)
        else:
            self._fixed_right = (lo, hi)
        self.update()

    def fixed_range(self, axis: str) -> tuple[float, float]:
        return self._fixed_left if axis == "left" else self._fixed_right

    def _axis_units(self) -> tuple[str | None, str | None]:
        seen: list[str] = []
        for unit in AXIS_UNIT_PRIORITY:
            if any(s.unit == unit for s in self._series):
                seen.append(unit)
        for s in self._series:
            if s.unit not in seen:
                seen.append(s.unit)
        left = seen[0] if seen else None
        right = seen[1] if len(seen) > 1 else None
        return left, right

    def _axis_for(self, series: SignalSeries, left_unit: str | None) -> str:
        return "left" if series.unit == left_unit else "right"

    def _value_range(self, axis: str, left_unit: str | None, t_lo: float, t_hi: float) -> tuple[float, float]:
        values = []
        for s in self._series:
            if self._axis_for(s, left_unit) != axis:
                continue
            values.extend(v for t, v in s.data if t_lo <= t <= t_hi)
        if not values:
            return 0.0, 1.0
        lo, hi = min(values), max(values)
        if hi <= lo:
            pad = abs(lo) * 0.1 or 1.0
            return lo - pad, hi + pad
        pad = (hi - lo) * 0.1
        return lo - pad, hi + pad

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = current_palette()
        painter.fillRect(self.rect(), QColor(pal.plot_bg))

        left_unit, right_unit = self._axis_units()
        left_margin = 56
        right_margin = 56 if right_unit else 16
        top_margin = 16
        bottom_margin = 24
        rect = self.rect().adjusted(left_margin, top_margin, -right_margin, -bottom_margin)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        # Gitter (horizontal + vertikal, siehe FEATURES.md Punkt 11)
        grid_pen = QPen(QColor(pal.plot_grid))
        painter.setPen(grid_pen)
        for i in range(5):
            y = rect.top() + i * rect.height() / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
        for i in range(5):
            x = rect.left() + i * rect.width() / 4
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())

        if not self._series:
            painter.setPen(QPen(QColor(pal.text_muted)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("Kein Signal zugeordnet"))
            return

        now = time.time()
        t_lo = now - self._window_s
        t_hi = now

        if self._y_mode == "fixed":
            left_lo, left_hi = self._fixed_left
            right_lo, right_hi = self._fixed_right
        else:
            left_lo, left_hi = self._value_range("left", left_unit, t_lo, t_hi)
            right_lo, right_hi = self._value_range("right", left_unit, t_lo, t_hi) if right_unit else (0.0, 1.0)

        def x_of(t: float) -> float:
            return rect.left() + (t - t_lo) / (t_hi - t_lo) * rect.width()

        def y_of(value: float, lo: float, hi: float) -> float:
            if hi <= lo:
                hi = lo + 1.0
            frac = (value - lo) / (hi - lo)
            return rect.bottom() - frac * rect.height()

        # Achsenbeschriftung (Werte)
        painter.setPen(QPen(QColor(pal.text_muted)))
        for i in range(5):
            frac = 1.0 - i / 4
            y = rect.top() + i * rect.height() / 4
            value = left_lo + frac * (left_hi - left_lo)
            painter.drawText(0, int(y) - 7, left_margin - 6, 14, Qt.AlignmentFlag.AlignRight, f"{value:.2f}")
            if right_unit:
                value_r = right_lo + frac * (right_hi - right_lo)
                painter.drawText(rect.right() + 6, int(y) - 7, right_margin - 6, 14, Qt.AlignmentFlag.AlignLeft, f"{value_r:.2f}")

        if left_unit:
            painter.drawText(0, 0, left_margin - 6, top_margin, Qt.AlignmentFlag.AlignRight, f"[{left_unit}]")
        if right_unit:
            painter.drawText(rect.right() + 6, 0, right_margin - 6, top_margin, Qt.AlignmentFlag.AlignLeft, f"[{right_unit}]")

        # Zeitachse
        for i in range(5):
            frac = i / 4
            x = rect.left() + frac * rect.width()
            secs_ago = self._window_s * (1 - frac)
            text = tr("jetzt") if secs_ago < 0.5 else f"-{secs_ago:.0f}s"
            painter.drawText(int(x) - 24, rect.bottom() + 4, 48, 16, Qt.AlignmentFlag.AlignCenter, text)

        # Kurven
        for series in self._series:
            lo, hi = (left_lo, left_hi) if self._axis_for(series, left_unit) == "left" else (right_lo, right_hi)
            points = [(x_of(t), y_of(v, lo, hi)) for t, v in series.data if t_lo <= t <= t_hi]
            if not points:
                continue
            pen = QPen(series.color)
            pen.setWidth(2)
            painter.setPen(pen)
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                painter.drawLine(int(x0), int(y0), int(x1), int(y1))


class _SignalChip(QWidget):
    """Kleine Markierung im Diagramm-Header: Farbe + Signalname + Entfernen-Button."""

    remove_clicked = Signal()

    def __init__(self, series: SignalSeries) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 2, 2)
        layout.setSpacing(4)

        self._swatch = QLabel()
        self._swatch.setFixedSize(10, 10)
        self._swatch.setStyleSheet(f"background-color: {series.color.name()}; border-radius: 2px;")
        layout.addWidget(self._swatch)

        self._text_label = QLabel()
        layout.addWidget(self._text_label)

        self._remove_button = IconButton("mdi.close", "")
        self._remove_button.clicked.connect(self.remove_clicked)
        layout.addWidget(self._remove_button)

        self.set_text(series.label, series.unit)

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._remove_button.setToolTip(tr("Signal aus Diagramm entfernen"))

    def set_text(self, label: str, unit: str) -> None:
        self._text_label.setText(f"{label} ({unit})")


class _YAxisDialog(QDialog):
    """Kleiner Dialog zum Umschalten der Y-Achsen-Skalierung EINES Diagramms
    zwischen automatisch (bisheriges Verhalten, staendig aus den sichtbaren
    Werten neu berechnet) und festen, vom Nutzer gesetzten Wertebereichen
    (FEATURES.md Punkt 11). Rechte-Achse-Felder bleiben deaktiviert, wenn das
    Diagramm aktuell keine zweite Einheit/Achse verwendet."""

    def __init__(
        self,
        mode: str,
        left_range: tuple[float, float],
        right_range: tuple[float, float],
        has_right_axis: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._has_right_axis = has_right_axis
        layout = QVBoxLayout(self)

        self._auto_check = QCheckBox()
        self._auto_check.setChecked(mode != "fixed")
        self._auto_check.toggled.connect(self._on_auto_toggled)
        layout.addWidget(self._auto_check)

        form = QFormLayout()
        self._left_min = SteppedDoubleSpinBox()
        self._left_max = SteppedDoubleSpinBox()
        self._right_min = SteppedDoubleSpinBox()
        self._right_max = SteppedDoubleSpinBox()
        for spin in (self._left_min, self._left_max, self._right_min, self._right_max):
            spin.setRange(-1_000_000.0, 1_000_000.0)
            spin.setDecimals(3)
        self._left_min.setValue(left_range[0])
        self._left_max.setValue(left_range[1])
        self._right_min.setValue(right_range[0])
        self._right_max.setValue(right_range[1])

        self._left_min_label = QLabel()
        self._left_max_label = QLabel()
        self._right_min_label = QLabel()
        self._right_max_label = QLabel()
        form.addRow(self._left_min_label, self._left_min)
        form.addRow(self._left_max_label, self._left_max)
        form.addRow(self._right_min_label, self._right_min)
        form.addRow(self._right_max_label, self._right_max)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_auto_toggled(self._auto_check.isChecked())

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self.setWindowTitle(tr("Y-Achse einstellen"))
        self._auto_check.setText(tr("Automatisch"))
        self._left_min_label.setText(tr("Links Min:"))
        self._left_max_label.setText(tr("Links Max:"))
        self._right_min_label.setText(tr("Rechts Min:"))
        self._right_max_label.setText(tr("Rechts Max:"))

    def _on_auto_toggled(self, auto: bool) -> None:
        self._left_min.setEnabled(not auto)
        self._left_max.setEnabled(not auto)
        self._right_min.setEnabled(not auto and self._has_right_axis)
        self._right_max.setEnabled(not auto and self._has_right_axis)

    def mode(self) -> str:
        return "auto" if self._auto_check.isChecked() else "fixed"

    def left_range(self) -> tuple[float, float]:
        return self._left_min.value(), self._left_max.value()

    def right_range(self) -> tuple[float, float]:
        return self._right_min.value(), self._right_max.value()


class _ChartRow(QGroupBox):
    """Ein Diagramm: Header mit Titel + "Signal hinzufuegen"-Menue + Entfernen-Button,
    darunter die Chips der zugeordneten Signale und das eigentliche Diagramm."""

    remove_clicked = Signal()
    rename_requested = Signal(str)
    add_signal_requested = Signal(object)     # SignalSeries
    remove_signal_requested = Signal(object)  # SignalSeries

    def __init__(self, chart_number: int, get_available_signals: Callable[[], list[SignalSeries]]) -> None:
        super().__init__()
        self.chart_id: int = 0  # von TimelineTab vergeben
        # Nur umbenannte Diagramme werden persistiert -- siehe TIMELINE_LAYOUT_PATH.
        self.has_custom_name: bool = False
        self._custom_title: str | None = None
        self._chart_number = chart_number
        self._get_available_signals = get_available_signals
        self._chips: dict[str, _SignalChip] = {}

        outer = QVBoxLayout(self)

        header = QHBoxLayout()
        self._title_label = QLabel()
        # background: transparent -- sonst zeigt dieses direkt im GroupBox-
        # Layout haengende QLabel opak den allgemeinen Seitenhintergrund statt
        # der GroupBox-Flaeche (siehe theme.no_own_background).
        self._title_label.setStyleSheet("font-weight: bold; background: transparent;")
        header.addWidget(self._title_label, 1)
        self._rename_button = IconButton("mdi.pencil-outline", "")
        self._rename_button.clicked.connect(self._on_rename_clicked)
        header.addWidget(self._rename_button)
        self._add_button = IconButton("mdi.playlist-plus", "")
        self._add_button.clicked.connect(self._open_add_menu)
        header.addWidget(self._add_button)
        self._yaxis_button = IconButton("mdi.arrow-expand-vertical", "")
        self._yaxis_button.clicked.connect(self._open_yaxis_dialog)
        header.addWidget(self._yaxis_button)
        self._remove_button = IconButton("mdi.trash-can-outline", "")
        self._remove_button.clicked.connect(self.remove_clicked)
        header.addWidget(self._remove_button)
        outer.addLayout(header)

        self._legend_widget = no_own_background(QWidget())
        self._legend_layout = QHBoxLayout(self._legend_widget)
        self._legend_layout.setContentsMargins(0, 0, 0, 0)
        self._legend_layout.setSpacing(6)
        self._legend_layout.addStretch()
        outer.addWidget(self._legend_widget)

        self.chart = _ScopeChart()
        outer.addWidget(self.chart)

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._title_label.setText(self.title())
        self._rename_button.setToolTip(tr("Diagramm umbenennen"))
        self._add_button.setToolTip(tr("Signal hinzufügen"))
        self._yaxis_button.setToolTip(tr("Y-Achse einstellen…"))
        self._remove_button.setToolTip(tr("Diagramm entfernen"))

    def title(self) -> str:
        if self.has_custom_name and self._custom_title is not None:
            return self._custom_title
        return tr("Diagramm {number}", number=self._chart_number)

    def set_chart_number(self, number: int) -> None:
        self._chart_number = number
        if not self.has_custom_name:
            self._title_label.setText(self.title())

    def set_title(self, title: str) -> None:
        self._custom_title = title
        self._title_label.setText(self.title())

    def set_removable(self, removable: bool) -> None:
        self._remove_button.setVisible(removable)

    def _on_rename_clicked(self) -> None:
        new_name, ok = QInputDialog.getText(self, tr("Diagramm umbenennen"), tr("Name:"), text=self.title())
        if ok and new_name.strip():
            self.rename_requested.emit(new_name.strip())

    def _open_add_menu(self) -> None:
        available = self._get_available_signals()
        menu = QMenu(self)
        if not available:
            action = menu.addAction(tr("Keine weiteren Signale verfügbar"))
            action.setEnabled(False)
        else:
            by_device: dict[str, list[SignalSeries]] = {}
            for series in available:
                by_device.setdefault(series.device_id, []).append(series)
            for device_signals in by_device.values():
                for series in device_signals:
                    action = menu.addAction(f"{series.label} ({series.unit})")
                    action.triggered.connect(lambda checked=False, s=series: self.add_signal_requested.emit(s))
        menu.exec(self._add_button.mapToGlobal(self._add_button.rect().bottomLeft()))

    def _open_yaxis_dialog(self) -> None:
        _left_unit, right_unit = self.chart._axis_units()
        dialog = _YAxisDialog(
            self.chart.y_mode(),
            self.chart.fixed_range("left"),
            self.chart.fixed_range("right"),
            has_right_axis=right_unit is not None,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.chart.set_y_mode(dialog.mode())
            if dialog.mode() == "fixed":
                lo, hi = dialog.left_range()
                self.chart.set_fixed_range("left", lo, hi)
                lo, hi = dialog.right_range()
                self.chart.set_fixed_range("right", lo, hi)
            self._update_yaxis_icon()

    def _update_yaxis_icon(self) -> None:
        icon = "mdi.lock-outline" if self.chart.y_mode() == "fixed" else "mdi.arrow-expand-vertical"
        self._yaxis_button.set_icon(icon)

    # -- Legende (zugeordnete Signale) -----------------------------------------

    def add_chip(self, series: SignalSeries) -> None:
        key = signal_key(series.device_id, series.field)
        if key in self._chips:
            return
        chip = _SignalChip(series)
        chip.remove_clicked.connect(lambda: self.remove_signal_requested.emit(series))
        self._legend_layout.insertWidget(self._legend_layout.count() - 1, chip)
        self._chips[key] = chip

    def remove_chip(self, key: str) -> None:
        chip = self._chips.pop(key, None)
        if chip is None:
            return
        self._legend_layout.removeWidget(chip)
        chip.setParent(None)
        chip.deleteLater()

    def update_chip_text(self, key: str, label: str, unit: str) -> None:
        chip = self._chips.get(key)
        if chip is not None:
            chip.set_text(label, unit)


class TimelineTab(QWidget):
    """Gestapelte, per Button erweiterbare Diagramme mit Signal-Zuordnung ueber
    ein Menue im jeweiligen Diagramm-Header, plus Aufzeichnung-Bereich
    (Start/Stop/Export) darueber -- siehe Modul-Docstring."""

    start_requested = Signal()
    stop_requested = Signal()
    clear_requested = Signal()
    export_csv_to = Signal(object)  # Path
    export_mf4_to = Signal(object)  # Path
    # (device_id, field)-Paare aller aktuell einem Diagramm zugeordneten
    # Signale -- an Recorder.set_active_signals durchgereicht (siehe
    # main_window._wire_recording), damit nur sichtbare Signale aufgezeichnet
    # werden (FEATURES.md Punkt 4).
    active_signals_changed = Signal(object)  # set[tuple[str, str]]

    def __init__(self) -> None:
        super().__init__()
        self._series: dict[str, SignalSeries] = {}
        self._color_index = 0
        self._charts: list[_ChartRow] = []
        self._next_chart_id = 1
        self._window_s = WINDOW_CHOICES[DEFAULT_WINDOW_INDEX][1]
        # signal_key -> chart_id: aus einem gespeicherten Layout wiederhergestellte
        # Zuordnungen, die auf das (Wieder-)Verbinden des jeweiligen Geraets warten
        # (die SignalSeries existiert erst ab on_device_known) -- siehe _resolve_pending.
        self._pending_assignments: dict[str, int] = {}

        # Aufzeichnung-Bereich: Zustand vom Recorder (siehe main_window.py:
        # _wire_recording) gespiegelt, fuer die Statuszeile/Button-Enablement.
        self._recording_sample_count = 0
        self._recording_elapsed_s = 0.0
        self._is_recording = False

        outer = QVBoxLayout(self)

        # Kompakt: alles in einer Zeile -- Start/Stop/Zuruecksetzen als reine
        # Icon-Buttons (selbsterklaerend, voller Name im Tooltip), die beiden
        # Export-Buttons mit Kurzlabel (die Icons allein unterscheiden die
        # Formate nicht), rechts daneben die Statuszeile. Der erklaerende
        # Hinweistext lebt als Tooltip auf der GroupBox statt als eigene Zeile.
        self._recording_group = QGroupBox()
        recording_layout = QHBoxLayout(self._recording_group)

        # Ein Button statt getrennter Start/Stop-Buttons (FEATURES.md Punkt 3):
        # statisch rot im Ruhezustand, blinkt rot waehrend die Aufnahme laeuft
        # (siehe _on_recording_blink_tick). Klick loest je nach Zustand
        # start_requested/stop_requested aus.
        self._recording_toggle_button = IconButton("mdi.record", "")
        self._recording_toggle_button.clicked.connect(self._on_recording_toggle_clicked)
        self._recording_blink_timer = QTimer(self)
        self._recording_blink_timer.timeout.connect(self._on_recording_blink_tick)
        self._recording_blink_on = False
        self._recording_clear_button = IconButton("mdi.delete-sweep-outline", "")
        self._recording_clear_button.clicked.connect(self._on_recording_clear_clicked)
        recording_layout.addWidget(self._recording_toggle_button)
        recording_layout.addWidget(self._recording_clear_button)
        recording_layout.addSpacing(12)

        # "CSV…"/"MF4…" sind Formatnamen, keine uebersetzbaren Texte.
        self._export_csv_button = IconButton("mdi.file-delimited-outline", "", text="CSV…")
        self._export_mf4_button = IconButton("mdi.file-chart-outline", "", text="MF4…")
        self._export_csv_button.clicked.connect(self._on_export_csv_clicked)
        self._export_mf4_button.clicked.connect(self._on_export_mf4_clicked)
        recording_layout.addWidget(self._export_csv_button)
        recording_layout.addWidget(self._export_mf4_button)
        recording_layout.addSpacing(12)

        self._recording_status_label = QLabel()
        self._recording_status_label.setStyleSheet("font-weight: bold;")
        recording_layout.addWidget(self._recording_status_label, 1)

        self._recording_duration_timer = QTimer(self)
        self._recording_duration_timer.timeout.connect(self._refresh_recording_status_text)
        self._update_recording_button_states()
        self._refresh_recording_toggle_color()

        controls = QHBoxLayout()
        self._window_label = QLabel()
        controls.addWidget(self._window_label)
        self._window_combo = QComboBox()
        for text, _seconds in WINDOW_CHOICES:
            self._window_combo.addItem(text)
        self._window_combo.setCurrentIndex(DEFAULT_WINDOW_INDEX)
        self._window_combo.currentIndexChanged.connect(self._on_window_changed)
        controls.addWidget(self._window_combo)

        self._pause_button = IconButton("mdi.pause", "")
        self._pause_button.setCheckable(True)
        self._pause_button.toggled.connect(self._on_pause_toggled)
        controls.addWidget(self._pause_button)

        self._view_clear_button = IconButton("mdi.delete-sweep-outline", "")
        self._view_clear_button.clicked.connect(self._on_view_clear_clicked)
        controls.addWidget(self._view_clear_button)

        self._add_chart_button = IconButton("mdi.plus", "")
        self._add_chart_button.clicked.connect(self._on_add_chart_clicked)
        controls.addWidget(self._add_chart_button)

        controls.addStretch()
        outer.addLayout(controls)

        charts_container = QWidget()
        self._charts_layout = QVBoxLayout(charts_container)
        charts_scroll = QScrollArea()
        charts_scroll.setWidgetResizable(True)
        charts_scroll.setWidget(charts_container)
        outer.addWidget(charts_scroll, 1)

        # Ausserhalb der QScrollArea (siehe oben): bleibt immer sichtbar am
        # unteren Rand des Tabs, auch wenn die Diagramme selbst scrollen.
        outer.addWidget(self._recording_group)

        self._restore_layout()

        self._repaint_timer = QTimer(self)
        self._repaint_timer.timeout.connect(self._repaint_all)
        self._repaint_timer.start(REPAINT_INTERVAL_MS)

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._recording_group.setTitle(tr("Aufzeichnung"))
        self._recording_group.setToolTip(
            tr(
                "Zeichnet Zeitstempel, Gerät und Messwert für alle Signale auf, die aktuell einem\n"
                "Diagramm unten zugeordnet sind, solange die Aufnahme läuft (noch keinem Diagramm\n"
                "zugeordnete Signale werden nicht aufgezeichnet). Export ist auch bei laufender\n"
                "Aufnahme möglich (Zwischenstand)."
            )
        )
        self._update_recording_toggle_tooltip()
        self._recording_clear_button.setToolTip(tr("Zurücksetzen"))
        self._export_csv_button.setToolTip(tr("Als CSV exportieren…"))
        self._export_mf4_button.setToolTip(tr("Als MF4 exportieren…"))
        self._refresh_recording_status_text()

        self._window_label.setText(tr("Zeitfenster:"))
        self._pause_button.setToolTip(tr("Fortsetzen") if self._pause_button.isChecked() else tr("Pause"))
        self._view_clear_button.setToolTip(tr("Anzeige zurücksetzen"))
        self._add_chart_button.setToolTip(tr("Diagramm hinzufügen"))
        for key, series in self._series.items():
            for row in self._charts:
                row.update_chip_text(key, series.label, series.unit)

    # -- Geraete-Lebenszyklus --------------------------------------------------

    @Slot(str, str, str)
    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        fields = KIND_FIELDS.get(kind)
        if fields is None:
            return

        for field_key in fields:
            key = signal_key(device_id, field_key)
            if key not in self._series:
                _name, unit = fields[field_key]
                self._series[key] = SignalSeries(
                    device_id=device_id,
                    kind=kind,
                    field=field_key,
                    device_label=label,
                    unit=unit,
                    color=self._next_color(),
                )
            else:
                self._series[key].device_label = label
            self._resolve_pending(key)

    @Slot(str, str, str)
    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        for key, series in self._series.items():
            if series.device_id != device_id:
                continue
            series.device_label = label
            for row in self._charts:
                row.update_chip_text(key, series.label, series.unit)

    # -- Messwerte -----------------------------------------------------------

    @Slot(str, float, float, float)
    def update_load(self, device_id: str, voltage: float, current: float, power: float) -> None:
        self._append(device_id, "voltage", voltage)
        self._append(device_id, "current", current)
        self._append(device_id, "power", power)

    @Slot(str, float, float, bool)
    def update_psu(self, device_id: str, voltage: float, current: float, constant_current: bool) -> None:
        self._append(device_id, "voltage", voltage)
        self._append(device_id, "current", current)

    def _append(self, device_id: str, field_key: str, value: float) -> None:
        series = self._series.get(signal_key(device_id, field_key))
        if series is None:
            return
        now = time.time()
        series.data.append((now, value))
        cutoff = now - MAX_WINDOW_S
        while series.data and series.data[0][0] < cutoff:
            series.data.popleft()

    # -- Diagramme: hinzufuegen/entfernen ---------------------------------------

    def _available_signals(self) -> list[SignalSeries]:
        return [s for s in self._series.values() if s.chart_id is None]

    def active_signal_keys(self) -> set[tuple[str, str]]:
        """(device_id, field)-Paare aller Signale, die aktuell einem Diagramm
        zugeordnet sind -- siehe active_signals_changed/Recorder.set_active_signals."""
        return {(s.device_id, s.field) for s in self._series.values() if s.chart_id is not None}

    def _emit_active_signals(self) -> None:
        self.active_signals_changed.emit(self.active_signal_keys())

    def _add_chart_row(self, title: str | None = None, custom_name: bool = False) -> _ChartRow:
        chart_id = self._next_chart_id
        self._next_chart_id += 1

        row = _ChartRow(len(self._charts) + 1, self._available_signals)
        row.chart_id = chart_id
        if custom_name and title:
            row.set_title(title)
            row.has_custom_name = True
        row.chart.set_window(self._window_s)
        row.remove_clicked.connect(lambda: self._on_remove_chart(chart_id))
        row.rename_requested.connect(lambda name: self._on_rename_chart(row, name))
        row.add_signal_requested.connect(lambda series: self._on_add_signal(row, series))
        row.remove_signal_requested.connect(lambda series: self._on_remove_signal(row, series))

        self._charts.append(row)
        self._charts_layout.addWidget(row)

        self._renumber_charts()
        self._update_removable_state()
        return row

    def _on_add_chart_clicked(self) -> None:
        self._add_chart_row()

    def _on_remove_chart(self, chart_id: int) -> None:
        if len(self._charts) <= 1:
            return  # mindestens ein Diagramm bleibt immer bestehen
        row = next((r for r in self._charts if r.chart_id == chart_id), None)
        if row is None:
            return

        self._charts.remove(row)
        self._charts_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

        # Signale, die diesem Diagramm zugeordnet waren, wieder freigeben.
        for series in self._series.values():
            if series.chart_id == chart_id:
                series.chart_id = None
        # Noch nicht aufgeloeste (Geraet noch nicht verbunden) Zuordnungen auf
        # das entfernte Diagramm verwerfen, damit sie nicht ins Leere resolven.
        self._pending_assignments = {
            key: cid for key, cid in self._pending_assignments.items() if cid != chart_id
        }

        self._renumber_charts()
        self._update_removable_state()
        self._save_layout()
        self._emit_active_signals()

    def _on_rename_chart(self, row: _ChartRow, name: str) -> None:
        row.set_title(name)
        row.has_custom_name = True
        self._save_layout()

    def _renumber_charts(self) -> None:
        # Umbenannte Diagramme behalten ihren Namen; nur die Standardtitel der
        # uebrigen werden an ihre aktuelle Position angepasst.
        for i, row in enumerate(self._charts):
            row.set_chart_number(i + 1)

    def _update_removable_state(self) -> None:
        removable = len(self._charts) > 1
        for row in self._charts:
            row.set_removable(removable)

    def _on_add_signal(self, row: _ChartRow, series: SignalSeries) -> None:
        if series.chart_id is not None:
            return  # bereits (in der Zwischenzeit) einem Diagramm zugeordnet
        series.chart_id = row.chart_id
        row.add_chip(series)
        row.chart.set_series([s for s in self._series.values() if s.chart_id == row.chart_id])
        self._save_layout()
        self._emit_active_signals()

    def _on_remove_signal(self, row: _ChartRow, series: SignalSeries) -> None:
        series.chart_id = None
        key = signal_key(series.device_id, series.field)
        row.remove_chip(key)
        row.chart.set_series([s for s in self._series.values() if s.chart_id == row.chart_id])
        self._save_layout()
        self._emit_active_signals()

    # -- Persistenz: Name + Signalzuordnung umbenannter Diagramme --------------

    def _resolve_pending(self, key: str) -> None:
        """Ordnet ein Signal seinem gespeicherten Diagramm zu, sobald dessen Geraet
        (wieder) verbunden ist und die SignalSeries dafuer existiert."""
        chart_id = self._pending_assignments.pop(key, None)
        if chart_id is None:
            return
        series = self._series.get(key)
        row = next((r for r in self._charts if r.chart_id == chart_id), None)
        if series is None or row is None or series.chart_id is not None:
            return
        series.chart_id = chart_id
        row.add_chip(series)
        row.chart.set_series([s for s in self._series.values() if s.chart_id == chart_id])
        self._emit_active_signals()

    def _save_layout(self) -> None:
        data = []
        for row in self._charts:
            if not row.has_custom_name:
                continue
            signals = [
                {"device_id": s.device_id, "field": s.field}
                for s in self._series.values()
                if s.chart_id == row.chart_id
            ]
            data.append({"name": row.title(), "signals": signals})
        try:
            TIMELINE_LAYOUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # Layout bleibt fuer die laufende Session gueltig, nur Persistenz betroffen

    def _restore_layout(self) -> None:
        saved = self._load_layout_data()
        if not saved:
            self._add_chart_row()
            return
        for entry in saved:
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            row = self._add_chart_row(title=name, custom_name=True)
            for sig in entry.get("signals", []):
                device_id = sig.get("device_id") if isinstance(sig, dict) else None
                field_key = sig.get("field") if isinstance(sig, dict) else None
                if isinstance(device_id, str) and isinstance(field_key, str):
                    self._pending_assignments[signal_key(device_id, field_key)] = row.chart_id
        if not self._charts:
            self._add_chart_row()

    @staticmethod
    def _load_layout_data() -> list[dict]:
        try:
            data = json.loads(TIMELINE_LAYOUT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    # -- Steuerelemente --------------------------------------------------------

    def _next_color(self) -> QColor:
        color = QColor(SERIES_COLORS[self._color_index % len(SERIES_COLORS)])
        self._color_index += 1
        return color

    def _on_window_changed(self, index: int) -> None:
        self._window_s = WINDOW_CHOICES[index][1]
        for row in self._charts:
            row.chart.set_window(self._window_s)

    def _repaint_all(self) -> None:
        for row in self._charts:
            row.chart.update()

    def _on_pause_toggled(self, paused: bool) -> None:
        # "Pause" friert nur die Anzeige ein (Repaint-Timer anhalten) -- die
        # Ringpuffer werden im Hintergrund weiter befuellt, damit nach dem
        # Fortsetzen kein Datenluecken-Sprung entsteht.
        if paused:
            self._repaint_timer.stop()
            self._pause_button.set_icon("mdi.play")
            self._pause_button.setToolTip(tr("Fortsetzen"))
        else:
            self._repaint_timer.start(REPAINT_INTERVAL_MS)
            self._repaint_all()
            self._pause_button.set_icon("mdi.pause")
            self._pause_button.setToolTip(tr("Pause"))

    def _on_view_clear_clicked(self) -> None:
        for series in self._series.values():
            series.data.clear()
        self._repaint_all()

    # -- Aufzeichnung: Start/Stop/Export ---------------------------------------
    # Steuerung/Statusanzeige nur; die eigentliche Sammlung/den Export macht
    # Recorder (recording.py), angebunden ueber die oben deklarierten Signale
    # (siehe main_window.py: _wire_recording).

    def _on_theme_changed(self, palette: Palette) -> None:
        # Die Statuszeile faerbt sich nach Palette (danger/text/text_muted) --
        # beim Theme-Wechsel neu rendern statt auf das naechste Update zu warten.
        self._refresh_recording_status_text()
        self._refresh_recording_toggle_color()

    def on_recording_changed(self, active: bool) -> None:
        self._is_recording = active
        if active:
            self._recording_duration_timer.start(RECORDING_DURATION_REFRESH_MS)
            self._recording_blink_on = True
            self._recording_blink_timer.start(RECORDING_BLINK_INTERVAL_MS)
        else:
            self._recording_duration_timer.stop()
            self._recording_blink_timer.stop()
            self._refresh_recording_toggle_color()
        self._update_recording_button_states()
        self._refresh_recording_status_text()

    def on_stats_changed(self, sample_count: int, elapsed_s: float) -> None:
        self._recording_sample_count = sample_count
        self._recording_elapsed_s = elapsed_s
        self._update_recording_button_states()
        self._refresh_recording_status_text()

    def _refresh_recording_status_text(self) -> None:
        if self._is_recording:
            device_count = len({s.device_id for s in self._series.values()})
            text = tr(
                "● Aufnahme läuft seit {duration} · {count} Werte ({devices} Geräte)",
                duration=_format_duration(self._recording_elapsed_s),
                count=self._recording_sample_count,
                devices=device_count,
            )
            self._recording_status_label.setStyleSheet(f"color: {current_palette().danger}; font-weight: bold;")
        elif self._recording_sample_count:
            text = tr(
                "Aufnahme gestoppt · {count} Werte über {duration} aufgezeichnet",
                count=self._recording_sample_count,
                duration=_format_duration(self._recording_elapsed_s),
            )
            self._recording_status_label.setStyleSheet(f"color: {current_palette().text}; font-weight: bold;")
        else:
            text = tr("Keine Aufnahme aktiv")
            self._recording_status_label.setStyleSheet(f"color: {current_palette().text_muted}; font-weight: bold;")
        self._recording_status_label.setText(text)

    def _update_recording_button_states(self) -> None:
        self._recording_clear_button.setEnabled(not self._is_recording and self._recording_sample_count > 0)
        has_samples = self._recording_sample_count > 0
        self._export_csv_button.setEnabled(has_samples)
        self._export_mf4_button.setEnabled(has_samples)
        self._update_recording_toggle_tooltip()

    def _update_recording_toggle_tooltip(self) -> None:
        self._recording_toggle_button.setToolTip(
            tr("Aufnahme stoppen") if self._is_recording else tr("Aufnahme starten")
        )

    def _on_recording_toggle_clicked(self) -> None:
        if self._is_recording:
            self.stop_requested.emit()
        else:
            self.start_requested.emit()

    def _on_recording_blink_tick(self) -> None:
        self._recording_blink_on = not self._recording_blink_on
        self._refresh_recording_toggle_color()

    def _refresh_recording_toggle_color(self) -> None:
        # Statisch rot im Ruhezustand; waehrend der Aufnahme abwechselnd rot/
        # gedaempft (siehe _on_recording_blink_tick) -- beides theme-abhaengig,
        # deshalb hier zentral statt an jeder Aufrufstelle einzeln berechnet.
        pal = current_palette()
        if self._is_recording:
            color = pal.danger if self._recording_blink_on else pal.text_muted
        else:
            color = pal.danger
        self._recording_toggle_button.set_color_override(color)

    def _on_recording_clear_clicked(self) -> None:
        if self._recording_sample_count and QMessageBox.question(
            self,
            tr("Zurücksetzen"),
            tr("Aufgezeichnete Werte wirklich verwerfen?"),
        ) != QMessageBox.StandardButton.Yes:
            return
        self.clear_requested.emit()

    def _on_export_csv_clicked(self) -> None:
        RECORDING_DEFAULT_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Als CSV exportieren"), str(RECORDING_DEFAULT_DIR), tr("CSV-Datei (*.csv)")
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        self.export_csv_to.emit(path)

    def _on_export_mf4_clicked(self) -> None:
        RECORDING_DEFAULT_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Als MF4 exportieren"), str(RECORDING_DEFAULT_DIR), tr("MF4-Datei (*.mf4)")
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".mf4":
            path = path.with_suffix(".mf4")
        self.export_mf4_to.emit(path)

    def show_export_error(self, message: str) -> None:
        QMessageBox.critical(self, tr("Fehler beim Export"), message)

    def show_export_success(self, path: Path) -> None:
        # Bewusst kein Modal-Dialog (anders als der Fehlerfall) -- blendet die
        # Statuszeile kurz um, dann zurueck zum normalen Aufnahmestatus.
        self._recording_status_label.setText(tr("Exportiert nach {name}", name=path.name))
        self._recording_status_label.setStyleSheet(f"color: {current_palette().success}; font-weight: bold;")
        QTimer.singleShot(4000, self._refresh_recording_status_text)

"""Individuelle Panel-Hintergrundfarbe fuer Geraete-Panels in Dashboard und
Control-Tab (FEATURES.md Punkt 2). Eine Farbe gilt pro Geraete-Instanz
(device_id) und wird in beiden Tabs identisch angezeigt -- so bleibt ein
Geraet an derselben Farbe wiedererkennbar, egal in welchem Tab man gerade ist
(siehe Settings.panel_colors/panel_colors_enabled, main_window._wire_panel_colors).

Die eigentlichen Hex-Werte je Farbe/Theme liegen in Palette.panel_tints
(theme.py) -- hier nur die Bedienung (Menue-Button je Panel) und ein kleiner
Farbquadrat-Icon-Renderer dafuer.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QWidget

from i18n import tr
from icons import IconButton
from theme import PANEL_COLOR_LABELS, PANEL_COLOR_ORDER, form_control_qss
from theme import current as current_palette

_SWATCH_SIZE = 14


def _swatch_icon(hex_color: str) -> QIcon:
    pixmap = QPixmap(_SWATCH_SIZE, _SWATCH_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(QPen(QColor(current_palette().border)))
    painter.drawRoundedRect(1, 1, _SWATCH_SIZE - 2, _SWATCH_SIZE - 2, 3, 3)
    painter.end()
    return QIcon(pixmap)


def apply_panel_tint(widget: QWidget, color_key: str | None) -> None:
    """Setzt (oder entfernt) die individuelle Hintergrundfarbe EINES
    QGroupBox-Panels als Instanz-Stylesheet, laesst Rahmen/Titel-Farbe/Radius
    aber unangetastet aus dem globalen stylesheet() (siehe dessen
    QGroupBox-Regel).

    Haengt zusaetzlich theme.form_control_qss() an dasselbe Stylesheet an, um
    zu verhindern, dass die Panel-Farbe auf Buttons/Eingabefelder im Panel
    durchschlaegt (siehe dortigen Docstring sowie BUGS.md #10f) -- ohne das
    wuerden z.B. der Sollwert-Spinbox oder die EIN/AUS-Buttons statt ihrer
    normalen Theme-Farbe die Panel-Tönung annehmen.

    WICHTIG: Die eigene Hintergrundfarbe MUSS als "QGroupBox { ... }"-Regel
    mit explizitem Typ-Selektor geschrieben werden, NICHT als nackte
    Eigenschaft ohne Selektor (z.B. "background-color: X;" allein) -- ein
    erster Versuch genau so ist an echter Hardware gescheitert (BUGS.md
    #10f, zweiter Anlauf): sobald im selben Instanz-Stylesheet zusaetzlich
    Selektor-Regeln (hier form_control_qss()) folgen, wird die fuehrende
    selektorlose Eigenschaft von Qt nicht mehr zuverlaessig nur auf dieses
    eine Widget beschraenkt und schlaegt trotz der spezifischeren
    QPushButton-Regel weiterhin auf Kind-Widgets durch (per minimalem
    Repro-Test bestaetigt). Mit explizitem "QGroupBox { ... }"-Selektor
    gewinnt die spezifischere "QPushButton { ... }"-Regel innerhalb
    desselben Stylesheets zuverlaessig, wie normale QSS-Spezifitaet es
    erwarten laesst."""
    if color_key is None:
        widget.setStyleSheet("")
        return
    pal = current_palette()
    hex_color = pal.panel_tints.get(color_key)
    if not hex_color:
        widget.setStyleSheet("")
        return
    widget.setStyleSheet(f"QGroupBox {{ background-color: {hex_color}; }}\n{form_control_qss(pal)}")


class PanelColorButton(IconButton):
    """Kleiner Panel-Header-Button: oeffnet ein Menue mit den verfuegbaren
    Panel-Farben (siehe theme.PANEL_COLOR_ORDER) plus "Kein (Standard)".
    Die eigene Icon-Farbe folgt normal dem Theme (siehe IconButton) -- nur
    die Menue-Eintraege zeigen kleine Farbquadrate der jeweiligen Toenung."""

    color_selected = Signal(object)  # str | None

    def __init__(self) -> None:
        super().__init__("mdi.palette-outline", "")
        self._current: str | None = None
        self.clicked.connect(self._open_menu)

    def set_current_color(self, color_key: str | None) -> None:
        self._current = color_key

    def _open_menu(self) -> None:
        menu = QMenu(self)
        none_action = menu.addAction(tr("Kein (Standard)"))
        none_action.setCheckable(True)
        none_action.setChecked(self._current is None)
        none_action.triggered.connect(lambda: self.color_selected.emit(None))
        menu.addSeparator()
        pal = current_palette()
        for key in PANEL_COLOR_ORDER:
            action = menu.addAction(_swatch_icon(pal.panel_tints[key]), tr(PANEL_COLOR_LABELS[key]))
            action.setCheckable(True)
            action.setChecked(self._current == key)
            action.triggered.connect(lambda checked=False, k=key: self.color_selected.emit(k))
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

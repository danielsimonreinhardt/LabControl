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
from theme import PANEL_COLOR_LABELS, PANEL_COLOR_ORDER
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
    QGroupBox-Panels als Instanz-Stylesheet -- analog zu theme.
    no_own_background: eine reine Eigenschaft ohne Selektor gewinnt fuer
    dieses eine Widget, laesst Rahmen/Titel-Farbe/Radius aber unangetastet
    aus dem globalen stylesheet() (siehe dessen QGroupBox-Regel)."""
    if color_key is None:
        widget.setStyleSheet("")
        return
    hex_color = current_palette().panel_tints.get(color_key)
    widget.setStyleSheet(f"background-color: {hex_color};" if hex_color else "")


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

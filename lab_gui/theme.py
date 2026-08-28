"""Zentrales Farb-Theme der Labor-App: zwei Paletten (Modern Light / Amber
Industrial) plus der QSS-Stylesheet-Aufbau dafuer.

Widgets mit eigenem Zeichnen bzw. inline setStyleSheet-Aufrufen (Status-Label
verbunden/getrennt, Blink-Farbe im Testcase-Tab, Oszilloskop-Vorschau im
Signal-Dialog) lesen ihre Farben ueber current() statt ueber fest verdrahtete
Hex-Werte, damit sie zur jeweils aktiven Palette passen. Der globale Look
(Hintergruende, Buttons, Tabs, Tabelle, Eingabefelder) kommt dagegen aus
stylesheet() und wird als ein Stueck auf die QApplication angewendet.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

# Absolute Pfade (QSS-url() wird sonst relativ zum aktuellen Arbeitsverzeichnis
# aufgeloest, was unzuverlaessig ist). Im PyInstaller-Onefile-Build liegt
# __file__ nicht am realen Ort der mitgelieferten Daten -- dort werden sie zur
# Laufzeit nach sys._MEIPASS entpackt (siehe PyInstaller-Doku); die icons/
# muessen beim Bauen per --add-data mitgegeben werden, sonst fehlen sie im .exe
# (Icons fehlen dann nur optisch, kein Absturz). Qt erwartet in url() immer
# Vorwaertsslashes, auch unter Windows.
_ICONS_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent) / "icons"
_SPIN_UP_ICON = (_ICONS_DIR / "spin_up.png").as_posix()
_SPIN_DOWN_ICON = (_ICONS_DIR / "spin_down.png").as_posix()


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    success: str
    warning: str
    danger: str
    selection: str
    plot_bg: str
    plot_grid: str
    plot_signal: str
    plot_ref: str


LIGHT = Palette(
    name="Modern Light",
    bg="#f5f7fa",
    surface="#ffffff",
    surface_alt="#eef1f6",
    border="#d8dee6",
    text="#1e2530",
    text_muted="#5b6472",
    accent="#4f46e5",
    accent_hover="#6366f1",
    success="#16a34a",
    warning="#d97706",
    danger="#dc2626",
    selection="#e0e7ff",
    plot_bg="#10131a",
    plot_grid="#2a3040",
    plot_signal="#22c55e",
    plot_ref="#f59e0b",
)

AMBER_DARK = Palette(
    name="Amber Industrial",
    bg="#14171c",
    surface="#1e2229",
    surface_alt="#262b33",
    border="#2c313a",
    text="#e8e6e1",
    text_muted="#9a978f",
    accent="#ff9f1c",
    accent_hover="#ffb347",
    success="#ff9f1c",
    warning="#ffcc00",
    danger="#ef4444",
    selection="#3a2f1d",
    plot_bg="#0c0e11",
    plot_grid="#2c313a",
    plot_signal="#ff9f1c",
    plot_ref="#ffcc00",
)


def stylesheet(pal: Palette) -> str:
    return f"""
    QWidget {{
        background-color: {pal.bg};
        color: {pal.text};
        selection-background-color: {pal.selection};
        selection-color: {pal.text};
    }}
    QMainWindow, QDialog {{
        background-color: {pal.bg};
    }}
    QGroupBox {{
        background-color: {pal.surface};
        border: 1px solid {pal.border};
        border-radius: 6px;
        margin-top: 14px;
        padding-top: 10px;
        font-weight: bold;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {pal.text};
    }}
    QTabWidget::pane {{
        border: 1px solid {pal.border};
        border-radius: 6px;
        background-color: {pal.surface};
    }}
    QTabBar::tab {{
        background-color: {pal.surface_alt};
        color: {pal.text_muted};
        padding: 6px 16px;
        border: 1px solid {pal.border};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background-color: {pal.surface};
        color: {pal.text};
        border-bottom: 2px solid {pal.accent};
    }}
    QTabBar::tab:hover {{
        color: {pal.text};
    }}
    QPushButton {{
        background-color: {pal.surface_alt};
        color: {pal.text};
        border: 1px solid {pal.border};
        border-radius: 4px;
        padding: 5px 12px;
    }}
    QPushButton:hover {{
        background-color: {pal.accent};
        color: {pal.surface};
        border-color: {pal.accent};
    }}
    QPushButton:pressed {{
        background-color: {pal.accent_hover};
    }}
    QPushButton:disabled {{
        color: {pal.text_muted};
        background-color: {pal.surface_alt};
        border-color: {pal.border};
    }}
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
        background-color: {pal.surface};
        color: {pal.text};
        border: 1px solid {pal.border};
        border-radius: 4px;
        padding: 3px 6px;
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QAbstractSpinBox {{
        padding-right: 2px;
    }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        subcontrol-origin: border;
        width: 18px;
        border: none;
        border-left: 1px solid {pal.border};
        background-color: {pal.surface_alt};
    }}
    QAbstractSpinBox::up-button {{
        subcontrol-position: top right;
        border-top-right-radius: 4px;
        border-bottom: 1px solid {pal.border};
    }}
    QAbstractSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: 4px;
    }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
        background-color: {pal.accent};
    }}
    QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{
        background-color: {pal.accent_hover};
    }}
    QAbstractSpinBox::up-arrow {{
        image: url({_SPIN_UP_ICON});
        width: 10px;
        height: 10px;
        subcontrol-position: center;
    }}
    QAbstractSpinBox::down-arrow {{
        image: url({_SPIN_DOWN_ICON});
        width: 10px;
        height: 10px;
        subcontrol-position: center;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {pal.border};
        border-radius: 3px;
        background-color: {pal.surface};
    }}
    QCheckBox::indicator:checked {{
        background-color: {pal.accent};
        border-color: {pal.accent};
    }}
    QTableWidget {{
        background-color: {pal.surface};
        gridline-color: {pal.border};
        border: 1px solid {pal.border};
        alternate-background-color: {pal.surface_alt};
    }}
    QHeaderView::section {{
        background-color: {pal.surface_alt};
        color: {pal.text_muted};
        padding: 4px;
        border: none;
        border-bottom: 1px solid {pal.border};
        border-right: 1px solid {pal.border};
    }}
    QScrollArea {{
        border: none;
    }}
    QScrollBar:horizontal, QScrollBar:vertical {{
        background: {pal.surface_alt};
        border: none;
    }}
    QScrollBar::handle {{
        background: {pal.border};
        border-radius: 4px;
    }}
    QStatusBar {{
        background-color: {pal.surface};
        border-top: 1px solid {pal.border};
    }}
    """


class ThemeManager(QObject):
    """Singleton: haelt die aktuell aktive Palette und benachrichtigt Widgets,
    die auf einen Theme-Wechsel live reagieren muessen (siehe Modul-Docstring)."""

    changed = Signal(Palette)

    _instance: "ThemeManager | None" = None

    def __init__(self) -> None:
        super().__init__()
        self._palette = LIGHT

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def palette(self) -> Palette:
        return self._palette

    def apply(self, dark: bool) -> None:
        self._palette = AMBER_DARK if dark else LIGHT
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self._palette))
        self.changed.emit(self._palette)


def current() -> Palette:
    return ThemeManager.instance().palette

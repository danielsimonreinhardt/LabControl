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
from PySide6.QtWidgets import QApplication, QWidget

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
    # Zeilenhintergrund fuer bestandene Pass/Fail-Pruefungen im Testcase-Tab.
    # Eigener Wert statt success, weil success im Amber-Industrial-Theme
    # bewusst amber ist (Theme-Akzent) -- ein Pruefergebnis soll aber in
    # beiden Themes intuitiv gruen (bestanden) vs. rot (fehlgeschlagen) sein.
    check_pass: str
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
    check_pass="#16a34a",
    warning="#d97706",
    danger="#dc2626",
    selection="#e0e7ff",
    # Anders als im Amber-Industrial-Theme (siehe unten, bewusst dunkles
    # Oszilloskop-Schwarz) folgt der Diagramm-Hintergrund hier der restlichen
    # hellen UI (surface/border) statt fest schwarz zu sein (siehe BUGS.md #9).
    plot_bg="#ffffff",
    plot_grid="#d8dee6",
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
    # Dunkleres Gruen als im Light-Theme, damit der helle Text (#e8e6e1)
    # auf dem Zeilenhintergrund lesbar bleibt.
    check_pass="#15803d",
    warning="#ffcc00",
    danger="#ef4444",
    selection="#3a2f1d",
    # Bewusst dunkles Oszilloskop-Schwarz, passend zum ohnehin dunklen
    # Amber-Industrial-Theme (siehe LIGHT oben fuer den Kontrast dazu).
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
    /* Ohne explizite :disabled-Regeln wuerden gesperrte Eingabefelder exakt
       wie aktive aussehen -- das Stylesheet oben ueberschreibt die native
       Ausgrau-Darstellung von Qt (aufgefallen im "Pruefung definieren"-Dialog,
       dessen Felder bei inaktiver Pruefung gesperrt sind). */
    QLineEdit:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled, QComboBox:disabled {{
        background-color: {pal.surface_alt};
        color: {pal.text_muted};
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
    QCheckBox:disabled {{
        color: {pal.text_muted};
    }}
    QCheckBox::indicator:disabled {{
        background-color: {pal.surface_alt};
    }}
    QCheckBox::indicator:checked:disabled {{
        background-color: {pal.text_muted};
        border-color: {pal.text_muted};
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


def no_own_background(widget: QWidget) -> QWidget:
    """Verhindert, dass ein reines Layout-Wrapper-QWidget (z.B. eine Zeile
    innerhalb eines QFormLayout, ein Legende-/Status-Container -- ueberall
    dort, wo mehrere Widgets nur fuers Anordnen in ein zusaetzliches QWidget
    gebuendelt werden) opak den allgemeinen Seitenhintergrund malt (globale
    "QWidget { background-color: ... }"-Regel in stylesheet()).

    Ohne das faerbt sich jeder ungenutzte Rest-Platz im Wrapper (z.B. hinter
    einem abschliessenden addStretch()) sichtbar in der Seiten- statt der
    Flaechenfarbe des tatsaechlich umschliessenden, farbig gestylten Widgets
    (QGroupBox/QStatusBar/...) -- daher als grauer Fleck/Streifen sichtbar
    (aufgetreten in control_tab._row(), timeline_tab._ChartRow, dashboard.
    _DevicePanel, main_window._status_container; siehe BUGS.md #8). Eine
    globale QSS-Regel wie "QGroupBox QWidget { background: transparent; }"
    waere zwar an einer Stelle, aber ihre Deszendenten-Selektor-Spezifitaet
    ist hoeher als z.B. "QPushButton { ... }" und würde damit auch die
    Farbe von Buttons/Eingabefeldern INNERHALB von GroupBoxen ueberschreiben
    -- deshalb hier bewusst als Instanz-Stylesheet (gewinnt immer, ganz ohne
    Seiteneffekte auf andere Widget-Typen) statt als globale Regel.

    Betrifft NICHT Widgets, die als QTableWidget-Zellwidget gesetzt werden --
    die zeigen bereits korrekt den Zeilen-/Auswahl-Hintergrund der Tabelle
    (siehe testcase_tab.py: WA_StyledBackground dort, separate Ursache).
    """
    widget.setStyleSheet("background: transparent;")
    return widget

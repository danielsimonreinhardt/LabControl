"""Touch-Profil fuer kleine Touch-Displays (Pi-Plan Phase 4, Entscheidung PI-5 a, 10.11.2026).

Gleiche Widgets wie am PC, nur anders angeordnet und groesser (Raspberry Pi, 1024 x 600):

- touch_qss(): Bedienelemente mindestens TOUCH_MIN px hoch, breitere Spin-Pfeile, grosse
  Haekchen und Scrollbalken -- kommt zusaetzlich zum Theme-Stylesheet (theme.ThemeManager.set_touch)
- NavBar: Reiter als grosse Schaltflaechen links (Symbol + Text) statt der Reiterleiste oben;
  die Uebersicht (Dashboard) ist dort ein eigener Reiter statt eines festen Streifens
- TopBar: Leiste oben mit Geraetezustand (wischbar), Fernsteuerungs-Hinweis, Vollbild-Knopf und
  ALLE AUS -- ersetzt die Statusleiste, die mit vielen Geraeten breiter als 1024 px wird

Eingeschaltet wird es in den Einstellungen; solange es nie gewaehlt wurde, schlaegt suggest_touch()
es bei Bildschirmen bis 1024 x 600 vor (MainWindow._wire_touch).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QScroller,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from theme import Palette, ThemeManager
from theme import current as current_palette

TOUCH_SCREEN_MAX = (1024, 600)
TOUCH_MIN = 40          # Mindesthoehe von Knoepfen und Eingabefeldern in px
NAV_WIDTH = 124
NAV_ICON = QSize(30, 30)
TOP_BAR_HEIGHT = 52
FONT_SCALE = 1.2


SPIN_KEY_WIDTH = 40
_KEY_ICON_CACHE: dict[tuple[str, str], str] = {}


def _key_icon_path(name: str, pal: Palette) -> str:
    """−/+ als PNG fuer QSS-url() (wie theme._spin_arrow_icon_path)."""
    key = (pal.name, name)
    if key not in _KEY_ICON_CACHE:
        path = Path(tempfile.gettempdir()) / f"labcontrol_key_{name}_{pal.name.replace(' ', '_')}.png"
        qta.icon(f"mdi.{name}", color=pal.text).pixmap(QSize(44, 44)).save(str(path), "PNG")
        _KEY_ICON_CACHE[key] = path.as_posix()
    return _KEY_ICON_CACHE[key]


def suggest_touch(screen) -> bool:
    """Kleiner Bildschirm (bis 1024 x 600) -> Touch-Profil vorschlagen."""
    if screen is None or QGuiApplication.platformName() == "offscreen":
        return False   # ohne echte Anzeige (Tests, CI: virtuell 800 x 600) nichts vorschlagen
    size = screen.size()
    return size.width() <= TOUCH_SCREEN_MAX[0] and size.height() <= TOUCH_SCREEN_MAX[1]


def touch_qss(pal: Palette) -> str:
    return f"""
    QPushButton, QToolButton {{
        min-height: {TOUCH_MIN - 12}px;
        padding: 6px 14px;
    }}
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
        min-height: {TOUCH_MIN - 8}px;
        padding: 3px 8px;
    }}
    /* Grosse −/+ Tasten links und rechts vom Wert statt kleiner Pfeile uebereinander (PI-6) */
    QGroupBox QAbstractSpinBox {{
        padding-left: {SPIN_KEY_WIDTH + 4}px;
        padding-right: {SPIN_KEY_WIDTH + 4}px;
        min-height: {TOUCH_MIN - 4}px;
        min-width: 160px;
        max-width: 230px;
    }}
    QGroupBox QAbstractSpinBox::up-button, QGroupBox QAbstractSpinBox::down-button {{
        subcontrol-origin: border;
        width: {SPIN_KEY_WIDTH}px;
        height: {TOUCH_MIN - 2}px;
        border: none;
        background-color: {pal.surface_alt};
    }}
    QGroupBox QAbstractSpinBox::up-button {{
        subcontrol-position: center right;
        border-left: 1px solid {pal.border};
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
    }}
    QGroupBox QAbstractSpinBox::down-button {{
        subcontrol-position: center left;
        border-right: 1px solid {pal.border};
        border-top-left-radius: 4px;
        border-bottom-left-radius: 4px;
    }}
    QGroupBox QAbstractSpinBox::up-arrow {{
        image: url({_key_icon_path("plus", pal)});
        width: 22px;
        height: 22px;
    }}
    QGroupBox QAbstractSpinBox::down-arrow {{
        image: url({_key_icon_path("minus", pal)});
        width: 22px;
        height: 22px;
    }}
    QCheckBox, QRadioButton {{
        spacing: 10px;
        min-height: {TOUCH_MIN - 8}px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 26px;
        height: 26px;
    }}
    QComboBox QAbstractItemView::item {{
        min-height: {TOUCH_MIN}px;
    }}
    QMenu::item {{
        padding: 10px 28px;
    }}
    QTabBar::tab {{
        padding: 10px 20px;
    }}
    QHeaderView::section {{
        padding: 8px;
    }}
    QScrollBar:vertical {{
        width: 22px;
    }}
    QScrollBar:horizontal {{
        height: 22px;
    }}
    QScrollBar::handle:vertical {{
        min-height: 48px;
    }}
    QScrollBar::handle:horizontal {{
        min-width: 48px;
    }}
    QWidget#touchNav {{
        background-color: {pal.surface};
        border-right: 1px solid {pal.border};
    }}
    QWidget#touchNav QToolButton {{
        background-color: transparent;
        color: {pal.text_muted};
        border: none;
        border-radius: 6px;
        padding: 6px 2px;
    }}
    QWidget#touchNav QToolButton:checked {{
        background-color: {pal.selection};
        color: {pal.text};
        border-left: 4px solid {pal.accent};
    }}
    QWidget#touchTopBar {{
        background-color: {pal.surface};
        border-bottom: 1px solid {pal.border};
    }}
    """


def enable_swipe(area: QScrollArea) -> None:
    """Wischen statt Scrollbalken (kinetisches Scrollen) fuer Finger und Maus."""
    QScroller.grabGesture(area.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture)


class NavBar(QWidget):
    """Grosse Reiter-Knoepfe links; steuert ein QTabWidget mit verborgener Reiterleiste."""

    selected = Signal(QWidget)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("touchNav")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(NAV_WIDTH)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._buttons: dict[int, tuple[QToolButton, QWidget, str, Callable[[], str]]] = {}
        ThemeManager.instance().changed.connect(lambda _pal: self._recolor())

    def add_page(self, page: QWidget, icon_name: str, text: Callable[[], str]) -> QToolButton:
        button = QToolButton()
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(NAV_ICON)
        button.setMinimumHeight(76)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.clicked.connect(lambda: self.selected.emit(page))
        self._layout.insertWidget(self._layout.count() - 1, button)
        self._buttons[id(page)] = (button, page, icon_name, text)
        button.setText(text())
        self._set_icon(button, icon_name)
        return button

    def set_current(self, page: QWidget | None) -> None:
        entry = self._buttons.get(id(page)) if page is not None else None
        if entry is not None:
            entry[0].setChecked(True)

    def retranslate(self) -> None:
        for button, _page, _icon, text in self._buttons.values():
            button.setText(text())

    def _recolor(self) -> None:
        for button, _page, icon_name, _text in self._buttons.values():
            self._set_icon(button, icon_name)

    @staticmethod
    def _set_icon(button: QToolButton, icon_name: str) -> None:
        pal = current_palette()
        button.setIcon(qta.icon(icon_name, color=pal.text_muted, color_on=pal.accent))


class TopBar(QWidget):
    """Leiste oben: Geraetezustand (wischbar) | Fernsteuerung | Vollbild | ALLE AUS."""

    fullscreen_requested = Signal()
    power_requested = Signal()   # Pi herunterfahren / neu starten (MainWindow fragt nach)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("touchTopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(TOP_BAR_HEIGHT)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._layout.setSpacing(8)
        self._status_area = QScrollArea()
        self._status_area.setFrameShape(QFrame.Shape.NoFrame)
        self._status_area.setWidgetResizable(True)
        self._status_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._status_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._status_area.setStyleSheet("background: transparent;")
        enable_swipe(self._status_area)
        self._layout.addWidget(self._status_area, 1)
        self._fullscreen_button = QToolButton()
        self._fullscreen_button.setIconSize(QSize(26, 26))
        self._fullscreen_button.setMinimumSize(48, 44)
        self._fullscreen_button.clicked.connect(self.fullscreen_requested)
        self._power_button = QToolButton()
        self._power_button.setIconSize(QSize(26, 26))
        self._power_button.setMinimumSize(48, 44)
        self._power_button.clicked.connect(self.power_requested)
        self._power_button.hide()
        self._trailing: list[QWidget] = []
        self.set_fullscreen(False)
        ThemeManager.instance().changed.connect(lambda _pal: self._recolor())

    def adopt(self, status: QWidget, trailing: list[QWidget]) -> None:
        """Statusanzeige in den wischbaren Bereich, die uebrigen rechts davon (ALLE AUS zuletzt)."""
        self._status_area.setWidget(status)
        self._trailing = trailing
        for widget in trailing[:-1]:
            self._layout.addWidget(widget)
        self._layout.addWidget(self._fullscreen_button)
        self._layout.addWidget(self._power_button)
        if trailing:
            self._layout.addWidget(trailing[-1])

    def release(self) -> QWidget | None:
        """Gibt die Statusanzeige zurueck (die rechten Widgets nimmt der neue Besitzer selbst)."""
        for widget in self._trailing:
            self._layout.removeWidget(widget)
        self._layout.removeWidget(self._fullscreen_button)
        self._layout.removeWidget(self._power_button)
        self._trailing = []
        return self._status_area.takeWidget()

    def set_power_available(self, on: bool) -> None:
        self._power_button.setVisible(on)

    def set_fullscreen(self, on: bool) -> None:
        self._fullscreen = on
        self._recolor()

    def _recolor(self) -> None:
        pal = current_palette()
        self._fullscreen_button.setIcon(qta.icon("mdi.fullscreen-exit" if self._fullscreen else "mdi.fullscreen",
                                                 color=pal.text))
        self._power_button.setIcon(qta.icon("mdi.power", color=pal.text))

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
import shiboken6
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScroller,
    QSizePolicy,
    QTabBar,
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


TOUCH_H = 44            # einheitliche Hoehe aller Knoepfe und Eingabefelder (TouchSizer)
_ORIGINAL_SIZE = "_touch_original_size"
# Eigene feste Groessen: Tastenfeld, Navigation/Leiste, Preset-Kachel (eigene Touch-Fassung);
# das Dashboard ist reine Anzeige -- gestreckte Zeilen zerrissen dort die Kacheln.
_SIZER_EXCLUDED = {"KeypadDialog", "NavBar", "TopBar", "_PresetSlotButton", "DashboardWidget"}


class TouchSizer(QObject):
    """Einheitliche Groessen im Touch-Profil: jedes Bedienelement wird beim Anzeigen (QEvent.Polish)
    auf TOUCH_H px Hoehe gebracht, reine Symbol-Knoepfe auf TOUCH_H x TOUCH_H, Formular-
    Beschriftungen bekommen dieselbe Mindesthoehe (stehen damit mittig neben dem Feld).

    Ueber das Stylesheet allein geht das nicht sauber: einzelne Zeilen haben eigene Stylesheets
    mit anderem Innenabstand, und QSS-min-height bezieht sich auf den Inhalt. Ausgenommen sind
    Tabellenzellen (Testablauf-Editor, CAN-Tabelle) und die Widgets in _SIZER_EXCLUDED.
    Ausschalten stellt die urspruenglichen Mindest-/Hoechstmasse wieder her."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._app = app
        self._on = False
        # Erst im naechsten Durchlauf der Ereignisschleife anpassen: Widgets werden oft schon
        # poliert, bevor sie in ihr endgueltiges Elternteil (z. B. das Dashboard) gehaengt sind.
        self._pending: list[QWidget] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(0)
        self._flush_timer.timeout.connect(self._flush)

    @property
    def active(self) -> bool:
        return self._on

    def set_active(self, on: bool) -> None:
        if on == self._on:
            return
        self._on = on
        if on:
            self._app.installEventFilter(self)
            for widget in self._app.allWidgets():
                self.apply(widget)
        else:
            self._app.removeEventFilter(self)
            for widget in self._app.allWidgets():
                self._restore(widget)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.Polish and isinstance(obj, QWidget):
            self._pending.append(obj)
            if not self._flush_timer.isActive():
                self._flush_timer.start()
        return False

    def _flush(self) -> None:
        pending, self._pending = self._pending, []
        for widget in pending:
            if shiboken6.isValid(widget):
                self.apply(widget)

    @staticmethod
    def _excluded(widget: QWidget) -> bool:
        parent = widget.parentWidget()
        if isinstance(widget, QLineEdit) and isinstance(parent, (QAbstractSpinBox, QComboBox)):
            return True   # Textfeld im Inneren eines Zahlenfelds/einer Auswahlliste
        node = widget
        for _ in range(12):
            if node is None:
                return False
            if isinstance(node, (QAbstractItemView, QTabBar)) or type(node).__name__ in _SIZER_EXCLUDED:
                return True
            node = node.parentWidget()
        return False

    def apply(self, widget: QWidget) -> None:
        if not self._on or self._excluded(widget):
            return
        name = type(widget).__name__
        if isinstance(widget, (QLineEdit, QComboBox, QAbstractSpinBox)):
            self._fix(widget, height=TOUCH_H)
        elif name == "_SegmentedToggle":          # gezeichneter AUS|EIN-Schalter (microHIL)
            self._fix(widget, width=88, height=TOUCH_H)
        elif name == "_DigitalOutToggle":         # LED-Knopf OUT1-8 (microHIL)
            self._fix(widget, width=TOUCH_H, height=TOUCH_H + 8)
        elif isinstance(widget, (QPushButton, QToolButton)):
            icon_only = not widget.text() and not widget.icon().isNull()
            self._fix(widget, width=TOUCH_H if icon_only else None, height=TOUCH_H)
        elif isinstance(widget, QLabel) and _is_form_label(widget):
            self._remember(widget)
            widget.setMinimumHeight(TOUCH_H)

    def _fix(self, widget: QWidget, height: int, width: int | None = None) -> None:
        self._remember(widget)
        widget.setFixedHeight(height)
        if width is not None:
            widget.setFixedWidth(width)

    @staticmethod
    def _remember(widget: QWidget) -> None:
        if getattr(widget, _ORIGINAL_SIZE, None) is None:
            setattr(widget, _ORIGINAL_SIZE, (widget.minimumSize(), widget.maximumSize()))

    @staticmethod
    def _restore(widget: QWidget) -> None:
        original = getattr(widget, _ORIGINAL_SIZE, None)
        if original is not None:
            widget.setMinimumSize(original[0])
            widget.setMaximumSize(original[1])
            setattr(widget, _ORIGINAL_SIZE, None)


def _is_form_label(label: QLabel) -> bool:
    """Beschriftung einer Formularzeile -- auch wenn das QFormLayout in ein anderes Layout geschachtelt ist."""
    parent = label.parentWidget()
    if parent is None:
        return False
    for layout in parent.findChildren(QFormLayout) + ([parent.layout()] if isinstance(parent.layout(), QFormLayout) else []):
        _row, role = layout.getWidgetPosition(label)
        if role == QFormLayout.ItemRole.LabelRole:
            return True
    return False


_SIZER: TouchSizer | None = None


def sizer() -> TouchSizer:
    global _SIZER
    if _SIZER is None:
        _SIZER = TouchSizer(QApplication.instance())
    return _SIZER


def touch_qss(pal: Palette) -> str:
    return f"""
    QPushButton, QToolButton {{
        padding: 6px 14px;
    }}
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
        padding: 3px 8px;
    }}
    /* Grosse −/+ Tasten links und rechts vom Wert statt kleiner Pfeile uebereinander (PI-6) */
    QGroupBox QAbstractSpinBox {{
        padding-left: {SPIN_KEY_WIDTH + 4}px;
        padding-right: {SPIN_KEY_WIDTH + 4}px;
        min-width: 150px;
        max-width: 170px;
    }}
    QGroupBox QAbstractSpinBox::up-button, QGroupBox QAbstractSpinBox::down-button {{
        subcontrol-origin: border;
        width: {SPIN_KEY_WIDTH}px;
        height: {TOUCH_H - 2}px;
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

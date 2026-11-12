"""Touch-Profil fuer kleine Touch-Displays (Pi-Plan Phase 4, Entscheidung PI-5 a, 10.11.2026).

Gleiche Widgets wie am PC, nur groesser (Raspberry Pi, 1024 x 600):

- touch_qss(): Bedienelemente mindestens TOUCH_MIN px hoch, breitere Spin-Pfeile, grosse
  Haekchen und Scrollbalken -- kommt zusaetzlich zum Theme-Stylesheet (theme.ThemeManager.set_touch)
- NavBar: Reiter als Schaltflaechen links (Symbol + Text) statt der Reiterleiste oben; im Touch-Profil
  ist die Uebersicht (Dashboard) dort ein eigener Reiter statt eines festen Streifens
- TopBar: Leiste oben mit Geraetezustand (wischbar), Fernsteuerungs-Hinweis, Vollbild-Knopf und
  ALLE AUS -- ersetzt die Statusleiste, die mit vielen Geraeten breiter als 1024 px wird

NavBar und TopBar (shell_qss) gelten seit 0.53.0 auch am PC, dort kleiner und mit dem Dashboard als
festem Streifen ueber dem Inhalt (Nutzerwunsch 12.11.2026).

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
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
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

from theme import METRICS, Palette, ThemeManager
from theme import current as current_palette

TOUCH_SCREEN_MAX = (1024, 600)
TOUCH_MIN = 40          # Mindesthoehe von Knoepfen und Eingabefeldern in px
NAV_WIDTH = 124
NAV_ICON = QSize(30, 30)
TOP_BAR_HEIGHT = 52
FONT_SCALE = 1.2


SPIN_KEY_WIDTH = 40
SCROLLBAR_W = 6         # Touch: Scrollleiste nur als Positionsanzeige (Wischen bedient), GUI-Review UX-8
_KEY_ICON_CACHE: dict[tuple[str, str], str] = {}


_ICON_PATH_CACHE: dict[tuple[str, str, int], str] = {}


def touch_icon_path(name: str, color: str, size: int) -> str:
    """qtawesome-Symbol als PNG-Datei fuer Rich-Text (<img src>) in QLabel, z. B. den Sicherheits-Chip."""
    key = (name, color, size)
    if key not in _ICON_PATH_CACHE:
        safe = name.replace(".", "_") + "_" + color.lstrip("#") + f"_{size}"
        path = Path(tempfile.gettempdir()) / f"labcontrol_icon_{safe}.png"
        qta.icon(name, color=color).pixmap(QSize(size * 2, size * 2)).save(str(path), "PNG")
        _ICON_PATH_CACHE[key] = path.as_posix()
    return _ICON_PATH_CACHE[key]


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
TOUCH_LABEL = "_touch_label"
TOUCH_KEEP_WIDTH = "_touch_keep_width"   # Symbol-Knopf, der breit bleiben soll (Start/Stop am Pi): nur Hoehe anpassen
SWIPE_ATTR = "_touch_swipe"            # am Viewport: Wischen eingeschaltet


def touch_label(button, text_fn: Callable[[], str]) -> None:
    """Symbol-Knopf bekommt im Touch-Profil einen kurzen Text (Tooltips gibt es am Touch nicht).
    text_fn wird bei jedem Anzeigen, Sprachwechsel und -- bei umschaltbaren Knoepfen -- Umschalten gefragt."""
    setattr(button, TOUCH_LABEL, text_fn)
    if button.isCheckable():
        button.toggled.connect(lambda _checked=False: sizer().relabel(button))
# Eigene feste Groessen: Tastenfeld, Navigation/Leiste, Preset-Kachel (eigene Touch-Fassung);
# das Dashboard ist reine Anzeige -- gestreckte Zeilen zerrissen dort die Kacheln.
_SIZER_EXCLUDED = {"KeypadDialog", "TextKeyboardDialog", "NavBar", "TopBar", "_PresetSlotButton", "DashboardWidget"}


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
        self._swipe_blocked: set[int] = set()
        from i18n import Translator   # lokal: i18n braucht eine laufende QApplication nicht, aber kurz halten
        Translator.instance().language_changed.connect(lambda *_: self._relabel_all())

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
                if isinstance(widget, QAbstractScrollArea):
                    self._update_swipe(widget)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.Polish and isinstance(obj, QWidget):
            self._pending.append(obj)
            if not self._flush_timer.isActive():
                self._flush_timer.start()
        elif event.type() == QEvent.Type.Show and isinstance(obj, QDialog) and obj.isWindow():
            fit_to_screen(obj)
        elif isinstance(obj, QLineEdit):
            from touch_keyboard import handle_event   # lokal: touch_keyboard -> touch_keypad -> theme
            return handle_event(obj, event)
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

    # -- Wischen ----------------------------------------------------------------

    def set_swipe_blocked(self, areas: list[QAbstractScrollArea], blocked: bool) -> None:
        """Modus "Anordnen": in diesen Flaechen ist Ziehen wieder Verschieben statt Wischen."""
        for area in areas:
            if blocked:
                self._swipe_blocked.add(id(area))
            else:
                self._swipe_blocked.discard(id(area))
            self._update_swipe(area)

    def _update_swipe(self, area: QAbstractScrollArea) -> None:
        want = self._on and id(area) not in self._swipe_blocked
        viewport = area.viewport()
        # Zustand selbst merken: QScroller.grabbedGesture liefert in PySide6 keine verlaesslichen Werte
        has = bool(getattr(viewport, SWIPE_ATTR, False))
        if want and not has:
            QScroller.grabGesture(viewport, QScroller.ScrollerGestureType.LeftMouseButtonGesture)
        elif not want and has:
            QScroller.ungrabGesture(viewport)
        setattr(viewport, SWIPE_ATTR, want)

    def relabel(self, button: QAbstractButton) -> None:
        text_fn = getattr(button, TOUCH_LABEL, None)
        if self._on and text_fn is not None:
            button.setText(text_fn())

    def _relabel_all(self) -> None:
        for widget in self._app.allWidgets():
            if isinstance(widget, QAbstractButton):
                self.relabel(widget)

    def apply(self, widget: QWidget) -> None:
        if not self._on:
            return
        if isinstance(widget, QAbstractScrollArea) and "QComboBox" not in type(widget.window()).__name__:
            self._update_swipe(widget)    # auch im Dashboard und in Tabellen
        if self._excluded(widget):
            return
        name = type(widget).__name__
        if isinstance(widget, (QPushButton, QToolButton)) and getattr(widget, TOUCH_LABEL, None) is not None:
            # Symbol-Knopf mit kurzem Text (am Touch gibt es keinen Tooltip)
            self._remember(widget)
            widget.setText(getattr(widget, TOUCH_LABEL)())
            widget.setMinimumWidth(0)
            widget.setMaximumWidth(16777215)
            widget.setFixedHeight(TOUCH_H)
            return
        if isinstance(widget, (QLineEdit, QComboBox, QAbstractSpinBox)):
            self._fix(widget, height=TOUCH_H)
        elif name == "_SegmentedToggle":          # gezeichneter AUS|EIN-Schalter (microHIL)
            self._fix(widget, width=88, height=TOUCH_H)
        elif name == "_DigitalOutToggle":         # LED-Knopf OUT1-8 (microHIL)
            self._fix(widget, width=TOUCH_H, height=TOUCH_H + 8)
        elif isinstance(widget, (QPushButton, QToolButton)):
            icon_only = (not widget.text() and not widget.icon().isNull()
                         and not getattr(widget, TOUCH_KEEP_WIDTH, False))
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
            if getattr(widget, TOUCH_LABEL, None) is not None:
                widget.setText("")
            widget.setMinimumSize(original[0])
            widget.setMaximumSize(original[1])
            setattr(widget, _ORIGINAL_SIZE, None)


DIALOG_DECORATION_H = 40   # Titelleiste des Dialogs (Wayland: vom Programm gezeichnet), nicht in geometry()


def fit_to_screen(dialog: QWidget) -> None:
    """Touch-Profil (Pi-Plan 4.6): Dialog nie groesser als der Bildschirm. Gilt fuer jeden Dialog
    (TouchSizer, beim Anzeigen) -- das Benutzerhandbuch kam mit 820 x 720 auf 1024 x 600."""
    screen = dialog.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    if QGuiApplication.platformName() == "offscreen":   # Tests/CI: virtuell 800 x 800, Ziel ist das Display
        avail.setSize(QSize(min(avail.width(), TOUCH_SCREEN_MAX[0]), min(avail.height(), TOUCH_SCREEN_MAX[1])))
    max_w, max_h = avail.width(), avail.height() - DIALOG_DECORATION_H
    if isinstance(dialog, QFileDialog):   # Qt-eigener Datei-Dialog (Pi): ganzer Bildschirm statt 654 x 433
        dialog.resize(max_w, max_h)
        return
    size = dialog.size()
    if size.width() > max_w or size.height() > max_h:
        dialog.resize(min(size.width(), max_w), min(size.height(), max_h))


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


def _spin(sub: str = "") -> str:
    """Zahlenfelder mit grossen −/+ Tasten: in Kacheln (QGroupBox) und Dialogen (Y-Achse ...)."""
    return ", ".join(f"{scope} QAbstractSpinBox{sub}" for scope in ("QGroupBox", "QDialog"))


def touch_qss(pal: Palette) -> str:
    return f"""
    QPushButton, QToolButton {{
        padding: 6px 14px;
    }}
    QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
        padding: 3px 8px;
    }}
    /* Grosse −/+ Tasten links und rechts vom Wert statt kleiner Pfeile uebereinander (PI-6) */
    {_spin()} {{
        padding-left: {SPIN_KEY_WIDTH + 4}px;
        padding-right: {SPIN_KEY_WIDTH + 4}px;
        min-width: 150px;
        max-width: 170px;
    }}
    {_spin('::up-button')}, {_spin('::down-button')} {{
        subcontrol-origin: border;
        width: {SPIN_KEY_WIDTH}px;
        height: {TOUCH_H - 2}px;
        border: none;
        background-color: {pal.surface_alt};
    }}
    {_spin('::up-button')} {{
        subcontrol-position: center right;
        border-left: 1px solid {pal.border};
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
    }}
    {_spin('::down-button')} {{
        subcontrol-position: center left;
        border-right: 1px solid {pal.border};
        border-top-left-radius: 4px;
        border-bottom-left-radius: 4px;
    }}
    {_spin('::up-arrow')} {{
        image: url({_key_icon_path("plus", pal)});
        width: 22px;
        height: 22px;
    }}
    {_spin('::down-arrow')} {{
        image: url({_key_icon_path("minus", pal)});
        width: 22px;
        height: 22px;
    }}
    /* Tastenleiste mit einer gewaehlten Taste (Zeitfenster im Verlauf) */
    QPushButton[segment="true"] {{
        padding: 6px 10px;
        border-radius: 0px;
    }}
    QPushButton[segment="true"]:checked {{
        background-color: {pal.accent};
        border-color: {pal.accent};
        color: {pal.on_accent};
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
    /* Datei-Dialog (am Pi der Qt-eigene): Zeilen fingergross statt ~19 px */
    QFileDialog QAbstractItemView::item {{
        padding: 9px 4px;
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
    /* Schmale Positionsanzeige statt 22-px-Leiste: bedient wird per Wischen (GUI-Review UX-8) */
    QScrollBar:vertical {{
        width: {SCROLLBAR_W}px;
        background: transparent;
        border: none;
        margin: 2px 1px;
    }}
    QScrollBar:horizontal {{
        height: {SCROLLBAR_W}px;
        background: transparent;
        border: none;
        margin: 1px 2px;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal,
    QScrollBar[hot="true"]::handle:vertical, QScrollBar[hot="true"]::handle:horizontal {{
        background: {pal.border_strong};
        border: none;
        border-radius: {SCROLLBAR_W // 2}px;
        margin: 0px;   /* die PC-Regel (theme.py, PI-20 b) setzt einen Rand fuer den schmalen Griff */
    }}
    QScrollBar::handle:vertical {{
        min-height: 40px;
    }}
    QScrollBar::handle:horizontal {{
        min-width: 40px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0px;
        height: 0px;
        border: none;
        background: transparent;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: transparent;
    }}
    """


def shell_qss(pal: Palette) -> str:
    """Rahmen der App (Navigation links, Leiste oben) -- am PC und im Touch-Profil gleich (seit 0.53.0,
    Nutzerwunsch 12.11.2026: das Touch-Konzept auch am PC)."""
    return f"""
    /* Haupt-Reiter: die Navigation ersetzt die Reiterleiste, der Rahmen um den Inhalt wirkte mit den
       Kacheln darin wie Rahmen im Rahmen (Nutzerhinweis 11.11.2026) */
    QTabWidget#mainTabs::pane {{
        border: none;
    }}
    QWidget#touchNav {{
        background-color: {pal.surface};
        border-right: 1px solid {pal.border};
    }}
    QWidget#touchNav QToolButton {{
        background-color: transparent;
        color: {pal.text_muted};
        border: none;
        border-radius: {METRICS.radius_md}px;
        padding: 6px 2px;
    }}
    QWidget#touchNav QToolButton:hover {{
        color: {pal.text};
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
    QLabel#statusToast {{
        background-color: {pal.surface_alt};
        color: {pal.text};
        border: 1px solid {pal.border_strong};
        border-radius: {METRICS.radius_md}px;
        padding: 8px 14px;
    }}
    """


def enable_swipe(area: QScrollArea) -> None:
    """Wischen statt Scrollbalken (kinetisches Scrollen) fuer Finger und Maus -- sofort, unabhaengig vom
    TouchSizer (Flaechen, die es nur im Touch-Profil gibt)."""
    QScroller.grabGesture(area.viewport(), QScroller.ScrollerGestureType.LeftMouseButtonGesture)
    setattr(area.viewport(), SWIPE_ATTR, True)


NAV_WIDTH_PC = 104
NAV_ICON_PC = QSize(24, 24)
TOP_BAR_HEIGHT_PC = 42


class NavBar(QWidget):
    """Reiter-Knoepfe links (Symbol + Text); steuert ein QTabWidget mit verborgener Reiterleiste.
    Am PC schmaler (set_touch(False)), im Touch-Profil fingergross."""

    selected = Signal(QWidget)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("touchNav")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._touch = True
        self.setFixedWidth(NAV_WIDTH)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._buttons: dict[int, tuple[QToolButton, QWidget, str, Callable[[], str]]] = {}
        ThemeManager.instance().changed.connect(lambda _pal: self._recolor())

    def set_touch(self, on: bool) -> None:
        self._touch = on
        self.setFixedWidth(NAV_WIDTH if on else NAV_WIDTH_PC)
        for button, *_rest in self._buttons.values():
            self._size_button(button)

    def _size_button(self, button: QToolButton) -> None:
        button.setIconSize(NAV_ICON if self._touch else NAV_ICON_PC)
        button.setMinimumHeight(76 if self._touch else 60)

    def set_page_visible(self, page: QWidget, visible: bool) -> None:
        entry = self._buttons.get(id(page))
        if entry is not None:
            entry[0].setVisible(visible)

    def add_page(self, page: QWidget, icon_name: str, text: Callable[[], str]) -> QToolButton:
        button = QToolButton()
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self._size_button(button)
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
    arrange_toggled = Signal(bool)   # Modus "Anordnen": Kacheln ziehen statt wischen

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
        # Mit Elternteil anlegen: ohne machte set_power_available(True) den Ein/Aus-Knopf zu einem eigenen
        # kleinen Fenster -- auch ohne Touch-Profil, unter Linux mit logind (WSL, Linux-PC; 11.11.2026).
        self._arrange_button = QToolButton(self)
        self._arrange_button.setCheckable(True)
        # Nur Symbol (Nutzerwunsch 12.11.2026): Verschiebe-Kreuz, im Modus "Anordnen" Haken auf Akzentfarbe
        self._arrange_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._arrange_button.setIconSize(QSize(26, 26))
        self._arrange_button.setMinimumSize(48, TOUCH_H)
        self._arrange_button.toggled.connect(self.arrange_toggled)
        self._arrange_button.toggled.connect(lambda _on: (self._recolor(), self.retranslate()))
        self._fullscreen_button = QToolButton(self)
        self._fullscreen_button.setIconSize(QSize(26, 26))
        self._fullscreen_button.setMinimumSize(48, 44)
        self._fullscreen_button.clicked.connect(self.fullscreen_requested)
        self._power_button = QToolButton(self)
        self._power_button.setIconSize(QSize(26, 26))
        self._power_button.setMinimumSize(48, 44)
        self._power_button.clicked.connect(self.power_requested)
        self._power_button.hide()
        self._trailing: list[QWidget] = []
        self.set_fullscreen(False)
        self.retranslate()
        ThemeManager.instance().changed.connect(lambda _pal: self._recolor())

    def adopt(self, status: QWidget, trailing: list[QWidget], leading: QWidget | None = None) -> None:
        """Statusanzeige in den wischbaren Bereich, die uebrigen rechts davon (ALLE AUS zuletzt).
        `leading` steht fest links davor und wischt nicht mit (Sicherheits-Chip, Nutzerwunsch 12.11.2026)."""
        if leading is not None:
            self._layout.insertWidget(0, leading)
        self._status_area.setWidget(status)
        self._trailing = trailing
        for widget in trailing[:-1]:
            self._layout.addWidget(widget)
        self._layout.addWidget(self._arrange_button)
        self._layout.addWidget(self._fullscreen_button)
        self._layout.addWidget(self._power_button)
        if trailing:
            self._layout.addWidget(trailing[-1])

    def release(self) -> QWidget | None:
        """Gibt die Statusanzeige zurueck (die rechten Widgets nimmt der neue Besitzer selbst)."""
        for widget in self._trailing:
            self._layout.removeWidget(widget)
        self._layout.removeWidget(self._arrange_button)
        self._layout.removeWidget(self._fullscreen_button)
        self._layout.removeWidget(self._power_button)
        self._arrange_button.setChecked(False)   # Touch aus: Anordnen endet
        self._trailing = []
        return self._status_area.takeWidget()

    def retranslate(self) -> None:
        from i18n import tr
        self._arrange_button.setToolTip(tr("Fertig") if self._arrange_button.isChecked()
                                        else tr("Kacheln in Übersicht und Steuerung verschieben"))
        # Review KN-16: Tooltips fuer die reinen Symbol-Knoepfe
        self._fullscreen_button.setToolTip(tr("Vollbild beenden") if getattr(self, "_fullscreen", False)
                                           else tr("Vollbild"))
        self._power_button.setToolTip(tr("Ausschalten"))

    def set_touch(self, on: bool) -> None:
        """Am PC flacher und ohne "Anordnen" (dort zieht man Kacheln direkt mit der Maus)."""
        self.setFixedHeight(TOP_BAR_HEIGHT if on else TOP_BAR_HEIGHT_PC)
        margin = 4 if on else 3
        self._layout.setContentsMargins(8, margin, 8, margin)
        if not on:
            self._arrange_button.setChecked(False)
        self._arrange_button.setVisible(on)
        icon = QSize(26, 26) if on else QSize(20, 20)
        for button in (self._fullscreen_button, self._power_button):
            button.setIconSize(icon)
            button.setMinimumSize(48 if on else 34, 44 if on else 30)

    def set_power_available(self, on: bool) -> None:
        self._power_button.setVisible(on)

    def set_fullscreen(self, on: bool) -> None:
        self._fullscreen = on
        self._recolor()
        self.retranslate()

    def _recolor(self) -> None:
        pal = current_palette()
        self._fullscreen_button.setIcon(qta.icon("mdi.fullscreen-exit" if self._fullscreen else "mdi.fullscreen",
                                                 color=pal.text))
        self._power_button.setIcon(qta.icon("mdi.power", color=pal.text))
        arranging = self._arrange_button.isChecked()
        self._arrange_button.setIcon(qta.icon("mdi.check" if arranging else "mdi.cursor-move",
                                              color=pal.surface if arranging else pal.text))
        self._arrange_button.setStyleSheet(
            f"QToolButton {{ background-color: {pal.accent}; color: {pal.on_accent}; font-weight: bold; }}"
            if arranging else "")

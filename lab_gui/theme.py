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

import tempfile
from dataclasses import dataclass
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtWidgets import QApplication, QWidget

# Pfeile fuer die Spin-Buttons (QAbstractSpinBox::up-arrow/down-arrow, siehe
# stylesheet() unten) werden aus qtawesome erzeugt -- wie alle anderen Icons
# im Programm (icons.py) -- statt als statische PNG-Dateien gepflegt zu
# werden. Das macht sie automatisch themefaehig (Farbe folgt pal.text) und
# vermeidet zwei Extra-Assets im Repo/.spec. QSS-url() kann kein QIcon live
# binden, daher wird pro Palette einmalig eine PNG-Datei ins Temp-Verzeichnis
# geschrieben (fluechtige Ableitung, kein Nutzerdaten-Persistenzbedarf wie bei
# settings.json/device_labels.json) und ihr Pfad im QSS referenziert. Qt
# erwartet in url() immer Vorwaertsslashes, auch unter Windows.
_SPIN_ARROW_CACHE: dict[tuple[str, str], str] = {}  # (Palettenname, Richtung) -> Dateipfad
_SPIN_ARROW_SIZE = QSize(28, 28)
_SPIN_ARROW_ICON_NAME = {"up": "mdi.chevron-up", "down": "mdi.chevron-down"}


def _spin_arrow_icon_path(direction: str, pal: "Palette") -> str:
    key = (pal.name, direction)
    cached = _SPIN_ARROW_CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(tempfile.gettempdir()) / f"labcontrol_spin_{direction}_{pal.name.replace(' ', '_')}.png"
    qta.icon(_SPIN_ARROW_ICON_NAME[direction], color=pal.text).pixmap(_SPIN_ARROW_SIZE).save(str(path), "PNG")
    result = path.as_posix()
    _SPIN_ARROW_CACHE[key] = result
    return result


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
    # Individuelle Geraete-Panel-Hintergruende (Dashboard/Control-Tab, siehe
    # panel_color.py) -- PANEL_COLOR_ORDER-Schluessel -> Hex-Farbe, je Theme
    # eigens abgestimmt (helle Toenung in Light, gedeckte Toenung in Dark).
    panel_tints: dict[str, str]


# Interner Farbschluessel -> deutscher Basis-Anzeigename (Uebersetzungsschluessel).
# Reihenfolge bestimmt die Anzeigereihenfolge im Panel-Farbmenue (siehe
# panel_color.PanelColorButton). Bewusst ohne reines Rot/Ampel-Gruen/Amber-Gelb
# -- Verwechslungsgefahr mit Fail/Pass-Zeilen, Sicherheits-Banner bzw. dem
# Amber-Industrial-Theme-Akzent selbst.
PANEL_COLOR_ORDER = ["blue", "teal", "green", "orange", "violet", "pink", "gray"]
PANEL_COLOR_LABELS = {
    "blue": "Blau",
    "teal": "Türkis",
    "green": "Grün",
    "orange": "Orange",
    "violet": "Violett",
    "pink": "Pink",
    "gray": "Grau",
}


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
    panel_tints={
        "blue": "#dbeafe",
        "teal": "#cffafe",
        "green": "#d1fae5",
        "orange": "#ffedd5",
        "violet": "#ede9fe",
        "pink": "#fce7f3",
        "gray": "#e5e7eb",
    },
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
    # Deutlich kraeftiger/heller als surface (#1e2229, Leuchtdichte ~34) statt
    # nur einer abgeschwaechten Variante der Light-Werte -- die urspruengliche
    # erste Fassung lag mit Leuchtdichte ~34-45 so nah an surface, dass die
    # Faerbung im Dark-Theme praktisch nicht auffiel (BUGS.md #10d). Ziel
    # bewusst ~65-83 (deutlich abgesetzt, aber noch kein Neon-/Akzent-Ton, der
    # mit `accent`/`success`/`warning`/`danger` verwechselt werden koennte).
    panel_tints={
        "blue": "#2a4a75",
        "teal": "#1f5f57",
        "green": "#2c6138",
        "orange": "#7a4a1a",
        "violet": "#4f3878",
        "pink": "#7a3559",
        "gray": "#4a5058",
    },
)


def form_control_qss(pal: Palette) -> str:
    """QPushButton-/QLineEdit-/QDoubleSpinBox-/QSpinBox-/QComboBox-Regeln,
    geteilt zwischen dem globalen stylesheet() und panel_color.apply_panel_tint.

    Grund fuer die Auslagerung: Qt liest Style-Sheet-Eigenschaften wie
    background-color entlang der Ancestor-Kette (eigenes Stylesheet des
    Widgets > naechstgelegenes Vorfahren-Stylesheet > qApp-Stylesheet) --
    ein Kind-Widget OHNE eigenes Stylesheet erbt die background-color eines
    Vorfahren-Widgets, SELBST WENN qApp fuer den Kind-Widget-Typ (z.B.
    QPushButton) bereits eine eigene Regel definiert, weil die Ancestor-Ebene
    Vorrang vor der qApp-Ebene hat. Das betraf bislang QGroupBox.
    panel_color.apply_panel_tint(): dessen individuelle Panel-Hintergrundfarbe
    (Instanz-Stylesheet auf der GroupBox) schlug dadurch unbeabsichtigt auf
    Buttons/Eingabefelder im Panel durch (siehe BUGS.md #10f) -- die
    Ausnahme waren die Inkrement-/Dekrement-Pfeile von QAbstractSpinBox, da
    das reine Subcontrols des Spinbox-Styles sind, keine eigenen
    Kind-Widgets, und deshalb von diesem Vererbungsmechanismus nicht
    betroffen sind. Fix: apply_panel_tint setzt dieselben Regeln zusaetzlich
    als eigenes (staerker priorisiertes) Stylesheet auf die Buttons/
    Eingabefelder, sodass sie nicht mehr beim Vorfahren (der GroupBox)
    nachschauen muessen."""
    return f"""
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
    /* QToolButton fehlte hier bisher komplett -- betraf nur SplitIconButton
       (icons.py, Testablauf-Reiter "Zeile hinzufuegen"-Button), der als
       einziger Button im Programm ein QToolButton statt QPushButton ist
       (fuer Qt's MenuButtonPopup-Modus, siehe dortigen Docstring). Ohne
       eigene Regel fiel er komplett auf den nativen Stil zurueck --
       andere Hintergrund-/Rahmenfarbe UND andere (native) Hover-
       Hervorhebung statt pal.accent. Dieselben Werte wie QPushButton, damit
       er sich nicht von den uebrigen Buttons abhebt. */
    QToolButton {{
        background-color: {pal.surface_alt};
        color: {pal.text};
        border: 1px solid {pal.border};
        border-radius: 4px;
        padding: 5px 8px;
        /* Rechtes Padding um die Breite von ::menu-button erweitert (siehe
           dort): Qt zentriert das Icon sonst im GESAMTEN Button inkl.
           Menue-Pfeil-Zone, wodurch es sichtbar aus der Mitte der eigenen
           (linken) Klickflaeche rutscht -- das zusaetzliche Padding gleicht
           das aus, sodass das Icon in seinem eigenen Bereich zentriert
           bleibt (per Screenshot-Vergleich austariert). */
        padding-right: 22px;
    }}
    QToolButton:hover {{
        background-color: {pal.accent};
        color: {pal.surface};
        border-color: {pal.accent};
    }}
    QToolButton:pressed {{
        background-color: {pal.accent_hover};
    }}
    QToolButton:disabled {{
        color: {pal.text_muted};
        background-color: {pal.surface_alt};
        border-color: {pal.border};
    }}
    /* Trennlinie zwischen Icon- und Menue-Pfeil-Klickzone (MenuButtonPopup) --
       ohne das wirkt der Pfeilbereich wie ein nahtloser Teil des Icons statt
       einer eigenen Klickflaeche. Schmaler als der native Default, passend
       zum kleineren Pfeil unten. */
    QToolButton::menu-button {{
        border-left: 1px solid {pal.border};
        width: 14px;
    }}
    /* Ersetzt Qt's natives (recht grobes Dreieck-)Pfeilsymbol durch dasselbe
       schlanke Chevron wie bei den Spinbox-Pfeilen (siehe
       QAbstractSpinBox::down-arrow unten) -- fuegt sich damit optisch in den
       Rest des Programms ein, statt aus dem Rahmen zu fallen. Bei
       QToolButton (MenuButtonPopup) heisst das zustaendige Subcontrol
       "menu-arrow", NICHT "down-arrow" wie bei QComboBox/QAbstractSpinBox --
       per gezieltem Test einzeln ermittelt ("down-arrow"/"menu-indicator"
       blieben wirkungslos, nur "menu-arrow" traf). subcontrol-origin/
       -position muessen explizit gesetzt sein, sonst bleibt das Bild leer. */
    QToolButton::menu-arrow {{
        image: url({_spin_arrow_icon_path("down", pal)});
        width: 10px;
        height: 10px;
        subcontrol-origin: padding;
        subcontrol-position: center;
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
    """


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
    {form_control_qss(pal)}
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
        image: url({_spin_arrow_icon_path("up", pal)});
        width: 14px;
        height: 14px;
        subcontrol-position: center;
    }}
    QAbstractSpinBox::down-arrow {{
        image: url({_spin_arrow_icon_path("down", pal)});
        width: 14px;
        height: 14px;
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

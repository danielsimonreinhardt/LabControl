"""Zentrales Farb-Theme der Labor-App: zwei Paletten (Slate Ops Light /
Slate Ops Dark), die Form-Tokens METRICS plus der QSS-Stylesheet-Aufbau dafuer.

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
from PySide6.QtCore import QEvent, QObject, QSize, Signal
from PySide6.QtWidgets import QApplication, QScrollBar, QWidget

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
    # Dezente Trennlinien (Gruppen, Tabs, Tabellenraster) -- darf unter 3:1 liegen.
    border: str
    # Umrandung bedienbarer Felder (Eingabefelder, Checkbox-Kaestchen): >= 3:1
    # gegen surface (WCAG 1.4.11), damit ein Feld als Feld erkennbar ist.
    border_strong: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    # Schrift-/Symbolfarbe AUF einer accent-/accent_hover-Flaeche
    # (Hover-Button, gewaehlte Segment-Taste) -- >= 4.5:1 gegen beide.
    on_accent: str
    # Statusfarbe als SCHRIFT auf bg/surface (Status-Labels "verbunden",
    # "Aufnahme laeuft" ...). Im Slate-Ops-Theme in beiden Modi gruen.
    success: str
    # Gefuellte "bestanden/EIN"-Flaeche: Pass/Fail-Zeilen im Testablauf,
    # EIN-Taste im Control-Tab, Status-Punkte. Text darauf: on_status.
    check_pass: str
    warning: str
    # Rot -- sowohl als Schrift auf surface als auch als gefuellte Flaeche
    # (Sicherheits-Banner, "Alles aus", FAIL-Zeile; Text darauf: on_status).
    danger: str
    # Schriftfarbe auf gefuellten Statusflaechen (check_pass, danger) --
    # >= 4.5:1 gegen beide; im Dark-Theme deshalb dunkel statt weiss.
    on_status: str
    selection: str
    # Tastaturfokus-Rahmen (:focus) -- >= 3:1 gegen bg und surface.
    focus: str
    plot_bg: str
    plot_grid: str
    plot_signal: str
    plot_ref: str
    # Individuelle Geraete-Panel-Hintergruende (Dashboard/Control-Tab, siehe
    # panel_color.py) -- PANEL_COLOR_ORDER-Schluessel -> Hex-Farbe, je Theme
    # eigens abgestimmt (helle Toenung in Light, gedeckte Toenung in Dark).
    panel_tints: dict[str, str]
    # Ziehen einer Kachel (tile_grid.make_drag_pixmap): Rahmen des schwebenden
    # Abbilds -- muss sich von Kachel- und Panel-Toenungsfarben abheben.
    drag_outline: str


@dataclass(frozen=True)
class Metrics:
    """Theme-unabhaengige Form-Tokens (Radien, Abstaende, Fokusrahmen) --
    gemeinsam fuer beide Paletten, im QSS und in Instanz-Stylesheets genutzt."""
    radius_sm: int = 4      # Buttons, Eingabefelder, Banner
    radius_md: int = 6      # Gruppen, Tab-Flaeche, Kacheln
    radius_check: int = 3   # Checkbox-Kaestchen
    pad_button: str = "5px 12px"
    pad_field: str = "3px 6px"
    focus_width: int = 2


METRICS = Metrics()


# Interner Farbschluessel -> deutscher Basis-Anzeigename (Uebersetzungsschluessel).
# Reihenfolge bestimmt die Anzeigereihenfolge im Panel-Farbmenue (siehe
# panel_color.PanelColorButton). Bewusst ohne reines Rot/Ampel-Gruen/Amber-Gelb
# -- Verwechslungsgefahr mit Fail/Pass-Zeilen bzw. dem Sicherheits-Banner.
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


# "Slate Ops" (ui-ux-pro-max, Kategorie IoT-/Ops-Dashboard): Schiefergrau mit
# Himmelblau als Akzent. Gruen ist bewusst NICHT Akzent (anders als der
# Vorschlag des Skills), sondern bleibt fuer "bestanden/EIN" reserviert --
# sonst waere eine Akzent-Taste nicht von einer bestandenen Pruefung zu
# unterscheiden. Alle Paare per WCAG-Kontrast nachgerechnet (Text >= 4.5:1,
# Feldrahmen/Fokus >= 3:1), siehe CHANGELOG v0.38.0.
LIGHT = Palette(
    name="Slate Ops Light",
    bg="#f8fafc",
    surface="#ffffff",
    surface_alt="#f1f5f9",
    border="#cbd5e1",
    border_strong="#7d8ba0",
    text="#0f172a",
    text_muted="#475569",
    accent="#0369a1",
    accent_hover="#075985",
    on_accent="#ffffff",
    success="#15803d",
    check_pass="#15803d",
    warning="#b45309",
    danger="#b91c1c",
    on_status="#ffffff",
    selection="#e0f2fe",
    focus="#0369a1",
    # Diagramm-Hintergrund folgt der hellen UI (siehe BUGS.md #9).
    plot_bg="#ffffff",
    plot_grid="#cbd5e1",
    plot_signal="#0284c7",
    plot_ref="#d97706",
    panel_tints={
        "blue": "#dbeafe",
        "teal": "#cffafe",
        "green": "#d1fae5",
        "orange": "#ffedd5",
        "violet": "#ede9fe",
        "pink": "#fce7f3",
        "gray": "#e2e8f0",
    },
    drag_outline="#0369a1",
)

DARK = Palette(
    name="Slate Ops Dark",
    bg="#0f172a",
    surface="#1b2336",
    surface_alt="#272f42",
    border="#475569",
    border_strong="#6b7a90",
    text="#f8fafc",
    text_muted="#b0bccc",
    accent="#38bdf8",
    accent_hover="#7dd3fc",
    on_accent="#0f172a",
    success="#22c55e",
    check_pass="#22c55e",
    warning="#fbbf24",
    danger="#f87171",
    on_status="#0f172a",
    selection="#0c4a6e",
    focus="#7dd3fc",
    plot_bg="#0b1120",
    plot_grid="#334155",
    plot_signal="#38bdf8",
    plot_ref="#fbbf24",
    # Deutlich heller als surface (BUGS.md #10d), aber kein Neon-/Statuston,
    # und dunkel genug, dass auch text_muted darauf >= 4.5:1 erreicht.
    panel_tints={
        "blue": "#2a4a75",
        "teal": "#1b544d",
        "green": "#265431",
        "orange": "#6b4117",
        "violet": "#4f3878",
        "pink": "#763356",
        "gray": "#404c5e",
    },
    drag_outline="#7dd3fc",
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
    M = METRICS
    return f"""
    QPushButton {{
        background-color: {pal.surface_alt};
        color: {pal.text};
        border: 1px solid {pal.border};
        border-radius: {M.radius_sm}px;
        padding: {M.pad_button};
    }}
    /* Tastaturfokus (Tab-Taste): Fokusfarbe als Rahmen plus Auswahl-Toenung,
       ohne Rahmenbreite/Padding zu aendern (kein Springen des Layouts).
       Steht VOR :hover, damit ein Hover auf einem fokussierten Knopf gewinnt. */
    QPushButton:focus {{
        border-color: {pal.focus};
        background-color: {pal.selection};
    }}
    QPushButton:hover {{
        background-color: {pal.accent};
        color: {pal.on_accent};
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
        border-radius: {M.radius_sm}px;
        padding: 5px 8px;
        /* Rechtes Padding um die Breite von ::menu-button erweitert (siehe
           dort): Qt zentriert das Icon sonst im GESAMTEN Button inkl.
           Menue-Pfeil-Zone, wodurch es sichtbar aus der Mitte der eigenen
           (linken) Klickflaeche rutscht -- das zusaetzliche Padding gleicht
           das aus, sodass das Icon in seinem eigenen Bereich zentriert
           bleibt (per Screenshot-Vergleich austariert). */
        padding-right: 22px;
    }}
    /* Tastaturfokus (Tab-Taste): Fokusfarbe als Rahmen plus Auswahl-Toenung,
       ohne Rahmenbreite/Padding zu aendern (kein Springen des Layouts).
       Steht VOR :hover, damit ein Hover auf einem fokussierten Knopf gewinnt. */
    QToolButton:focus {{
        border-color: {pal.focus};
        background-color: {pal.selection};
    }}
    QToolButton:hover {{
        background-color: {pal.accent};
        color: {pal.on_accent};
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
        border: 1px solid {pal.border_strong};
        border-radius: {M.radius_sm}px;
        padding: {M.pad_field};
    }}
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{
        border-color: {pal.focus};
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
    M = METRICS
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
        border-radius: {M.radius_md}px;
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
        border-radius: {M.radius_md}px;
        background-color: {pal.surface};
    }}
    QTabBar::tab {{
        background-color: {pal.surface_alt};
        color: {pal.text_muted};
        padding: 6px 16px;
        border: 1px solid {pal.border};
        border-bottom: none;
        border-top-left-radius: {M.radius_md}px;
        border-top-right-radius: {M.radius_md}px;
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
        border-top-right-radius: {M.radius_sm}px;
        border-bottom: 1px solid {pal.border};
    }}
    QAbstractSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: {M.radius_sm}px;
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
        border: 1px solid {pal.border_strong};
        border-radius: {M.radius_check}px;
        background-color: {pal.surface};
    }}
    QCheckBox::indicator:checked {{
        background-color: {pal.accent};
        border-color: {pal.accent};
    }}
    QCheckBox::indicator:focus {{
        border-color: {pal.focus};
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
    /* Schmale Scrollleisten, beim Ueberfahren breit zum Greifen (PI-20 b, 11.11.2026): die Leiste bleibt
       12 px breit, nur der Griff waechst von 6 px auf die volle Breite -- der Inhalt daneben springt nicht.
       "hot" setzt ScrollbarHover beim Betreten/Verlassen (QSS :hover erreicht den Griff nicht, geprueft). */
    QScrollBar:vertical {{
        width: 12px;
        background: transparent;
        border: none;
    }}
    QScrollBar:horizontal {{
        height: 12px;
        background: transparent;
        border: none;
    }}
    QScrollBar[hot="true"] {{
        background: {pal.surface_alt};
    }}
    QScrollBar::handle:vertical {{
        background: {pal.border_strong};
        border-radius: 3px;
        min-height: 32px;
        margin: 2px 3px;
    }}
    QScrollBar::handle:horizontal {{
        background: {pal.border_strong};
        border-radius: 3px;
        min-width: 32px;
        margin: 3px 2px;
    }}
    QScrollBar[hot="true"]::handle:vertical {{
        margin: 2px 1px;
        border-radius: 5px;
    }}
    QScrollBar[hot="true"]::handle:horizontal {{
        margin: 1px 2px;
        border-radius: 5px;
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
    /* Hinweistexte (setProperty("role", "hint")) -- ueber diese globale Regel
       statt per Instanz-Stylesheet, damit sie beim Live-Umschalten Hell/Dunkel
       mitwechseln. */
    QLabel[role="hint"] {{
        color: {pal.text_muted};
    }}
    /* Status-Texte mit wechselnder Bedeutung (set_tone) -- ebenfalls global,
       damit sie beim Live-Umschalten Hell/Dunkel mitwechseln. */
    QLabel[tone="muted"] {{ color: {pal.text_muted}; }}
    QLabel[tone="success"] {{ color: {pal.success}; }}
    QLabel[tone="warning"] {{ color: {pal.warning}; }}
    QLabel[tone="danger"] {{ color: {pal.danger}; }}
    QLabel[strong="true"] {{ font-weight: bold; }}
    QFrame[role="separator"] {{
        background-color: {pal.border};
    }}
    QStatusBar {{
        background-color: {pal.surface};
        border-top: 1px solid {pal.border};
    }}
    """


class ScrollbarHover(QObject):
    """Setzt beim Betreten/Verlassen eines Scrollbalkens die Eigenschaft "hot" (QSS: Griff voll breit, PI-20 b).
    Nur am PC installiert; im Touch-Profil ist die Leiste reine Positionsanzeige (touch_ui, UX-8)."""

    _TYPES = (QEvent.Type.Enter, QEvent.Type.Leave)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() in self._TYPES and isinstance(obj, QScrollBar):
            hot = event.type() == QEvent.Type.Enter
            if obj.property("hot") != hot:
                obj.setProperty("hot", hot)
                obj.style().unpolish(obj)
                obj.style().polish(obj)
                obj.update()
        return False


class ThemeManager(QObject):
    """Singleton: haelt die aktuell aktive Palette und benachrichtigt Widgets,
    die auf einen Theme-Wechsel live reagieren muessen (siehe Modul-Docstring)."""

    changed = Signal(Palette)

    _instance: "ThemeManager | None" = None

    def __init__(self) -> None:
        super().__init__()
        self._palette = LIGHT
        self._touch = False
        self._scroll_hover = ScrollbarHover(self)
        self._scroll_hover_on = False

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def palette(self) -> Palette:
        return self._palette

    @property
    def touch(self) -> bool:
        return self._touch

    def apply(self, dark: bool) -> None:
        self._palette = DARK if dark else LIGHT
        self._restyle()
        self.changed.emit(self._palette)

    def set_touch(self, on: bool) -> None:
        """Touch-Profil: groessere Bedienelemente (touch_ui.touch_qss) zusaetzlich zum Theme."""
        if on == self._touch:
            return
        self._touch = on
        self._restyle()
        self.changed.emit(self._palette)

    def _restyle(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        qss = stylesheet(self._palette)
        if self._touch:
            from touch_ui import touch_qss   # lokal: touch_ui importiert theme
            qss += touch_qss(self._palette)
        app.setStyleSheet(qss)
        want_hover = not self._touch
        if want_hover != self._scroll_hover_on:
            if want_hover:
                app.installEventFilter(self._scroll_hover)
            else:
                app.removeEventFilter(self._scroll_hover)
            self._scroll_hover_on = want_hover


def set_tone(label: QWidget, tone: str, strong: bool = False) -> None:
    """Faerbt einen Status-Text ueber die globalen QLabel[tone=...]-Regeln
    (muted/success/warning/danger) statt per Instanz-Stylesheet -- so folgt
    er einem spaeteren Theme-Wechsel ohne eigenen Neu-Aufruf. Qt wertet
    Property-Selektoren nur beim Polieren aus, daher unpolish/polish."""
    label.setProperty("tone", tone)
    label.setProperty("strong", strong)
    label.style().unpolish(label)
    label.style().polish(label)


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

"""Dashboard mit aktuellen Messwerten aller verbundenen Geraete.

Zeigt pro verbundener Geraete-Instanz ein Panel fester Breite. Ein Panel
erscheint erst, wenn das zugehoerige Geraet tatsaechlich verbunden ist, und
verschwindet wieder, sobald es getrennt wird -- bleibt aber (versteckt) im
Speicher, damit ein Wiederverbinden ohne Zustandsverlust/Flackern moeglich
ist. Bei mehreren baugleichen Geraeten (z.B. zwei Netzteilen) bekommt jedes
sein eigenes Panel mit eindeutigem, umbenennbarem Label.
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QEasingCurve, QEvent, QMimeData, QPropertyAnimation, QSize, Qt, Signal, Slot
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from i18n import Translator, tr
from icons import IconButton
from microhil_panel import MicroHilPanel
from no_device_tile import OFFLINE_BACKGROUND, OFFLINE_BORDER, OFFLINE_TEXT, NoDeviceTile
from picoscope2000.driver import launch_app as launch_picoscope_app
from picoscope_panel import PicoscopePanel
from theme import Palette, ThemeManager, no_own_background
from theme import current as current_palette
from tile_grid import (
    PANEL_DRAG_HANDLE_HEIGHT,
    PANEL_DRAG_MIME_TYPE,
    cell_size_ratchet,
    make_drag_pixmap,
    pack_tiles_by_row,
)

VALUE_STYLE = "font-size: 20px; font-weight: bold;"
COMPACT_VALUE_STYLE = "font-size: 16px; font-weight: bold;"
COMPACT_ICON_SIZE = 18
KIND_ICON_SIZE = 26
# Qt-Konstante QWIDGETSIZE_MAX (in PySide6 nicht exportiert) -- hebt das
# setFixedWidth der Normalansicht im Kompaktmodus wieder auf.
_WIDGET_SIZE_MAX = 16777215
# Zusätzlicher Platz für die Scrollleiste am unteren Rand (falls horizontal
# gescrollt werden muss) sowie den Rahmen der ScrollArea.
SCROLL_AREA_MARGIN = 24
# Abstand zwischen den Kacheln im Raster (siehe DashboardWidget._relayout_panels).
GRID_SPACING = 12

# kind -> Kachelhoehe (FEATURES.md Punkt 5): nur microHIL (deutlich mehr
# Inhalt als die uebrigen Panels, siehe microhil_panel.py) bekommt die
# doppelte Hoehe, alles andere (auch "picoscope" -- laut picoscope_panel.py
# nur Statuszeile + Button, also kurz) bleibt einfach.
TILE_HEIGHT_BY_KIND: dict[str, str] = {"hil": "double"}

# field_key -> (deutscher Basis-Anzeigename, Einheit); Einheit ist
# sprachunabhaengig und wird nicht ueber i18n.tr uebersetzt.
FIELD_DEFS: dict[str, tuple[str, str]] = {
    "voltage": ("Spannung", "V"),
    "current": ("Strom", "A"),
    "power": ("Leistung", "W"),
    "mode": ("Modus", ""),
    "tx_count": ("Gesendet", ""),
    "rx_count": ("Empfangen", ""),
}
LOAD_FIELD_KEYS = ["voltage", "current", "power", "mode"]
PSU_FIELD_KEYS = ["voltage", "current", "mode"]
CAN_FIELD_KEYS = ["tx_count", "rx_count"]
# Last-Funktionscode -> kompakte Anzeige. get_function() liefert auf echter
# Hardware bereits die Kurzform (CC/CV/CR/CW, siehe korad_kel102/README.md
# "Bekannte Eigenheiten"), MockKoradKEL102 dagegen den SET-Code aus
# korad_kel102.driver.FUNCTIONS (CURR/VOLT/RES/POW) -- beide Formate werden
# hier auf dieselbe Anzeige gemappt.
LOAD_MODE_SHORT: dict[str, str] = {
    "CURR": "CC", "CC": "CC",
    "VOLT": "CV", "CV": "CV",
    "RES": "CR", "CR": "CR",
    "POW": "CW", "CW": "CW",
    "SHORT": "SHORT",
}
KIND_TITLE = {"load": "Elektronische Last", "psu": "Labornetzteil", "can": "CAN-Bus"}
# Ersetzt die bisherige Geraeteart-Textzeile im Normal-Panel: platzsparendes
# Icon unten rechts im Panel statt einer eigenen Zeile, voller Name als
# Tooltip (siehe KIND_TITLE) weiterhin erreichbar.
KIND_ICON = {"load": "mdi.resistor", "psu": "mdi.power-plug-outline", "can": "mdi.chip"}

# "Verbindung getrennt"-Badge oben rechts im Panel, siehe
# _DevicePanel.set_online -- Position wird per resizeEvent nachgefuehrt, da
# die Panel-Breite sich nachtraeglich angleicht (siehe DashboardWidget.
# _relayout_panels). Netzwerk-Trennsymbol statt eines Stecker-Icons -- passt
# sowohl fuer die Last als auch fuers Netzteil (kein Bezug zu "Stecker"
# speziell). mdi.lan-disconnect (zwei Geraete-Rechtecke + Verbindungslinie +
# X) war bei der bisherigen Badge-Groesse zu detailreich, um noch erkennbar
# zu sein (siehe Bugmeldung) -- close-network-outline (ein Geraete-Symbol
# mit X, klare Silhouette) bleibt bei kleiner Groesse deutlich lesbar.
OFFLINE_ICON_NAME = "mdi.close-network-outline"
OFFLINE_ICON_SIZE = 22
OFFLINE_ICON_MARGIN = 5

# Icons fuer die Kompaktansicht: dort ersetzen sie die Text-Beschriftung der
# Messgroessen komplett (der volle Name bleibt als Tooltip erreichbar).
FIELD_ICONS: dict[str, str] = {
    "voltage": "mdi.flash-outline",
    "current": "mdi.current-dc",
    "power": "mdi.gauge",
    "mode": "mdi.swap-horizontal-bold",
    "tx_count": "mdi.upload-outline",
    "rx_count": "mdi.download-outline",
}


def _field_display(field_key: str) -> str:
    name, unit = FIELD_DEFS[field_key]
    return f"{tr(name)} ({unit})" if unit else tr(name)


class _DevicePanel(QGroupBox):
    def __init__(self, kind: str, device_id: str, label: str, field_keys: list[str]) -> None:
        super().__init__()
        self._kind = kind
        self._device_id = device_id
        self._field_keys = field_keys
        self._color_key: str | None = None
        # Bleibt (anders als frueher) auch nach dem Trennen als Panel stehen,
        # nur ausgegraut statt versteckt -- siehe set_online(). True ist der
        # Startwert, da _set_online(True) unmittelbar nach dem Erzeugen des
        # Panels folgt (siehe DashboardWidget.on_device_known/_set_online).
        self._online = True
        # Feste Breite wird nicht hier, sondern zentral von DashboardWidget
        # gesetzt (siehe _relayout_panels) -- Last- und Netzteil-Panels
        # brauchen unterschiedlich viel Platz (z.B. 3 statt 2 Nachkommastellen),
        # ein hier fest verdrahteter Wert wuerde bei laengeren Werten/Labels
        # (andere Sprache, groessere Schrift) abgeschnitten.
        # Geraetename als natives QGroupBox-Title (wie DashboardWidget selbst
        # -- "Dashboard" sitzt genauso auf dem oberen Rahmen), statt als
        # eigenes QLabel im Panel-Inneren.
        self.setTitle(label)

        outer = QVBoxLayout(self)
        self._outer = outer

        # Normalansicht: nur noch das Formular mit den Messwerten -- Name
        # steht im Rahmentitel (siehe oben), Umbenennen-Button und Geraeteart-
        # Icon sitzen platzsparend IN der ersten/letzten Werte-Zeile statt in
        # eigenen Zeilen (siehe Schleife unten). Als eigenes Widget gebuendelt,
        # damit die Kompaktansicht es mit einem einzigen hide() ausblenden kann.
        self._normal_widget = no_own_background(QWidget())
        normal = QVBoxLayout(self._normal_widget)
        normal.setContentsMargins(0, 0, 0, 0)
        ThemeManager.instance().changed.connect(self._on_theme_changed)

        self._kind_icon = QLabel()
        self._kind_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_kind_icon(current_palette())

        self._form = QFormLayout()
        self._value_labels: dict[str, QLabel] = {}
        self._value_rows: dict[str, QWidget] = {}
        for i, field_key in enumerate(field_keys):
            value_label = QLabel("--")
            value_label.setStyleSheet(VALUE_STYLE)
            self._value_labels[field_key] = value_label

            row_widget = no_own_background(QWidget())
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(value_label)
            row_layout.addStretch()
            if i == len(field_keys) - 1:
                # Geraeteart-Icon unten rechts, neben der letzten Werteanzeige
                # -- spart die eigene Fusszeile.
                row_layout.addSpacing(6)
                row_layout.addWidget(self._kind_icon)
            self._value_rows[field_key] = row_widget
            self._form.addRow(" ", row_widget)
        normal.addLayout(self._form)

        outer.addWidget(self._normal_widget)

        # Kompaktansicht: eine einzige Zeile -- je Messgroesse Icon + Wert (mit
        # Einheit) statt Text-Beschriftung; der Geraetename steht bereits im
        # Rahmentitel (siehe oben), braucht hier also keine eigene Zeile mehr.
        # Der Name der Messgroesse bleibt als Tooltip auf Icon und Wert
        # erreichbar.
        self._compact_widget = no_own_background(QWidget())
        compact = QHBoxLayout(self._compact_widget)
        compact.setContentsMargins(0, 0, 0, 0)
        self._compact_icons: dict[str, QLabel] = {}
        self._compact_values: dict[str, QLabel] = {}
        for field_key in field_keys:
            icon_label = QLabel()
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_label = QLabel("--")
            value_label.setStyleSheet(COMPACT_VALUE_STYLE)
            self._compact_icons[field_key] = icon_label
            self._compact_values[field_key] = value_label
            compact.addWidget(icon_label)
            compact.addWidget(value_label)
            compact.addSpacing(10)
        # Haelt den Inhalt links gepackt, wenn das Panel (per Breiten-Ratsche,
        # siehe DashboardWidget._relayout_panels) breiter ist als sein Inhalt.
        compact.addStretch()
        outer.addWidget(self._compact_widget)
        self._compact_widget.hide()
        self._apply_compact_icons(current_palette())

        # "Verbindung getrennt"-Badge: eigenes Kind-Widget mit absoluter
        # Position (statt im Layout) statt eigener Zeile -- so sitzt es
        # wirklich in der Panel-Ecke, unabhaengig von Normal-/Kompaktansicht.
        # Feste Farbe (OFFLINE_TEXT) statt Theme-Farbe, da es nur auf dem
        # ebenfalls fest grauen "getrennt"-Hintergrund erscheint (siehe
        # set_online/_apply_style) und dort in beiden Themes gleich lesbar
        # bleiben soll. no_own_background() ist hier PFLICHT, nicht nur
        # Kosmetik: als direktes Kind-Widget DIESER QGroupBox (nicht ueber
        # einen no_own_background()-Wrapper wie die uebrigen Labels, siehe
        # oben) erbt es sonst die globale "QWidget{background-color:pal.bg}"-
        # Regel aus theme.stylesheet() und malt ein opakes Quadrat in der
        # Seitenhintergrundfarbe -- das Icon-Pixmap war dahinter komplett
        # unsichtbar (in Amber Dark quasi ein schwarzes Quadrat, per
        # Screenshot bestaetigt).
        self._offline_icon = no_own_background(QLabel(self))
        self._offline_icon.setFixedSize(OFFLINE_ICON_SIZE, OFFLINE_ICON_SIZE)
        self._offline_icon.setPixmap(
            qta.icon(OFFLINE_ICON_NAME, color=OFFLINE_TEXT).pixmap(OFFLINE_ICON_SIZE, OFFLINE_ICON_SIZE)
        )
        self._offline_icon.hide()
        self._offline_icon.raise_()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()
        self._apply_style(current_palette())

    def _retranslate(self) -> None:
        self._kind_icon.setToolTip(tr(KIND_TITLE.get(self._kind, self._kind)))
        self._offline_icon.setToolTip(tr("Verbindung getrennt"))
        for field_key in self._field_keys:
            self._form.labelForField(self._value_rows[field_key]).setText(_field_display(field_key) + ":")
            tooltip = _field_display(field_key)
            self._compact_icons[field_key].setToolTip(tooltip)
            self._compact_values[field_key].setToolTip(tooltip)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._position_offline_icon()

    def _position_offline_icon(self) -> None:
        self._offline_icon.move(
            self.width() - OFFLINE_ICON_SIZE - OFFLINE_ICON_MARGIN, OFFLINE_ICON_MARGIN
        )

    def _on_theme_changed(self, palette: Palette) -> None:
        self._apply_compact_icons(palette)
        self._apply_kind_icon(palette)
        self._apply_style(palette)

    def set_panel_color(self, color_key: str | None) -> None:
        """Wird von aussen aufgerufen, um eine (im Control-Tab gewaehlte)
        Panel-Farbe hier nur ANZUZEIGEN -- die Auswahl selbst findet gemaess
        BUGS.md #10b nur noch im Control-Tab statt (siehe control_tab.py:
        LoadControlGroup/PsuControlGroup)."""
        self._color_key = color_key
        self._apply_style(current_palette())

    def _apply_style(self, palette: Palette) -> None:
        """Wie panel_color.apply_panel_tint (individuelle Panel-Farbe als
        Instanz-Stylesheet), ERGAENZT hier aber immer um einen sichtbaren
        Rahmen -- unabhaengig davon, ob eine individuelle Farbe gesetzt ist.

        Grund: DashboardWidget._container hat seit dem Bug-8-Fix (siehe
        theme.no_own_background) dieselbe Flaechenfarbe (pal.surface) wie
        dieses Panel selbst -- ohne Farbunterschied faellt der normale, sehr
        helle QGroupBox-Rahmen (pal.border, siehe theme.stylesheet) zwischen
        Panels kaum noch auf (BUGS.md #10a). pal.text_muted ist deutlich
        praesenter und dient bereits andernorts als "gedaempfter, aber gut
        lesbarer" Grauton (Subtitle-Labels etc.), daher hier wiederverwendet
        statt eines neuen Palettenwerts."""
        if not self._online:
            # Fest grau -- siehe no_device_tile.py (dieselben Farben, gleicher
            # "kein aktives Geraet"-Zustand) statt einer aus palette
            # abgeleiteten oder der individuellen Panel-Farbe (_color_key
            # wird hier bewusst ignoriert, kommt beim naechsten Online-Gehen
            # ueber _apply_style wieder zum Zug). Der Rahmentitel (Geraete-
            # name) bekommt HIER bewusst KEINE eigene Farbregel -- er faellt
            # damit auf die globale "QGroupBox::title { color: pal.text }"-
            # Regel aus theme.stylesheet() zurueck, die in beiden Themes auf
            # dem Panel-Hintergrund gut lesbar ist (dieselbe Regel, die auch
            # die normalen Online-Panels ohne Probleme nutzen). Eine fest
            # verdrahtete Titel-Farbe (fruehere Fassung: OFFLINE_TEXT, dunkel)
            # war im Amber-Dark-Theme praktisch unlesbar (Screenshot-Bug).
            self.setStyleSheet(
                f"QGroupBox {{ background-color: {OFFLINE_BACKGROUND}; "
                f"border: 1px solid {OFFLINE_BORDER}; border-radius: 6px; }}"
            )
            return
        border_rule = f"border: 1px solid {palette.text_muted}; border-radius: 6px;"
        if self._color_key is None:
            self.setStyleSheet(f"QGroupBox {{ {border_rule} }}")
            return
        hex_color = palette.panel_tints.get(self._color_key)
        bg_rule = f"background-color: {hex_color};" if hex_color else ""
        self.setStyleSheet(f"QGroupBox {{ {border_rule} {bg_rule} }}")

    def set_online(self, online: bool) -> None:
        """Verbindungsstatus-Wechsel (siehe DashboardWidget._set_online).

        Anders als frueher wird das Panel beim Trennen NICHT mehr versteckt,
        sondern bleibt sichtbar und wird nur ausgegraut (fest grau, siehe
        _apply_style) -- der Nutzer soll auf einen Blick sehen, welches
        zuletzt bekannte Geraet gerade fehlt, statt dass die Kachel spurlos
        verschwindet."""
        self._online = online
        if not online:
            self.clear_values()
        self._offline_icon.setVisible(not online)
        self.setVisible(True)
        self._apply_style(current_palette())

    def _apply_compact_icons(self, palette: Palette) -> None:
        for field_key, icon_label in self._compact_icons.items():
            icon_label.setPixmap(
                qta.icon(FIELD_ICONS[field_key], color=palette.text).pixmap(
                    COMPACT_ICON_SIZE, COMPACT_ICON_SIZE
                )
            )

    def _apply_kind_icon(self, palette: Palette) -> None:
        icon_name = KIND_ICON.get(self._kind)
        if icon_name is None:
            return
        self._kind_icon.setPixmap(
            qta.icon(icon_name, color=palette.text_muted).pixmap(KIND_ICON_SIZE, KIND_ICON_SIZE)
        )

    def set_compact(self, compact: bool) -> None:
        self._normal_widget.setVisible(not compact)
        self._compact_widget.setVisible(compact)
        if compact:
            self._outer.setContentsMargins(8, 2, 8, 4)
            # Feste Breite aufheben: die Kompaktzeile schmiegt sich an ihren
            # eigenen Inhalt an, statt die (fuer die Normalansicht gedachte)
            # angeglichene Breite zu behalten.
            self.setMinimumWidth(0)
            self.setMaximumWidth(_WIDGET_SIZE_MAX)
        else:
            # Feste Breite wird nicht hier gesetzt, sondern gleich danach von
            # DashboardWidget.set_compact() ueber _relayout_panels() -- die
            # kennt (anders als ein einzelnes Panel) die Breitenanforderung
            # aller Panels und kann sie angleichen.
            self._outer.unsetContentsMargins()

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_value(self, field_key: str, text: str) -> None:
        self._value_labels[field_key].setText(text)
        unit = FIELD_DEFS[field_key][1]
        self._compact_values[field_key].setText(f"{text} {unit}" if unit else text)

    def clear_values(self) -> None:
        for value_label in self._value_labels.values():
            value_label.setText("--")
        for value_label in self._compact_values.values():
            value_label.setText("--")


class DashboardWidget(QGroupBox):
    # Klick auf den Ansicht-Umschalter unten rechts; MainWindow verdrahtet ihn
    # mit der persistierten Einstellung, die dann set_compact zurueckruft.
    compact_toggle_requested = Signal()
    # Neue Kachel-Reihenfolge nach einem Drag&Drop (Liste von device_ids,
    # links nach rechts) -- MainWindow verdrahtet das mit Settings.
    # set_panel_order() zur Persistenz, analog zu panel_color_requested.
    panel_order_changed = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        # Vertikal Fixed: die Hoehe ergibt sich vollstaendig aus der fest
        # gesetzten ScrollArea-Hoehe (_relayout_panels). Ohne das verteilt
        # das Eltern-Layout ueberschuessige Fensterhoehe auch auf das Dashboard
        # (Preferred darf wachsen) -- die fixierte Scroll-Flaeche schwebt dann
        # mittig in einem viel zu hohen Rahmen, besonders sichtbar in der
        # Kompaktansicht.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        self._container = QWidget()
        # Raster mit max. 2 Zeilen (einfache/doppelte Kachelhoehe, siehe
        # TILE_HEIGHT_BY_KIND/_relayout_panels) -- waechst nach rechts, kein
        # Spalten-Stretch-Faktor gesetzt, damit die natuerliche Breite gleich
        # der Summe der belegten Spalten bleibt (fuer das horizontale
        # Scrollen der umschliessenden ScrollArea, siehe unten).
        self._panel_layout = QGridLayout(self._container)
        self._panel_layout.setSpacing(GRID_SPACING)
        self._panel_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Platzhalter, solange kein Geraet verbunden ist (siehe
        # _update_empty_tile) -- von Anfang an sichtbar, da beim Start noch
        # kein Panel existiert.
        self._empty_tile = NoDeviceTile()
        self._panel_layout.addWidget(self._empty_tile, 0, 0)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setWidget(self._container)
        ThemeManager.instance().changed.connect(self._style_scroll_area)
        self._style_scroll_area(current_palette())
        # Waechst/schrumpft der Panel-Inhalt nachtraeglich (laengere Messwert-
        # Texte, Theme-/Sprachwechsel, Ansichtsumschaltung), loest das ein
        # LayoutRequest im Container aus -- Panel-Breiten und die feste Hoehe
        # der ScrollArea muessen dann nachgezogen werden, sonst bleiben sie
        # veraltet und schneiden Panels ab (siehe _relayout_panels).
        self._container.installEventFilter(self)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # ScrollArea und Ansicht-Umschalter teilen sich eine Zeile: der Button
        # sitzt unten rechts im Dashboard-Bereich, ohne eigene Zeile und damit
        # ohne zusaetzliche vertikale Hoehe.
        body = QHBoxLayout()
        body.addWidget(self._scroll_area, 1)
        corner = QVBoxLayout()
        corner.addStretch()
        self._view_toggle_button = IconButton("mdi.arrow-collapse-vertical", "")
        self._view_toggle_button.setFixedSize(QSize(28, 24))
        self._view_toggle_button.clicked.connect(self.compact_toggle_requested)
        corner.addWidget(self._view_toggle_button)
        body.addLayout(corner)
        outer.addLayout(body)

        self._panels: dict[str, _DevicePanel] = {}
        # kind je device_id -- fuer die Kachelhoehe (siehe TILE_HEIGHT_BY_KIND/
        # _tile_span), da _DevicePanel zwar ein eigenes self._kind hat,
        # MicroHilPanel/PicoscopePanel aber nicht (eigenstaendige Panel-
        # Klassen ohne das generische FIELD_DEFS-Schema, siehe deren
        # Modul-Docstrings).
        self._panel_kind: dict[str, str] = {}
        # Rohe (gespeicherte) Panel-Farbwahl je Geraet -- unabhaengig vom
        # An/Aus-Schalter (siehe set_panel_colors_enabled), damit eine
        # deaktivierte Auswahl beim Wieder-Aktivieren erhalten bleibt.
        self._panel_colors: dict[str, str | None] = {}
        self._colors_enabled = False
        self._compact = False
        # Aktuell angeglichene Panel-Breite (0 = noch keine gesetzt) -- als
        # Ratsche gefuehrt, siehe _relayout_panels. In der Kompaktansicht
        # stattdessen eine Ratsche je Panel (device_id -> Breite), weil die
        # Panels dort bewusst unterschiedlich breit sind.
        self._panel_width = 0
        self._compact_widths: dict[str, int] = {}
        # Basis-Zellhoehe (einfache Hoehe), ebenfalls als Ratsche (siehe
        # TILE_HEIGHT_BY_KIND/_relayout_panels) -- doppelte Kacheln
        # bekommen 2*_cell_height + Spacing.
        self._cell_height = 0

        # Drag & Drop (Kachel-Reihenfolge, siehe eventFilter/_start_panel_
        # drag/_drop_panel weiter unten). _container ist das Drop-Ziel (dort
        # sitzen alle Panels nebeneinander in _panel_layout), einzelne Panels
        # sind die Drag-Quellen -- beide werden ueber installEventFilter(self)
        # bedient statt eigener Subklassen, dieselbe Technik wie schon fuer
        # das LayoutRequest-Handling des Containers.
        self._container.setAcceptDrops(True)
        self._drag_device_id: str | None = None
        self._drag_start_pos = None
        # Waehrend eines laufenden Drags ausgeblendete Kachel (siehe
        # _start_panel_drag) -- bleibt bekannt, damit _drop_panel sie am Ende
        # wieder einblenden kann und ein abgebrochener Drag (kein Drop) sie
        # ebenfalls zurueckbekommt.
        self._drag_hidden_panel: QWidget | None = None
        # Zuletzt live vorgeschlagene Reihenfolge waehrend DragMove (siehe
        # _preview_panel_drag) -- verhindert unnoetige Relayouts/Animationen,
        # wenn sich der Zielindex zwischen zwei Mausereignissen nicht
        # geaendert hat.
        self._drag_preview_order: list[str] | None = None
        # Gewuenschte Reihenfolge (device_id-Liste), die noch nicht (voll-
        # staendig) angewendet werden konnte, weil die betroffenen Geraete
        # beim Aufruf von set_panel_order() noch nicht verbunden waren --
        # wird bei jedem neuen Panel erneut versucht (siehe on_device_known).
        self._pending_panel_order: list[str] = []

        self._relayout_panels()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def set_panel_order(self, order: list[str]) -> None:
        """Wendet eine gespeicherte Kachel-Reihenfolge an (siehe settings.py:
        Settings.panel_order) -- von MainWindow einmalig beim Start
        aufgerufen, i.d.R. BEVOR die zugehoerigen Geraete ueberhaupt bekannt
        sind. Noch unbekannte device_ids bleiben in _pending_panel_order
        vorgemerkt (siehe _current_order()) und ziehen ihr Panel an die
        richtige Stelle, sobald es in on_device_known() entsteht."""
        self._pending_panel_order = list(order)
        self._relayout_panels()

    def _current_order(self) -> list[str]:
        """Aktuelle Anzeigereihenfolge alle bekannten Panels: erst alle in
        _pending_panel_order genannten (Wunschreihenfolge aus Settings bzw.
        letztem Drag, siehe set_panel_order/_persist_panel_order), danach
        alle uebrigen bekannten Panels (z.B. brandneue, noch nie einsortierte
        Geraete) in ihrer Erstverbindungsreihenfolge. _pending_panel_order
        selbst bleibt dabei unveraendert -- noch nicht verbundene device_ids
        bleiben darin vorgemerkt, bis ihr Panel entsteht.

        Waehrend eines laufenden Drags hat _drag_preview_order (siehe
        _preview_panel_drag) Vorrang vor _pending_panel_order: JEDE
        Geometrieaenderung (auch die durch die Live-Vorschau selbst
        ausgeloeste) erzeugt ein LayoutRequest-Event, dessen Handler
        _relayout_panels() -- und damit _current_order() -- erneut aufruft
        (siehe eventFilter). Ohne diesen Vorrang wuerde genau dieser
        automatische Folgeaufruf die Live-Vorschau sofort wieder auf den
        zuletzt PERSISTIERTEN Stand zuruecksetzen, noch bevor der Nutzer
        ueberhaupt loslassen konnte (an echter Hardware/Interaktion
        reproduziert -- ohne diesen Vorrang blieb die Vorschau wirkungslos)."""
        if self._drag_preview_order is not None:
            return [d for d in self._drag_preview_order if d in self._panels]
        known_ordered = [d for d in self._pending_panel_order if d in self._panels]
        remaining = [d for d in self._panels if d not in self._pending_panel_order]
        return known_ordered + remaining

    def _tile_span(self, device_id: str) -> tuple[int, int]:
        """(col_span, row_span) in Rastereinheiten -- die Breite ist im
        Dashboard (anders als im Control-Tab) nie klassifiziert, nur die
        Hoehe (siehe TILE_HEIGHT_BY_KIND, FEATURES.md Punkt 5: "es wird
        allerdings nur die Hoehe fest vorgegeben")."""
        kind = self._panel_kind.get(device_id, "")
        row_span = 2 if TILE_HEIGHT_BY_KIND.get(kind) == "double" else 1
        return (1, row_span)

    def _retranslate(self) -> None:
        self.setTitle(tr("Dashboard"))
        self._update_toggle_button()

    def _style_scroll_area(self, palette: Palette) -> None:
        """Faerbt ScrollArea/Container auf die Flaeche der umschliessenden
        QGroupBox (pal.surface) statt des allgemeinen Seitenhintergrunds
        (pal.bg), den QScrollArea/QWidget sonst ueber die globale
        QWidget-Regel in theme.stylesheet() bekaemen -- ohne das entsteht ein
        sichtbarer (im Light-Theme grauer) Rand zwischen dem Dashboard-Rahmen
        und den Geraete-Panels darin (siehe BUGS.md #8). viewport() wird
        separat gesetzt, da QScrollArea::setStyleSheet nicht zuverlaessig auf
        das interne Viewport-Widget durchschlaegt.
        """
        style = f"background-color: {palette.surface}; border: none;"
        self._scroll_area.setStyleSheet(f"QScrollArea {{ {style} }}")
        self._scroll_area.viewport().setStyleSheet(style)
        self._container.setStyleSheet(f"background-color: {palette.surface};")

    def _update_toggle_button(self) -> None:
        self._view_toggle_button.set_icon(
            "mdi.arrow-expand-vertical" if self._compact else "mdi.arrow-collapse-vertical"
        )
        self._view_toggle_button.setToolTip(
            tr("Normale Ansicht") if self._compact else tr("Kompakte Ansicht")
        )

    @Slot(bool)
    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        for panel in self._panels.values():
            panel.set_compact(compact)
        self._update_toggle_button()
        self._relayout_panels(reset_width=True)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self._container:
            event_type = event.type()
            if event_type == QEvent.Type.LayoutRequest:
                self._relayout_panels()
            elif event_type == QEvent.Type.DragEnter:
                if event.mimeData().hasFormat(PANEL_DRAG_MIME_TYPE):
                    event.acceptProposedAction()
                    return True
            elif event_type == QEvent.Type.DragMove:
                if event.mimeData().hasFormat(PANEL_DRAG_MIME_TYPE):
                    event.acceptProposedAction()
                    dragged_id = bytes(event.mimeData().data(PANEL_DRAG_MIME_TYPE)).decode("utf-8")
                    self._preview_panel_drag(dragged_id, event.position())
                    return True
            elif event_type == QEvent.Type.Drop:
                if event.mimeData().hasFormat(PANEL_DRAG_MIME_TYPE):
                    dragged_id = bytes(event.mimeData().data(PANEL_DRAG_MIME_TYPE)).decode("utf-8")
                    self._drop_panel(dragged_id, event.position())
                    event.acceptProposedAction()
                    return True
            return False
        if obj in self._panels.values():
            self._handle_panel_drag_event(obj, event)
            return False
        return super().eventFilter(obj, event)

    def _device_id_for_panel(self, panel: QWidget) -> str | None:
        for device_id, candidate in self._panels.items():
            if candidate is panel:
                return device_id
        return None

    def _handle_panel_drag_event(self, panel: QWidget, event) -> None:
        """Erkennt den Beginn eines Kachel-Drags -- nur wenn der Druckpunkt
        im oberen Rahmentitel-Streifen liegt (siehe PANEL_DRAG_HANDLE_HEIGHT),
        und erst nach QApplication.startDragDistance() Bewegung (Qt-Standard-
        Schwelle, verhindert versehentliches Ziehen bei einem blossen Tipp/
        Klick, wichtig auf dem touchbedienten Kiosk-Display)."""
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= PANEL_DRAG_HANDLE_HEIGHT:
                self._drag_device_id = self._device_id_for_panel(panel)
                self._drag_start_pos = event.position()
            return
        if event_type == QEvent.Type.MouseMove:
            if self._drag_device_id is None or self._drag_start_pos is None:
                return
            if (event.position() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
                return
            device_id = self._drag_device_id
            press_pos = self._drag_start_pos
            self._drag_device_id = None
            self._drag_start_pos = None
            self._start_panel_drag(panel, device_id, press_pos)
            return
        if event_type in (QEvent.Type.MouseButtonRelease, QEvent.Type.Leave):
            self._drag_device_id = None
            self._drag_start_pos = None

    def _start_panel_drag(self, panel: QWidget, device_id: str, press_pos) -> None:
        drag = QDrag(panel)
        mime = QMimeData()
        mime.setData(PANEL_DRAG_MIME_TYPE, device_id.encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(make_drag_pixmap(panel))
        # Hotspot = urspruenglicher Druckpunkt (relativ zum Panel): ohne das
        # faellt Qt auf (0, 0) zurueck, die Kachel "springt" beim Drag-Start
        # sichtbar so, dass ihre Ecke statt des gegriffenen Punkts unter dem
        # Cursor/Finger sitzt.
        drag.setHotSpot(press_pos.toPoint())

        # Die echte Kachel wird fuer die Dauer des Ziehens ausgeblendet --
        # bleibt aber in self._panels/_current_order() bekannt und belegt
        # dadurch weiterhin eine Rasterzelle (siehe _order_with_dragged_at/
        # pack_tiles_by_row), nur unsichtbar. Sichtbar ist waehrenddessen nur
        # noch das schwebende Abbild unter dem Cursor (make_drag_pixmap); die
        # uebrigen Kacheln ruecken bei jeder Vorschau-Aktualisierung
        # (_preview_panel_drag) live an ihre neue Position, als haette die
        # gezogene Kachel dort bereits eine Luecke geoeffnet. Ohne dieses
        # Ausblenden waere die ECHTE Kachel zusaetzlich zum schwebenden
        # Abbild sichtbar und wuerde bei jeder Vorschau selbst mit an ihre
        # neue Position springen -- zwei sich ueberlagernde, gegenlaeufig
        # bewegte Abbilder.
        panel.hide()
        self._drag_hidden_panel = panel
        self._drag_preview_order = None
        action = drag.exec(Qt.DropAction.MoveAction)
        if action == Qt.DropAction.IgnoreAction:
            # Kein gueltiger Drop (z.B. ausserhalb des Dashboards
            # losgelassen oder mit Escape abgebrochen) -- dropEvent() hat
            # dann NICHT gefeuert, also hier selbst aufraeumen: Kachel
            # wieder einblenden und zur unveraenderten Reihenfolge
            # zurueckkehren (_drag_preview_order = None laesst
            # _current_order() wieder auf _pending_panel_order zurueckfallen,
            # also den Stand VOR diesem Drag).
            panel.show()
            self._drag_hidden_panel = None
            self._drag_preview_order = None
            self._relayout_panels()

    def _order_with_dragged_at(self, dragged_id: str, pos) -> list[str]:
        """Baut die Anzeigereihenfolge, WENN die gezogene Kachel jetzt an der
        durch `pos` (Position relativ zum Raster-Container) markierten
        Stelle fallen wuerde -- gemeinsame Berechnung fuer die Live-Vorschau
        waehrend DragMove (siehe _preview_panel_drag) und den finalen Drop
        (siehe _drop_panel), damit beide exakt denselben Zielindex ermitteln
        und die Kachel beim Loslassen nicht nochmal springt.

        Zielzelle aus `pos` ueber Breiten-/Hoehenratsche bestimmt.
        Lesereihenfolge ist SPALTENweise (Kacheln fuellen eine Spalte, bevor
        die naechste beginnt, siehe pack_tiles_by_row): Schluessel daher
        Spalte zuerst, dann Zeile. Eingefuegt wird vor der ersten aktuell
        platzierten Kachel, deren Zelle nicht vor der Zielzelle liegt, sonst
        ans Ende."""
        unit_w = self._panel_width + GRID_SPACING
        unit_h = self._cell_height + GRID_SPACING
        order = [d for d in self._current_order() if d != dragged_id]
        if unit_w <= 0 or unit_h <= 0:
            order.append(dragged_id)
            return order
        target_col = max(0, int(pos.x()) // unit_w)
        target_row = max(0, int(pos.y()) // unit_h)
        target_key = target_col * 2 + target_row

        spans = {d: self._tile_span(d) for d in order}
        positions = pack_tiles_by_row(order, spans, max_rows=2)

        insert_before = None
        for device_id in order:
            row, col = positions[device_id]
            if col * 2 + row >= target_key:
                insert_before = device_id
                break

        if insert_before is None:
            order.append(dragged_id)
        else:
            order.insert(order.index(insert_before), dragged_id)
        return order

    def _preview_panel_drag(self, dragged_id: str, pos) -> None:
        """Waehrend DragMove kontinuierlich aufgerufen: lasst die uebrigen
        Kacheln bereits so anordnen, als waere an der aktuellen Cursor-/
        Fingerposition schon losgelassen worden -- das ist das eigentliche
        "Nachbarn ruecken zur Seite und machen Platz"-Verhalten
        (Nutzerfeedback). Wird uebersprungen, wenn sich der Zielindex seit
        dem letzten Aufruf nicht geaendert hat (haeufigster Fall: der
        Cursor bewegt sich innerhalb derselben Zielzelle)."""
        if dragged_id not in self._panels:
            return
        order = self._order_with_dragged_at(dragged_id, pos)
        if order == self._drag_preview_order:
            return
        self._drag_preview_order = order
        self._animate_relayout()

    def _animate_relayout(self) -> None:
        """Wie _relayout_panels(), laesst aber jede Kachel, deren Position
        sich dadurch aendert, sanft an ihren neuen Platz gleiten statt
        instantan zu springen -- der "leicht zur Seite rutschen"-Effekt aus
        dem Nutzerfeedback. Positionen VOR dem Relayout festhalten, danach
        (nach _panel_layout.activate(), siehe dort) die neuen Positionen
        auslesen und nur bei tatsaechlicher Verschiebung animieren. Nutzt
        _current_order() (ueber _relayout_panels()) wie jeder andere
        Relayout-Aufruf auch -- _drag_preview_order (siehe _preview_panel_
        drag) hat dort waehrend eines laufenden Drags automatisch Vorrang."""
        old_positions = {panel: panel.pos() for panel in self._panels.values() if panel.isVisible()}
        self._relayout_panels()
        for panel, old_pos in old_positions.items():
            new_pos = panel.pos()
            if new_pos == old_pos:
                continue
            animation = QPropertyAnimation(panel, b"pos", panel)
            animation.setDuration(140)
            animation.setStartValue(old_pos)
            animation.setEndValue(new_pos)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _drop_panel(self, dragged_id: str, drop_pos) -> None:
        if dragged_id not in self._panels:
            return
        order = self._order_with_dragged_at(dragged_id, drop_pos)
        panel = self._drag_hidden_panel
        self._drag_hidden_panel = None
        self._drag_preview_order = None
        self._persist_panel_order(order)
        if panel is not None:
            panel.show()

    def _persist_panel_order(self, order: list[str]) -> None:
        self._pending_panel_order = order
        self._relayout_panels()
        self.panel_order_changed.emit(order)

    def _relayout_panels(self, reset_width: bool = False) -> None:
        # Panel-Breite/-Hoehe ergeben sich aus dem tatsaechlichen Inhalt
        # (Schrift, Uebersetzung, DPI, ...), nicht aus festen Konstanten --
        # sonst werden bei laengeren Texten/anderen Schriftarten Werte oder
        # der untere Teil der Panels abgeschnitten.
        #
        # Breite (nur Normalansicht): alle Panels auf die breiteste
        # Anforderung angleichen (analog zu ControlTab._relayout_grid) --
        # Last- und Netzteil-Panels brauchen unterschiedlich viel Platz (z.B.
        # 3 statt 2 Nachkommastellen), aber sollen optisch gleich breit
        # bleiben. Zwei Regeln halten die Panels dabei ruhig, statt sie bei
        # jedem Messwert huepfen zu lassen:
        #
        # 1. sizeHint() liefert die natuerliche Inhaltsbreite unabhaengig von
        #    einer bestehenden setFixedWidth-Beschraenkung -- die Fixierung
        #    wird also nie zwischendurch aufgehoben, und neu fixiert wird nur,
        #    wenn die gesetzte Beschraenkung (minimum-/maximumWidth) abweicht.
        #    (Ein Vergleich gegen die momentane Geometrie (width()) wuerde
        #    sich mit den selbst ausgeloesten LayoutRequests endlos
        #    abwechselnd zuruecksetzen/neu fixieren.)
        # 2. Die angeglichene Breite ist eine Ratsche: Ziffern sind in der
        #    Proportionalschrift unterschiedlich breit, die natuerliche
        #    Breite schwankt deshalb mit jedem Messwert um einige Pixel.
        #    Die Panels wachsen daher nur (auf die breiteste je gesehene
        #    Anforderung) und folgen nicht jeder Schwankung nach unten.
        #    Zurueckgesetzt wird die Ratsche nur bei strukturellen Wechseln
        #    (Ansicht-Umschaltung, siehe set_compact).
        if self._panels:
            if reset_width:
                self._panel_width = 0
                self._compact_widths.clear()
            if self._compact:
                # Kompaktansicht: jedes Panel behaelt seine eigene, an den
                # Inhalt geschmiegte Breite -- aber ebenfalls als Ratsche,
                # sonst schieben die schwankenden Wertetexte alle rechts
                # daneben liegenden Panels staendig hin und her.
                for device_id, panel in self._panels.items():
                    width = max(self._compact_widths.get(device_id, 0), panel.sizeHint().width())
                    self._compact_widths[device_id] = width
                    if panel.minimumWidth() != width or panel.maximumWidth() != width:
                        panel.setFixedWidth(width)
            else:
                max_width = max(panel.sizeHint().width() for panel in self._panels.values())
                self._panel_width = max(self._panel_width, max_width)
                for panel in self._panels.values():
                    if panel.minimumWidth() != self._panel_width or panel.maximumWidth() != self._panel_width:
                        panel.setFixedWidth(self._panel_width)

            order = self._current_order()
            for panel in self._panels.values():
                self._panel_layout.removeWidget(panel)
            if self._compact:
                # Kompaktansicht bleibt ein einzeiliges Regal wie bisher (nur
                # jetzt ueber QGridLayout statt QHBoxLayout platziert) -- die
                # neuen Hoehenklassen (TILE_HEIGHT_BY_KIND) gelten bewusst nur
                # fuer die Normalansicht, dort wo FEATURES.md Punkt 5 die
                # doppelte Hoehe ueberhaupt erst herleitet (heutiges
                # microHIL-Panel in der NORMALEN Ansicht). Zeile 1 (fuer
                # doppelte Kacheln) bleibt deshalb ungenutzt/ungereserviert.
                self._panel_layout.setRowMinimumHeight(0, 0)
                self._panel_layout.setRowMinimumHeight(1, 0)
                for col, device_id in enumerate(order):
                    panel = self._panels[device_id]
                    # Loest eine aus der Normalansicht uebernommene
                    # setFixedHeight() (siehe unten) wieder -- sonst bliebe
                    # z.B. eine microHIL-Kachel beim Umschalten in die
                    # Kompaktansicht auf ihrer doppelten Normalansicht-Hoehe
                    # eingefroren, obwohl ihr einzeiliger Kompaktinhalt
                    # deutlich weniger Platz braucht.
                    if panel.maximumHeight() != _WIDGET_SIZE_MAX:
                        panel.setMinimumHeight(0)
                        panel.setMaximumHeight(_WIDGET_SIZE_MAX)
                    self._panel_layout.addWidget(panel, 0, col, alignment=Qt.AlignmentFlag.AlignTop)
            else:
                # Hoehen-Ratsche (analog zur Breiten-Ratsche oben): stellt
                # sicher, dass die Basis-Zellhoehe im Normalfall auch die
                # deutlich groessere microHIL-Kachel (doppelte Hoehe) bequem
                # fasst.
                height_sizes = [(self._panels[d].sizeHint().height(), self._tile_span(d)[1]) for d in order]
                self._cell_height = cell_size_ratchet(self._cell_height, height_sizes)
                # Erzwingt die volle Doppelzeilen-Hoehe auch dann, wenn
                # aktuell gar keine doppelt hohe Kachel verbunden ist (siehe
                # FEATURES.md Punkt 5: "doppelte Hoehe als maximale
                # Ausdehnung") -- sonst wuerde die ScrollArea unten kleiner
                # ausfallen und beim naechsten Verbinden einer microHIL
                # sichtbar in der Hoehe springen.
                self._panel_layout.setRowMinimumHeight(0, self._cell_height)
                self._panel_layout.setRowMinimumHeight(1, self._cell_height)
                spans = {d: self._tile_span(d) for d in order}
                positions = pack_tiles_by_row(order, spans, max_rows=2)
                for device_id, (row, col) in positions.items():
                    panel = self._panels[device_id]
                    row_span = spans[device_id][1]
                    panel.setFixedHeight(row_span * self._cell_height + (row_span - 1) * GRID_SPACING)
                    self._panel_layout.addWidget(
                        panel, row, col, row_span, 1, alignment=Qt.AlignmentFlag.AlignTop
                    )

            # Erzwingt die sofortige Geometrie-Neuberechnung, statt auf die
            # naechste Event-Loop-Runde zu warten -- _animate_relayout()
            # liest direkt im Anschluss panel.pos() aus, das ohne dieses
            # activate() noch die ALTE Position zeigen wuerde (Qt legt neue
            # Layout-Geometrie sonst erst beim naechsten verarbeiteten
            # Event fest).
            self._panel_layout.activate()

        # Hoehe: etwas Rand fuer eine ggf. sichtbare horizontale Scrollleiste
        # einrechnen. sizeHint() spiegelt in der Normalansicht wegen der
        # oben erzwungenen setRowMinimumHeight()-Werte bereits zuverlaessig
        # die volle Doppelzeilen-Hoehe wider.
        content_height = self._container.sizeHint().height()
        self._scroll_area.setFixedHeight(content_height + SCROLL_AREA_MARGIN)

    # -- Geraete-Lebenszyklus --------------------------------------------------

    def _update_empty_tile(self) -> None:
        # Anders als beim Control-Tab (dort verschwinden Sektionen beim
        # Trennen, siehe ControlTab._update_empty_tile) bleiben Dashboard-
        # Panels nach dem ersten Bekanntwerden dauerhaft (ausgegraut)
        # sichtbar (siehe _DevicePanel.set_online) -- die Platzhalter-Kachel
        # richtet sich hier also danach, ob JEMALS ein Geraet bekannt wurde,
        # nicht nach dessen aktuellem Online-Status.
        self._empty_tile.setVisible(not self._panels)

    @Slot(str, str, str)
    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            if kind == "hil":
                # Eigenstaendiges Panel statt des generischen FIELD_DEFS-
                # Schemas (siehe microhil_panel.py-Modul-Docstring) -- 4
                # Relais/8+8 Digital-IO/4+2 Analog-IO/2x12V-OUT passen nicht
                # in eine einzelne Werteliste.
                panel = MicroHilPanel(device_id, label)
            elif kind == "picoscope":
                # Eigenstaendiges Panel statt FIELD_DEFS -- Status (frei/
                # belegt) + Start-Button passen nicht ins Messwerte-Schema
                # (siehe picoscope_panel.py-Modul-Docstring).
                panel = PicoscopePanel(device_id, label)
                panel.launch_requested.connect(self._on_picoscope_launch_requested)
            else:
                field_keys = {"load": LOAD_FIELD_KEYS, "psu": PSU_FIELD_KEYS}.get(kind, CAN_FIELD_KEYS)
                panel = _DevicePanel(kind, device_id, label, field_keys)
            if self._compact:
                panel.set_compact(True)
            panel.hide()
            panel.installEventFilter(self)
            self._panels[device_id] = panel
            self._panel_kind[device_id] = kind
            # Tatsaechliche Platzierung im Raster (Reihenfolge + Kachelgroesse)
            # uebernimmt komplett _relayout_panels() (_current_order() setzt
            # ein neues Panel automatisch ans Ende, falls fuer dieses Geraet
            # noch keine gespeicherte Position vorliegt, siehe set_panel_order).
            self._update_empty_tile()
            self._relayout_panels()
        else:
            panel.set_label(label)

    def forget_device(self, device_id: str) -> None:
        """Entfernt ein Geraet vollstaendig (auch die ausgegraute Kachel
        eines aktuell getrennten Geraets, siehe _DevicePanel.set_online) --
        anders als eine normale Trennung, die das Panel bewusst als
        Erinnerung stehen laesst. Nur fuer den "Geraetezuordnung loeschen"-
        Button (main_window._on_reset_devices_requested) gedacht: ein noch
        VERBUNDENES Geraet nutzt stattdessen den Live-Relabel-Pfad
        (DeviceRegistry.on_device_added), da sein Panel ja weiter gebraucht
        wird."""
        panel = self._panels.pop(device_id, None)
        if panel is None:
            return
        self._panel_layout.removeWidget(panel)
        panel.deleteLater()
        self._panel_colors.pop(device_id, None)
        self._compact_widths.pop(device_id, None)
        self._panel_kind.pop(device_id, None)
        self._update_empty_tile()
        self._relayout_panels()

    def set_panel_color(self, device_id: str, color_key: str | None) -> None:
        self._panel_colors[device_id] = color_key
        panel = self._panels.get(device_id)
        if panel is not None:
            panel.set_panel_color(color_key if self._colors_enabled else None)

    def set_panel_colors_enabled(self, enabled: bool) -> None:
        self._colors_enabled = enabled
        for device_id, panel in self._panels.items():
            panel.set_panel_color(self._panel_colors.get(device_id) if enabled else None)

    @Slot(str, str, str)
    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        panel = self._panels.get(device_id)
        if panel is not None:
            panel.set_label(label)

    @Slot(str, bool)
    def set_load_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_psu_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_can_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_hil_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_picoscope_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    def _set_online(self, device_id: str, online: bool) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_online(online)
        self._relayout_panels()

    # -- Messwerte -----------------------------------------------------------

    @Slot(str, float, float, float)
    def update_load(self, device_id: str, voltage: float, current: float, power: float) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_value("voltage", f"{voltage:.3f}")
        panel.set_value("current", f"{current:.3f}")
        panel.set_value("power", f"{power:.3f}")

    @Slot(str, float, float, bool)
    def update_psu(self, device_id: str, voltage: float, current: float, constant_current: bool) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_value("voltage", f"{voltage:.2f}")
        panel.set_value("current", f"{current:.2f}")
        panel.set_value("mode", "CC" if constant_current else "CV")

    @Slot(str, int, int)
    def update_can(self, device_id: str, tx_count: int, rx_count: int) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_value("tx_count", str(tx_count))
        panel.set_value("rx_count", str(rx_count))

    @Slot(str, str)
    def set_load_mode(self, device_id: str, function_code: str) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        code = function_code.upper()
        panel.set_value("mode", LOAD_MODE_SHORT.get(code, code))

    # -- microHIL --------------------------------------------------------------
    # Eigene Slots statt set_value()/update_load()-artiger Weiterleitung: das
    # MicroHilPanel hat kein FIELD_DEFS-Schema, siehe dessen Modul-Docstring.
    # panel.get(device_id) liefert hier immer ein MicroHilPanel (oder None,
    # falls das Geraet noch kein device_known durchlaufen hat) -- device_worker.
    # py emittiert die hil_*-Signale ausschliesslich fuer kind="hil".

    @Slot(str, list, list)
    def update_hil_digital(self, device_id: str, inputs: list, outputs: list) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.update_inputs(inputs)
        panel.update_outputs(outputs)

    @Slot(str, list)
    def update_hil_relays(self, device_id: str, relays: list) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.update_relays(relays)

    @Slot(str, list)
    def update_hil_analog_in(self, device_id: str, values_mv: list) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.update_analog_in(values_mv)

    @Slot(str, list, list)
    def update_hil_pwr12(self, device_id: str, enabled: list, current_ma: list) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.update_pwr12(enabled, current_ma)

    @Slot(str, int, int)
    def set_hil_analog_out(self, device_id: str, channel: int, millivolts: int) -> None:
        """Zeigt den im Control-Tab (control_tab.HilControlGroup) zuletzt
        angewendeten AOUT-Sollwert an -- direkt am DeviceWorker/Poll-Zyklus
        vorbei verdrahtet (siehe main_window._on_control_section_created,
        kind=="hil"), da es fuer AOUT kein `AOUT?`-Kommando zum Zuruecklesen
        gibt (siehe microhil_panel.MicroHilPanel.set_analog_out_value())."""
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_analog_out_value(channel, millivolts)

    # -- PicoScope -------------------------------------------------------------

    @Slot(str, str, str, str)
    def update_picoscope_state(self, device_id: str, status: str, variant: str, serial: str) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_state(status, variant, serial)

    def _on_picoscope_launch_requested(self) -> None:
        # Direkt hier statt ueber den DeviceWorker/Thread geroutet: reiner
        # Prozessstart der PicoScope-7-App (kein Zugriff auf das offene
        # Geraete-Handle, siehe picoscope2000.driver.launch_app), also ohne
        # die Thread-Sicherheits-Gruende, die die uebrigen Steuerbefehle
        # zwingend ueber den Worker laufen lassen.
        launch_picoscope_app()

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
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QMimeData,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
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

from field_catalog import LOAD_MODE_SHORT
from i18n import Translator, tr
from icons import IconButton
from jds66xx.driver import waveform_name
from microhil_panel import MicroHilPanel
from no_device_tile import NoDeviceTile, OfflineBadge, offline_group_qss
from picoscope2000.driver import launch_app as launch_picoscope_app
from picoscope_panel import PicoscopePanel
from theme import METRICS, Palette, ThemeManager, no_own_background
from theme import current as current_palette
from touch_ui import TOUCH_H
from tile_grid import (
    PANEL_DRAG_HANDLE_HEIGHT,
    PANEL_DRAG_MIME_TYPE,
    cell_size_ratchet,
    make_drag_pixmap,
    pack_tiles,
    pack_tiles_by_row,
    rect_distance,
)

VALUE_STYLE = "font-size: 20px; font-weight: bold;"
COMPACT_VALUE_STYLE = "font-size: 16px; font-weight: bold;"
COMPACT_ICON_SIZE = 18
# Touch-Profil: Uebersicht immer kompakt, dafuer etwas groesser (_DevicePanel.set_large, Nutzerwunsch 11.11.2026)
COMPACT_VALUE_STYLE_LARGE = "font-size: 20px; font-weight: bold;"
COMPACT_ICON_SIZE_LARGE = 22
COMPACT_LARGE_PER_ROW = 4   # Touch: hoechstens so viele Werte je Zeile (Batterie-Simulator hat sieben)
KIND_ICON_SIZE = 26
# Qt-Konstante QWIDGETSIZE_MAX (in PySide6 nicht exportiert) -- hebt das
# setFixedWidth der Normalansicht im Kompaktmodus wieder auf.
_WIDGET_SIZE_MAX = 16777215
# Zusätzlicher Platz für die Scrollleiste am unteren Rand (falls horizontal
# gescrollt werden muss) sowie den Rahmen der ScrollArea.
SCROLL_AREA_MARGIN = 24
# Abstand (ms) der Kontrolle, ob die Kachelanordnung noch zu den Kachelgroessen passt
# (siehe DashboardWidget._layout_check_timer).
LAYOUT_CHECK_INTERVAL_MS = 500

# Abstand zwischen den Kacheln im Raster (siehe DashboardWidget._relayout_panels).
GRID_SPACING = 12

# kind -> Kachelhoehe (FEATURES.md Punkt 5): nur microHIL (deutlich mehr
# Inhalt als die uebrigen Panels, siehe microhil_panel.py) bekommt die
# doppelte Hoehe, alles andere (auch "picoscope" -- laut picoscope_panel.py
# nur Statuszeile + Button, also kurz) bleibt einfach.
TILE_HEIGHT_BY_KIND: dict[str, str] = {"hil": "double"}
# Touch-Profil (mehrzeilige Uebersicht, DashboardWidget._multi_row_heights): diese Kacheln
# bestimmen die Zellhoehe nicht mit, sondern spannen so viele Zeilen, wie sie brauchen.
MULTI_ROW_TALL_KINDS = {"hil", "battsim"}

# Dieselbe Klassifizierung wie TILE_HEIGHT_BY_KIND, aber fuer die
# KOMPAKTANSICHT eigens ermittelt -- deckt sich NICHT mit der Normalansicht,
# da "kurz" dort etwas anderes bedeutet als hier: microHIL bleibt wegen
# seines Digital-IO-Blocks (IN+OUT uebereinander, siehe microhil_panel.py)
# auch im Kompaktmodus zweizeilig, und PicoscopePanel ist trotz "nur
# Statuszeile + Button" in der Normalansicht dort trotzdem zweizeilig
# (Status ueber dem Button gestapelt) -- empirisch mit sizeHint() gemessen
# (26px fuer psu/load/can, 68px microHIL, 76px picoscope, siehe
# BUGS_GESCHLOSSEN.md #36-Nachtrag). Eine solche Kachel kann folglich NIE
# nur der oberen oder nur der unteren Zeile zugewiesen werden -- sie belegt
# beim Packen (_tile_span/pack_tiles_by_row) immer beide, genau wie eine
# doppelt hohe Kachel in der Normalansicht.
COMPACT_TILE_HEIGHT_BY_KIND: dict[str, str] = {"hil": "double", "picoscope": "double", "scpiscope": "double"}

# Feines horizontales Raster der Kompaktansicht: eine Kachel belegt dort
# nicht mehr genau EINE inhaltsbreite Spalte, sondern eine SPALTENSPANNE in
# Vielfachen dieser Einheit (analog zur Zeilenspanne, siehe
# COMPACT_TILE_HEIGHT_BY_KIND). Grund: mit einer Spalte je Kachel kann eine
# Spalte je Zeile immer nur eine einzige Kachel aufnehmen -- "unter einer
# breiten Kachel zwei schmale nebeneinander" ist damit prinzipiell
# unmoeglich, egal wie gezogen wird (Nutzerwunsch, siehe
# BUGS_GESCHLOSSEN.md #36). Da eine ueber n Einheiten gespannte Kachel die
# n-1 Zwischenabstaende mitbenutzt, fasst sie n*UNIT + (n-1)*GRID_SPACING
# Pixel; die Rasterweite betraegt also UNIT+GRID_SPACING = 20px, auf die
# eine Kachelbreite aufgerundet wird (im Mittel ~10px Verschnitt).
COMPACT_COLUMN_UNIT = 8


def compact_column_span(width: int) -> int:
    """Rastereinheiten, die eine Kachel der natuerlichen Breite `width` in
    der Kompaktansicht braucht (siehe COMPACT_COLUMN_UNIT)."""
    step = COMPACT_COLUMN_UNIT + GRID_SPACING
    return max(1, -(-(width + GRID_SPACING) // step))


def compact_span_width(span: int) -> int:
    """Umkehrung zu compact_column_span(): Pixelbreite einer ueber `span`
    Einheiten gespannten Kachel, inklusive der mitbenutzten Zwischenabstaende."""
    return span * COMPACT_COLUMN_UNIT + (span - 1) * GRID_SPACING

# field_key -> (deutscher Basis-Anzeigename, Einheit); Einheit ist
# sprachunabhaengig und wird nicht ueber i18n.tr uebersetzt.
FIELD_DEFS: dict[str, tuple[str, str]] = {
    "voltage": ("Spannung", "V"),
    "current": ("Strom", "A"),
    "power": ("Leistung", "W"),
    "mode": ("Modus", ""),
    "tx_count": ("Gesendet", ""),
    "rx_count": ("Empfangen", ""),
    "fg_ch1": ("Kanal 1", ""),
    "fg_ch2": ("Kanal 2", ""),
    "bs_state": ("Zustand", ""),
    "bs_output": ("Ausgang", ""),
    "bs_stack": ("Stapel", "V"),
    "bs_cell_min": ("Zelle min", "V"),
    "bs_cell_max": ("Zelle max", "V"),
    "bs_fail": ("FAIL-Grund", ""),
    "bs_controller": ("Regler", ""),
    "jl_target": ("Ziel", ""),
    "jl_vtref": ("Zielspannung", "V"),
    "jl_last": ("Zuletzt", ""),
}
LOAD_FIELD_KEYS = ["voltage", "current", "power", "mode"]
PSU_FIELD_KEYS = ["voltage", "current", "mode"]
CAN_FIELD_KEYS = ["tx_count", "rx_count"]
FG_FIELD_KEYS = ["fg_ch1", "fg_ch2"]
BATTSIM_FIELD_KEYS = ["bs_state", "bs_output", "bs_stack", "bs_cell_min", "bs_cell_max", "bs_fail", "bs_controller"]
JLINK_FIELD_KEYS = ["jl_target", "jl_vtref", "jl_last"]
KIND_TITLE = {"load": "Elektronische Last", "psu": "Labornetzteil", "can": "CAN-Bus",
              "fg": "Funktionsgenerator", "battsim": "Batterie-Simulator", "jlink": "J-Link"}
# Ersetzt die bisherige Geraeteart-Textzeile im Normal-Panel: platzsparendes
# Icon unten rechts im Panel statt einer eigenen Zeile, voller Name als
# Tooltip (siehe KIND_TITLE) weiterhin erreichbar.
KIND_ICON = {"load": "mdi.resistor", "psu": "mdi.power-plug-outline", "can": "mdi.chip",
             "fg": "mdi.sine-wave", "battsim": "mdi.battery-charging-outline", "jlink": "mdi.chip"}

# "Verbindung getrennt"-Kennzeichnung oben rechts im Panel: no_device_tile.OfflineBadge (Symbol
# mdi.close-network-outline + "getrennt", Theme-Farben); Position per resizeEvent nachgefuehrt, da die
# Panel-Breite sich nachtraeglich angleicht (siehe DashboardWidget._relayout_panels).

# Icons fuer die Kompaktansicht: dort ersetzen sie die Text-Beschriftung der
# Messgroessen komplett (der volle Name bleibt als Tooltip erreichbar).
FIELD_ICONS: dict[str, str] = {
    "voltage": "mdi.flash-outline",
    "current": "mdi.current-dc",
    "power": "mdi.gauge",
    "mode": "mdi.swap-horizontal-bold",
    "tx_count": "mdi.upload-outline",
    "rx_count": "mdi.download-outline",
    "fg_ch1": "mdi.numeric-1-box-outline",
    "fg_ch2": "mdi.numeric-2-box-outline",
    "bs_state": "mdi.state-machine",
    "bs_output": "mdi.power",
    "bs_stack": "mdi.battery-outline",
    "bs_cell_min": "mdi.arrow-collapse-down",
    "bs_cell_max": "mdi.arrow-collapse-up",
    "bs_fail": "mdi.alert-circle-outline",
    "bs_controller": "mdi.tune-variant",
    "jl_target": "mdi.chip",
    "jl_vtref": "mdi.flash-outline",
    "jl_last": "mdi.history",
}


def _format_frequency(hertz: float) -> str:
    if hertz >= 1_000_000:
        return f"{hertz / 1_000_000:.4g} MHz"
    if hertz >= 1_000:
        return f"{hertz / 1_000:.4g} kHz"
    return f"{hertz:.4g} Hz"


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
        # Seit 0.39.2 ein Raster statt einer HBox: am PC eine Zeile wie bisher, im Touch-Profil (set_large)
        # hoechstens COMPACT_LARGE_PER_ROW Werte je Zeile -- der Batterie-Simulator war mit sieben Werten
        # in einer Zeile breiter als die Seite.
        self._compact_widget = no_own_background(QWidget())
        compact = QGridLayout(self._compact_widget)
        compact.setContentsMargins(0, 0, 0, 0)
        compact.setVerticalSpacing(4)
        self._compact_icons: dict[str, QLabel] = {}
        self._compact_values: dict[str, QLabel] = {}
        for field_key in field_keys:
            icon_label = QLabel()
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_label = QLabel("--")
            value_label.setStyleSheet(COMPACT_VALUE_STYLE)
            self._compact_icons[field_key] = icon_label
            self._compact_values[field_key] = value_label
        outer.addWidget(self._compact_widget)
        self._compact_widget.hide()
        self._compact_layout = compact
        self._compact_columns = 0
        self._large = False
        self._compact_mode = False
        self._arrange_compact()
        self._apply_compact_icons(current_palette())

        # "Verbindung getrennt"-Kennzeichnung: eigenes Kind-Widget mit absoluter Position in der
        # Panel-Ecke, unabhaengig von Normal-/Kompaktansicht. Seit 0.38.3 Symbol + "getrennt" in
        # Theme-Farben (no_device_tile.OfflineBadge, GUI-Review UX-2); OfflineBadge setzt
        # no_own_background() selbst (sonst malt es ein opakes Quadrat in Seitenfarbe).
        self._offline_icon = OfflineBadge(self)

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()
        self._apply_style(current_palette())

    def _retranslate(self) -> None:
        self._kind_icon.setToolTip(tr(KIND_TITLE.get(self._kind, self._kind)))
        for field_key in self._field_keys:
            self._form.labelForField(self._value_rows[field_key]).setText(_field_display(field_key) + ":")
            tooltip = _field_display(field_key)
            self._compact_icons[field_key].setToolTip(tooltip)
            self._compact_values[field_key].setToolTip(tooltip)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._position_offline_icon()

    def _position_offline_icon(self) -> None:
        self._offline_icon.place()

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
            # Getrennt-Darstellung aus no_device_tile.py (dieselbe wie "kein
            # aktives Geraet") statt der individuellen Panel-Farbe (_color_key
            # wird hier bewusst ignoriert, kommt beim naechsten Online-Gehen
            # ueber _apply_style wieder zum Zug). Der Rahmentitel (Geraete-
            # name) bekommt HIER bewusst KEINE eigene Farbregel -- er faellt
            # damit auf die globale "QGroupBox::title { color: pal.text }"-
            # Regel aus theme.stylesheet() zurueck, die in beiden Themes auf
            # dem Panel-Hintergrund gut lesbar ist (dieselbe Regel, die auch
            # die normalen Online-Panels ohne Probleme nutzen). Eine fest
            # verdrahtete Titel-Farbe (fruehere Fassung: OFFLINE_TEXT, dunkel)
            # war im Amber-Dark-Theme praktisch unlesbar (Screenshot-Bug).
            # Seit 0.38.3 aus dem Theme (no_device_tile.offline_group_qss, GUI-Review UX-2)
            self.setStyleSheet(offline_group_qss(palette))
            return
        border_rule = f"border: 1px solid {palette.text_muted}; border-radius: {METRICS.radius_md}px;"
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
        size = COMPACT_ICON_SIZE_LARGE if self._large else COMPACT_ICON_SIZE
        for field_key, icon_label in self._compact_icons.items():
            icon_label.setPixmap(qta.icon(FIELD_ICONS[field_key], color=palette.text).pixmap(size, size))

    def _arrange_compact(self) -> None:
        """Symbol + Wert je Messgroesse ins Kompaktraster; am PC alle in einer Zeile, im Touch-Profil
        hoechstens COMPACT_LARGE_PER_ROW je Zeile. Rechts eine dehnbare Spalte haelt den Inhalt links."""
        grid = self._compact_layout
        keys = list(self._compact_icons)
        per_row = min(len(keys), COMPACT_LARGE_PER_ROW) if self._large else len(keys)
        per_row = max(per_row, 1)
        for column in range(self._compact_columns):
            grid.setColumnStretch(column, 0)
            grid.setColumnMinimumWidth(column, 0)
        for key in keys:
            grid.removeWidget(self._compact_icons[key])
            grid.removeWidget(self._compact_values[key])
        for i, key in enumerate(keys):
            row, col = divmod(i, per_row)
            grid.addWidget(self._compact_icons[key], row, 3 * col)
            grid.addWidget(self._compact_values[key], row, 3 * col + 1)
            grid.setColumnMinimumWidth(3 * col + 2, 10)   # Abstand bis zum naechsten Wert
        self._compact_columns = 3 * per_row + 1
        grid.setColumnStretch(3 * per_row, 1)

    def set_large(self, large: bool) -> None:
        """Touch-Profil: Kompaktansicht etwas groesser (Werte 20 statt 16 px, Symbole 22 statt 18 px) und
        bei vielen Werten auf mehrere Zeilen verteilt."""
        if large == self._large:
            return
        self._large = large
        style = COMPACT_VALUE_STYLE_LARGE if large else COMPACT_VALUE_STYLE
        for value_label in self._compact_values.values():
            value_label.setStyleSheet(style)
        self._compact_layout.setHorizontalSpacing(6 if large else 4)
        self._arrange_compact()
        self._apply_compact_icons(current_palette())
        if self._compact_mode:
            self.set_compact(True)   # Innenabstand nachziehen

    def _apply_kind_icon(self, palette: Palette) -> None:
        icon_name = KIND_ICON.get(self._kind)
        if icon_name is None:
            return
        self._kind_icon.setPixmap(
            qta.icon(icon_name, color=palette.text_muted).pixmap(KIND_ICON_SIZE, KIND_ICON_SIZE)
        )

    def set_compact(self, compact: bool) -> None:
        self._compact_mode = compact
        self._normal_widget.setVisible(not compact)
        self._compact_widget.setVisible(compact)
        if compact:
            if self._large:
                self._outer.setContentsMargins(12, 6, 12, 8)   # Touch: etwas mehr Luft
            else:
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
    # Klick auf den Abdock-Knopf daneben: MainWindow verschiebt das Dashboard in
    # ein eigenes Fenster bzw. holt es zurueck und ruft dann set_detached.
    detach_toggle_requested = Signal()
    # device_id -- Oszilloskop-Verbindung des ScopeService trennen (Kachel-Knopf
    # "Trennen" und vor dem Start von PicoScope 7), siehe scope_service.py.
    picoscope_release_requested = Signal(str)
    # device_id -- letzte Erfassung ueber Netzwerk/MCP als Bild zeigen (main_window).
    picoscope_plot_requested = Signal(str)
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
        self._viewport = self._scroll_area.viewport()   # fuer eventFilter, siehe dort
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
        # ohne zusaetzliche vertikale Hoehe. Der Abdock-Knopf steht links daneben
        # statt darueber -- uebereinander wuerden die zwei Knoepfe die
        # Kompaktansicht hoeher machen.
        body = QHBoxLayout()
        body.addWidget(self._scroll_area, 1)
        corner = QVBoxLayout()
        corner.addStretch()
        corner_buttons = QHBoxLayout()
        corner_buttons.setSpacing(2)
        corner_buttons.addStretch()   # Touch: Zeile unter den Kacheln, Knoepfe rechts (set_multi_row)
        self._detach_button = IconButton("mdi.open-in-new", "")
        self._detach_button.setFixedSize(QSize(28, 24))
        self._detach_button.clicked.connect(self.detach_toggle_requested)
        corner_buttons.addWidget(self._detach_button)
        self._view_toggle_button = IconButton("mdi.arrow-collapse-vertical", "")
        self._view_toggle_button.setFixedSize(QSize(28, 24))
        self._view_toggle_button.clicked.connect(self.compact_toggle_requested)
        corner_buttons.addWidget(self._view_toggle_button)
        corner.addLayout(corner_buttons)
        body.addLayout(corner)
        outer.addLayout(body)
        self._outer_layout, self._body_layout, self._corner_layout = outer, body, corner

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
        self._user_compact = False   # Wahl am PC (set_compact); Touch ist immer kompakt
        self._large = False          # Touch: Kompaktkacheln etwas groesser (_apply_view)
        self._detached = False
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
        # (kompakt?, Reihenfolge) der letzten vollstaendigen Anordnung -- damit ein
        # LayoutRequest ohne echte Aenderung nichts neu berechnet
        # (siehe _layout_is_current).
        self._laid_out: tuple[bool, tuple[str, ...], int] | None = None
        # Touch-Profil (set_multi_row): Spaltenzahl aus der Breite, beliebig viele Zeilen nach unten
        # statt zwei Zeilen und waagerechtem Scrollen (HIL-Kachel war am 1024-px-Display angeschnitten).
        self._multi_row = False
        self._row_count = 0
        self._row_spans: dict[str, int] = {}
        # Sicherheitsnetz: Wuchs eine Kachel, ohne dass danach ein LayoutRequest kam (die
        # Reihenfolge der Ereignisse beim Start entscheidet das), bliebe die Anordnung sonst
        # veraltet -- frueher heilte das die LayoutRequest-Dauerschleife zufaellig. Billig, weil
        # ohne Aenderung nur die sizeHints gelesen werden (siehe _layout_is_current).
        self._layout_check_timer = QTimer(self)
        self._layout_check_timer.setInterval(LAYOUT_CHECK_INTERVAL_MS)
        self._layout_check_timer.timeout.connect(self._check_layout)
        self._layout_check_timer.start()
        # Eigene Zellhoehen-Ratsche fuer die Kompaktansicht (siehe
        # COMPACT_TILE_HEIGHT_BY_KIND) -- unabhaengig von _cell_height, da
        # die natuerlichen Groessen dort viel kleiner sind als in der
        # Normalansicht (26px statt z.B. >100px) und sonst faelschlich vom
        # viel groesseren Normalansicht-Wert dominiert wuerden.
        self._compact_cell_height = 0
        # Spaltenspanne je Kachel im feinen Kompakt-Raster (siehe
        # COMPACT_COLUMN_UNIT/_tile_span), von _relayout_panels() gefuellt.
        self._compact_col_spans: dict[str, int] = {}
        # Anzahl der aktuell mit einer Mindestbreite belegten Rasterspalten
        # -- beim Schrumpfen (Geraet entfernt, Ansicht gewechselt) muessen
        # die frueher gesetzten Mindestbreiten wieder auf 0 zurueck, sonst
        # bleibt rechts eine leere Geisterspalte stehen.
        self._compact_column_count = 0

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
        """(col_span, row_span) in Rastereinheiten.

        Hoehe: klassifiziert nach Geraeteart (FEATURES.md Punkt 5: "es wird
        allerdings nur die Hoehe fest vorgegeben"), in der Kompaktansicht
        ueber eine EIGENE Tabelle (COMPACT_TILE_HEIGHT_BY_KIND), da dort
        andere Geraetearten zweizeilig sind als in der Normalansicht.

        Breite: in der Normalansicht immer 1 -- dort sind ohnehin alle
        Kacheln auf dieselbe Breite angeglichen (siehe _relayout_panels),
        eine Spalte fasst also genau eine Kachel. In der Kompaktansicht
        dagegen eine Spanne im feinen Raster (COMPACT_COLUMN_UNIT), damit
        unter einer breiten Kachel zwei schmale nebeneinander Platz finden.
        Die Spannen berechnet _relayout_panels() aus den (geratschten)
        natuerlichen Breiten und legt sie in _compact_col_spans ab -- hier
        wird nur nachgeschlagen, damit Layout und Ziel-Bestimmung beim
        Ziehen (_tile_rects) garantiert dieselben Spannen sehen."""
        kind = self._panel_kind.get(device_id, "")
        if self._compact:
            row_span = 2 if COMPACT_TILE_HEIGHT_BY_KIND.get(kind) == "double" else 1
            if self._multi_row:
                row_span = self._row_spans.get(device_id, 1)   # siehe _multi_row_heights
            return (self._compact_col_spans.get(device_id, 1), row_span)
        if self._multi_row:
            return (1, self._row_spans.get(device_id, 1))   # siehe _multi_row_heights
        return (1, 2 if TILE_HEIGHT_BY_KIND.get(kind) == "double" else 1)

    def _retranslate(self) -> None:
        # Touch: die Navigation nennt die Seite schon ("Übersicht") -> Rahmentitel spart eine Zeile
        self.setTitle("" if self._multi_row else tr("Dashboard"))
        self._update_toggle_button()
        self._update_detach_button()

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
        # Touch-Profil: Seitenhintergrund statt eigener Flaeche (kein Rahmen im Rahmen, set_multi_row)
        ground = palette.bg if getattr(self, "_multi_row", False) else palette.surface   # init: noch nicht gesetzt
        style = f"background-color: {ground}; border: none;"
        self._scroll_area.setStyleSheet(f"QScrollArea {{ {style} }}")
        self._scroll_area.viewport().setStyleSheet(style)
        self._container.setStyleSheet(f"background-color: {ground};")

    def _update_toggle_button(self) -> None:
        self._view_toggle_button.set_icon(
            "mdi.arrow-expand-vertical" if self._compact else "mdi.arrow-collapse-vertical"
        )
        self._view_toggle_button.setToolTip(
            tr("Normale Ansicht") if self._compact else tr("Kompakte Ansicht")
        )

    def _update_detach_button(self) -> None:
        self._detach_button.set_icon("mdi.dock-top" if self._detached else "mdi.open-in-new")
        self._detach_button.setToolTip(
            tr("Dashboard wieder ins Hauptfenster holen") if self._detached
            else tr("Dashboard in eigenem Fenster öffnen")
        )

    def set_drag_allowed(self, allowed: bool) -> None:
        """Touch-Profil: Kacheln nur im Modus "Anordnen" verschiebbar (MainWindow)."""
        self._drag_allowed = allowed

    def scroll_area(self):
        return self._scroll_area

    def set_multi_row(self, on: bool) -> None:
        """Touch-Profil (Reiter "Übersicht"): Kacheln brechen an der verfuegbaren Breite um und
        wachsen nach unten (die Seite scrollt senkrecht), statt in zwei Zeilen waagerecht zu
        scrollen. Der Ansicht-Umschalter bekommt Fingergroesse."""
        if on == self._multi_row:
            return
        self._multi_row = on
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if on else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if on:
            self._viewport.installEventFilter(self)
        else:
            self._viewport.removeEventFilter(self)
        # Touch: Knopfspalte rechts kostet Kachelbreite -> als Zeile unter die Kacheln
        if on:
            self._body_layout.removeItem(self._corner_layout)
            self._outer_layout.addLayout(self._corner_layout)
        else:
            self._outer_layout.removeItem(self._corner_layout)
            self._body_layout.addLayout(self._corner_layout)
        size = QSize(TOUCH_H, TOUCH_H) if on else QSize(28, 24)
        self._view_toggle_button.setFixedSize(size)
        self._detach_button.setFixedSize(size)
        # Touch: immer kompakt -> kein Umschalter; kein eigener Rahmen und keine eigene Flaeche um die
        # Kacheln (sah am Pi aus wie Rahmen im Rahmen, Nutzerhinweis 11.11.2026)
        self._view_toggle_button.setVisible(not on)
        self.setStyleSheet("DashboardWidget { border: none; background: transparent; margin-top: 0px;"
                           " padding-top: 0px; }" if on else "")
        self._style_scroll_area(current_palette())
        self._retranslate()
        self._cell_height = 0   # Zellhoehe wird je Profil anders bestimmt (_multi_row_heights)
        self._row_spans = {}
        self._compact_cell_height = 0
        before = (self._compact, self._large)
        self._apply_view()
        if (self._compact, self._large) == before:
            self._relayout_panels(reset_width=True)

    @staticmethod
    def _set_horizontal_policy(panel: QWidget, policy: QSizePolicy.Policy) -> None:
        current = panel.sizePolicy()
        if current.horizontalPolicy() != policy:
            current.setHorizontalPolicy(policy)
            panel.setSizePolicy(current)

    def _avail_width(self) -> int:
        margins = self._panel_layout.contentsMargins()
        return self._viewport.width() - margins.left() - margins.right()

    def _max_cols(self) -> int:
        """Rasterspalten, die in die sichtbare Breite passen (nur im Touch-Profil, set_multi_row)."""
        unit = (COMPACT_COLUMN_UNIT if self._compact else self._panel_width) + GRID_SPACING
        if self._compact:   # Spalten tragen ihren Abstand selbst (siehe Kompakt-Zweig in _relayout_panels)
            return max(1, self._avail_width() // unit)
        return max(1, (self._avail_width() + GRID_SPACING) // max(unit, 1))

    def _multi_row_heights(self, order: list[str]) -> tuple[int, dict[str, int]]:
        """Touch, Normalansicht: Zellhoehe nach den einfachen Kacheln (Ratsche), hohe Kacheln (HIL)
        spannen so viele Zeilen, wie sie brauchen -- sonst gibt die HIL-Kachel mit halber Hoehe
        allen anderen viel Leerraum (2 x 238 px statt ~160 px fuer drei Messwerte)."""
        # Seit 0.39.2 auch fuer die Kompaktansicht (im Touch-Profil immer kompakt): dort zaehlen zusaetzlich
        # die in der Kompaktansicht zweizeiligen Kacheln (Oszilloskope) als hoch.
        tall = MULTI_ROW_TALL_KINDS | ({k for k, v in COMPACT_TILE_HEIGHT_BY_KIND.items() if v == "double"}
                                       if self._compact else set())
        current = self._compact_cell_height if self._compact else self._cell_height
        single = [(self._panels[d].sizeHint().height(), 1) for d in order
                  if self._panel_kind.get(d, "") not in tall]
        cell = cell_size_ratchet(current, single) if single else current
        if cell <= 0:
            cell = max((self._panels[d].sizeHint().height() for d in order), default=1) // 2 or 1
        spans = {}
        for d in order:
            if self._panel_kind.get(d, "") in tall:
                need = self._panels[d].sizeHint().height()
                spans[d] = max(1, -(-(need + GRID_SPACING) // (cell + GRID_SPACING)))
        return cell, spans

    def _pack(self, order: list[str], spans: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
        """Gemeinsames Packen fuer Anordnung und Ziehen (_relayout_panels/_tile_rects)."""
        if self._multi_row:
            return pack_tiles(order, spans, self._max_cols())
        return pack_tiles_by_row(order, spans, max_rows=2)

    def _used_rows(self, positions: dict[str, tuple[int, int]], spans: dict[str, tuple[int, int]]) -> int:
        """Belegte Zeilen; ohne Touch immer zwei (volle Doppelhoehe, siehe _relayout_panels)."""
        used = max((row + spans[d][1] for d, (row, _col) in positions.items()), default=1)
        return used if self._multi_row else 2

    def _size_rows(self, rows: int, height: int) -> None:
        for row in range(max(rows, self._row_count)):
            self._panel_layout.setRowMinimumHeight(row, height if row < rows else 0)
        self._row_count = rows

    def set_detach_available(self, available: bool) -> None:
        """Touch-Profil: kein Abdocken (Dashboard ist dort ein eigener Reiter) -> Knopf ausblenden."""
        self._detach_button.setVisible(available)

    def set_detached(self, detached: bool) -> None:
        """Nur Anzeige des Abdock-Knopfs -- das Umhaengen selbst macht MainWindow."""
        self._detached = detached
        self._update_detach_button()

    @Slot(bool)
    def set_compact(self, compact: bool) -> None:
        """Wahl des Nutzers (Einstellung, Umschalter am PC). Im Touch-Profil gilt immer die Kompaktansicht
        (_apply_view); die Wahl bleibt gespeichert und gilt wieder ohne Touch-Profil."""
        self._user_compact = compact
        self._apply_view()

    def _apply_view(self) -> None:
        # Touch-Profil (Nutzerwunsch 11.11.2026): immer kompakt, ohne Umschalter, etwas groesser
        compact = self._user_compact or self._multi_row
        large = self._multi_row
        if (compact, large) == (self._compact, self._large):
            return
        self._compact, self._large = compact, large
        for panel in self._panels.values():
            panel.set_compact(compact)
            if hasattr(panel, "set_large"):
                panel.set_large(large)
        self._update_toggle_button()
        self._compact_cell_height = 0
        self._relayout_panels(reset_width=True)
        # Direkt nach dem Umschalten liefert Qt teils noch die alte Breite (microHIL einzeilig 980 px), und
        # die Breiten-Ratsche haelt sie fest -- einmal verzoegert neu messen, wenn die Groessen stehen.
        QTimer.singleShot(0, lambda: self._relayout_panels(reset_width=True))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        # Nur Python-Referenz vergleichen: viewport() beim Beenden (Objekt schon abgebaut) warf in
        # verschachtelten Ereignissen immer wieder -> Rekursion (am Pi gesehen, 11.11.2026)
        if obj is self._viewport:
            # Touch-Profil: andere Breite -> Spaltenzahl und Kachelbreite neu (set_multi_row)
            if event.type() == QEvent.Type.Resize and self._multi_row and self._laid_out is not None \
                    and self._laid_out[2] != self._avail_width():
                QTimer.singleShot(0, lambda: self._relayout_panels(only_if_changed=True))
            return False
        if obj is self._container:
            event_type = event.type()
            if event_type == QEvent.Type.LayoutRequest:
                self._relayout_panels(only_if_changed=True)
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
            # Touch-Profil: verschieben nur im Modus "Anordnen", sonst ist Ziehen Wischen (set_drag_allowed)
            if (event.button() == Qt.MouseButton.LeftButton and event.position().y() <= PANEL_DRAG_HANDLE_HEIGHT
                    and getattr(self, "_drag_allowed", True)):
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
        self._set_keeps_space_when_hidden(panel, True)
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
            self._set_keeps_space_when_hidden(panel, False)
            panel.show()
            self._drag_hidden_panel = None
            self._drag_preview_order = None
            self._relayout_panels()

    @staticmethod
    def _set_keeps_space_when_hidden(panel: QWidget, keep: bool) -> None:
        """Laesst `panel` seine Rasterzelle auch im ausgeblendeten Zustand
        belegen (oder wieder freigeben). Fuer die Dauer eines Drags gesetzt:
        das QGridLayout ignoriert ausgeblendete Widgets sonst vollstaendig
        und zieht ihre Spalte auf Breite 0 zusammen, sobald keine sichtbare
        Kachel mehr darin liegt -- die gezogene Kachel hinterlaesst dann gar
        keine Luecke ("Platz machen" bleibt unsichtbar), und schlimmer: die
        Zielbestimmung (siehe _order_with_dragged_at) rechnet gegen eine
        Anordnung ohne diese Luecke und liefert fuer denselben Mauspunkt
        abwechselnd zwei verschiedene Reihenfolgen -- die Kacheln flackern
        bei ruhig gehaltener Maus zwischen beiden hin und her."""
        policy = panel.sizePolicy()
        policy.setRetainSizeWhenHidden(keep)
        panel.setSizePolicy(policy)

    def _tile_rects(self, order: list[str]) -> dict[str, QRect]:
        """Rasterzellen der Kacheln in `order`, so wie sie GERADE auf dem
        Bildschirm liegen -- gelesen aus dem QGridLayout selbst
        (cellRect(), doppelt hohe Kacheln als Vereinigung ihrer beiden
        Zeilen) statt aus panel.geometry(). Zwei Gruende dafuer:

        * cellRect() liefert die vom Layout festgelegte ENDposition. Waehrend
          eines Drags gleiten die Kacheln per QPropertyAnimation an ihren
          neuen Platz (siehe _animate_relayout); panel.geometry() zeigt
          dabei die momentane Zwischenposition und wuerde die Zielbestimmung
          waehrend jeder laufenden Animation verfaelschen.
        * Die tatsaechlichen Zellgrenzen beruecksichtigen automatisch die
          Layout-Raender, GRID_SPACING und -- in der Kompaktansicht -- die
          je Kachel unterschiedlichen Breiten (siehe _relayout_panels).

        Nutzt denselben 2-zeiligen Packalgorithmus wie die Normalansicht,
        auch in der Kompaktansicht (vorher dort stur auf Zeile 0 verdrahtet,
        Nutzerwunsch) -- _tile_span() liefert je nach Ansicht die passende
        Hoehenklasse (TILE_HEIGHT_BY_KIND bzw. COMPACT_TILE_HEIGHT_BY_KIND).
        Zwei schmale (einfach hohe) Kacheln koennen sich dadurch dieselbe
        Spalte teilen (uebereinander), waehrend eine zweizeilige Kachel
        (microHIL/PicoScope, auch in der Kompaktansicht) wie in der
        Normalansicht immer beide Zeilen zugleich belegt und nie nur der
        oberen oder unteren zugewiesen werden kann."""
        spans = {device_id: self._tile_span(device_id) for device_id in order}
        positions = self._pack(order, spans)
        rects: dict[str, QRect] = {}
        for device_id, (row, col) in positions.items():
            col_span, row_span = spans[device_id]
            rect = self._panel_layout.cellRect(row, col)
            if row_span > 1 or col_span > 1:
                rect = rect.united(
                    self._panel_layout.cellRect(row + row_span - 1, col + col_span - 1)
                )
            rects[device_id] = rect
        return rects

    def _order_with_dragged_at(self, dragged_id: str, pos) -> list[str]:
        """Baut die Anzeigereihenfolge, WENN die gezogene Kachel jetzt an der
        durch `pos` (Position relativ zum Raster-Container) markierten
        Stelle fallen wuerde -- gemeinsame Berechnung fuer die Live-Vorschau
        waehrend DragMove (siehe _preview_panel_drag) und den finalen Drop
        (siehe _drop_panel), damit beide exakt denselben Zielindex ermitteln
        und die Kachel beim Loslassen nicht nochmal springt.

        Gearbeitet wird direkt auf den Rasterzellen der GERADE angezeigten
        Anordnung (siehe _tile_rects): gesucht wird die Kachel unter `pos`
        (bzw. bei einem Drop ins Leere die naechstgelegene), die gezogene
        Kachel wird davor oder dahinter in die LISTE einsortiert -- vor ihr,
        wenn `pos` in der ersten Haelfte liegt, sonst dahinter.

        "Haelfte" bezieht sich dabei auf die DOMINANTE Richtung: der Abstand
        zur Kachelmitte wird in x- und y-Richtung je auf die halbe
        Kachelausdehnung normiert (sonst gewaenne bei einer flachen, breiten
        Kachel immer x und bei einer schmalen, hohen immer y), und die
        groessere der beiden Abweichungen entscheidet. Beide Richtungen
        zeigen dabei in dieselbe Leserichtung: das dichte Packing fuellt
        erst eine Spalte von oben nach unten und geht dann nach rechts
        (siehe pack_tiles_by_row), "weiter unten" und "weiter rechts"
        heissen also gleichermassen "dahinter". Diese Zweiachsigkeit ist
        noetig, seit Kacheln in der Kompaktansicht unterschiedlich breit
        sind und damit auch NEBENeinander in derselben Zeile liegen koennen
        (siehe COMPACT_COLUMN_UNIT) -- eine reine y-Entscheidung (obere/
        untere Haelfte) waere fuer zwei nebeneinanderliegende Kacheln
        unbedienbar.

        Wichtig ist dabei, die Zielbestimmung an der ANGEZEIGTEN Anordnung
        (also inklusive der gezogenen Kachel, die waehrend des Drags nur
        ausgeblendet ist, ihre Rasterzelle aber weiterhin belegt, siehe
        _start_panel_drag) auszurichten und die Einfuegestelle als
        LISTENindex neben der Treffer-Kachel zu bestimmen (BUGS_OFFEN.md
        #30). Zwei Fallen, in die die frueheren Anlaeufe getappt sind:

        * Die Zielzelle aus `pos` und die Kachelpositionen duerfen nicht aus
          zwei VERSCHIEDENEN Anordnungen stammen. Frueher wurde `pos` gegen
          das Bild auf dem Schirm (mit gezogener Kachel) gerechnet, die
          Kachelpositionen dagegen aus einem Packing OHNE sie -- das
          Entfernen der gezogenen Kachel packt aber alles neu, dieselbe
          Zellkoordinate meint in beiden Anordnungen etwas anderes. Beispiel
          (real nachgemessen): PSU/Last (Spalte 0), doppelt hohe microHIL
          (Spalte 1), CAN (Spalte 2); PSU auf die Mitte der microHIL-Kachel
          gezogen ergab die Zielzelle (Spalte 1, Zeile 1), im Packing ohne
          PSU liegt dort aber gar keine Kachel mehr -- die gezogene Kachel
          landete am Ende statt an der Zielstelle.
        * Ueber die Kachelpositionen nach einem Positions-Schluessel zu
          SUCHEN ist ohnehin unnoetig heikel: das dichte Packing ordnet bei
          gemischten Kachelhoehen NICHT monoton zur Listenreihenfolge (eine
          spaeter genannte kleine Kachel fuellt eine Luecke, die eine
          frueher genannte doppelt hohe ausgelassen hat, und liegt visuell
          VOR ihr). Der Listenindex der tatsaechlich getroffenen Kachel ist
          davon unabhaengig immer richtig."""
        order = [d for d in self._current_order() if d != dragged_id]
        if not order:
            return [dragged_id]

        point = pos.toPoint()
        # Rasterzellen der ANGEZEIGTEN Anordnung, also einschliesslich der
        # gezogenen (nur ausgeblendeten) Kachel.
        rects = self._tile_rects(self._current_order())

        own_rect = rects.get(dragged_id)
        if own_rect is not None and own_rect.contains(point):
            # Zeiger noch ueber der eigenen Luecke -- nichts umsortieren,
            # sonst zappelt die Vorschau beim Anheben der Kachel.
            return list(self._current_order())

        target = None
        for device_id in order:
            if rects[device_id].contains(point):
                target = device_id
                break
        if target is None:
            # Abgelegt zwischen/neben den Kacheln (Zwischenraum, freie
            # Flaeche rechts der letzten Spalte): naechstgelegene Kachel.
            target = min(order, key=lambda d: rect_distance(rects[d], point))

        rect = rects[target]
        # Auf die halbe Kachelausdehnung normierte Abweichung von der Mitte
        # (siehe Docstring): die dominante Richtung entscheidet, beide
        # zeigen in dieselbe Leserichtung (rechts/unten = dahinter).
        offset_x = (point.x() - rect.center().x()) / max(rect.width() / 2, 1)
        offset_y = (point.y() - rect.center().y()) / max(rect.height() / 2, 1)
        after = (offset_x if abs(offset_x) >= abs(offset_y) else offset_y) >= 0

        order.insert(order.index(target) + (1 if after else 0), dragged_id)
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
        # Bewusst die zuletzt VORGESCHAUTE Reihenfolge uebernehmen, statt sie
        # aus drop_pos neu zu rechnen: die Vorschau hat die Kacheln waehrend
        # des Ziehens laengst umsortiert, derselbe Bildschirmpunkt bezeichnet
        # in dieser neuen Anordnung also eine andere Stelle als noch beim
        # letzten DragMove. Ein Neuberechnen liesse die Kachel beim Loslassen
        # deshalb nochmal woanders hin springen als angezeigt -- genau das
        # sichtbare Fehlverhalten aus BUGS_OFFEN.md #30. Nur wenn ueberhaupt
        # keine Vorschau zustande kam (Drop ohne vorangegangenes DragMove),
        # wird aus drop_pos gerechnet.
        order = self._drag_preview_order
        if order is None:
            order = self._order_with_dragged_at(dragged_id, drop_pos)
        panel = self._drag_hidden_panel
        self._drag_hidden_panel = None
        self._drag_preview_order = None
        self._persist_panel_order(order)
        if panel is not None:
            self._set_keeps_space_when_hidden(panel, False)
            panel.show()

    def _persist_panel_order(self, order: list[str]) -> None:
        self._pending_panel_order = order
        self._relayout_panels()
        self.panel_order_changed.emit(order)

    def _check_layout(self) -> None:
        if self.isVisible():
            self._relayout_panels(only_if_changed=True)

    def _layout_is_current(self, order: list[str]) -> bool:
        """True, wenn eine vollstaendige Neuanordnung nichts veraendern wuerde: gleiche
        Ansicht und Reihenfolge wie beim letzten Mal, und keine der Ratschen (Breite je
        Kachel/gemeinsame Breite, Zellhoehe) wuerde wachsen.

        Jede Textaenderung in einer Kachel (Messwerte, 10 Hz je Geraet) loest ein LayoutRequest
        am Container aus. Die volle Neuanordnung (sizeHint aller Kacheln, alle Widgets aus dem
        Layout nehmen und neu einfuegen, Packen, activate) war dafuer jedes Mal fuellig: in der
        Kompaktansicht ~11 ms je Aufruf, rund 60-90 % der GUI-Last. Die Ratschen sorgen dafuer,
        dass sich die Breite nach unten ohnehin nicht aendert -- hier wird nur noch geprueft,
        ob sie wachsen muessten."""
        if self._laid_out != (self._compact, tuple(order), self._avail_width() if self._multi_row else 0):
            return False
        if self._compact:
            for device_id in order:
                if self._panels[device_id].sizeHint().width() > self._compact_widths.get(device_id, 0):
                    return False
            if self._multi_row:
                return self._multi_row_heights(order) == (self._compact_cell_height, self._row_spans)
            height_sizes = [(self._panels[d].sizeHint().height(), self._tile_span(d)[1]) for d in order]
            return cell_size_ratchet(self._compact_cell_height, height_sizes) == self._compact_cell_height
        if max(panel.sizeHint().width() for panel in self._panels.values()) > self._panel_width:
            return False
        if self._multi_row:
            return self._multi_row_heights(order) == (self._cell_height, self._row_spans)
        height_sizes = [(self._panels[d].sizeHint().height(), self._tile_span(d)[1]) for d in order]
        return cell_size_ratchet(self._cell_height, height_sizes) == self._cell_height

    def _relayout_panels(self, reset_width: bool = False, only_if_changed: bool = False) -> None:
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
            order = self._current_order()
            if only_if_changed and not reset_width and self._layout_is_current(order):
                return
            if self._compact:
                # Kompaktansicht: jede Kachel behaelt ihre eigene, an den
                # Inhalt geschmiegte Breite (Ratsche je Geraet, siehe Punkt 2
                # oben) -- sie wird lediglich auf die naechste Rasterweite
                # des feinen Spaltenrasters aufgerundet (COMPACT_COLUMN_UNIT,
                # ~10px Verschnitt im Mittel). Die daraus folgende
                # Spaltenspanne muss VOR dem Packing feststehen, da
                # _tile_span() sie von hier bezieht.
                for device_id, panel in self._panels.items():
                    width = max(self._compact_widths.get(device_id, 0), panel.sizeHint().width())
                    self._compact_widths[device_id] = width
                self._compact_col_spans = {
                    device_id: compact_column_span(self._compact_widths[device_id])
                    for device_id in order
                }
                for device_id in order:
                    panel = self._panels[device_id]
                    width = compact_span_width(self._compact_col_spans[device_id])
                    if self._multi_row:
                        # Touch, mehrzeilig: Kacheln melden dem Raster ihre Breite nicht (Ignored, Mindestbreite
                        # 0) -- sonst verteilte QGridLayout die Breiten ueberlappender Spannen ungenau, Spalten
                        # wurden breiter als 8 px und Kacheln ragten rechts ueber die Seite (am Pi gesehen).
                        # Jede Spalte bleibt so bei ihrer Mindestbreite, jede Kachel bekommt genau ihre Spannen.
                        self._set_horizontal_policy(panel, QSizePolicy.Policy.Ignored)
                        if panel.minimumWidth() != 0 or panel.maximumWidth() != width:
                            panel.setMinimumWidth(0)
                            panel.setMaximumWidth(width)
                    else:
                        self._set_horizontal_policy(panel, QSizePolicy.Policy.Preferred)
                        if panel.minimumWidth() != width or panel.maximumWidth() != width:
                            panel.setFixedWidth(width)
            else:
                max_width = max(panel.sizeHint().width() for panel in self._panels.values())
                self._panel_width = max(self._panel_width, max_width)
                width = self._panel_width
                if self._multi_row:
                    # Touch: Spalten teilen sich die ganze Breite (kein leerer Rand rechts)
                    cols = self._max_cols()
                    width = max(width, (self._avail_width() - (cols - 1) * GRID_SPACING) // cols)
                for panel in self._panels.values():
                    self._set_horizontal_policy(panel, QSizePolicy.Policy.Preferred)
                    if panel.minimumWidth() != width or panel.maximumWidth() != width:
                        panel.setFixedWidth(width)

            for panel in self._panels.values():
                self._panel_layout.removeWidget(panel)
            if self._compact:
                # Kompaktansicht: wie die Normalansicht ein bis zu 2-zeiliges
                # Raster (siehe pack_tiles_by_row/_tile_rects), frueher stur
                # auf Zeile 0 verdrahtet (Nutzerwunsch: schmale Kacheln
                # sollen sich per Drag&Drop eine Spalte teilen koennen).
                # Waagerecht laeuft es hier aber ueber das feine Raster
                # (COMPACT_COLUMN_UNIT) statt ueber eine Spalte je Kachel --
                # nur so passen unter eine breite Kachel zwei schmale
                # nebeneinander. Eigene Hoehenklassen (siehe
                # COMPACT_TILE_HEIGHT_BY_KIND/_tile_span): ANDERS als in der
                # Normalansicht ist hier NICHT jede Kachel einfach hoch --
                # microHIL (Digital-IO-Block) und PicoScope (Status ueber dem
                # Button) bleiben auch im Kompaktmodus zweizeilig und belegen
                # deshalb IMMER beide Zeilen zusammen, statt allein in eine
                # Zeile gezwaengt deren Hoehe fuer alle anderen zu verzerren
                # (Bugmeldung: "Zeilenhoehe passt nicht").
                if self._multi_row:   # Touch: hohe Kacheln spannen Zeilen nach Bedarf (_multi_row_heights)
                    self._compact_cell_height, self._row_spans = self._multi_row_heights(order)
                compact_spans = {d: self._tile_span(d) for d in order}
                compact_positions = self._pack(order, compact_spans)
                if not self._multi_row:
                    height_sizes = [(self._panels[d].sizeHint().height(), compact_spans[d][1]) for d in order]
                    self._compact_cell_height = cell_size_ratchet(self._compact_cell_height, height_sizes)
                self._size_rows(self._used_rows(compact_positions, compact_spans), self._compact_cell_height)
                # Alle Rasterspalten auf dieselbe Mindestbreite: nur dann
                # sind die Zellgrenzen (cellRect, siehe _tile_rects) auch in
                # Spalten definiert, die gerade von keiner Kachel BEGONNEN
                # werden, und eine Spanne von n Einheiten ist ueberall
                # gleich breit. Ueberzaehlige Spalten aus einer frueheren,
                # breiteren Anordnung werden dabei zurueckgesetzt.
                used_columns = max(
                    (col + compact_spans[d][0] for d, (row, col) in compact_positions.items()),
                    default=0,
                )
                # Touch, mehrzeilig: Spaltenabstand in die Spalte eingerechnet (20 statt 8 px, Raster ohne
                # waagerechten Abstand) -- QGridLayout laesst den Abstand bei Spalten ohne beginnende Kachel
                # weg, Spannen wurden dann zu schmal, sobald die Kacheln ihre Breite nicht mehr festlegen.
                unit = COMPACT_COLUMN_UNIT + GRID_SPACING if self._multi_row else COMPACT_COLUMN_UNIT
                self._panel_layout.setHorizontalSpacing(0 if self._multi_row else GRID_SPACING)
                for column in range(max(used_columns, self._compact_column_count)):
                    self._panel_layout.setColumnMinimumWidth(column, unit if column < used_columns else 0)
                self._compact_column_count = used_columns
                for device_id, (row, col) in compact_positions.items():
                    panel = self._panels[device_id]
                    col_span, row_span = compact_spans[device_id]
                    panel.setFixedHeight(row_span * self._compact_cell_height + (row_span - 1) * GRID_SPACING)
                    self._panel_layout.addWidget(
                        panel, row, col, row_span, col_span, alignment=Qt.AlignmentFlag.AlignTop
                    )
            else:
                # Mindestbreiten des Kompakt-Rasters wieder aufheben -- die
                # Normalansicht hat eine Spalte je Kachel, eine stehen
                # gebliebene 8px-Mindestbreite je Rastereinheit wuerde dort
                # eine leere Geisterspalte rechts erzeugen.
                for column in range(self._compact_column_count):
                    self._panel_layout.setColumnMinimumWidth(column, 0)
                self._compact_column_count = 0
                self._panel_layout.setHorizontalSpacing(GRID_SPACING)   # siehe Kompakt/Touch oben
                # Hoehen-Ratsche (analog zur Breiten-Ratsche oben): stellt
                # sicher, dass die Basis-Zellhoehe im Normalfall auch die
                # deutlich groessere microHIL-Kachel (doppelte Hoehe) bequem
                # fasst.
                if self._multi_row:
                    self._cell_height, self._row_spans = self._multi_row_heights(order)
                else:
                    height_sizes = [(self._panels[d].sizeHint().height(), self._tile_span(d)[1]) for d in order]
                    self._cell_height = cell_size_ratchet(self._cell_height, height_sizes)
                # Erzwingt die volle Doppelzeilen-Hoehe auch dann, wenn
                # aktuell gar keine doppelt hohe Kachel verbunden ist (siehe
                # FEATURES.md Punkt 5: "doppelte Hoehe als maximale
                # Ausdehnung") -- sonst wuerde die ScrollArea unten kleiner
                # ausfallen und beim naechsten Verbinden einer microHIL
                # sichtbar in der Hoehe springen.
                spans = {d: self._tile_span(d) for d in order}
                positions = self._pack(order, spans)
                self._size_rows(self._used_rows(positions, spans), self._cell_height)
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
            self._laid_out = (self._compact, tuple(order), self._avail_width() if self._multi_row else 0)

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
                panel.launch_requested.connect(
                    lambda d=device_id: self.launch_picoscope(d))
                panel.release_requested.connect(self.picoscope_release_requested)
                panel.plot_requested.connect(self.picoscope_plot_requested)
            elif kind == "scpiscope":
                # SCPI-Oszilloskop (UNI-T MSO1000HD): dieselbe Kachel, ohne Start-Knopf.
                panel = PicoscopePanel(device_id, label, with_launch=False)
                panel.release_requested.connect(self.picoscope_release_requested)
                panel.plot_requested.connect(self.picoscope_plot_requested)
            else:
                field_keys = {"load": LOAD_FIELD_KEYS, "psu": PSU_FIELD_KEYS, "fg": FG_FIELD_KEYS,
                              "battsim": BATTSIM_FIELD_KEYS, "jlink": JLINK_FIELD_KEYS}.get(
                    kind, CAN_FIELD_KEYS
                )
                panel = _DevicePanel(kind, device_id, label, field_keys)
            if self._compact:
                panel.set_compact(True)
            if self._large and hasattr(panel, "set_large"):
                panel.set_large(True)
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
        Erinnerung stehen laesst. Zwei Aufrufer: der "Geraetezuordnung
        loeschen"-Button (main_window._on_reset_devices_requested) fuer ALLE
        Geraete, und main_window._on_can_configs_changed fuer ein einzelnes,
        in den Einstellungen geloeschtes CAN-Interface (kein Hotplug, siehe
        dort -- eine ausgegraute Karteileiche waere dort reine Verwirrung).
        Ein noch VERBUNDENES Geraet nutzt stattdessen den Live-Relabel-Pfad
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
    def set_fg_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_picoscope_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_battsim_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_jlink_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    def update_jlink(self, device_id: str, state: dict) -> None:
        """J-Link (PI-31 a): Ziel, Zielspannung, letzte Bedienung -- Stand der letzten Bedienung,
        der J-Link wird nicht im Hintergrund abgefragt."""
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_value("jl_target", state.get("device") or "--")
        vtref = state.get("vtref")
        panel.set_value("jl_vtref", f"{vtref:.2f}" if isinstance(vtref, (int, float)) else "--")
        if state.get("busy"):
            last = tr("läuft …")
        elif "ok" in state:
            last = ("✓ " if state.get("ok") else "✕ ") + state.get("time", "")
        else:
            last = "--"
        panel.set_value("jl_last", last)

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

    @Slot(str, object)
    def update_fg(self, device_id: str, state: dict) -> None:
        """Eine Textzeile je Kanal: Wellenform, Frequenz, Amplitude -- oder
        "aus". Ein Generator hat keine Messwerte; angezeigt wird der zuletzt
        vom Geraet zurueckgelesene Einstellungsstand (siehe
        device_worker.fg_state)."""
        panel = self._panels.get(device_id)
        if panel is None:
            return
        for n, (on, ch) in enumerate(zip(state["outputs"], state["channels"]), start=1):
            if not on:
                panel.set_value(f"fg_ch{n}", tr("aus"))
                continue
            panel.set_value(
                f"fg_ch{n}",
                f"{tr(waveform_name(ch['waveform']))}  {_format_frequency(ch['frequency'])}  {ch['amplitude']:.2f} V",
            )

    @Slot(str, object)
    def update_battsim(self, device_id: str, state: dict) -> None:
        """Zusammenfassung des Batterie-Simulators: Zustand, Ausgang, Stapel und
        kleinste/groesste Zellspannung (die Einzelwerte zeigt die Control-Kachel)."""
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_value("bs_state", state.get("status_name") or "--")
        output_on = state.get("output_on")
        panel.set_value("bs_output", "--" if output_on is None else (tr("ein") if output_on else tr("aus")))
        stack = state.get("stack_voltage")
        panel.set_value("bs_stack", "--" if stack is None else f"{stack:.3f}")
        # Nur eingeschaltete Zellen: eine abgeschaltete ist gebrueckt (0 V) und
        # wuerde das Minimum der simulierten Zellen verdecken.
        volts = [c["voltage"] for c in state.get("cells", [])
                 if c.get("voltage") is not None and c.get("enabled")]
        panel.set_value("bs_cell_min", f"{min(volts):.3f}" if volts else "--")
        panel.set_value("bs_cell_max", f"{max(volts):.3f}" if volts else "--")
        # FAIL-Grund und Regler kommen aus 0x121 der neuen Firmware; die alte sendet sie nicht
        panel.set_value("bs_fail", state.get("fail_reason_name") or "--")
        controller_on = state.get("controller_on")
        panel.set_value("bs_controller", "--" if controller_on is None else (tr("ein") if controller_on else tr("aus")))

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

    @Slot(str, str, str)
    def update_picoscope_capture(self, device_id: str, capture_id: str, text: str) -> None:
        panel = self._panels.get(device_id)
        if isinstance(panel, PicoscopePanel):
            panel.set_last_capture(text)

    def launch_picoscope(self, device_id: str = "") -> None:
        """Auch vom Knopf der Control-Kachel (control_tab.PicoscopeControlGroup)."""
        # Haelt der ScopeService das Geraet (Erfassungen ueber MCP), vorher
        # trennen -- sonst kann PicoScope 7 es nicht oeffnen (exklusiv). Das
        # Trennen laeuft im Scope-Thread und ist lange vor dem App-Start fertig.
        if device_id:
            self.picoscope_release_requested.emit(device_id)
        # Direkt hier statt ueber den DeviceWorker/Thread geroutet: reiner
        # Prozessstart der PicoScope-7-App (kein Zugriff auf das offene
        # Geraete-Handle, siehe picoscope2000.driver.launch_app), also ohne
        # die Thread-Sicherheits-Gruende, die die uebrigen Steuerbefehle
        # zwingend ueber den Worker laufen lassen.
        launch_picoscope_app()

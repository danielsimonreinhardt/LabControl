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
from PySide6.QtCore import QEvent, QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from i18n import Translator, tr
from icons import IconButton
from theme import Palette, ThemeManager, no_own_background
from theme import current as current_palette

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

# field_key -> (deutscher Basis-Anzeigename, Einheit); Einheit ist
# sprachunabhaengig und wird nicht ueber i18n.tr uebersetzt.
FIELD_DEFS: dict[str, tuple[str, str]] = {
    "voltage": ("Spannung", "V"),
    "current": ("Strom", "A"),
    "power": ("Leistung", "W"),
    "mode": ("Modus", ""),
}
LOAD_FIELD_KEYS = ["voltage", "current", "power"]
PSU_FIELD_KEYS = ["voltage", "current", "mode"]
KIND_TITLE = {"load": "Elektronische Last", "psu": "Labornetzteil"}
# Ersetzt die bisherige Geraeteart-Textzeile im Normal-Panel: platzsparendes
# Icon unten rechts im Panel statt einer eigenen Zeile, voller Name als
# Tooltip (siehe KIND_TITLE) weiterhin erreichbar.
KIND_ICON = {"load": "mdi.resistor", "psu": "mdi.power-plug-outline"}

# Icons fuer die Kompaktansicht: dort ersetzen sie die Text-Beschriftung der
# Messgroessen komplett (der volle Name bleibt als Tooltip erreichbar).
FIELD_ICONS: dict[str, str] = {
    "voltage": "mdi.flash-outline",
    "current": "mdi.current-dc",
    "power": "mdi.gauge",
    "mode": "mdi.swap-horizontal-bold",
}


def _field_display(field_key: str) -> str:
    name, unit = FIELD_DEFS[field_key]
    return f"{tr(name)} ({unit})" if unit else tr(name)


class _DevicePanel(QGroupBox):
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, kind: str, device_id: str, label: str, field_keys: list[str]) -> None:
        super().__init__()
        self._kind = kind
        self._device_id = device_id
        self._field_keys = field_keys
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

        self._rename_button = IconButton("mdi.pencil-outline", "")
        self._rename_button.clicked.connect(self._on_rename_clicked)
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
            if i == 0:
                # Umbenennen-Button oben rechts, neben der ersten Werteanzeige
                # -- spart die eigene Kopfzeile, die er vorher belegt hat.
                row_layout.addSpacing(6)
                row_layout.addWidget(self._rename_button)
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

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._kind_icon.setToolTip(tr(KIND_TITLE.get(self._kind, self._kind)))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        for field_key in self._field_keys:
            self._form.labelForField(self._value_rows[field_key]).setText(_field_display(field_key) + ":")
            tooltip = _field_display(field_key)
            self._compact_icons[field_key].setToolTip(tooltip)
            self._compact_values[field_key].setToolTip(tooltip)

    def _on_theme_changed(self, palette: Palette) -> None:
        self._apply_compact_icons(palette)
        self._apply_kind_icon(palette)

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

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(
            self, tr("Gerät umbenennen"), tr("Name:"), text=self.title()
        )
        if ok and new_label.strip():
            self.rename_requested.emit(self._kind, self._device_id, new_label.strip())


class DashboardWidget(QGroupBox):
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label
    # Klick auf den Ansicht-Umschalter unten rechts; MainWindow verdrahtet ihn
    # mit der persistierten Einstellung, die dann set_compact zurueckruft.
    compact_toggle_requested = Signal()

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
        self._panel_layout = QHBoxLayout(self._container)
        self._panel_layout.addStretch()

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
        self._compact = False
        # Aktuell angeglichene Panel-Breite (0 = noch keine gesetzt) -- als
        # Ratsche gefuehrt, siehe _relayout_panels. In der Kompaktansicht
        # stattdessen eine Ratsche je Panel (device_id -> Breite), weil die
        # Panels dort bewusst unterschiedlich breit sind.
        self._panel_width = 0
        self._compact_widths: dict[str, int] = {}
        self._relayout_panels()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

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
        if obj is self._container and event.type() == QEvent.Type.LayoutRequest:
            self._relayout_panels()
        return super().eventFilter(obj, event)

    def _relayout_panels(self, reset_width: bool = False) -> None:
        # Panel-Breite/-Hoehe ergeben sich aus dem tatsaechlichen Inhalt
        # (Schrift, Uebersetzung, DPI, ...), nicht aus festen Konstanten --
        # sonst werden bei laengeren Texten/anderen Schriftarten Werte oder
        # der untere Teil der Panels abgeschnitten.
        #
        # Breite (nur Normalansicht): alle Panels auf die breiteste
        # Anforderung angleichen (analog zu ControlTab._equalize_sections) --
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

        # Hoehe: etwas Rand fuer eine ggf. sichtbare horizontale Scrollleiste
        # einrechnen.
        content_height = self._container.sizeHint().height()
        self._scroll_area.setFixedHeight(content_height + SCROLL_AREA_MARGIN)

    # -- Geraete-Lebenszyklus --------------------------------------------------

    @Slot(str, str, str)
    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            field_keys = LOAD_FIELD_KEYS if kind == "load" else PSU_FIELD_KEYS
            panel = _DevicePanel(kind, device_id, label, field_keys)
            if self._compact:
                panel.set_compact(True)
            panel.rename_requested.connect(self.rename_requested)
            panel.hide()
            self._panel_layout.insertWidget(self._panel_layout.count() - 1, panel)
            self._panels[device_id] = panel
            self._relayout_panels()
        else:
            panel.set_label(label)

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

    def _set_online(self, device_id: str, online: bool) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.setVisible(online)
        if not online:
            panel.clear_values()
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

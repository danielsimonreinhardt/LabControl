"""Dashboard-Kachel fuer das PicoScope (siehe picoscope2000/driver.py).

Anders als dashboard._DevicePanel (generische Messwerteliste) zeigt diese
Kachel bewusst NUR den Verbindungs-/Belegt-Status plus Geraeteinfo --
LabControl kopiert nicht den Funktionsumfang der PicoScope-7-App (Kanaele,
Trigger, Kurvenanzeige), sondern bietet stattdessen einen Start-Button dafuer
(siehe picoscope2000/README.md: "Andere Geraeteklasse als PSU/Last").

"Belegt" bedeutet: physisch angeschlossen (device_worker.picoscope_connected),
aber LabControl konnte es beim letzten Reconnect-Tick nicht selbst oeffnen --
typischerweise, weil die PicoScope-7-App es gerade offen haelt (siehe
device_worker._reconnect_picoscope). variant/serial bleiben in diesem Fall
auf dem zuletzt bekannten Stand stehen, falls schon einmal ermittelt.

"Testlauf laeuft" (status "test") bedeutet: LabControl selbst haelt das
Geraet gerade fuer einen laufenden Testablauf offen (siehe device_worker.
open_picoscope_session) -- die periodische Reconnect-Probe pausiert
waehrenddessen, der Status bleibt bis zum Laufende auf "test" stehen statt
faelschlich "frei"/"belegt" zu suggerieren.
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from i18n import Translator, tr
from icons import IconButton
from no_device_tile import OFFLINE_BACKGROUND, OFFLINE_BORDER
from theme import Palette, ThemeManager, no_own_background
from theme import current as current_palette

STATUS_ICON = {
    "free": "mdi.check-circle-outline", "busy": "mdi.lock-outline", "test": "mdi.flask-outline",
}
STATUS_TEXT = {"free": "Frei", "busy": "Belegt (andere App?)", "test": "Belegt (Testlauf läuft)"}
STATUS_ICON_SIZE = 18


class PicoscopePanel(QGroupBox):
    launch_requested = Signal()

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._online = True
        self._status = "busy"
        self._variant = ""
        self._serial = ""
        self._color_key: str | None = None

        self.setTitle(label)
        outer = QVBoxLayout(self)

        # Status + Typenbezeichnung in EINER Zeile statt zwei (Nutzerfeedback:
        # die Kachel wirkte in der Kompaktansicht unnoetig hoch, siehe
        # set_compact()-Docstring -- diese Kachel hat ohnehin keine eigene
        # Kompaktansicht, die Ersparnis kommt also in BEIDEN Ansichten an).
        # self._variant_label sitzt bewusst IM SELBEN no_own_background()-
        # Wrapper wie Status-Icon/-Text -- eine fruehere Fassung haengte die
        # Typenbezeichnung als eigenes QLabel DIREKT in outer, das dadurch
        # (siehe theme.no_own_background-Docstring, exakt derselbe Bug wie
        # BUGS.md #8/#9) die globale QWidget{background-color: pal.bg}-Regel
        # erbte und als sichtbar andersfarbiger Kasten von der individuellen
        # Panel-Faerbung absetzte (Nutzer-Screenshot: weisses Rechteck auf
        # sonst pfirsichfarbener Kachel).
        status_row = no_own_background(QWidget())
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self._status_icon = QLabel()
        self._status_text = QLabel()
        self._variant_label = QLabel()
        status_layout.addWidget(self._status_icon)
        status_layout.addWidget(self._status_text)
        status_layout.addWidget(self._variant_label)
        status_layout.addStretch()
        outer.addWidget(status_row)

        # Anders als die uebrigen Dashboard-Kacheln (reine Statusanzeige)
        # erwartet diese Kachel aktive Bedienung -- ein normaler, neutral
        # gestylter QPushButton ging darin optisch unter (Nutzerfeedback).
        # Permanent accentfarben statt nur bei Hover, dieselbe Farbkombi wie
        # theme.form_control_qss()s QPushButton:hover-Regel (accent-Hintergrund
        # + surface-Vordergrund), die dank Palette-Design in beiden Themes
        # kontrastiert (helle surface auf dunklerem Accent im Light-Theme,
        # dunkle surface auf hellem Accent im Amber-Dark-Theme).
        self._launch_button = IconButton("mdi.open-in-new", "", text=tr("PicoScope 7 öffnen"))
        self._launch_button.setObjectName("picoscopeLaunchButton")
        self._launch_button.set_color_override(current_palette().surface)
        self._launch_button.clicked.connect(self.launch_requested)
        outer.addWidget(self._launch_button)

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()
        self._apply_style(current_palette())
        self._apply_status_icon(current_palette())
        self._apply_variant_style(current_palette())

    def _retranslate(self) -> None:
        self._launch_button.setText(tr("PicoScope 7 öffnen"))
        self._status_text.setText(tr(STATUS_TEXT.get(self._status, "Unbekannt")))

    def _on_theme_changed(self, palette: Palette) -> None:
        self._apply_style(palette)
        self._apply_status_icon(palette)
        self._apply_variant_style(palette)
        self._launch_button.set_color_override(palette.surface)

    def _apply_variant_style(self, palette: Palette) -> None:
        self._variant_label.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")

    def _apply_status_icon(self, palette: Palette) -> None:
        icon_name = STATUS_ICON.get(self._status, "mdi.help-circle-outline")
        color = palette.check_pass if self._status == "free" else palette.text_muted
        self._status_icon.setPixmap(
            qta.icon(icon_name, color=color).pixmap(STATUS_ICON_SIZE, STATUS_ICON_SIZE)
        )

    def set_panel_color(self, color_key: str | None) -> None:
        self._color_key = color_key
        self._apply_style(current_palette())

    def _apply_style(self, palette: Palette) -> None:
        # Der Launch-Button bekommt IMMER (auch offline/eingefaerbt) seine
        # eigene, vom GroupBox-Hintergrund unabhaengige Akzentfaerbung --
        # daher als eigener Selektor an jeden der drei Zweige unten angehaengt
        # statt nur einmal am Ende gesetzt zu werden (setStyleSheet ersetzt
        # das gesamte Stylesheet des Widgets inkl. aller Kind-Selektoren).
        button_rule = (
            f"QPushButton#picoscopeLaunchButton {{"
            f" background-color: {palette.accent}; color: {palette.surface};"
            f" border: 1px solid {palette.accent}; border-radius: 4px;"
            f" font-weight: bold; padding: 6px 12px; }}"
            f"QPushButton#picoscopeLaunchButton:hover {{"
            f" background-color: {palette.accent_hover}; border-color: {palette.accent_hover}; }}"
            f"QPushButton#picoscopeLaunchButton:pressed {{"
            f" background-color: {palette.accent_hover}; }}"
        )
        if not self._online:
            self.setStyleSheet(
                f"QGroupBox {{ background-color: {OFFLINE_BACKGROUND}; "
                f"border: 1px solid {OFFLINE_BORDER}; border-radius: 6px; }}"
                f"{button_rule}"
            )
            return
        border_rule = f"border: 1px solid {palette.text_muted}; border-radius: 6px;"
        if self._color_key is None:
            self.setStyleSheet(f"QGroupBox {{ {border_rule} }}{button_rule}")
            return
        hex_color = palette.panel_tints.get(self._color_key)
        bg_rule = f"background-color: {hex_color};" if hex_color else ""
        self.setStyleSheet(f"QGroupBox {{ {border_rule} {bg_rule} }}{button_rule}")

    def set_online(self, online: bool) -> None:
        """online = physische USB-Praesenz (picoscope_connected-Signal), NICHT
        dasselbe wie "frei" -- siehe set_state()."""
        self._online = online
        if not online:
            self._status = "busy"
            self._variant = ""
            self._serial = ""
            self._variant_label.setText("")
            self._variant_label.setToolTip("")
        self.setVisible(True)
        self._apply_style(current_palette())
        self._apply_status_icon(current_palette())
        self._retranslate()

    def set_state(self, status: str, variant: str, serial: str) -> None:
        self._status = status
        self._variant = variant
        self._serial = serial
        self._apply_status_icon(current_palette())
        self._retranslate()
        # Nur die kurze Typenbezeichnung sichtbar (Nutzerfeedback: "reicht
        # das kompakte Format, z.B. nur 2204A") -- die Seriennummer bleibt
        # als Tooltip erreichbar statt die Zeile zu verlaengern.
        if variant:
            self._variant_label.setText(f"· {variant}")
            self._variant_label.setToolTip(f"{variant} ({serial})" if serial else variant)
        else:
            self._variant_label.setText("")
            self._variant_label.setToolTip("")

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_compact(self, compact: bool) -> None:
        # Bewusst keine eigene Kompaktansicht -- wenig Inhalt (Status + ein
        # Button), der in beiden Ansichten gleich gut klickbar bleiben soll,
        # anders als dashboard._DevicePanel/MicroHilPanel.
        pass

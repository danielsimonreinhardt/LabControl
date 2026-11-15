"""Kachel "Pruefling (XCP)" in der Steuerung (0.64.3, FT-10): Messliste abfragen (Start/Stopp) und die aktuellen
Werte zeigen. Verstellen nur ueber Ablaufschritte (Rueckleseprobe, Grenzen der A2L). Die Abfrage-Werte gehen
zusaetzlich in Verlauf, Aufzeichnung und Zeitpruefungen (MainWindow)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QInputDialog, QLabel, QVBoxLayout

from i18n import Translator, tr
from icons import IconButton
from panel_color import PanelColorButton, apply_panel_tint
from theme import Palette, ThemeManager, set_tone
from theme import current as current_palette

MAX_LINES = 12


class XcpControlGroup(QGroupBox):
    polling_requested = Signal(bool)
    panel_color_requested = Signal(str, object)
    rename_requested = Signal(str, str, str)

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
        self._state: dict = {}
        self._values: dict = {}
        self._units: dict = {}
        self._module = ""
        self.setTitle(label)
        outer = QVBoxLayout(self)
        self._subtitle = QLabel()
        self._color_button = PanelColorButton()
        self._color_button.color_selected.connect(lambda key: self.panel_color_requested.emit(self._device_id, key))
        self._rename_button = IconButton("mdi.pencil-outline", "")
        self._rename_button.clicked.connect(self._on_rename_clicked)
        top = QHBoxLayout()
        top.addWidget(self._subtitle, 1)
        top.addWidget(self._color_button)
        top.addWidget(self._rename_button)
        outer.addLayout(top)
        row = QHBoxLayout()
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._poll_button = IconButton("mdi.play-circle-outline", "")
        self._poll_button.clicked.connect(lambda: self.polling_requested.emit(not self._state.get("active")))
        row.addWidget(self._status, 1)
        row.addWidget(self._poll_button)
        outer.addLayout(row)
        self._values_label = QLabel()
        self._values_label.setWordWrap(True)
        self._values_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self._values_label)
        outer.addStretch(1)
        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._on_theme_changed(current_palette())
        self._retranslate()

    def _retranslate(self) -> None:
        state = self._state
        self._subtitle.setText(tr("Prüfling (XCP)") + (f" · {self._module}" if self._module else ""))
        self._color_button.setToolTip(tr("Panel-Farbe wählen…"))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        active = bool(state.get("active"))
        self._poll_button.set_icon("mdi.stop-circle-outline" if active else "mdi.play-circle-outline")
        self._poll_button.setToolTip(tr("Messliste stoppen") if active else tr("Messliste abfragen"))
        error = state.get("error", "")
        names = state.get("names", [])
        if error:
            text = tr("Fehler: {error}", error=error)
        elif active:
            text = tr("misst {n} Größen mit {rate:g} Hz", n=len(names), rate=state.get("rate_hz", 0))
        else:
            text = tr("Messliste: {n} Größen", n=len(names)) if names else tr("keine Messliste")
        self._status.setText(text)
        set_tone(self._status, "danger" if error else "success" if active else "muted")
        lines = []
        for name in list(self._values)[:MAX_LINES]:
            unit = self._units.get(name, "")
            lines.append(f"{name} = {self._values[name]:g}{' ' + unit if unit else ''}")
        if len(self._values) > MAX_LINES:
            lines.append(tr("… {n} weitere", n=len(self._values) - MAX_LINES))
        self._values_label.setText("\n".join(lines))

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")
        apply_panel_tint(self, self._color_key)

    def set_module(self, module: str) -> None:
        self._module = module
        self._retranslate()

    def set_state(self, state: dict) -> None:
        self._state = dict(state)   # letzte Werte bleiben nach dem Stopp stehen
        self._retranslate()

    def set_values(self, payload: dict) -> None:
        self._values = dict((payload or {}).get("values", {}))
        self._units = dict((payload or {}).get("units", {}))
        self._retranslate()

    # -- Kachel-Schnittstelle --------------------------------------------------------------

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_panel_color(self, color_key: str | None) -> None:
        self._color_key = color_key
        apply_panel_tint(self, color_key)
        self._color_button.set_current_color(color_key)

    def set_colors_enabled(self, enabled: bool) -> None:
        self._color_button.setVisible(enabled)

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(self, tr("Gerät umbenennen"), tr("Name:"), text=self.title())
        if ok and new_label.strip():
            self.rename_requested.emit("xcp", self._device_id, new_label.strip())

    def capture_state(self) -> dict:
        return {}

    def apply_state(self, state: dict) -> None:
        pass

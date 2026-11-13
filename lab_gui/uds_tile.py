"""Kachel "Pruefling (UDS)" in der Steuerung (FT-1, 0.60.2): Session wechseln, entsperren, DID lesen (dekodiert
mit Einheiten aus dem ODX), Fehlerspeicher lesen/loeschen, Reset. Kompakt fuer 1024 x 600: Auswahllisten und
Symbolknoepfe mit Tooltip. Die Befehle gehen als Signal an MainWindow -> UdsService; das Ergebnis kommt ueber
set_state zurueck."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QVBoxLayout,
)

from i18n import Translator, tr
from icons import IconButton
from panel_color import PanelColorButton, apply_panel_tint
from theme import Palette, ThemeManager, set_tone
from theme import current as current_palette


def format_values(decoded: dict, did=None) -> str:
    units = {e.name: e.unit for e in did.elements} if did is not None else {}
    parts = []
    for name, value in decoded.items():
        text = f"{value:g}" if isinstance(value, float) else str(value)
        unit = units.get(name, "")
        parts.append(f"{name} = {text}{' ' + unit if unit else ''}")
    return "\n".join(parts)


class UdsControlGroup(QGroupBox):
    command_requested = Signal(str, object)     # Befehl (uds_service.COMMANDS), Argumente
    panel_color_requested = Signal(str, object)
    rename_requested = Signal(str, str, str)

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
        self._odx = None
        self._state: dict = {}
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

        self._form = QFormLayout()
        outer.addLayout(self._form)
        self._session_label = QLabel()
        self._last_label = QLabel()
        self._last_label.setWordWrap(True)
        self._form.addRow(" ", self._session_label)
        self._form.addRow(" ", self._last_label)

        session_row = QHBoxLayout()
        self._session_combo = QComboBox()
        self._session_button = IconButton("mdi.swap-horizontal", "")
        self._session_button.clicked.connect(
            lambda: self.command_requested.emit("session", {"session": self._session_combo.currentData()}))
        self._unlock_button = IconButton("mdi.lock-open-variant-outline", "")
        self._unlock_button.clicked.connect(lambda: self.command_requested.emit("unlock", {}))
        session_row.addWidget(self._session_combo, 1)
        session_row.addWidget(self._session_button)
        session_row.addWidget(self._unlock_button)
        outer.addLayout(session_row)

        did_row = QHBoxLayout()
        self._did_combo = QComboBox()
        self._read_button = IconButton("mdi.book-open-variant", "")
        self._read_button.clicked.connect(
            lambda: self.command_requested.emit("read_did", {"did": self._did_combo.currentData()}))
        did_row.addWidget(self._did_combo, 1)
        did_row.addWidget(self._read_button)
        outer.addLayout(did_row)
        self._values_label = QLabel()
        self._values_label.setWordWrap(True)
        self._values_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self._values_label)

        dtc_row = QHBoxLayout()
        self._dtc_read_button = IconButton("mdi.alert-circle-outline", "")
        self._dtc_read_button.clicked.connect(lambda: self.command_requested.emit("read_dtcs", {"mask": 0xFF}))
        self._dtc_clear_button = IconButton("mdi.delete-sweep-outline", "")
        self._dtc_clear_button.clicked.connect(self._on_clear_clicked)
        self._reset_button = IconButton("mdi.restart", "")
        self._reset_button.clicked.connect(self._on_reset_clicked)
        dtc_row.addWidget(self._dtc_read_button)
        dtc_row.addWidget(self._dtc_clear_button)
        dtc_row.addStretch(1)
        dtc_row.addWidget(self._reset_button)
        outer.addLayout(dtc_row)
        self._dtc_label = QLabel()
        self._dtc_label.setWordWrap(True)
        outer.addWidget(self._dtc_label)
        outer.addStretch(1)
        self._buttons = (self._session_button, self._unlock_button, self._read_button, self._dtc_read_button,
                         self._dtc_clear_button, self._reset_button)

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._on_theme_changed(current_palette())
        self._retranslate()

    # -- Darstellung -------------------------------------------------------------------

    def _retranslate(self) -> None:
        state = self._state
        self._subtitle.setText(tr("Prüfling (UDS)") + (f" · {self._odx.name}" if self._odx is not None else ""))
        self._color_button.setToolTip(tr("Panel-Farbe wählen…"))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        self._form.labelForField(self._session_label).setText(tr("Session:"))
        self._form.labelForField(self._last_label).setText(tr("Zuletzt:"))
        session = state.get("session") or "Default"
        self._session_label.setText(session + (" · " + tr("entsperrt") if state.get("unlocked") else ""))
        last = state.get("last", "")
        self._last_label.setText(last or "--")
        set_tone(self._last_label, "muted" if not last or state.get("busy")
                 else "success" if state.get("ok") else "danger")
        self._values_label.setText(state.get("values", ""))
        self._dtc_label.setText(state.get("dtcs", ""))
        self._session_button.setToolTip(tr("Session wechseln"))
        self._unlock_button.setToolTip(tr("Entsperren (SecurityAccess)"))
        self._read_button.setToolTip(tr("DID lesen"))
        self._dtc_read_button.setToolTip(tr("Fehlerspeicher lesen"))
        self._dtc_clear_button.setToolTip(tr("Fehlerspeicher löschen"))
        self._reset_button.setToolTip(tr("Steuergerät zurücksetzen (Reset)"))
        busy = bool(state.get("busy"))
        for button in self._buttons:
            button.setEnabled(not busy and self._odx is not None)

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")
        apply_panel_tint(self, self._color_key)

    def set_odx(self, odx) -> None:
        self._odx = odx
        for combo, items in ((self._session_combo, [(f"{n} (0x{i:02X})", n) for n, i in odx.sessions.items()]
                              if odx else []),
                             (self._did_combo, [(f"{d.name} (0x{d.did:04X})", d.did) for d in odx.dids.values()
                                                if d.read] if odx else [])):
            current = combo.currentData()
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
            combo.setCurrentIndex(max(0, combo.findData(current)))
        self._retranslate()

    def set_state(self, state: dict) -> None:
        self._state = dict(state)
        self._retranslate()

    # -- Rueckfragen -----------------------------------------------------------------------

    def _on_clear_clicked(self) -> None:
        if QMessageBox.question(self, tr("Fehlerspeicher löschen"),
                                tr("Alle Einträge im Fehlerspeicher des Prüflings löschen?")) == QMessageBox.StandardButton.Yes:
            self.command_requested.emit("clear_dtcs", {})

    def _on_reset_clicked(self) -> None:
        if QMessageBox.question(self, tr("Reset"), tr("Steuergerät zurücksetzen?")) == QMessageBox.StandardButton.Yes:
            self.command_requested.emit("reset", {"type": 1})

    # -- Kachel-Schnittstelle (wie die anderen ControlGroups) --------------------------------

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
            self.rename_requested.emit("uds", self._device_id, new_label.strip())

    def capture_state(self) -> dict:
        return {}

    def apply_state(self, state: dict) -> None:
        pass

"""Kachel "Fehlerbox" in der Steuerung (FT-9, 0.61.0): je Fehler eine Taste, der aktive ist markiert, dazu
"Aufheben". Setzen ist abgelehnt, solange ein anderer Fehler aktiv ist (FP-26 c: an der Kachel bewusst von Hand);
die Schaltfolge steht im Tooltip. Waehrend eines Testlaufs gesperrt."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QPushButton, QVBoxLayout

from i18n import Translator, tr
from icons import IconButton
from panel_color import PanelColorButton, apply_panel_tint
from theme import Palette, ThemeManager, set_tone
from theme import current as current_palette

COLUMNS = 2


class FaultBoxControlGroup(QGroupBox):
    fault_requested = Signal(str, str)          # "set" + Name | "clear" + ""
    panel_color_requested = Signal(str, object)
    rename_requested = Signal(str, str, str)

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
        self._state: dict = {}
        self._faults: list = []
        self._buttons: dict[str, QPushButton] = {}
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
        self._grid = QGridLayout()
        outer.addLayout(self._grid)
        bottom = QHBoxLayout()
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._clear_button = IconButton("mdi.restore", "")
        self._clear_button.clicked.connect(lambda: self.fault_requested.emit("clear", ""))
        bottom.addWidget(self._status, 1)
        bottom.addWidget(self._clear_button)
        outer.addLayout(bottom)
        outer.addStretch(1)
        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._on_theme_changed(current_palette())
        self._retranslate()

    def set_faults(self, faults: list) -> None:
        """Liste der Fehler (faultbox.Fault) -- baut die Tasten neu."""
        self._faults = list(faults)
        for button in self._buttons.values():
            button.deleteLater()
        self._buttons = {}
        for index, fault in enumerate(self._faults):
            button = QPushButton(fault.name)
            button.setCheckable(True)
            button.setToolTip(fault.sequence + (f"\n{fault.description}" if fault.description else ""))
            button.clicked.connect(lambda _c=False, n=fault.name, b=button: self._on_fault_clicked(n, b))
            self._grid.addWidget(button, index // COLUMNS, index % COLUMNS)
            self._buttons[fault.name] = button
        self._retranslate()

    def _on_fault_clicked(self, name: str, button: QPushButton) -> None:
        button.setChecked(self._state.get("active") == name)   # Zustand kommt vom Dienst
        if self._state.get("active") == name:
            self.fault_requested.emit("clear", "")
        else:
            self.fault_requested.emit("set", name)

    def set_state(self, state: dict) -> None:
        self._state = dict(state)
        self._retranslate()

    def _retranslate(self) -> None:
        state = self._state
        active = state.get("active")
        self._subtitle.setText(tr("Fehlerbox"))
        self._color_button.setToolTip(tr("Panel-Farbe wählen…"))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        self._clear_button.setToolTip(tr("Fehler aufheben"))
        locked = bool(state.get("busy")) or bool(state.get("run_lock"))
        for name, button in self._buttons.items():
            button.setChecked(name == active)
            button.setEnabled(not locked)
        self._clear_button.setEnabled(not locked and (bool(active) or bool(state.get("locked"))))
        if state.get("busy"):
            text = tr("schaltet …")
        elif active:
            text = tr("aktiv: {name}", name=active)
        else:
            text = state.get("last") or tr("kein Fehler aktiv")
        self._status.setText(text)
        set_tone(self._status, "warning" if active else "muted")

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")
        apply_panel_tint(self, self._color_key)

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
            self.rename_requested.emit("fault", self._device_id, new_label.strip())

    def capture_state(self) -> dict:
        return {}

    def apply_state(self, state: dict) -> None:
        pass

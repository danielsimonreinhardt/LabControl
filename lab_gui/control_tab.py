"""Control-Tab: Eingabemasken fuer die wichtigsten Funktionen aller verbundenen Geraete.

Pro verbundener Geraete-Instanz (Last oder Netzteil) wird eine eigene,
unabhaengige Steuersektion angezeigt -- bei zwei baugleichen Netzteilen also
zwei Sektionen, die getrennt voneinander angesteuert werden koennen. Eine
Sektion erscheint erst, wenn das zugehoerige Geraet verbunden ist, und wird
beim Trennen wieder versteckt (nicht zerstoert), damit beim Wiederverbinden
keine eingestellten Werte verloren gehen.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from flow_layout import FlowLayout
from i18n import Translator, tr
from icons import IconButton
from panel_color import PanelColorButton, apply_panel_tint
from presets import PresetStore
from theme import Palette, ThemeManager, no_own_background
from theme import current as current_palette

# Interner SCPI-Funktionscode (siehe korad_kel102/driver.py: FUNCTIONS) ->
# deutscher Basis-Anzeigename (Uebersetzungsschluessel fuer i18n.tr).
LOAD_MODES = {
    "CURR": "Konstantstrom (CC)",
    "VOLT": "Konstantspannung (CV)",
    "RES": "Konstantwiderstand (CR)",
    "POW": "Konstantleistung (CW)",
    "SHORT": "Kurzschluss (SHORT)",
}

LOAD_MODE_UNITS = {
    "CURR": ("A", 0, 40),
    "VOLT": ("V", 0, 150),
    "RES": ("Ohm", 0, 7500),
    "POW": ("W", 0, 300),
    "SHORT": ("", 0, 0),
}


def _row(*widgets: QWidget) -> QWidget:
    """Reiht Widgets (z.B. Eingabefeld + Setzen-Button) in einer Zeile auf."""
    container = no_own_background(QWidget())
    row_layout = QHBoxLayout(container)
    row_layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        row_layout.addWidget(widget)
    row_layout.addStretch()
    return container


def _style_toggle_buttons(
    on_button: QPushButton, off_button: QPushButton, state: bool | None, pal: Palette
) -> None:
    """Hebt den Button des aktiven Zustands farbig hervor (gruen=ein, rot=aus).

    state=None (Zustand noch unbekannt, z.B. vor der ersten Hardware-Rueckfrage
    bei der Last) laesst beide Buttons im neutralen Standard-Look.

    Nutzt bewusst pal.check_pass statt pal.success: success ist im Amber-
    Industrial-Theme absichtlich amber (Theme-Akzent, siehe theme.Palette),
    aber die EIN/AUS-Anzeige eines Ausgangs ist eine sicherheitsrelevante
    Information (auf einen Blick erkennbar, ob Spannung/Strom anliegt) und
    muss deshalb in beiden Themes gruen bleiben -- check_pass ist genau dafuer
    vorgesehen (siehe dessen Docstring in theme.py)."""
    active_style = "background-color: {color}; color: {text}; font-weight: bold;"
    on_button.setStyleSheet(active_style.format(color=pal.check_pass, text=pal.surface) if state is True else "")
    off_button.setStyleSheet(active_style.format(color=pal.danger, text=pal.surface) if state is False else "")


class LoadControlGroup(QGroupBox):
    apply_function = Signal(str, str)         # device_id, SCPI mode code
    apply_setpoint = Signal(str, str, float)  # device_id, SCPI mode code, value
    set_input = Signal(str, bool)             # device_id, on
    panel_color_requested = Signal(str, object)  # device_id, color_key (str | None)
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, device_id: str, label: str, presets: PresetStore) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
        self._presets = presets
        # Geraetename als natives QGroupBox-Title (wie DashboardWidget/
        # _DevicePanel), statt als eigenes QLabel im Panel-Inneren.
        self.setTitle(label)

        outer = QVBoxLayout(self)
        self._subtitle = QLabel()
        # background: transparent -- sonst zeigt dieses direkt im GroupBox-
        # Layout haengende QLabel opak den allgemeinen Seitenhintergrund statt
        # der GroupBox-Flaeche (siehe theme.no_own_background).
        self._subtitle.setStyleSheet(f"color: {current_palette().text_muted}; background: transparent;")
        self._color_button = PanelColorButton()
        self._color_button.color_selected.connect(self._on_color_selected)
        # Umbenennen-Button sitzt seit BUGS.md #10c hier statt im Dashboard-
        # Panel (dort entfernt) -- Control-Tab ist der einzige Ort mit
        # Geraete-Bedienelementen, daher passt eine Umbenennen-Aktion
        # thematisch besser hierher als ins reine Anzeige-Dashboard.
        self._rename_button = IconButton("mdi.pencil-outline", "")
        self._rename_button.clicked.connect(self._on_rename_clicked)
        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(self._subtitle, 1)
        subtitle_row.addWidget(self._color_button)
        subtitle_row.addWidget(self._rename_button)
        outer.addLayout(subtitle_row)
        ThemeManager.instance().changed.connect(self._on_theme_changed)

        self._form = QFormLayout()
        outer.addLayout(self._form)

        self._mode_combo = QComboBox()
        self._populate_mode_combo()
        self._mode_combo.currentIndexChanged.connect(self._on_mode_index_changed)
        self._form.addRow(" ", self._mode_combo)

        self._setpoint_spin = QDoubleSpinBox()
        self._setpoint_spin.setDecimals(3)
        self._setpoint_spin.setMaximumWidth(150)
        self._apply_button = IconButton("mdi.check-bold", "")
        self._apply_button.clicked.connect(self._on_apply)
        self._setpoint_row = _row(self._setpoint_spin, self._apply_button)
        self._form.addRow(" ", self._setpoint_row)
        self._on_mode_index_changed(self._mode_combo.currentIndex())

        # Software-Presets (siehe presets.py) -- ersetzen die frueheren
        # geraeteseitigen PSU-Presets, hier geraetetyp-uebergreifend fuer
        # Last UND Netzteil nutzbar. "Laden" fuellt nur die Eingabefelder
        # (kein Hardware-Schreibvorgang), Uebernehmen bleibt bewusst ein
        # separater Klick auf den bestehenden "Setzen"-Button (_on_apply).
        self._preset_combo = QComboBox()
        self._preset_load_button = IconButton("mdi.check-bold", "")
        self._preset_load_button.clicked.connect(self._on_preset_load)
        self._preset_save_button = IconButton("mdi.content-save-outline", "")
        self._preset_save_button.clicked.connect(self._on_preset_save)
        self._preset_delete_button = IconButton("mdi.trash-can-outline", "")
        self._preset_delete_button.clicked.connect(self._on_preset_delete)
        self._preset_row = _row(
            self._preset_combo, self._preset_load_button, self._preset_save_button, self._preset_delete_button
        )
        self._form.addRow(" ", self._preset_row)
        self._presets.presets_changed.connect(self._on_presets_changed)
        self._refresh_presets()

        # Ausgang-Schalter sitzen ganz unten im Panel -- der Stretch drueckt
        # sie an den unteren Rand, auch wenn das Panel (siehe ControlTab.
        # _equalize_sections) auf die Hoehe des groessten Panels gebracht wird.
        outer.addStretch(1)
        self._output_form = QFormLayout()
        self._input_layout = input_layout = QHBoxLayout()
        self._on_button = QPushButton()
        self._off_button = QPushButton()
        # Expanding statt addStretch(): beide Buttons teilen sich zu gleichen
        # Teilen die Feldbreite, die QFormLayout auch den anderen Zeilen
        # (Modus-Combo, Sollwert-Spinbox) gibt, statt schmal und mit
        # ungenutztem Leerraum danebenzustehen.
        self._on_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._off_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._on_button.clicked.connect(lambda: self.set_input.emit(self._device_id, True))
        self._off_button.clicked.connect(lambda: self.set_input.emit(self._device_id, False))
        input_layout.addWidget(self._on_button)
        input_layout.addWidget(self._off_button)
        self._output_form.addRow(" ", input_layout)
        outer.addLayout(self._output_form)

        # Solange noch keine Hardware-Rueckfrage eingetroffen ist (siehe
        # set_input_state, gespeist vom DeviceWorker-Polling), ist der
        # tatsaechliche Eingangszustand unbekannt -- neutrale Buttons statt
        # eines geratenen Zustands.
        self._input_on: bool | None = None
        self._update_input_buttons()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _populate_mode_combo(self) -> None:
        current_code = self._mode_combo.currentData() if self._mode_combo.count() else None
        self._mode_combo.blockSignals(True)
        self._mode_combo.clear()
        for code, base_label in LOAD_MODES.items():
            self._mode_combo.addItem(tr(base_label), code)
        index = self._mode_combo.findData(current_code) if current_code else 0
        self._mode_combo.setCurrentIndex(max(index, 0))
        self._mode_combo.blockSignals(False)

    def _retranslate(self) -> None:
        self._subtitle.setText(tr("Elektronische Last (KEL102)"))
        self._color_button.setToolTip(tr("Panel-Farbe wählen…"))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        self._populate_mode_combo()
        self._form.labelForField(self._mode_combo).setText(tr("Modus:"))
        self._form.labelForField(self._setpoint_row).setText(tr("Sollwert:"))
        self._apply_button.setToolTip(tr("Übernehmen"))
        self._form.labelForField(self._preset_row).setText(tr("Preset:"))
        self._preset_load_button.setToolTip(tr("Preset laden"))
        self._preset_save_button.setToolTip(tr("Als Preset speichern…"))
        self._preset_delete_button.setToolTip(tr("Preset löschen"))
        self._on_button.setText(tr("EIN"))
        self._off_button.setText(tr("AUS"))
        self._output_form.labelForField(self._input_layout).setText(tr("Ausgang:"))

    def set_input_state(self, on: bool) -> None:
        self._input_on = on
        self._update_input_buttons()

    def _update_input_buttons(self) -> None:
        _style_toggle_buttons(self._on_button, self._off_button, self._input_on, current_palette())

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")
        self._update_input_buttons()
        apply_panel_tint(self, self._color_key)

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def _on_color_selected(self, color_key) -> None:
        self.panel_color_requested.emit(self._device_id, color_key)

    def set_panel_color(self, color_key: str | None) -> None:
        self._color_key = color_key
        apply_panel_tint(self, color_key)
        self._color_button.set_current_color(color_key)

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(
            self, tr("Gerät umbenennen"), tr("Name:"), text=self.title()
        )
        if ok and new_label.strip():
            self.rename_requested.emit("load", self._device_id, new_label.strip())

    def _on_mode_index_changed(self, index: int) -> None:
        code = self._mode_combo.itemData(index)
        unit, lo, hi = LOAD_MODE_UNITS[code]
        self._setpoint_spin.setSuffix(f" {unit}" if unit else "")
        self._setpoint_spin.setRange(lo, hi)
        self._setpoint_spin.setEnabled(code != "SHORT")

    def _on_apply(self) -> None:
        code = self._mode_combo.currentData()
        self.apply_function.emit(self._device_id, code)
        if code != "SHORT":
            self.apply_setpoint.emit(self._device_id, code, self._setpoint_spin.value())

    def _on_presets_changed(self, kind: str) -> None:
        if kind == "load":
            self._refresh_presets()

    def _refresh_presets(self) -> None:
        current = self._preset_combo.currentText()
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for preset in self._presets.presets_for("load"):
            self._preset_combo.addItem(preset["name"])
        index = self._preset_combo.findText(current)
        self._preset_combo.setCurrentIndex(max(index, 0))
        self._preset_combo.blockSignals(False)

    def _on_preset_load(self) -> None:
        name = self._preset_combo.currentText()
        preset = next((p for p in self._presets.presets_for("load") if p.get("name") == name), None)
        if preset is None:
            return
        index = self._mode_combo.findData(preset.get("mode"))
        if index >= 0:
            self._mode_combo.setCurrentIndex(index)
        try:
            self._setpoint_spin.setValue(float(preset.get("value", 0.0)))
        except (TypeError, ValueError):
            pass

    def _on_preset_save(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Als Preset speichern"), tr("Name:"))
        if ok and name.strip():
            self._presets.save_preset(
                "load", name.strip(), {"mode": self._mode_combo.currentData(), "value": self._setpoint_spin.value()}
            )

    def _on_preset_delete(self) -> None:
        name = self._preset_combo.currentText()
        if not name:
            return
        answer = QMessageBox.question(
            self, tr("Preset löschen"), tr("Preset '{name}' löschen?", name=name)
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._presets.delete_preset("load", name)


class PsuControlGroup(QGroupBox):
    set_voltage = Signal(str, float)   # device_id, volts
    set_current = Signal(str, float)   # device_id, amps
    set_ovp = Signal(str, float)       # device_id, volts
    set_ocp = Signal(str, float)       # device_id, amps
    recall_memory = Signal(str, int)   # device_id, index
    panel_color_requested = Signal(str, object)  # device_id, color_key (str | None)
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, device_id: str, label: str, presets: PresetStore) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
        self._presets = presets
        # Geraetename als natives QGroupBox-Title (wie DashboardWidget/
        # _DevicePanel), statt als eigenes QLabel im Panel-Inneren.
        self.setTitle(label)

        outer = QVBoxLayout(self)
        self._subtitle = QLabel()
        # background: transparent -- sonst zeigt dieses direkt im GroupBox-
        # Layout haengende QLabel opak den allgemeinen Seitenhintergrund statt
        # der GroupBox-Flaeche (siehe theme.no_own_background).
        self._subtitle.setStyleSheet(f"color: {current_palette().text_muted}; background: transparent;")
        self._color_button = PanelColorButton()
        self._color_button.color_selected.connect(self._on_color_selected)
        # Umbenennen-Button sitzt seit BUGS.md #10c hier statt im Dashboard-
        # Panel (dort entfernt) -- Control-Tab ist der einzige Ort mit
        # Geraete-Bedienelementen, daher passt eine Umbenennen-Aktion
        # thematisch besser hierher als ins reine Anzeige-Dashboard.
        self._rename_button = IconButton("mdi.pencil-outline", "")
        self._rename_button.clicked.connect(self._on_rename_clicked)
        subtitle_row = QHBoxLayout()
        subtitle_row.addWidget(self._subtitle, 1)
        subtitle_row.addWidget(self._color_button)
        subtitle_row.addWidget(self._rename_button)
        outer.addLayout(subtitle_row)
        ThemeManager.instance().changed.connect(self._on_theme_changed)

        self._form = QFormLayout()
        outer.addLayout(self._form)

        self._voltage_spin = QDoubleSpinBox()
        self._voltage_spin.setDecimals(1)
        self._voltage_spin.setRange(1, 60)  # Geraet nimmt Werte unter 1V nicht an
        self._voltage_spin.setSuffix(" V")
        self._voltage_spin.setMaximumWidth(120)
        self._voltage_button = IconButton("mdi.check", "")
        self._voltage_button.clicked.connect(
            lambda: self.set_voltage.emit(self._device_id, self._voltage_spin.value())
        )
        self._voltage_row = _row(self._voltage_spin, self._voltage_button)
        self._form.addRow(" ", self._voltage_row)

        self._current_spin = QDoubleSpinBox()
        self._current_spin.setDecimals(1)
        self._current_spin.setRange(0, 10)
        self._current_spin.setSuffix(" A")
        self._current_spin.setMaximumWidth(120)
        self._current_button = IconButton("mdi.check", "")
        self._current_button.clicked.connect(
            lambda: self.set_current.emit(self._device_id, self._current_spin.value())
        )
        self._current_row = _row(self._current_spin, self._current_button)
        self._form.addRow(" ", self._current_row)

        # Software-Presets (siehe presets.py) -- decken bewusst nur Spannung+
        # Strom ab (wie die frueheren geraeteseitigen PSU-Presets), nicht
        # OVP/OCP. "Laden" fuellt nur die Eingabefelder, Uebernehmen bleibt
        # ein separater Klick auf die bestehenden "Setzen"-Buttons.
        self._preset_combo = QComboBox()
        self._preset_load_button = IconButton("mdi.check-bold", "")
        self._preset_load_button.clicked.connect(self._on_preset_load)
        self._preset_save_button = IconButton("mdi.content-save-outline", "")
        self._preset_save_button.clicked.connect(self._on_preset_save)
        self._preset_delete_button = IconButton("mdi.trash-can-outline", "")
        self._preset_delete_button.clicked.connect(self._on_preset_delete)
        self._preset_row = _row(
            self._preset_combo, self._preset_load_button, self._preset_save_button, self._preset_delete_button
        )
        self._form.addRow(" ", self._preset_row)
        self._presets.presets_changed.connect(self._on_presets_changed)
        self._refresh_presets()

        self._ovp_spin = QDoubleSpinBox()
        self._ovp_spin.setDecimals(1)
        self._ovp_spin.setRange(1, 65)  # Geraet nimmt Werte unter 1V nicht an
        self._ovp_spin.setSuffix(" V")
        self._ovp_spin.setMaximumWidth(120)
        self._ovp_button = IconButton("mdi.check", "")
        self._ovp_button.clicked.connect(lambda: self.set_ovp.emit(self._device_id, self._ovp_spin.value()))
        self._ovp_row = _row(self._ovp_spin, self._ovp_button)
        self._form.addRow(" ", self._ovp_row)

        self._ocp_spin = QDoubleSpinBox()
        self._ocp_spin.setDecimals(1)
        self._ocp_spin.setRange(0, 11)
        self._ocp_spin.setSuffix(" A")
        self._ocp_spin.setMaximumWidth(120)
        self._ocp_button = IconButton("mdi.check", "")
        self._ocp_button.clicked.connect(lambda: self.set_ocp.emit(self._device_id, self._ocp_spin.value()))
        self._ocp_row = _row(self._ocp_spin, self._ocp_button)
        self._form.addRow(" ", self._ocp_row)

        # Das Geraet ignoriert Spannungs-/Stromwerte oberhalb der aktuell
        # eingestellten OVP/OCP-Schwelle kommentarlos (siehe hcs34xx/driver.py)
        # -- dieser Hinweis vergleicht die Eingabefelder live, statt den
        # Nutzer erst beim Klick auf "Setzen" scheitern zu lassen.
        self._limit_warning = QLabel("")
        self._limit_warning.setWordWrap(True)
        self._limit_warning.setStyleSheet(f"color: {current_palette().warning}; background: transparent;")
        outer.addWidget(self._limit_warning)

        self._voltage_spin.valueChanged.connect(self._update_limit_warning)
        self._current_spin.valueChanged.connect(self._update_limit_warning)
        self._ovp_spin.valueChanged.connect(self._update_limit_warning)
        self._ocp_spin.valueChanged.connect(self._update_limit_warning)
        self._update_limit_warning()

        # Ausgang-Schalter ganz unten im Panel (siehe LoadControlGroup) -- der
        # Stretch drueckt sie an den unteren Rand, auch bei angeglichener
        # Panel-Hoehe (ControlTab._equalize_sections).
        outer.addStretch(1)
        self._output_form = QFormLayout()
        self._output_layout = QHBoxLayout()
        self._output_on_button = QPushButton()
        self._output_off_button = QPushButton()
        self._output_on_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._output_off_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._output_on_button.clicked.connect(self._on_output_on)
        self._output_off_button.clicked.connect(self._on_output_off)
        self._output_layout.addWidget(self._output_on_button)
        self._output_layout.addWidget(self._output_off_button)
        self._output_form.addRow(" ", self._output_layout)
        outer.addLayout(self._output_form)

        # Das HCS-34xx-Protokoll kennt keine Abfrage des tatsaechlichen
        # Ausgangszustands (siehe hcs34xx/driver.py) -- "AUS" wird nur simuliert,
        # indem der Strom auf 0 gesetzt wird. Deshalb kein "unbekannter"
        # Startzustand wie bei der Last: ein frisch verbundenes/simuliertes
        # Netzteil hat Strom 0, gilt also standardmaessig als AUS, bis hier
        # geklickt wird.
        self._output_on: bool | None = False
        self._update_output_buttons()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._subtitle.setText(tr("Labornetzteil (HCS-34xx)"))
        self._color_button.setToolTip(tr("Panel-Farbe wählen…"))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        self._form.labelForField(self._voltage_row).setText(tr("Spannung:"))
        self._form.labelForField(self._current_row).setText(tr("Strom:"))
        self._form.labelForField(self._preset_row).setText(tr("Preset:"))
        self._preset_load_button.setToolTip(tr("Preset laden"))
        self._preset_save_button.setToolTip(tr("Als Preset speichern…"))
        self._preset_delete_button.setToolTip(tr("Preset löschen"))
        self._output_form.labelForField(self._output_layout).setText(tr("Ausgang:"))
        self._form.labelForField(self._ovp_row).setText(tr("OVP:"))
        self._form.labelForField(self._ocp_row).setText(tr("OCP:"))
        for button in (self._voltage_button, self._current_button, self._ovp_button, self._ocp_button):
            button.setToolTip(tr("Setzen"))
        self._output_on_button.setText(tr("EIN"))
        self._output_off_button.setText(tr("AUS"))
        self._update_limit_warning()

    def set_limits(self, ovp: float, ocp: float) -> None:
        """Uebernimmt die vom Geraet bekannte OVP/OCP-Schwelle in die Felder.

        Nur beim (Wieder-)Verbinden und nach einem eigenen "Setzen"-Klick
        aufgerufen (siehe device_worker._emit_psu_limits) -- nicht bei jedem
        Poll-Zyklus, damit eine laufende Eingabe hier nicht ueberschrieben wird.
        """
        self._ovp_spin.blockSignals(True)
        self._ovp_spin.setValue(ovp)
        self._ovp_spin.blockSignals(False)
        self._ocp_spin.blockSignals(True)
        self._ocp_spin.setValue(ocp)
        self._ocp_spin.blockSignals(False)
        self._update_limit_warning()

    def _update_limit_warning(self) -> None:
        messages = []
        if self._voltage_spin.value() > self._ovp_spin.value():
            messages.append(
                tr(
                    "Spannung ({voltage:g}V) liegt über der OVP-Schwelle "
                    "({threshold:g}V) -- wird vom Gerät kommentarlos abgelehnt.",
                    voltage=self._voltage_spin.value(), threshold=self._ovp_spin.value(),
                )
            )
        if self._current_spin.value() > self._ocp_spin.value():
            messages.append(
                tr(
                    "Strom ({current:g}A) liegt über der OCP-Schwelle "
                    "({threshold:g}A) -- wird vom Gerät kommentarlos abgelehnt.",
                    current=self._current_spin.value(), threshold=self._ocp_spin.value(),
                )
            )
        self._limit_warning.setText(" ".join(messages))

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")
        self._limit_warning.setStyleSheet(f"color: {palette.warning}; background: transparent;")
        self._update_output_buttons()
        apply_panel_tint(self, self._color_key)

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def _on_color_selected(self, color_key) -> None:
        self.panel_color_requested.emit(self._device_id, color_key)

    def set_panel_color(self, color_key: str | None) -> None:
        self._color_key = color_key
        apply_panel_tint(self, color_key)
        self._color_button.set_current_color(color_key)

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(
            self, tr("Gerät umbenennen"), tr("Name:"), text=self.title()
        )
        if ok and new_label.strip():
            self.rename_requested.emit("psu", self._device_id, new_label.strip())

    def _on_presets_changed(self, kind: str) -> None:
        if kind == "psu":
            self._refresh_presets()

    def _refresh_presets(self) -> None:
        current = self._preset_combo.currentText()
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for preset in self._presets.presets_for("psu"):
            self._preset_combo.addItem(preset["name"])
        index = self._preset_combo.findText(current)
        self._preset_combo.setCurrentIndex(max(index, 0))
        self._preset_combo.blockSignals(False)

    def _on_preset_load(self) -> None:
        name = self._preset_combo.currentText()
        preset = next((p for p in self._presets.presets_for("psu") if p.get("name") == name), None)
        if preset is None:
            return
        try:
            self._voltage_spin.setValue(float(preset.get("voltage", self._voltage_spin.value())))
            self._current_spin.setValue(float(preset.get("current", self._current_spin.value())))
        except (TypeError, ValueError):
            pass

    def _on_preset_save(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Als Preset speichern"), tr("Name:"))
        if ok and name.strip():
            self._presets.save_preset(
                "psu", name.strip(), {"voltage": self._voltage_spin.value(), "current": self._current_spin.value()}
            )

    def _on_preset_delete(self) -> None:
        name = self._preset_combo.currentText()
        if not name:
            return
        answer = QMessageBox.question(
            self, tr("Preset löschen"), tr("Preset '{name}' löschen?", name=name)
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._presets.delete_preset("psu", name)

    def _on_output_on(self) -> None:
        self.set_voltage.emit(self._device_id, self._voltage_spin.value())
        self.set_current.emit(self._device_id, max(self._current_spin.value(), 0.1))
        self._output_on = True
        self._update_output_buttons()

    def _on_output_off(self) -> None:
        self.set_current.emit(self._device_id, 0.0)
        self._output_on = False
        self._update_output_buttons()

    def set_output_state(self, on: bool) -> None:
        """Wird vom Worker aufgerufen, wenn ER den Ausgang setzt (Alle-Aus,
        Safety-Trip, Verbindungsaufbau) statt eines direkten Klicks hier im
        Panel -- siehe device_worker.psu_output_state. Ohne das bliebe der
        Schalter faelschlich auf "EIN" stehen, obwohl der Ausgang laengst
        abgeschaltet wurde."""
        self._output_on = on
        self._update_output_buttons()

    def _update_output_buttons(self) -> None:
        _style_toggle_buttons(self._output_on_button, self._output_off_button, self._output_on, current_palette())
        # SICHERHEITSKRITISCH (an echter Hardware reproduziert, siehe BUGS.md
        # #1b): Das HCS-34xx kennt kein echtes Ausgang-AUS -- "Aus" wird nur
        # durch Strom=0A emuliert, was eine anliegende Spannung im Leerlauf
        # (ohne angeschlossene Last) NICHT verhindert. Ohne diese Sperre
        # koennte "Spannung setzen"/"Strom setzen" bei "Aus" den emulierten
        # Aus-Zustand unbemerkt aufheben (Strom > 0A setzen, oder eine neue
        # Spannung bei bereits vorhandenem Reststrom anlegen), waehrend die
        # GUI weiterhin "Aus" anzeigt. Aendern ist daher erst moeglich, nachdem
        # der Ausgang ueber den EIN-Button aktiv eingeschaltet wurde (der
        # Spannung UND einen Mindeststrom bewusst zusammen setzt, siehe
        # _on_output_on) -- die Sollwert-Felder selbst bleiben editierbar, nur
        # das Anwenden ist gesperrt.
        self._voltage_button.setEnabled(bool(self._output_on))
        self._current_button.setEnabled(bool(self._output_on))


class ControlTab(QWidget):
    """Scrollbar, damit auf kleinen/hochskalierten Bildschirmen nichts unerreichbar wird."""

    # kind ("load"/"psu"), device_id, neu erzeugte Sektion -- fuer die einmalige
    # Verkabelung ihrer Signale mit dem DeviceWorker durch MainWindow.
    section_created = Signal(str, str, QWidget)
    panel_color_requested = Signal(str, object)  # device_id, color_key (str | None)
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, presets: PresetStore) -> None:
        super().__init__()
        self._presets = presets
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        self._content_layout = FlowLayout(content)
        # FlowLayout zeroet standardmaessig seine Aussenraender (siehe
        # flow_layout.py), damit die Geraete-Panels hier nicht direkt am
        # Fensterrand anstossen: gleicher Aussenabstand wie der Innenabstand
        # (spacing) zwischen den einzelnen Panels.
        spacing = self._content_layout.spacing()
        self._content_layout.setContentsMargins(spacing, spacing, spacing, spacing)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area)

        self._sections: dict[str, QWidget] = {}
        # Rohe (gespeicherte) Panel-Farbwahl je Geraet -- unabhaengig vom
        # An/Aus-Schalter (siehe set_panel_colors_enabled), damit eine
        # deaktivierte Auswahl beim Wieder-Aktivieren erhalten bleibt.
        self._panel_colors: dict[str, str | None] = {}
        self._colors_enabled = False

        # Nach einem Sprachwechsel aendern sich Label-Breiten/-Hoehen -- die
        # Panel-Groessen muessen dann neu angeglichen werden.
        Translator.instance().language_changed.connect(self._equalize_sections)

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        section = self._sections.get(device_id)
        if section is not None:
            section.set_label(label)
            return
        if kind == "load":
            section = LoadControlGroup(device_id, label, self._presets)
        else:
            section = PsuControlGroup(device_id, label, self._presets)
        section.hide()
        section.panel_color_requested.connect(self.panel_color_requested)
        section.rename_requested.connect(self.rename_requested)
        self._content_layout.addWidget(section)
        self._sections[device_id] = section
        self._equalize_sections()
        self.section_created.emit(kind, device_id, section)

    def set_panel_color(self, device_id: str, color_key: str | None) -> None:
        self._panel_colors[device_id] = color_key
        section = self._sections.get(device_id)
        if section is not None:
            section.set_panel_color(color_key if self._colors_enabled else None)

    def set_panel_colors_enabled(self, enabled: bool) -> None:
        self._colors_enabled = enabled
        for device_id, section in self._sections.items():
            section.set_panel_color(self._panel_colors.get(device_id) if enabled else None)

    def _equalize_sections(self) -> None:
        """Bringt alle Panels auf Hoehe und Breite des groessten Panels.

        Ueber die Mindestgroesse statt einer festen Groesse: erscheint z.B. die
        OVP/OCP-Warnung im Netzteil-Panel, darf dieses eine Panel noch
        wachsen, statt den Warntext abzuschneiden.
        """
        if not self._sections:
            return
        for section in self._sections.values():
            section.setMinimumSize(0, 0)
        max_width = max(s.sizeHint().width() for s in self._sections.values())
        max_height = max(s.sizeHint().height() for s in self._sections.values())
        for section in self._sections.values():
            section.setMinimumSize(max_width, max_height)

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        section = self._sections.get(device_id)
        if section is not None:
            section.set_label(label)

    def set_load_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    def set_psu_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    def _set_online(self, device_id: str, online: bool) -> None:
        section = self._sections.get(device_id)
        if section is not None:
            section.setVisible(online)

    def set_psu_limits(self, device_id: str, ovp: float, ocp: float) -> None:
        section = self._sections.get(device_id)
        if isinstance(section, PsuControlGroup):
            section.set_limits(ovp, ocp)

    def set_load_input_state(self, device_id: str, on: bool) -> None:
        section = self._sections.get(device_id)
        if isinstance(section, LoadControlGroup):
            section.set_input_state(on)

    def set_psu_output_state(self, device_id: str, on: bool) -> None:
        section = self._sections.get(device_id)
        if isinstance(section, PsuControlGroup):
            section.set_output_state(on)

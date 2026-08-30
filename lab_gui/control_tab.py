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
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from flow_layout import FlowLayout
from i18n import Translator, tr
from icons import IconButton
from theme import Palette, ThemeManager
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


def _style_toggle_buttons(
    on_button: QPushButton, off_button: QPushButton, state: bool | None, pal: Palette
) -> None:
    """Hebt den Button des aktiven Zustands farbig hervor (gruen=ein, rot=aus).

    state=None (Zustand noch unbekannt, z.B. vor der ersten Hardware-Rueckfrage
    bei der Last) laesst beide Buttons im neutralen Standard-Look."""
    active_style = "background-color: {color}; color: {text}; font-weight: bold;"
    on_button.setStyleSheet(active_style.format(color=pal.success, text=pal.surface) if state is True else "")
    off_button.setStyleSheet(active_style.format(color=pal.danger, text=pal.surface) if state is False else "")


class LoadControlGroup(QGroupBox):
    apply_function = Signal(str, str)         # device_id, SCPI mode code
    apply_setpoint = Signal(str, str, float)  # device_id, SCPI mode code, value
    set_input = Signal(str, bool)             # device_id, on

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id

        outer = QVBoxLayout(self)
        self._title_label = QLabel(label)
        self._title_label.setStyleSheet("font-weight: bold;")
        self._subtitle = QLabel()
        self._subtitle.setStyleSheet(f"color: {current_palette().text_muted};")
        outer.addWidget(self._title_label)
        outer.addWidget(self._subtitle)
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
        self._form.addRow(" ", self._setpoint_spin)
        self._on_mode_index_changed(self._mode_combo.currentIndex())

        self._apply_button = IconButton("mdi.check-bold", "")
        self._apply_button.clicked.connect(self._on_apply)
        self._form.addRow(self._apply_button)

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
        self._form.addRow(" ", input_layout)

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
        self._populate_mode_combo()
        self._form.labelForField(self._mode_combo).setText(tr("Modus:"))
        self._form.labelForField(self._setpoint_spin).setText(tr("Sollwert:"))
        self._apply_button.setToolTip(tr("Übernehmen"))
        self._on_button.setText(tr("EIN"))
        self._off_button.setText(tr("AUS"))
        self._form.labelForField(self._input_layout).setText(tr("Ausgang:"))

    def set_input_state(self, on: bool) -> None:
        self._input_on = on
        self._update_input_buttons()

    def _update_input_buttons(self) -> None:
        _style_toggle_buttons(self._on_button, self._off_button, self._input_on, current_palette())

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted};")
        self._update_input_buttons()

    def set_label(self, label: str) -> None:
        self._title_label.setText(label)

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


class PsuControlGroup(QGroupBox):
    set_voltage = Signal(str, float)   # device_id, volts
    set_current = Signal(str, float)   # device_id, amps
    set_ovp = Signal(str, float)       # device_id, volts
    set_ocp = Signal(str, float)       # device_id, amps
    recall_memory = Signal(str, int)   # device_id, index

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id

        outer = QVBoxLayout(self)
        self._title_label = QLabel(label)
        self._title_label.setStyleSheet("font-weight: bold;")
        self._subtitle = QLabel()
        self._subtitle.setStyleSheet(f"color: {current_palette().text_muted};")
        outer.addWidget(self._title_label)
        outer.addWidget(self._subtitle)
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
        self._voltage_row = self._row(self._voltage_spin, self._voltage_button)
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
        self._current_row = self._row(self._current_spin, self._current_button)
        self._form.addRow(" ", self._current_row)

        self._output_layout = QHBoxLayout()
        self._output_on_button = QPushButton()
        self._output_off_button = QPushButton()
        self._output_on_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._output_off_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._output_on_button.clicked.connect(self._on_output_on)
        self._output_off_button.clicked.connect(self._on_output_off)
        self._output_layout.addWidget(self._output_on_button)
        self._output_layout.addWidget(self._output_off_button)
        self._form.addRow(" ", self._output_layout)

        # Das HCS-34xx-Protokoll kennt keine Abfrage des tatsaechlichen
        # Ausgangszustands (siehe hcs34xx/driver.py) -- "AUS" wird nur simuliert,
        # indem der Strom auf 0 gesetzt wird. Deshalb kein "unbekannter"
        # Startzustand wie bei der Last: ein frisch verbundenes/simuliertes
        # Netzteil hat Strom 0, gilt also standardmaessig als AUS, bis hier
        # geklickt wird.
        self._output_on: bool | None = False
        self._update_output_buttons()

        self._ovp_spin = QDoubleSpinBox()
        self._ovp_spin.setDecimals(1)
        self._ovp_spin.setRange(1, 65)  # Geraet nimmt Werte unter 1V nicht an
        self._ovp_spin.setSuffix(" V")
        self._ovp_spin.setMaximumWidth(120)
        self._ovp_button = IconButton("mdi.check", "")
        self._ovp_button.clicked.connect(lambda: self.set_ovp.emit(self._device_id, self._ovp_spin.value()))
        self._ovp_row = self._row(self._ovp_spin, self._ovp_button)
        self._form.addRow(" ", self._ovp_row)

        self._ocp_spin = QDoubleSpinBox()
        self._ocp_spin.setDecimals(1)
        self._ocp_spin.setRange(0, 11)
        self._ocp_spin.setSuffix(" A")
        self._ocp_spin.setMaximumWidth(120)
        self._ocp_button = IconButton("mdi.check", "")
        self._ocp_button.clicked.connect(lambda: self.set_ocp.emit(self._device_id, self._ocp_spin.value()))
        self._ocp_row = self._row(self._ocp_spin, self._ocp_button)
        self._form.addRow(" ", self._ocp_row)

        # Das Geraet ignoriert Spannungs-/Stromwerte oberhalb der aktuell
        # eingestellten OVP/OCP-Schwelle kommentarlos (siehe hcs34xx/driver.py)
        # -- dieser Hinweis vergleicht die Eingabefelder live, statt den
        # Nutzer erst beim Klick auf "Setzen" scheitern zu lassen.
        self._limit_warning = QLabel("")
        self._limit_warning.setWordWrap(True)
        self._limit_warning.setStyleSheet(f"color: {current_palette().warning};")
        outer.addWidget(self._limit_warning)

        self._voltage_spin.valueChanged.connect(self._update_limit_warning)
        self._current_spin.valueChanged.connect(self._update_limit_warning)
        self._ovp_spin.valueChanged.connect(self._update_limit_warning)
        self._ocp_spin.valueChanged.connect(self._update_limit_warning)
        self._update_limit_warning()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._subtitle.setText(tr("Labornetzteil (HCS-34xx)"))
        self._form.labelForField(self._voltage_row).setText(tr("Spannung:"))
        self._form.labelForField(self._current_row).setText(tr("Strom:"))
        self._form.labelForField(self._output_layout).setText(tr("Ausgang:"))
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
        self._subtitle.setStyleSheet(f"color: {palette.text_muted};")
        self._limit_warning.setStyleSheet(f"color: {palette.warning};")
        self._update_output_buttons()

    def set_label(self, label: str) -> None:
        self._title_label.setText(label)

    def _on_output_on(self) -> None:
        self.set_voltage.emit(self._device_id, self._voltage_spin.value())
        self.set_current.emit(self._device_id, max(self._current_spin.value(), 0.1))
        self._output_on = True
        self._update_output_buttons()

    def _on_output_off(self) -> None:
        self.set_current.emit(self._device_id, 0.0)
        self._output_on = False
        self._update_output_buttons()

    def _update_output_buttons(self) -> None:
        _style_toggle_buttons(self._output_on_button, self._output_off_button, self._output_on, current_palette())

    @staticmethod
    def _row(*widgets: QWidget) -> QWidget:
        container = QWidget()
        row_layout = QHBoxLayout(container)
        row_layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            row_layout.addWidget(widget)
        row_layout.addStretch()
        return container


class ControlTab(QWidget):
    """Scrollbar, damit auf kleinen/hochskalierten Bildschirmen nichts unerreichbar wird."""

    # kind ("load"/"psu"), device_id, neu erzeugte Sektion -- fuer die einmalige
    # Verkabelung ihrer Signale mit dem DeviceWorker durch MainWindow.
    section_created = Signal(str, str, QWidget)

    def __init__(self) -> None:
        super().__init__()
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        self._content_layout = FlowLayout(content)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area)

        self._sections: dict[str, QWidget] = {}

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        section = self._sections.get(device_id)
        if section is not None:
            section.set_label(label)
            return
        if kind == "load":
            section = LoadControlGroup(device_id, label)
        else:
            section = PsuControlGroup(device_id, label)
        section.hide()
        self._content_layout.addWidget(section)
        self._sections[device_id] = section
        self.section_created.emit(kind, device_id, section)

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

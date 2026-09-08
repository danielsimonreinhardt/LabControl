"""Control-Tab: Eingabemasken fuer die wichtigsten Funktionen aller verbundenen Geraete.

Pro verbundener Geraete-Instanz (Last oder Netzteil) wird eine eigene,
unabhaengige Steuersektion angezeigt -- bei zwei baugleichen Netzteilen also
zwei Sektionen, die getrennt voneinander angesteuert werden koennen. Eine
Sektion erscheint erst, wenn das zugehoerige Geraet verbunden ist, und wird
beim Trennen wieder versteckt (nicht zerstoert), damit beim Wiederverbinden
keine eingestellten Werte verloren gehen.
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from flow_layout import FlowLayout
from i18n import Translator, tr
from icons import IconButton
from microhil.driver import (
    AOUT_COUNT,
    AOUT_MAX_MV,
    OUT_COUNT,
    PWM_COUNT,
    PWM_MAX_PERMILLE,
    PWR12_COUNT,
    RELAY_COUNT,
    defects_for_device_id,
)
from microhil_panel import DOT_ICON_SIZE, DOT_OFF, DOT_ON
from no_device_tile import NoDeviceTile
from panel_color import PanelColorButton, apply_panel_tint
from presets import PresetStore, SLOT_COUNT
from step_spinbox import SteppedDoubleSpinBox, SteppedSpinBox
from theme import Palette, ThemeManager, form_control_qss
from theme import current as current_palette

# Obergrenze der im Live-Traffic-Tisch angezeigten Zeilen (siehe
# CanControlGroup.append_frame) -- reine GUI-Anzeige, kein Log/Export, daher
# reicht ein kleiner Ausschnitt der zuletzt empfangenen Frames.
CAN_TRAFFIC_ROW_LIMIT = 50
CAN_STANDARD_ID_MAX = 0x7FF
CAN_EXTENDED_ID_MAX = 0x1FFFFFFF

# Obergrenze fuer das Strombegrenzung-Eingabefeld (HilControlGroup) -- rein
# provisorisch: die Firmware kennt aktuell noch kein Kommando dafuer (siehe
# microhil/driver.py: set_current_limit()-Docstring), es gibt also keine
# vom Geraet vorgegebene Grenze, an der sich dieser Wert orientieren koennte.
HIL_CURRENT_LIMIT_MAX_MA = 3000

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


def _row_stylesheet(pal: Palette) -> str:
    """Stylesheet fuer den _row()-Wrapper: transparent (siehe theme.
    no_own_background) DAMIT ZUSAETZLICH theme.form_control_qss(), sonst
    schlaegt eine individuelle Panel-Faerbung (panel_color.apply_panel_tint)
    auf die in der Zeile enthaltenen Buttons/Eingabefelder durch (BUGS.md
    #10f, an echter Hardware reproduziert).

    WICHTIG: "background: transparent" MUSS hier als "QWidget { ... }"-Regel
    mit explizitem Typ-Selektor stehen, NICHT als nackte Eigenschaft ohne
    Selektor -- siehe ausfuehrliche Begruendung in panel_color.
    apply_panel_tint(). Ein QWidget-Typ-Selektor matcht zwar technisch auch
    die enthaltenen QPushButton/QDoubleSpinBox (Qt-QSS-Typselektoren matchen
    Subklassen), die nachfolgende spezifischere "QPushButton { ... }"-Regel
    aus form_control_qss() gewinnt dort aber zuverlaessig (normale
    QSS-Spezifitaet innerhalb ein und desselben Stylesheets, per Test
    bestaetigt) -- nur der ungenutzte Rest-Platz in der Zeile bleibt
    transparent und zeigt die (ggf. getoente) GroupBox-Flaeche durch."""
    return f"QWidget {{ background: transparent; }}\n{form_control_qss(pal)}"


def _row(*widgets: QWidget) -> QWidget:
    """Reiht Widgets (z.B. Eingabefeld + Setzen-Button) in einer Zeile auf."""
    container = QWidget()
    container.setStyleSheet(_row_stylesheet(current_palette()))
    row_layout = QHBoxLayout(container)
    row_layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        row_layout.addWidget(widget)
    row_layout.addStretch()
    return container


def _detint_label(form: QFormLayout, field: QWidget | QHBoxLayout) -> None:
    """Laesst die von QFormLayout automatisch erzeugte Zeilen-Beschriftung
    (z.B. "Sollwert:") die individuelle Panel-Faerbung durchscheinen, statt
    opak die allgemeine Seitenhintergrundfarbe zu zeigen -- anders als
    _row_stylesheet() betrifft das NUR reinen Text (color bleibt unberuehrt),
    keine Buttons/Eingabefelder, die sollen weiterhin die normale
    Theme-Farbe behalten (siehe BUGS.md #10f). Eine reine "background:
    transparent"-Eigenschaft ohne weitere Selektor-Regeln im selben String
    ist hier -- anders als bei _row_stylesheet()/apply_panel_tint() -- sicher
    (siehe deren Docstrings zur Ursache), da nichts anderes damit gemischt
    wird; deshalb auch ohne Theme-Wechsel-Refresh, die Eigenschaft ist
    farbunabhaengig."""
    label = form.labelForField(field)
    if label is not None:
        label.setStyleSheet("background: transparent;")


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


class _DigitalOutToggle(QPushButton):
    """Schalter fuer die Digitalausgaenge (OUT1-8) in HilControlGroup --
    LED-Punkt + Kanalnummer, dieselbe Ikonografie wie im Dashboard
    (microhil_panel.DOT_ON/DOT_OFF), hier zusaetzlich klickbar. Ersetzt den
    ehemaligen _channel_toggle (blosse Farbfuellung ohne jede Kennzeichnung
    im AUS-Zustand): Variante 04 aus dem Schalterkatalog, vom Nutzer
    ausdruecklich fuer OUT1-8 gewaehlt. Vorteil gegenueber Farbfuellung
    allein: identische Bildsprache in Dashboard und Control-Tab -- derselbe
    Punkt bedeutet ueberall dasselbe.

    Dot-Icon als QLabel-Pixmap (wie microhil_panel._dot_cell), NICHT ueber
    QToolButton.setIcon()/setIconSize(): eine erste Fassung nutzte genau das
    und produzierte in der echten App (nicht im Offscreen-Preview-Skript!)
    einen auf einen Strich zusammengequetschten Punkt -- QToolButtons interne
    Icon-Rect-Berechnung fuer ToolButtonTextUnderIcon skaliert das Icon
    offenbar nicht immer seitenverhaeltnistreu, wenn das Widget insgesamt
    sehr klein ist. Zwei echte QLabel in einer QVBoxLayout (wie im Dashboard
    seit je bewaehrt) umgehen dieses Problem, weil QLabel.setPixmap() das
    Bild unveraendert zeichnet, ohne eigene Skalierungslogik."""

    def __init__(self, number: int) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(36, 44)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(2)
        self.dot_label = QLabel()
        self.dot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dot_label.setStyleSheet("background: transparent; border: none;")
        self.number_label = QLabel(str(number))
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number_label.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self.dot_label)
        layout.addWidget(self.number_label)


def _digital_out_toggle(number: int) -> _DigitalOutToggle:
    return _DigitalOutToggle(number)


def _style_digital_out_toggle(button: _DigitalOutToggle, on: bool, pal: Palette) -> None:
    """Setzt LED-Pixmap, Zahlenfarbe und Rahmen eines _DigitalOutToggle
    passend zum Checked-Zustand -- Icon/Farbe exakt wie microhil_panel.
    _dot_pixmap (check_pass statt success, siehe dessen Begruendung in
    theme.py: die EIN/AUS-Anzeige eines Ausgangs ist sicherheitsrelevant und
    muss deshalb in beiden Themes gruen bleiben)."""
    color = pal.check_pass if on else pal.text_muted
    button.dot_label.setPixmap(qta.icon(DOT_ON if on else DOT_OFF, color=color).pixmap(DOT_ICON_SIZE, DOT_ICON_SIZE))
    button.number_label.setStyleSheet(f"background: transparent; border: none; color: {pal.text};")
    button.setStyleSheet(
        f"QPushButton {{ border: 1px solid {pal.border}; border-radius: 6px; "
        f"background-color: {pal.surface_alt}; }}"
    )


class _SegmentedToggle(QPushButton):
    """Zweigeteilter AUS|EIN-Schalter fuer Relais und 12V-Ausgaenge in
    HilControlGroup -- Variante 07 aus dem Schalterkatalog, vom Nutzer
    ausdruecklich fuer diese beiden Gruppen gewaehlt (nur bis zu 4 Kanaele,
    das rechtfertigt den zusaetzlichen Platzbedarf gegenueber der
    kompakteren LED-Variante bei OUT1-8): ausgeschriebener Text statt
    blosser Farbcodierung, wichtig bei Relais/12V, wo ein versehentlich
    uebersehener EIN-Zustand echte Spannung am Ausgang bedeutet.

    Eigener paintEvent statt QSS: ein Qt-Stylesheet kennt pro Widget-Zustand
    nur EINE Hintergrundfarbe, kann also nicht zwei unabhaengig gefaerbte
    Textbereiche in demselben Button gleichzeitig darstellen. isChecked()/
    toggled/setChecked bleiben unveraendert (geerbt von QPushButton) --
    HilControlGroup behandelt diesen Schalter dadurch identisch zum
    ehemaligen _channel_toggle."""

    SEGMENT_LABELS = ("AUS", "EIN")

    def __init__(self) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(72, 30)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        pal = current_palette()
        on = self.isChecked()
        enabled = self.isEnabled()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        half_width = rect.width() / 2
        off_rect = QRectF(rect.left(), rect.top(), half_width, rect.height())
        on_rect = QRectF(rect.left() + half_width, rect.top(), half_width, rect.height())

        idle_bg = QColor(pal.surface_alt)
        muted = QColor(pal.text_muted)
        off_bg = QColor(pal.border) if (not on and enabled) else idle_bg
        on_bg = QColor(pal.check_pass) if (on and enabled) else idle_bg
        off_text = QColor(pal.text) if (not on and enabled) else muted
        on_text = QColor(pal.surface) if (on and enabled) else muted

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(rect, 6, 6)
        painter.setClipPath(clip_path)
        painter.fillRect(off_rect, off_bg)
        painter.fillRect(on_rect, on_bg)
        painter.setClipping(False)

        painter.setPen(QPen(off_text))
        painter.drawText(off_rect, Qt.AlignmentFlag.AlignCenter, tr(self.SEGMENT_LABELS[0]))
        painter.setPen(QPen(on_text))
        painter.drawText(on_rect, Qt.AlignmentFlag.AlignCenter, tr(self.SEGMENT_LABELS[1]))

        painter.setPen(QPen(QColor(pal.border), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 6, 6)
        painter.end()


def _segmented_toggle_cell(label_text: str) -> tuple[QWidget, _SegmentedToggle, QLabel]:
    """Ein _SegmentedToggle mit vorangestelltem Kanal-Label (z.B. "REL1") --
    anders als bei _digital_out_toggle steckt die Kanalkennung hier NICHT im
    Schalter selbst (kein Platz neben "AUS"/"EIN"), sondern in einem
    separaten Label darueber (siehe Schalterkatalog, Variante 07: "Kanal-ID:
    separates Label davor")."""
    cell = QWidget()
    layout = QVBoxLayout(cell)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    caption = QLabel(label_text)
    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
    toggle = _SegmentedToggle()
    layout.addWidget(caption, alignment=Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignCenter)
    return cell, toggle, caption


class LoadControlGroup(QGroupBox):
    apply_function = Signal(str, str)         # device_id, SCPI mode code
    apply_setpoint = Signal(str, str, float)  # device_id, SCPI mode code, value
    set_input = Signal(str, bool)             # device_id, on
    panel_color_requested = Signal(str, object)  # device_id, color_key (str | None)
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
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
        _detint_label(self._form, self._mode_combo)

        self._setpoint_spin = SteppedDoubleSpinBox()
        self._setpoint_spin.setDecimals(3)
        self._setpoint_spin.setMaximumWidth(150)
        self._apply_button = IconButton("mdi.check-bold", "")
        self._apply_button.clicked.connect(self._on_apply)
        self._setpoint_row = _row(self._setpoint_spin, self._apply_button)
        self._form.addRow(" ", self._setpoint_row)
        _detint_label(self._form, self._setpoint_row)
        self._on_mode_index_changed(self._mode_combo.currentIndex())

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
        _detint_label(self._output_form, input_layout)
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
        self._setpoint_row.setStyleSheet(_row_stylesheet(palette))
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

    def set_colors_enabled(self, enabled: bool) -> None:
        """Blendet den Panel-Farbe-Button aus, solange individuelle
        Panel-Farben global deaktiviert sind (Einstellungen-Tab) -- eine
        Auswahlmoeglichkeit fuer eine Funktion anzuzeigen, die gerade gar
        nicht wirkt, waere irrefuehrend."""
        self._color_button.setVisible(enabled)

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

    def capture_state(self) -> dict:
        """Aktueller Zustand fuer die globale Preset-Leiste (siehe PresetBar).

        Schaltstatus nur enthalten, wenn er bereits per Hardware-Rueckfrage
        bekannt ist (siehe set_input_state) -- sonst wuerde ein spaeteres
        Laden dieses Presets einen ungeprueft geratenen Zustand erzwingen.
        """
        state = {"mode": self._mode_combo.currentData(), "value": self._setpoint_spin.value()}
        if self._input_on is not None:
            state["input_on"] = self._input_on
        return state

    def apply_state(self, state: dict) -> None:
        """Uebernimmt ein Preset (siehe PresetBar) -- fuellt die Felder UND
        schreibt sofort auf die Hardware (Sollwert per _on_apply, Eingang per
        set_input), da ein Schaltstatus anders als ein reiner Sollwert nicht
        sinnvoll nur "vorbelegt, aber nicht angewendet" dargestellt werden
        kann."""
        index = self._mode_combo.findData(state.get("mode"))
        if index >= 0:
            self._mode_combo.setCurrentIndex(index)
        try:
            self._setpoint_spin.setValue(float(state.get("value", 0.0)))
        except (TypeError, ValueError):
            pass
        self._on_apply()
        if "input_on" in state:
            self.set_input.emit(self._device_id, bool(state["input_on"]))


class PsuControlGroup(QGroupBox):
    set_voltage = Signal(str, float)   # device_id, volts
    set_current = Signal(str, float)   # device_id, amps
    set_ovp = Signal(str, float)       # device_id, volts
    set_ocp = Signal(str, float)       # device_id, amps
    recall_memory = Signal(str, int)   # device_id, index
    panel_color_requested = Signal(str, object)  # device_id, color_key (str | None)
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
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

        self._voltage_spin = SteppedDoubleSpinBox()
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
        _detint_label(self._form, self._voltage_row)

        self._current_spin = SteppedDoubleSpinBox()
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
        _detint_label(self._form, self._current_row)

        self._ovp_spin = SteppedDoubleSpinBox()
        self._ovp_spin.setDecimals(1)
        self._ovp_spin.setRange(1, 65)  # Geraet nimmt Werte unter 1V nicht an
        self._ovp_spin.setSuffix(" V")
        self._ovp_spin.setMaximumWidth(120)
        self._ovp_button = IconButton("mdi.check", "")
        self._ovp_button.clicked.connect(lambda: self.set_ovp.emit(self._device_id, self._ovp_spin.value()))
        self._ovp_row = _row(self._ovp_spin, self._ovp_button)
        self._form.addRow(" ", self._ovp_row)
        _detint_label(self._form, self._ovp_row)

        self._ocp_spin = SteppedDoubleSpinBox()
        self._ocp_spin.setDecimals(1)
        self._ocp_spin.setRange(0, 11)
        self._ocp_spin.setSuffix(" A")
        self._ocp_spin.setMaximumWidth(120)
        self._ocp_button = IconButton("mdi.check", "")
        self._ocp_button.clicked.connect(lambda: self.set_ocp.emit(self._device_id, self._ocp_spin.value()))
        self._ocp_row = _row(self._ocp_spin, self._ocp_button)
        self._form.addRow(" ", self._ocp_row)
        _detint_label(self._form, self._ocp_row)

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
        _detint_label(self._output_form, self._output_layout)
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
        for row in (self._voltage_row, self._current_row, self._ovp_row, self._ocp_row):
            row.setStyleSheet(_row_stylesheet(palette))
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

    def set_colors_enabled(self, enabled: bool) -> None:
        """Blendet den Panel-Farbe-Button aus, solange individuelle
        Panel-Farben global deaktiviert sind (Einstellungen-Tab) -- eine
        Auswahlmoeglichkeit fuer eine Funktion anzuzeigen, die gerade gar
        nicht wirkt, waere irrefuehrend."""
        self._color_button.setVisible(enabled)

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(
            self, tr("Gerät umbenennen"), tr("Name:"), text=self.title()
        )
        if ok and new_label.strip():
            self.rename_requested.emit("psu", self._device_id, new_label.strip())

    def capture_state(self) -> dict:
        """Aktueller Zustand fuer die globale Preset-Leiste (siehe PresetBar)."""
        return {
            "voltage": self._voltage_spin.value(),
            "current": self._current_spin.value(),
            "output_on": bool(self._output_on),
        }

    def apply_state(self, state: dict) -> None:
        """Uebernimmt ein Preset (siehe PresetBar) -- fuellt die Felder UND
        schaltet den Ausgang sofort auf den gespeicherten Zustand (ueber
        _on_output_on/_on_output_off, damit dieselbe Sicherheitslogik greift
        wie bei einem manuellen EIN/AUS-Klick, siehe _update_output_buttons)."""
        try:
            self._voltage_spin.setValue(float(state.get("voltage", self._voltage_spin.value())))
            self._current_spin.setValue(float(state.get("current", self._current_spin.value())))
        except (TypeError, ValueError):
            pass
        if state.get("output_on"):
            self._on_output_on()
        else:
            self._on_output_off()

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


class CanControlGroup(QGroupBox):
    """Steuersektion fuer ein CAN-Interface: Formular zum Senden eines
    einzelnen Frames + eine kleine Live-Traffic-Tabelle. Anders als Last/
    Netzteil kein Dauer-Sollwert -- daher deutlich schlanker als
    LoadControlGroup/PsuControlGroup (kein Preset-Zustand, keine
    Sicherheits-Grenzwerte: ein Bus hat keinen "Ausgang", der abzuschalten
    waere, siehe device_worker.py)."""

    send_frame = Signal(str, int, str, bool)  # device_id, arbitration_id, data (Hex-String), extended
    panel_color_requested = Signal(str, object)  # device_id, color_key (str | None)
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
        self.setTitle(label)

        outer = QVBoxLayout(self)
        self._subtitle = QLabel()
        self._subtitle.setStyleSheet(f"color: {current_palette().text_muted}; background: transparent;")
        self._color_button = PanelColorButton()
        self._color_button.color_selected.connect(self._on_color_selected)
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

        self._id_spin = SteppedSpinBox()
        self._id_spin.setDisplayIntegerBase(16)
        self._id_spin.setPrefix("0x")
        self._extended_check = QCheckBox()
        self._extended_check.toggled.connect(self._on_extended_toggled)
        self._on_extended_toggled(False)
        self._id_row = _row(self._id_spin, self._extended_check)
        self._form.addRow(" ", self._id_row)
        _detint_label(self._form, self._id_row)

        self._data_edit = QLineEdit()
        self._data_edit.setPlaceholderText("01 A2 FF")
        self._send_button = IconButton("mdi.send-outline", "")
        self._send_button.clicked.connect(self._on_send_clicked)
        self._data_row = _row(self._data_edit, self._send_button)
        self._form.addRow(" ", self._data_row)
        _detint_label(self._form, self._data_row)

        self._traffic_table = QTableWidget(0, 3)
        self._traffic_table.setHorizontalHeaderItem(0, QTableWidgetItem())
        self._traffic_table.setHorizontalHeaderItem(1, QTableWidgetItem())
        self._traffic_table.setHorizontalHeaderItem(2, QTableWidgetItem())
        self._traffic_table.verticalHeader().setVisible(False)
        self._traffic_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._traffic_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._traffic_table.setMinimumHeight(160)
        outer.addWidget(self._traffic_table, 1)

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._subtitle.setText(tr("CAN-Bus"))
        self._color_button.setToolTip(tr("Panel-Farbe wählen…"))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        self._form.labelForField(self._id_row).setText(tr("ID / Extended:"))
        self._extended_check.setText(tr("Extended"))
        self._form.labelForField(self._data_row).setText(tr("Daten (Hex):"))
        self._send_button.setToolTip(tr("Senden"))
        self._traffic_table.setHorizontalHeaderLabels([tr("Zeit (s)"), tr("ID"), tr("Daten")])

    def _on_extended_toggled(self, extended: bool) -> None:
        self._id_spin.setRange(0, CAN_EXTENDED_ID_MAX if extended else CAN_STANDARD_ID_MAX)

    def _on_send_clicked(self) -> None:
        self.send_frame.emit(
            self._device_id, self._id_spin.value(), self._data_edit.text().strip(),
            self._extended_check.isChecked(),
        )

    def append_frame(self, arbitration_id: int, data_hex: str, extended: bool, timestamp: float) -> None:
        id_text = f"0x{arbitration_id:X}" if extended else f"0x{arbitration_id:03X}"
        self._traffic_table.insertRow(0)
        self._traffic_table.setItem(0, 0, QTableWidgetItem(f"{timestamp:.3f}"))
        self._traffic_table.setItem(0, 1, QTableWidgetItem(id_text))
        self._traffic_table.setItem(0, 2, QTableWidgetItem(data_hex))
        while self._traffic_table.rowCount() > CAN_TRAFFIC_ROW_LIMIT:
            self._traffic_table.removeRow(self._traffic_table.rowCount() - 1)

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")
        for row in (self._id_row, self._data_row):
            row.setStyleSheet(_row_stylesheet(palette))
        apply_panel_tint(self, self._color_key)

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def _on_color_selected(self, color_key) -> None:
        self.panel_color_requested.emit(self._device_id, color_key)

    def set_panel_color(self, color_key: str | None) -> None:
        self._color_key = color_key
        apply_panel_tint(self, color_key)
        self._color_button.set_current_color(color_key)

    def set_colors_enabled(self, enabled: bool) -> None:
        self._color_button.setVisible(enabled)

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(
            self, tr("Gerät umbenennen"), tr("Name:"), text=self.title()
        )
        if ok and new_label.strip():
            self.rename_requested.emit("can", self._device_id, new_label.strip())

    def capture_state(self) -> dict:
        # Kein sinnvoller "Preset-Zustand" fuer ein CAN-Interface (kein
        # Dauer-Sollwert wie bei Last/Netzteil) -- die globale Preset-Leiste
        # (PresetBar) ruft capture_state/apply_state trotzdem fuer JEDE
        # sichtbare Sektion auf, daher leere Implementierung statt Absturz.
        return {}

    def apply_state(self, state: dict) -> None:
        pass


class HilControlGroup(QGroupBox):
    """Steuersektion fuer den microHIL: Schalter fuer Digitalausgaenge
    (OUT1-8), Relais (REL1-4) und 12V-Ausgaenge (PWR12 1-2), Eingabefelder
    fuer Analogausgaenge (AOUT1-2) sowie eine Strombegrenzung je 12V-Kanal.

    Digitaleingaenge (IN1-8) sind absichtlich NICHT hier -- rein lesend, das
    Dashboard (microhil_panel.MicroHilPanel) zeigt sie bereits an, eine
    zweite Anzeige derselben Werte im Control-Tab waere redundant.

    PWM1-4 (Nutzerfeedback: fehlte hier komplett) wie AOUT als Sollwertfeld
    + Uebernehmen-Button (0-1000 Promille Duty-Cycle, siehe microhil/
    driver.py: set_pwm()/PWM_MAX_PERMILLE) statt als Kanal-Schalter -- kein
    einfaches Ein/Aus wie OUT/Relais/PWR12, sondern ein kontinuierlicher
    Wert, analog zu den AOUT-Feldern oben. Mit OUT1-4 hardwareseitig
    verriegelt (siehe microhil/driver.py: INTERLOCKED_CHANNELS) -- ein
    gesetzter PWM-Wert > 0 schaltet den gleichnamigen Digitalausgang
    zwangsweise aus; der OUT-Schalter zeigt das beim naechsten Poll-Zyklus
    korrekt an (set_output_states()), ohne dass PWM hier extra dagegen
    verriegelt werden muesste.

    Kanal-Schalter als einzelne checkable Buttons statt EIN/AUS-Buttonpaaren
    wie bei LoadControlGroup/PsuControlGroup -- bei bis zu 8 Kanaelen (OUT)
    waere ein Buttonpaar pro Kanal deutlich zu hoch. Zwei unterschiedliche
    Auspraegungen je nach Kanalzahl (Absprache, siehe Schalterkatalog-
    Artefakt): OUT1-8 nutzt _digital_out_toggle (LED-Punkt + Nummer, kompakt
    genug fuer 8 Kanaele in einer Zeile), Relais/PWR12 (hoechstens 4 Kanaele)
    nutzen _SegmentedToggle (ausgeschriebenes AUS|EIN) -- dort ist genug
    Platz, und ein uebersehener EIN-Zustand bedeutet echte Spannung am
    Ausgang. Ihr Zustand wird bei jedem Poll-Zyklus mit der echten Hardware synchronisiert
    (siehe set_output_states/set_relay_states/set_pwr12_states, gespeist von
    device_worker.hil_digital_state/hil_relay_state/hil_pwr12_state -- exakt
    dieselben Signale, die auch das Dashboard fuellen) -- anders als beim
    HCS-34xx-Netzteil (kein Software-Ausgang-Ein/Aus, siehe hcs34xx/README.md)
    braucht es hier also KEINEN erzwungenen Sicherheits-Reset beim Verbinden:
    ein bereits eingeschalteter Kanal wird korrekt als "ein" angezeigt statt
    unbemerkt zurueckgesetzt zu werden.

    AOUT-Felder werden NIE vom Geraet zurueckgelesen (kein `AOUT?`-Kommando,
    siehe microhil/driver.py) -- sie zeigen also nur den zuletzt hier selbst
    eingegebenen Sollwert, wie microhil_panel.MicroHilPanel es fuer das
    Dashboard bereits dokumentiert.

    Strombegrenzung (Eingabefeld je 12V-Kanal): microHIL-Firmware kennt
    aktuell (protocol.md) kein Kommando dafuer, nur CURR?/CURRRAW? zum reinen
    Auslesen der Stromsense -- das eigentliche Begrenzen ist geplante
    Firmware-Arbeit (siehe microhil/driver.py: set_current_limit()-Docstring),
    nicht Teil dieser GUI. Das Feld ist bereits vorbereitet (sendet den
    vorgeschlagenen, noch nicht mit der Firmware abgestimmten Befehl
    `ILIM <ch> <mA>`) -- ein Klick auf "Übernehmen" schlaegt bis zur
    entsprechenden Firmware-Aenderung mit `ERR UNKNOWN` fehl, was
    device_worker._guard_hil wie jeden anderen Kommunikationsfehler behandelt
    (Verbindung wird geschlossen, automatischer Wiederverbindungsversuch nach
    RECONNECT_INTERVAL_MS) -- auf dem simulierten Geraet (microhil.mock)
    funktioniert es dagegen bereits."""

    set_output = Signal(str, int, bool)         # device_id, Kanal (1-8), ein
    set_analog_output = Signal(str, int, int)   # device_id, Kanal (1-2), mV
    set_pwm = Signal(str, int, int)             # device_id, Kanal (1-4), Promille
    set_relay = Signal(str, int, bool)          # device_id, Kanal (1-4), ein
    set_pwr12 = Signal(str, int, bool)          # device_id, Kanal (1-2), ein
    set_current_limit = Signal(str, int, int)   # device_id, Kanal (1-2), mA
    panel_color_requested = Signal(str, object)  # device_id, color_key (str | None)
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._color_key: str | None = None
        self.setTitle(label)

        outer = QVBoxLayout(self)
        self._subtitle = QLabel()
        self._subtitle.setStyleSheet(f"color: {current_palette().text_muted}; background: transparent;")
        self._color_button = PanelColorButton()
        self._color_button.color_selected.connect(self._on_color_selected)
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

        # -- Digitalausgaenge (OUT1-8) --
        self._out_buttons = [_digital_out_toggle(ch) for ch in range(1, OUT_COUNT + 1)]
        for button in self._out_buttons:
            _style_digital_out_toggle(button, False, current_palette())
        for ch, button in enumerate(self._out_buttons, start=1):
            button.toggled.connect(lambda on, c=ch: self.set_output.emit(self._device_id, c, on))
            button.toggled.connect(lambda on, b=button: _style_digital_out_toggle(b, on, current_palette()))
        self._out_row = _row(*self._out_buttons)
        self._form.addRow(" ", self._out_row)
        _detint_label(self._form, self._out_row)

        # -- Analogausgaenge (AOUT1-2) -- je Kanal ein eigenes Sollwert-Feld
        # + Uebernehmen-Button (kein Massen-"Setzen" wie bei den Schaltern
        # oben: ein Zahlenwert braucht einen expliziten Bestaetigungs-Klick,
        # analog zu PsuControlGroup.voltage/current).
        self._aout_spins: list[SteppedSpinBox] = []
        self._aout_rows: list[QWidget] = []
        for ch in range(1, AOUT_COUNT + 1):
            spin = SteppedSpinBox(small_step=10, large_step=100)
            spin.setRange(0, AOUT_MAX_MV)
            spin.setSuffix(" mV")
            spin.setMaximumWidth(120)
            button = IconButton("mdi.check", "")
            button.clicked.connect(lambda _, c=ch, s=spin: self.set_analog_output.emit(self._device_id, c, s.value()))
            row = _row(spin, button)
            self._form.addRow(" ", row)
            _detint_label(self._form, row)
            self._aout_spins.append(spin)
            self._aout_rows.append(row)

        # -- PWM (PWM1-4) -- Sollwertfeld + Uebernehmen-Button wie AOUT
        # (kontinuierlicher Wert, kein Kanal-Schalter). Verriegelung mit
        # OUT1-4 passiert firmwareseitig, siehe Klassendocstring.
        self._pwm_spins: list[SteppedSpinBox] = []
        self._pwm_rows: list[QWidget] = []
        for ch in range(1, PWM_COUNT + 1):
            spin = SteppedSpinBox(small_step=10, large_step=100)
            spin.setRange(0, PWM_MAX_PERMILLE)
            spin.setSuffix(" ‰")
            spin.setMaximumWidth(120)
            button = IconButton("mdi.check", "")
            button.clicked.connect(lambda _, c=ch, s=spin: self.set_pwm.emit(self._device_id, c, s.value()))
            row = _row(spin, button)
            self._form.addRow(" ", row)
            _detint_label(self._form, row)
            self._pwm_spins.append(spin)
            self._pwm_rows.append(row)

        # -- Relais (REL1-4) -- Segmented AUS|EIN statt blosser Farbfuellung
        # (Absprache, Variante 07 im Schalterkatalog): anders als bei OUT1-8
        # teilen sich hier alle Kanaele EINE Zeile mit einer gemeinsamen
        # Formular-Beschriftung ("Relais:"), der Schalter selbst zeigt keine
        # Kanalnummer -- deshalb je Kanal ein eigenes "REL{n}"-Caption-Label
        # ueber dem Schalter (_segmented_toggle_cell), das PWR12-Aequivalent
        # unten braucht das NICHT (dort hat jeder Kanal ohnehin schon eine
        # eigene Formularzeile mit eigenem Label).
        self._relay_buttons: list[_SegmentedToggle] = []
        relay_cells: list[QWidget] = []
        for ch in range(1, RELAY_COUNT + 1):
            cell, toggle, _caption = _segmented_toggle_cell(f"REL{ch}")
            toggle.toggled.connect(lambda on, c=ch: self.set_relay.emit(self._device_id, c, on))
            self._relay_buttons.append(toggle)
            relay_cells.append(cell)
        self._relay_row = _row(*relay_cells)
        self._form.addRow(" ", self._relay_row)
        _detint_label(self._form, self._relay_row)

        # -- 12V-Ausgaenge (PWR12 1-2) + Strombegrenzung je Kanal --
        # Kanaele mit bekanntem Hardware-Defekt auf DIESEM Board (siehe
        # microhil/driver.py: KNOWN_HARDWARE_DEFECTS/defects_for_device_id,
        # Quelle docs/hardware-notes.md im microHIL-Repo) werden deaktiviert
        # statt scheinbar funktionsfaehig angezeigt -- device_id enthaelt seit
        # device_worker._reconnect_hils bereits die USB-Seriennummer
        # ("hil:<serial>"), identisch mit dem Firmware-seitigen *IDN?-
        # SN=-Feld, es braucht also keine zusaetzliche Geraeteabfrage hier.
        hil_defects = defects_for_device_id(device_id)
        # Segmented AUS|EIN wie bei Relais oben (Absprache, Variante 07) --
        # hier ohne _segmented_toggle_cell/Caption-Label: jeder Kanal hat
        # bereits seine eigene Formularzeile mit eigenem Label
        # ("12V-Ausgang {n}:", siehe _retranslate), eine zusaetzliche
        # Kanalkennung am Schalter selbst waere redundant.
        self._pwr12_buttons: list[_SegmentedToggle] = []
        self._limit_spins: list[SteppedSpinBox] = []
        self._pwr12_rows: list[QWidget] = []
        for ch in range(1, PWR12_COUNT + 1):
            broken = f"pwr12:{ch}" in hil_defects
            toggle = _SegmentedToggle()
            toggle.toggled.connect(lambda on, c=ch: self.set_pwr12.emit(self._device_id, c, on))
            limit_spin = SteppedSpinBox(small_step=10, large_step=100)
            limit_spin.setRange(0, HIL_CURRENT_LIMIT_MAX_MA)
            limit_spin.setSuffix(" mA")
            limit_spin.setMaximumWidth(120)
            limit_button = IconButton("mdi.check", "")
            limit_button.clicked.connect(
                lambda _, c=ch, s=limit_spin: self.set_current_limit.emit(self._device_id, c, s.value())
            )
            if broken:
                defect_tooltip = tr(
                    "Bekannter Hardware-Defekt auf diesem Board (siehe docs/hardware-notes.md "
                    "im microHIL-Repo) -- deaktiviert bis zur Reparatur."
                )
                toggle.setEnabled(False)
                toggle.setToolTip(defect_tooltip)
                limit_spin.setEnabled(False)
                limit_spin.setToolTip(defect_tooltip)
                limit_button.setEnabled(False)
                limit_button.setToolTip(defect_tooltip)
            row = _row(toggle, limit_spin, limit_button)
            self._form.addRow(" ", row)
            _detint_label(self._form, row)
            self._pwr12_buttons.append(toggle)
            self._limit_spins.append(limit_spin)
            self._pwr12_rows.append(row)

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._subtitle.setText(tr("microHIL"))
        self._color_button.setToolTip(tr("Panel-Farbe wählen…"))
        self._rename_button.setToolTip(tr("Gerät umbenennen"))
        self._form.labelForField(self._out_row).setText(tr("Digitalausgänge:"))
        for i, row in enumerate(self._aout_rows, start=1):
            self._form.labelForField(row).setText(tr("Analogausgang {n}:", n=i))
        for i, row in enumerate(self._pwm_rows, start=1):
            self._form.labelForField(row).setText(tr("PWM {n}:", n=i))
        self._form.labelForField(self._relay_row).setText(tr("Relais:"))
        for i, row in enumerate(self._pwr12_rows, start=1):
            self._form.labelForField(row).setText(tr("12V-Ausgang {n}:", n=i))

    def _on_theme_changed(self, palette: Palette) -> None:
        self._subtitle.setStyleSheet(f"color: {palette.text_muted}; background: transparent;")
        for row in (self._out_row, self._relay_row, *self._aout_rows, *self._pwm_rows, *self._pwr12_rows):
            row.setStyleSheet(_row_stylesheet(palette))
        for button in self._out_buttons:
            _style_digital_out_toggle(button, button.isChecked(), palette)
        for button in (*self._relay_buttons, *self._pwr12_buttons):
            # _SegmentedToggle liest die Palette in seinem eigenen paintEvent
            # jedes Mal frisch (current_palette()) -- ein update() reicht,
            # um mit den neuen Farben neu zu zeichnen.
            button.update()
        apply_panel_tint(self, self._color_key)

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def _on_color_selected(self, color_key) -> None:
        self.panel_color_requested.emit(self._device_id, color_key)

    def set_panel_color(self, color_key: str | None) -> None:
        self._color_key = color_key
        apply_panel_tint(self, color_key)
        self._color_button.set_current_color(color_key)

    def set_colors_enabled(self, enabled: bool) -> None:
        self._color_button.setVisible(enabled)

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(
            self, tr("Gerät umbenennen"), tr("Name:"), text=self.title()
        )
        if ok and new_label.strip():
            self.rename_requested.emit("hil", self._device_id, new_label.strip())

    def _sync_toggles(self, buttons: list[QWidget], states: list[bool], styler=None) -> None:
        pal = current_palette()
        for button, on in zip(buttons, states):
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)
            if styler is not None:
                styler(button, on, pal)
            else:
                # _SegmentedToggle braucht keinen externen Styler (siehe
                # _on_theme_changed) -- setChecked() stoesst Qt-intern
                # bereits ein Repaint an, ein zusaetzliches update() stellt
                # das auch bei blockierten Signalen sicher.
                button.update()

    def set_output_states(self, states: list[bool]) -> None:
        self._sync_toggles(self._out_buttons, states, _style_digital_out_toggle)

    def set_relay_states(self, states: list[bool]) -> None:
        self._sync_toggles(self._relay_buttons, states)

    def set_pwr12_states(self, states: list[bool]) -> None:
        self._sync_toggles(self._pwr12_buttons, states)

    def capture_state(self) -> dict:
        """Aktueller Zustand fuer die globale Preset-Leiste (siehe PresetBar)."""
        return {
            "outputs": [b.isChecked() for b in self._out_buttons],
            "analog_out": [s.value() for s in self._aout_spins],
            "pwm": [s.value() for s in self._pwm_spins],
            "relays": [b.isChecked() for b in self._relay_buttons],
            "pwr12": [b.isChecked() for b in self._pwr12_buttons],
            "current_limits": [s.value() for s in self._limit_spins],
        }

    def apply_state(self, state: dict) -> None:
        """Uebernimmt ein Preset (siehe PresetBar) -- schreibt jeden Wert
        sofort auf die Hardware (Signal-Emits), analog zu
        LoadControlGroup.apply_state. Fehlende/kaputte Eintraege werden
        uebersprungen statt die uebrigen Kanaele zu blockieren."""
        for ch, on in enumerate(state.get("outputs", []), start=1):
            self.set_output.emit(self._device_id, ch, bool(on))
        for ch, mv in enumerate(state.get("analog_out", []), start=1):
            try:
                self.set_analog_output.emit(self._device_id, ch, int(mv))
            except (TypeError, ValueError):
                pass
        for ch, permille in enumerate(state.get("pwm", []), start=1):
            try:
                self.set_pwm.emit(self._device_id, ch, int(permille))
            except (TypeError, ValueError):
                pass
        for ch, on in enumerate(state.get("relays", []), start=1):
            self.set_relay.emit(self._device_id, ch, bool(on))
        for ch, on in enumerate(state.get("pwr12", []), start=1):
            self.set_pwr12.emit(self._device_id, ch, bool(on))
        for ch, ma in enumerate(state.get("current_limits", []), start=1):
            try:
                self.set_current_limit.emit(self._device_id, ch, int(ma))
            except (TypeError, ValueError):
                pass


PRESET_BUTTON_SIZE = QSize(132, 60)
PRESET_SUB_BUTTON_SIZE = QSize(24, 22)
PRESET_SUB_BUTTON_MARGIN = 3


class _PresetSlotButton(QWidget):
    """Ein einzelner Preset-Platz: ein grosser, deutlich hervorgehobener
    Haupt-Button (Preset laden) mit den beiden Sub-Buttons Speichern und
    Umbenennen als kleine Ecken-Buttons oben rechts bzw. unten rechts --
    optisch Teil des Haupt-Buttons statt einer eigenen Reihe daneben. Die
    Sub-Buttons sind eigene Kind-Widgets, ueber move()+raise_() auf dem
    Haupt-Button platziert (Qt-Layouts kennen kein Ueberlappen von Kindern
    mit unterschiedlicher Klickfaeche, daher hier bewusst absolute
    Positionierung statt eines Layouts)."""

    load_clicked = Signal()
    save_clicked = Signal()
    rename_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(PRESET_BUTTON_SIZE)

        self._main_button = QPushButton(self)
        self._main_button.setGeometry(0, 0, PRESET_BUTTON_SIZE.width(), PRESET_BUTTON_SIZE.height())
        self._main_button.clicked.connect(self.load_clicked)

        self._save_button = self._make_sub_button("mdi.content-save-outline")
        self._save_button.move(
            PRESET_BUTTON_SIZE.width() - PRESET_SUB_BUTTON_SIZE.width() - PRESET_SUB_BUTTON_MARGIN,
            PRESET_SUB_BUTTON_MARGIN,
        )
        self._save_button.clicked.connect(self.save_clicked)

        self._rename_button = self._make_sub_button("mdi.pencil-outline")
        self._rename_button.move(
            PRESET_BUTTON_SIZE.width() - PRESET_SUB_BUTTON_SIZE.width() - PRESET_SUB_BUTTON_MARGIN,
            PRESET_BUTTON_SIZE.height() - PRESET_SUB_BUTTON_SIZE.height() - PRESET_SUB_BUTTON_MARGIN,
        )
        self._rename_button.clicked.connect(self.rename_clicked)

    def _make_sub_button(self, icon_name: str) -> IconButton:
        button = IconButton(icon_name, "")
        button.setParent(self)
        button.setFixedSize(PRESET_SUB_BUTTON_SIZE)
        button.setIconSize(QSize(14, 14))
        button.raise_()  # ueber dem Haupt-Button, sonst schluckt der die Klicks
        return button

    def set_text(self, text: str) -> None:
        self._main_button.setText(text)

    def set_tooltips(self, load: str, save: str, rename: str) -> None:
        self._main_button.setToolTip(load)
        self._save_button.setToolTip(save)
        self._rename_button.setToolTip(rename)

    def apply_style(self, pal: Palette) -> None:
        """Haupt-Button dezent hervorgehoben statt im neutralen Standard-
        Button-Look -- ein Preset-Platz ist eine haeufig genutzte
        Schnellzugriffs-Aktion und soll auf einen Blick auffindbar sein.

        Nutzt bewusst denselben abgetoenten Farbton wie die individuellen
        Geraete-Panel-Farben (pal.panel_tints, siehe panel_color.py) statt
        des vollen Akzent-Tons (pal.accent) -- letzterer wirkte zu grell/
        knallig als dauerhafte Flaeche. "blue" ist hier kein Bezug zu einem
        bestimmten Geraet (Presets sind geraeteuebergreifend), sondern nur
        als ruhiger, einheitlicher Ton fuer alle 5 Plaetze gewaehlt. Text in
        pal.text statt pal.surface, da die Panel-Tints (anders als pal.accent)
        bewusst nah an der normalen Oberflaechenhelligkeit liegen -- genau wie
        beim GroupBox-Titel einer getoenten Geraete-Panel bleibt pal.text
        darauf gut lesbar. Ein Rahmen in der Akzentfarbe bei Hover/Pressed
        gibt weiterhin klares Klick-Feedback, ohne die Ruheflaeche zu grell
        zu machen. Die Sub-Buttons bleiben bewusst im normalen IconButton-Look
        (globales Stylesheet), damit sie sich als "kleinere Nebenaktion" vom
        Haupt-Button abheben."""
        tint = pal.panel_tints["blue"]
        self._main_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {tint};
                color: {pal.text};
                border: 1px solid {pal.border};
                border-radius: 8px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                border: 2px solid {pal.accent};
            }}
            QPushButton:pressed {{
                border: 2px solid {pal.accent_hover};
            }}
        """)


class PresetBar(QWidget):
    """Leiste mit 5 festen, geraeteuebergreifenden Preset-Plaetzen (siehe
    presets.py) ganz oben im Control-Tab."""

    load_requested = Signal(int)    # slot index
    save_requested = Signal(int)
    rename_requested = Signal(int)  # slot index

    def __init__(self, presets: PresetStore) -> None:
        super().__init__()
        self._presets = presets
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)

        self._slot_buttons: list[_PresetSlotButton] = []
        for slot in range(SLOT_COUNT):
            slot_button = _PresetSlotButton()
            slot_button.load_clicked.connect(lambda s=slot: self.load_requested.emit(s))
            slot_button.save_clicked.connect(lambda s=slot: self.save_requested.emit(s))
            slot_button.rename_clicked.connect(lambda s=slot: self.rename_requested.emit(s))
            layout.addWidget(slot_button)
            self._slot_buttons.append(slot_button)
        layout.addStretch()

        presets.preset_changed.connect(self._refresh_names)
        Translator.instance().language_changed.connect(self._refresh_names)
        ThemeManager.instance().changed.connect(self._on_theme_changed)
        self._on_theme_changed(current_palette())
        self._refresh_names()

    def _on_theme_changed(self, pal: Palette) -> None:
        for button in self._slot_buttons:
            button.apply_style(pal)

    def _refresh_names(self, *_args) -> None:
        for slot, button in enumerate(self._slot_buttons):
            button.set_text(self._presets.name(slot))
            button.set_tooltips(tr("Preset laden"), tr("Preset speichern"), tr("Preset umbenennen"))


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

        self._preset_bar = PresetBar(presets)
        self._preset_bar.load_requested.connect(self._on_preset_load)
        self._preset_bar.save_requested.connect(self._on_preset_save)
        self._preset_bar.rename_requested.connect(self._on_preset_rename)
        outer_layout.addWidget(self._preset_bar)

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

        # Platzhalter, solange kein Geraet verbunden ist (siehe
        # _update_empty_tile) -- von Anfang an sichtbar, da beim Start noch
        # keine Sektion existiert.
        self._empty_tile = NoDeviceTile()
        self._content_layout.addWidget(self._empty_tile)

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
        if kind == "picoscope":
            # Bewusst KEINE Control-Tab-Sektion: das PicoScope hat nur eine
            # Dashboard-Kachel + Start-Button fuer die PicoScope-7-App
            # (siehe picoscope_panel.py-Modul-Docstring) -- ohne diesen
            # fruehen Ausstieg wuerde es faelschlich in den generischen
            # "else"-Zweig unten fallen und eine bedeutungslose
            # CanControlGroup-Sektion bekommen.
            return
        section = self._sections.get(device_id)
        if section is not None:
            section.set_label(label)
            # BUGS.md #16: dieser Pfad wird auch beim "Geraetezuordnung
            # loeschen"-Reset fuer weiterhin verbundene Geraete durchlaufen
            # (main_window._on_reset_devices_requested -> on_device_added),
            # im selben Zug wie forget_device() fuer nicht mehr verbundene
            # Geraete -- ohne diesen Aufruf blieb die Sichtbarkeit der
            # "kein Geraet verbunden"-Kachel je nach Reihenfolge veraltet.
            self._update_empty_tile()
            return
        if kind == "load":
            section = LoadControlGroup(device_id, label)
        elif kind == "psu":
            section = PsuControlGroup(device_id, label)
        elif kind == "hil":
            section = HilControlGroup(device_id, label)
        else:
            section = CanControlGroup(device_id, label)
        section.hide()
        section.set_colors_enabled(self._colors_enabled)
        section.panel_color_requested.connect(self.panel_color_requested)
        section.rename_requested.connect(self.rename_requested)
        self._content_layout.addWidget(section)
        self._sections[device_id] = section
        self._equalize_sections()
        self.section_created.emit(kind, device_id, section)

    def forget_device(self, device_id: str) -> None:
        """Entfernt ein Geraet vollstaendig -- nur fuer den "Geraetezuordnung
        loeschen"-Button (main_window._on_reset_devices_requested) gedacht,
        siehe dashboard.DashboardWidget.forget_device fuer die Begruendung.
        Die Sektion ist bei einem getrennten Geraet ohnehin schon versteckt
        (siehe _set_online), wird hier aber zusaetzlich zerstoert statt nur
        unsichtbar zu bleiben."""
        section = self._sections.pop(device_id, None)
        if section is None:
            return
        self._content_layout.removeWidget(section)
        section.deleteLater()
        self._panel_colors.pop(device_id, None)
        self._equalize_sections()
        self._update_empty_tile()

    def set_panel_color(self, device_id: str, color_key: str | None) -> None:
        self._panel_colors[device_id] = color_key
        section = self._sections.get(device_id)
        if section is not None:
            section.set_panel_color(color_key if self._colors_enabled else None)

    def set_panel_colors_enabled(self, enabled: bool) -> None:
        self._colors_enabled = enabled
        for device_id, section in self._sections.items():
            section.set_panel_color(self._panel_colors.get(device_id) if enabled else None)
            section.set_colors_enabled(enabled)

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

    def set_can_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    def set_hil_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    def on_can_frame(self, device_id: str, arbitration_id: int, data_hex: str, extended: bool, timestamp: float) -> None:
        section = self._sections.get(device_id)
        if isinstance(section, CanControlGroup):
            section.append_frame(arbitration_id, data_hex, extended, timestamp)

    def set_hil_digital_state(self, device_id: str, inputs: list, outputs: list) -> None:
        # Nur outputs relevant -- inputs sind rein lesend und werden bereits
        # im Dashboard angezeigt (siehe HilControlGroup-Docstring).
        section = self._sections.get(device_id)
        if isinstance(section, HilControlGroup):
            section.set_output_states(outputs)

    def set_hil_relay_state(self, device_id: str, relays: list) -> None:
        section = self._sections.get(device_id)
        if isinstance(section, HilControlGroup):
            section.set_relay_states(relays)

    def set_hil_pwr12_state(self, device_id: str, enabled: list, current_ma: list) -> None:
        # current_ma hier ungenutzt -- die Sektion synchronisiert nur
        # den Schaltzustand, die Strommessung zeigt bereits das Dashboard.
        section = self._sections.get(device_id)
        if isinstance(section, HilControlGroup):
            section.set_pwr12_states(enabled)

    def _update_empty_tile(self) -> None:
        # BUGS.md #16 (Root Cause, per Live-Debug in einem QTabWidget mit
        # zweitem Tab bestaetigt): NICHT s.isVisible() verwenden. isVisible()
        # ist NICHT die eigene, explizit gesetzte Sichtbarkeit einer Sektion
        # (das waere hier gewollt) -- Qt liefert dort false, sobald IRGENDEIN
        # Vorfahre im Widget-Baum unsichtbar ist. ControlTab selbst ist aber
        # nur die AKTIVE Seite eines QTabWidget (siehe main_window.py):
        # solange z.B. der SettingsTab angezeigt wird (wo der "Geraetezu-
        # ordnung loeschen"-Button sitzt, der on_device_known/forget_device
        # und damit diese Methode ausloest), ist die ControlTab-Seite selbst
        # unsichtbar -- dann liefert JEDE Sektion isVisible()==False, egal ob
        # sie fuer ein verbundenes Geraet regulaer sichtbar geschaltet wurde.
        # Die Kachel wuerde dadurch faelschlich sichtbar geschaltet und blieb
        # es auch nach dem Zurueckwechseln auf den Control-Tab, waehrend die
        # eigentlich verbundenen Geraete-Sektionen (ihr eigenes Sichtbarkeits-
        # Flag blieb ja unveraendert "sichtbar") ebenfalls wieder erschienen
        # -- genau das gemeldete gleichzeitige Anzeigen beider.
        #
        # isHidden() dagegen spiegelt NUR das eigene, explizit gesetzte
        # hide()/show()-Flag der Sektion wider (siehe Qt-Doku), unabhaengig
        # vom Sichtbarkeitszustand der Vorfahren -- genau das hier benoetigte
        # "ist diese Sektion fuer ihr Geraet als verbunden markiert".
        self._empty_tile.setVisible(not any(not s.isHidden() for s in self._sections.values()))

    def _set_online(self, device_id: str, online: bool) -> None:
        section = self._sections.get(device_id)
        if section is not None:
            section.setVisible(online)
        self._update_empty_tile()

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

    def _on_preset_save(self, slot: int) -> None:
        devices = {
            device_id: section.capture_state()
            for device_id, section in self._sections.items()
            if section.isVisible()
        }
        self._presets.save(slot, devices)

    def _on_preset_load(self, slot: int) -> None:
        for device_id, state in self._presets.devices(slot).items():
            section = self._sections.get(device_id)
            if section is not None and section.isVisible():
                section.apply_state(state)

    def _on_preset_rename(self, slot: int) -> None:
        name, ok = QInputDialog.getText(
            self, tr("Preset umbenennen"), tr("Name:"), text=self._presets.name(slot)
        )
        if ok and name.strip():
            self._presets.rename(slot, name.strip())

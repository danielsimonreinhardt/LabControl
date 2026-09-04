"""Dashboard-Kachel fuer den microHIL (siehe microhil/driver.py).

Anders als dashboard._DevicePanel (generische Liste einzelner Messwerte,
siehe dort) braucht der microHIL eine strukturierte Anzeige: 4 Relais,
8 Digitalein-/-ausgaenge, 4 Analogeingaenge, 2 Analogausgaenge und 2
schaltbare 12V-Ausgaenge mit Stromsense lassen sich nicht sinnvoll in
dessen FIELD_DEFS/QFormLayout-Schema pressen. Deshalb ein eigenstaendiges
Panel mit vier untereinander gestapelten, durch Trennlinien abgesetzten
Bereichen (Digital IO, Analog IO, Relais, 12V-OUT) -- Aufteilung und
LED-Punkt-Optik nach Absprache.

PWM1-4 bewusst NICHT auf dem Dashboard: PWM ist ein Sollwert/Steuerelement
(mit OUT1-4 verriegelt, siehe driver.INTERLOCKED_CHANNELS), das Dashboard
zeigt nur Ist-Zustaende an -- passt eher in einen kuenftigen Control-Tab-
Abschnitt (analog zu control_tab.LoadControlGroup/PsuControlGroup).

Noch NICHT an device_worker.py/DashboardWidget angeschlossen (siehe
microhil/README.md, "Naechste Schritte") -- dieses Modul stellt nur das
Panel selbst bereit, mit einer set_online()/update_*()-Schnittstelle nach
dem Vorbild von dashboard._DevicePanel, damit die spaetere Verdrahtung
(device_registry-Kind "hil", DashboardWidget.on_device_known, Polling in
device_worker.py) sich direkt anschliessen laesst. Wichtig fuer diese
Verdrahtung: DashboardWidget._relayout_panels() gleicht aktuell die Breite
ALLER Panels auf das breiteste an (siehe dortigen Kommentar) -- dieses
Panel ist durch die 8er-Punktreihen deutlich breiter als ein Last-/
Netzteil-Panel, das muesste vor dem Anschluss noch beruecksichtigt werden
(z.B. eigene Breiten-Ratsche je Kind statt einer gemeinsamen).
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtWidgets import QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from i18n import Translator, tr
from microhil.driver import AIN_COUNT, AOUT_COUNT, IN_COUNT, OUT_COUNT, PWR12_COUNT, RELAY_COUNT
from no_device_tile import OFFLINE_BACKGROUND, OFFLINE_BORDER, OFFLINE_TEXT
from theme import Palette, ThemeManager, no_own_background
from theme import current as current_palette

DOT_ICON_SIZE = 15
DOT_ON = "mdi.circle"
DOT_OFF = "mdi.circle-outline"
OFFLINE_ICON_NAME = "mdi.close-network-outline"
OFFLINE_ICON_SIZE = 22
OFFLINE_ICON_MARGIN = 5

SECTION_TITLES = ["Digital IO", "Analog IO", "Relais", "12V-OUT"]


def _dot_pixmap(on: bool, palette: Palette):
    color = palette.check_pass if on else palette.text_muted
    return qta.icon(DOT_ON if on else DOT_OFF, color=color).pixmap(DOT_ICON_SIZE, DOT_ICON_SIZE)


class _SectionTitle(QLabel):
    """Gedaempfte Zeile fuer die vier Bereichs-Ueberschriften -- text_muted
    statt normaler Textfarbe, analog zu anderen gedaempften Beschriftungen
    im Projekt (siehe dashboard.py-Docstring zu pal.text_muted).

    "background: transparent" ist hier PFLICHT, nicht Kosmetik (siehe
    theme.no_own_background-Docstring): als direktes Kind der QVBoxLayout-
    Spalte (nicht wie die Werte-Zeilen ueber einen no_own_background()-
    Wrapper) spannt sich das Label ueber die volle Panel-Breite und wuerde
    sonst die globale "QWidget{background-color:pal.bg}"-Regel aus
    theme.stylesheet() zeigen -- ein sichtbarer Seitenhintergrund-Balken
    quer durchs Panel, der sich farblich vom Panel selbst (pal.surface)
    abhebt."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setStyleSheet("font-weight: bold; background: transparent;")

    def apply_palette(self, palette: Palette) -> None:
        self.setStyleSheet(f"font-weight: bold; color: {palette.text_muted}; background: transparent;")


class _Divider(QFrame):
    """Trennlinie zwischen den vier Bereichen. Explizit gefaerbt (statt der
    Default-OS-Rahmenoptik von QFrame.Shape.HLine) und ueber apply_palette()
    themefaehig, sonst bleibt sie in beiden Themes praktisch unsichtbar
    (Default-Sunken-Schatten setzt auf Kontrast zum umgebenden Widget-
    Hintergrund, den es hier per Stylesheet nicht gibt)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setFixedHeight(1)

    def apply_palette(self, palette: Palette) -> None:
        # text_muted statt border: border ist auf denselben pal.surface-
        # Hintergrund abgestimmt wie dieses Panel selbst (siehe
        # dashboard._DevicePanel-Docstring zu genau demselben Kontrastproblem
        # bei Panel-Rahmen) und faellt hier praktisch unsichtbar aus.
        self.setStyleSheet(f"background-color: {palette.text_muted}; border: none;")


class _DotArray(QWidget):
    """Eine Zeile mit Praefix-Label (z.B. "IN") gefolgt von `count`
    Punkt+Nummer-Indikatoren -- fuer IN1-8/OUT1-8/RELAY1-4."""

    def __init__(self, prefix: str, count: int, tooltip_key: str) -> None:
        super().__init__()
        # Rohes (unuebersetztes) Format-Template, z.B. "Digitaleingang {0}"
        # -- wird bei jedem retranslate() (Spracheinstellung geaendert) neu
        # durch tr() gejagt, statt die Tooltips nur einmal bei der
        # Konstruktion zu uebersetzen.
        self._tooltip_key = tooltip_key
        self._icons: list[QLabel] = []
        self._numbers: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._prefix_label = QLabel(prefix)
        self._prefix_label.setMinimumWidth(28)
        layout.addWidget(self._prefix_label)

        for i in range(count):
            cell = no_own_background(QWidget())
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            icon = QLabel()
            number = QLabel(str(i + 1))
            cell_layout.addWidget(icon)
            cell_layout.addWidget(number)
            layout.addWidget(cell)
            self._icons.append(icon)
            self._numbers.append(number)
        layout.addStretch()

        self.retranslate()
        self.set_states([False] * count)

    def set_states(self, states: list[bool]) -> None:
        palette = current_palette()
        for icon, on in zip(self._icons, states):
            icon.setPixmap(_dot_pixmap(on, palette))

    def retranslate(self) -> None:
        for i, (icon, number) in enumerate(zip(self._icons, self._numbers)):
            tooltip = tr(self._tooltip_key, index=i + 1)
            icon.setToolTip(tooltip)
            number.setToolTip(tooltip)


class _ValueGrid(QWidget):
    """2-spaltiges Raster aus Label+Wert-Paaren, z.B. fuer AIN1-4/AOUT1-2."""

    def __init__(self, labels: list[str], columns: int = 2) -> None:
        super().__init__()
        self._value_labels: dict[str, QLabel] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        for index, name in enumerate(labels):
            row, col = divmod(index, columns)
            cell = no_own_background(QWidget())
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(4)
            name_label = QLabel(f"{name}:")
            value_label = QLabel("--")
            cell_layout.addWidget(name_label)
            cell_layout.addWidget(value_label)
            cell_layout.addStretch()
            grid.addWidget(cell, row, col)
            self._value_labels[name] = value_label

    def set_value(self, name: str, text: str) -> None:
        self._value_labels[name].setText(text)

    def clear_values(self) -> None:
        for label in self._value_labels.values():
            label.setText("--")


class _Pwr12Row(QWidget):
    """Eine Zeile je 12V-Ausgang: Punkt (Enable-Zustand) + Kanalnummer +
    Stromsense-Spannung (siehe driver.get_current_sense_mv -- rohe mV,
    keine mA-Umrechnung, daher hier ebenfalls "mV" statt "mA")."""

    def __init__(self, count: int) -> None:
        super().__init__()
        self._icons: list[QLabel] = []
        self._numbers: list[QLabel] = []
        self._value_labels: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        for i in range(count):
            cell = no_own_background(QWidget())
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(4)
            icon = QLabel()
            number = QLabel(str(i + 1))
            value = QLabel("-- mV")
            cell_layout.addWidget(icon)
            cell_layout.addWidget(number)
            cell_layout.addWidget(value)
            layout.addWidget(cell)
            self._icons.append(icon)
            self._numbers.append(number)
            self._value_labels.append(value)
        layout.addStretch()
        self.retranslate()
        self.set_states([False] * count)

    def set_states(self, enabled: list[bool]) -> None:
        palette = current_palette()
        for icon, on in zip(self._icons, enabled):
            icon.setPixmap(_dot_pixmap(on, palette))

    def retranslate(self) -> None:
        for i, (icon, number) in enumerate(zip(self._icons, self._numbers)):
            tooltip = tr("12V-Ausgang {index}", index=i + 1)
            icon.setToolTip(tooltip)
            number.setToolTip(tooltip)

    def set_values(self, values_mv: list[int]) -> None:
        for label, value in zip(self._value_labels, values_mv):
            label.setText(f"{value} mV")

    def clear_values(self) -> None:
        for label in self._value_labels:
            label.setText("-- mV")


class MicroHilPanel(QGroupBox):
    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._online = True
        self.setTitle(label)

        # Zuletzt gesetzte Bit-Zustaende, gemerkt fuer _on_theme_changed
        # (siehe dort) -- als Instanzattribute, NICHT als Klassenattribute,
        # sonst wuerden mehrere Panel-Instanzen (z.B. zwei microHIL-Geraete)
        # sich denselben Listeninhalt teilen.
        self._last_in: list[bool] = [False] * IN_COUNT
        self._last_out: list[bool] = [False] * OUT_COUNT
        self._last_relay: list[bool] = [False] * RELAY_COUNT
        self._last_pwr12_enabled: list[bool] = [False] * PWR12_COUNT

        outer = QVBoxLayout(self)
        self._section_titles: list[_SectionTitle] = []
        self._dividers: list[_Divider] = []

        # -- Digital IO --------------------------------------------------
        outer.addWidget(self._section_title(SECTION_TITLES[0]))
        self._in_array = _DotArray("IN", IN_COUNT, "Digitaleingang {index}")
        self._out_array = _DotArray("OUT", OUT_COUNT, "Digitalausgang {index}")
        outer.addWidget(self._in_array)
        outer.addWidget(self._out_array)
        outer.addWidget(self._new_divider())

        # -- Analog IO -----------------------------------------------------
        outer.addWidget(self._section_title(SECTION_TITLES[1]))
        self._ain_grid = _ValueGrid([f"AIN{i}" for i in range(1, AIN_COUNT + 1)])
        self._aout_grid = _ValueGrid([f"AOUT{i}" for i in range(1, AOUT_COUNT + 1)])
        outer.addWidget(self._ain_grid)
        outer.addWidget(self._aout_grid)
        outer.addWidget(self._new_divider())

        # -- Relais ----------------------------------------------------------
        outer.addWidget(self._section_title(SECTION_TITLES[2]))
        self._relay_array = _DotArray("", RELAY_COUNT, "Relais {index}")
        outer.addWidget(self._relay_array)
        outer.addWidget(self._new_divider())

        # -- 12V-OUT -----------------------------------------------------------
        outer.addWidget(self._section_title(SECTION_TITLES[3]))
        self._pwr12_row = _Pwr12Row(PWR12_COUNT)
        outer.addWidget(self._pwr12_row)

        # "Verbindung getrennt"-Badge -- gleiches Prinzip wie
        # dashboard._DevicePanel (siehe dortigen ausfuehrlichen Kommentar
        # zu no_own_background() als Pflicht, nicht nur Kosmetik).
        self._offline_icon = no_own_background(QLabel(self))
        self._offline_icon.setFixedSize(OFFLINE_ICON_SIZE, OFFLINE_ICON_SIZE)
        self._offline_icon.setPixmap(
            qta.icon(OFFLINE_ICON_NAME, color=OFFLINE_TEXT).pixmap(OFFLINE_ICON_SIZE, OFFLINE_ICON_SIZE)
        )
        self._offline_icon.hide()
        self._offline_icon.raise_()

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()
        self._apply_style(current_palette())

    def _section_title(self, text: str) -> _SectionTitle:
        title = _SectionTitle(tr(text))
        title.apply_palette(current_palette())
        self._section_titles.append(title)
        return title

    def _new_divider(self) -> _Divider:
        divider = _Divider()
        divider.apply_palette(current_palette())
        self._dividers.append(divider)
        return divider

    def _retranslate(self) -> None:
        for title, text in zip(self._section_titles, SECTION_TITLES):
            title.setText(tr(text))
        self._in_array.retranslate()
        self._out_array.retranslate()
        self._relay_array.retranslate()
        self._pwr12_row.retranslate()
        self._offline_icon.setToolTip(tr("Verbindung getrennt"))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._offline_icon.move(self.width() - OFFLINE_ICON_SIZE - OFFLINE_ICON_MARGIN, OFFLINE_ICON_MARGIN)

    def _on_theme_changed(self, palette: Palette) -> None:
        for title in self._section_titles:
            title.apply_palette(palette)
        for divider in self._dividers:
            divider.apply_palette(palette)
        self._apply_style(palette)
        # Punkt-Pixmaps haengen an der Palette (check_pass/text_muted) --
        # mit den zuletzt bekannten Zustaenden neu zeichnen statt sie zu
        # verlieren.
        self._in_array.set_states(self._last_in)
        self._out_array.set_states(self._last_out)
        self._relay_array.set_states(self._last_relay)
        self._pwr12_row.set_states(self._last_pwr12_enabled)

    def _apply_style(self, palette: Palette) -> None:
        if not self._online:
            self.setStyleSheet(
                f"QGroupBox {{ background-color: {OFFLINE_BACKGROUND}; "
                f"border: 1px solid {OFFLINE_BORDER}; border-radius: 6px; }}"
            )
            return
        self.setStyleSheet(
            f"QGroupBox {{ border: 1px solid {palette.text_muted}; border-radius: 6px; }}"
        )

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_online(self, online: bool) -> None:
        self._online = online
        if not online:
            self.clear_values()
        self._offline_icon.setVisible(not online)
        self.setVisible(True)
        self._apply_style(current_palette())

    # -- Werte -----------------------------------------------------------------

    def update_inputs(self, states: list[bool]) -> None:
        self._last_in = list(states)
        self._in_array.set_states(states)

    def update_outputs(self, states: list[bool]) -> None:
        self._last_out = list(states)
        self._out_array.set_states(states)

    def update_relays(self, states: list[bool]) -> None:
        self._last_relay = list(states)
        self._relay_array.set_states(states)

    def update_analog_in(self, values_mv: list[int]) -> None:
        for i, value in enumerate(values_mv, start=1):
            self._ain_grid.set_value(f"AIN{i}", f"{value} mV")

    def update_analog_out(self, values_mv: list[int]) -> None:
        for i, value in enumerate(values_mv, start=1):
            self._aout_grid.set_value(f"AOUT{i}", f"{value} mV")

    def update_pwr12(self, enabled: list[bool], current_sense_mv: list[int]) -> None:
        self._last_pwr12_enabled = list(enabled)
        self._pwr12_row.set_states(enabled)
        self._pwr12_row.set_values(current_sense_mv)

    def clear_values(self) -> None:
        self.update_inputs([False] * IN_COUNT)
        self.update_outputs([False] * OUT_COUNT)
        self.update_relays([False] * RELAY_COUNT)
        self._ain_grid.clear_values()
        self._aout_grid.clear_values()
        self._pwr12_row.set_states([False] * PWR12_COUNT)
        self._pwr12_row.clear_values()

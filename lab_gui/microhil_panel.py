"""Dashboard-Kachel fuer den microHIL (siehe microhil/driver.py).

Anders als dashboard._DevicePanel (generische Liste einzelner Messwerte,
siehe dort) braucht der microHIL eine strukturierte Anzeige: 4 Relais,
8 Digitalein-/-ausgaenge, 4 Analogeingaenge, 2 Analogausgaenge und 2
schaltbare 12V-Ausgaenge mit Stromsense lassen sich nicht sinnvoll in
dessen FIELD_DEFS/QFormLayout-Schema pressen. Deshalb ein eigenstaendiges
Panel mit vier Bereichen (Digital IO, Analog IO, Relais, 12V-OUT) --
Aufteilung und LED-Punkt-Optik nach Absprache.

Zwei Ansichten, analog zu dashboard._DevicePanel.set_compact():
- Normal: die vier Bereiche untereinander gestapelt, durch Trennlinien
  abgesetzt (_normal_widget).
- Kompakt: alle Bereiche NEBENEINANDER statt gestapelt (_compact_widget),
  damit die Kachel nicht wesentlich hoeher wird als die Last-/Netzteil-
  Kompaktansicht (dashboard._DevicePanel: eine einzige Zeile) -- eine
  fruehere 2x2-Raster-Fassung war dafuer immer noch deutlich zu hoch.
  Analog IO (AIN+AOUT) UND die zu einer Gruppe zusammengefassten Relais+
  12V-OUT sitzen jeweils in einem 3x2-Raster statt einer einzeiligen
  Zeile (_ValueGrid(columns=3) bzw. _RelayPwr12Grid) -- spart Breite, ohne
  die Zeilenhoehe zu erhoehen, die ohnehin schon Digital IO vorgibt (IN +
  OUT uebereinander, 16 Einzel-Bits lassen sich nicht sinnvoll in eine
  Zeile pressen, ohne entweder unleserlich klein oder unhandlich breit zu
  werden). Beide Ansichten benutzen eigene Widget-Instanzen (siehe
  update_*()-Methoden, die beide Saetze gleichzeitig fuellen) statt
  derselben Widgets in zwei Layouts -- ein Qt-Widget kann nur in einem
  Layout gleichzeitig haengen, dasselbe Duplizierungsprinzip nutzt bereits
  dashboard._DevicePanel fuer seine Normal-/Kompaktwerte.

PWM1-4 bewusst NICHT auf dem Dashboard: PWM ist ein Sollwert/Steuerelement
(mit OUT1-4 verriegelt, siehe driver.INTERLOCKED_CHANNELS), das Dashboard
zeigt nur Ist-Zustaende an -- passt eher in einen kuenftigen Control-Tab-
Abschnitt (analog zu control_tab.LoadControlGroup/PsuControlGroup).

An device_worker.py/DashboardWidget angeschlossen (dashboard.
on_device_known erzeugt bei kind="hil" ein MicroHilPanel statt des
generischen _DevicePanel, device_worker._poll_hil() fuellt es ueber die
hil_*-Signale/dashboard.update_hil_*()-Slots) -- AOUT1-2 bleiben dabei
absichtlich bei "--": es gibt kein `AOUT?`-Kommando zum Zuruecklesen
(siehe microhil/driver.py), ohne einen Control-Tab-Abschnitt kennt diese
App also keinen tatsaechlichen AOUT-Sollwert.

Achtung bei Aenderungen an der Panel-Groesse: DashboardWidget.
_relayout_panels() gleicht in der NORMALANSICHT die Breite ALLER Panels
auf das breiteste an (siehe dortigen Kommentar) -- ein sichtbares, aber
rein kosmetisches Detail, seit dieses Panel neben Last-/Netzteil-Panels
auftaucht (es ist breiter, zieht die anderen Panels also etwas breiter
als deren eigenes Minimum). In der KOMPAKTANSICHT betrifft das nicht: dort bekommt jedes Panel
bereits seine eigene, an den Inhalt angepasste Breiten-Ratsche (siehe
_relayout_panels, compact-Zweig).
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
# Trennt in _Pwr12Row optisch den Schaltzustand (Punkt+Nummer) von der
# Stromangabe -- dieselbe Ikonografie wie dashboard.FIELD_ICONS["current"],
# rein dekorativ (text_muted), kein eigener Zustand.
CURRENT_ICON_NAME = "mdi.current-dc"
CURRENT_ICON_SIZE = 14

SECTION_TITLES = ["Digital IO", "Analog IO", "Relais", "12V-OUT"]


def _dot_pixmap(on: bool, palette: Palette):
    color = palette.check_pass if on else palette.text_muted
    return qta.icon(DOT_ON if on else DOT_OFF, color=color).pixmap(DOT_ICON_SIZE, DOT_ICON_SIZE)


def _dot_cell(number: int) -> tuple[QWidget, QLabel, QLabel]:
    """Ein einzelner Punkt+Nummer-Indikator als eigenstaendiges Widget --
    Baustein sowohl fuer _DotArray als auch _RelayPwr12Grid (siehe dort):
    gibt (Zelle, Icon-Label, Nummern-Label) zurueck, damit der Aufrufer
    Icon/Nummer selbst in seiner eigenen Icons-/Numbers-Liste fuer
    set_states()/retranslate() nachfuehren kann.

    KEIN eigener addStretch() hier -- eine fruehere Fassung hatte einen
    (fuer ein inzwischen verworfenes QGridLayout-Design mit spalten-
    uebergreifendem Widget, siehe Git-Historie), der aber Qt's Box-Layout-
    Algorithmus jeder Zelle bereits dann sichtbare Zusatzbreite zuteilen
    liess, wenn IRGENDEIN Geschwister-Widget im selben Zeilen-Layout
    (z.B. der breite _Pwr12Row) selbst einen Stretch enthielt -- mit
    sichtbaren Luecken zwischen den Zellen zur Folge, obwohl die Zeile
    insgesamt exakt ihre sizeHint()-Breite bekam. Genau wie bei _DotArray
    reicht EIN einzelner addStretch() am Ende der jeweiligen Zeile
    (row1_layout/row2_layout in _RelayPwr12Grid)."""
    cell = no_own_background(QWidget())
    cell_layout = QHBoxLayout(cell)
    cell_layout.setContentsMargins(0, 0, 0, 0)
    cell_layout.setSpacing(2)
    icon = QLabel()
    number_label = QLabel(str(number))
    cell_layout.addWidget(icon)
    cell_layout.addWidget(number_label)
    return cell, icon, number_label


class _SectionTitle(QLabel):
    """Gedaempfte Zeile fuer die vier Bereichs-Ueberschriften -- text_muted
    statt normaler Textfarbe, analog zu anderen gedaempften Beschriftungen
    im Projekt (siehe dashboard.py-Docstring zu pal.text_muted).

    "background: transparent" ist hier PFLICHT, nicht Kosmetik (siehe
    theme.no_own_background-Docstring): als direktes Kind der QVBoxLayout-
    Spalte (nicht wie die Werte-Zeilen ueber einen no_own_background()-
    Wrapper) spannt sich das Label ueber die volle Breite und wuerde sonst
    die globale "QWidget{background-color:pal.bg}"-Regel aus
    theme.stylesheet() zeigen -- ein sichtbarer Seitenhintergrund-Balken
    quer durchs Panel, der sich farblich vom Panel selbst (pal.surface)
    abhebt."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setStyleSheet("font-weight: bold; background: transparent;")

    def apply_palette(self, palette: Palette) -> None:
        self.setStyleSheet(f"font-weight: bold; color: {palette.text_muted}; background: transparent;")


class _Divider(QFrame):
    """Trennlinie zwischen Bereichen -- horizontal in der Normalansicht,
    vertikal zwischen den nebeneinander liegenden Gruppen der
    Kompaktansicht (siehe MicroHilPanel.__init__). Explizit gefaerbt
    (statt der Default-OS-Rahmenoptik von QFrame.Shape.HLine/VLine) und
    ueber apply_palette() themefaehig, sonst bleibt sie in beiden Themes
    praktisch unsichtbar (Default-Sunken-Schatten setzt auf Kontrast zum
    umgebenden Widget-Hintergrund, den es hier per Stylesheet nicht
    gibt)."""

    def __init__(self, vertical: bool = False) -> None:
        super().__init__()
        if vertical:
            self.setFrameShape(QFrame.Shape.VLine)
            self.setFixedWidth(1)
        else:
            self.setFrameShape(QFrame.Shape.HLine)
            self.setFixedHeight(1)
        self.setFrameShadow(QFrame.Shadow.Plain)

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
        # Rohes (unuebersetztes) Format-Template, z.B. "Digitaleingang {index}"
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
            cell, icon, number = _dot_cell(i + 1)
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


class _RelayPwr12Grid(QWidget):
    """Kompakte 3x2-Anordnung fuer Relais 1-4 + 12V-OUT 1-2 als EINE
    gemeinsame Gruppe (Absprache) -- Relais 1-3 in Zeile 1, Relais 4 +
    der vollstaendige _Pwr12Row in Zeile 2. Reine Kompaktansichts-
    Variante: die Normalansicht zeigt Relais/12V-OUT weiterhin als zwei
    eigene Bereiche (_relay_array/_pwr12_row), siehe MicroHilPanel.
    __init__.

    ZWEI EINFACHE ZEILEN (QHBoxLayout) statt eines echten QGridLayout mit
    spaltenuebergreifendem Widget: eine erste Fassung mit QGridLayout +
    _Pwr12Row per columnSpan() ueber 2 Spalten fuehrte zu unvorhersehbar
    breiten Spalten (Qt verteilt die vom spannenden Widget benoetigte
    Breite nicht gleichmaessig auf die ueberspannten Spalten, siehe
    Git-Historie) -- Relais 2/3 landeten dadurch mit riesigen Luecken
    dazwischen. Zwei unabhaengige Zeilen (wie _DotArray/_ValueGrid es
    bereits vormachen) sind dagegen von Natur aus vorhersehbar: jede
    Zeile bemisst sich nur an ihrem eigenen Inhalt.

    Feste "3 in Zeile 1, Rest in Zeile 2"-Aufteilung fuer die konkrete
    microHIL-Hardware (4 Relais) statt einer allgemeinen Formel -- diese
    Zahl aendert sich nicht, eine generische Berechnung waere hier nur
    unnoetige Indirektion."""

    RELAYS_IN_FIRST_ROW = 3

    def __init__(self, relay_count: int, pwr12_count: int) -> None:
        super().__init__()
        self._relay_icons: list[QLabel] = []
        self._relay_numbers: list[QLabel] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        row1 = no_own_background(QWidget())
        row1_layout = QHBoxLayout(row1)
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(10)
        for i in range(min(self.RELAYS_IN_FIRST_ROW, relay_count)):
            cell, icon, number = _dot_cell(i + 1)
            row1_layout.addWidget(cell)
            self._relay_icons.append(icon)
            self._relay_numbers.append(number)
        row1_layout.addStretch()
        outer.addWidget(row1)

        row2 = no_own_background(QWidget())
        row2_layout = QHBoxLayout(row2)
        row2_layout.setContentsMargins(0, 0, 0, 0)
        row2_layout.setSpacing(10)
        for i in range(self.RELAYS_IN_FIRST_ROW, relay_count):
            cell, icon, number = _dot_cell(i + 1)
            row2_layout.addWidget(cell)
            self._relay_icons.append(icon)
            self._relay_numbers.append(number)
        # 12V-Praefix bleibt (siehe _Pwr12Row): ohne "Relais"-/"12V-OUT"-
        # Bereichsueberschriften in der Kompaktansicht ist er die einzige
        # Beschriftung, die die zweite Zeile von den Relais-Zellen absetzt.
        self.pwr12_row = _Pwr12Row(pwr12_count, prefix="12V")
        row2_layout.addWidget(self.pwr12_row)
        row2_layout.addStretch()
        outer.addWidget(row2)

    def set_relay_states(self, states: list[bool]) -> None:
        palette = current_palette()
        for icon, on in zip(self._relay_icons, states):
            icon.setPixmap(_dot_pixmap(on, palette))

    def retranslate_relays(self) -> None:
        for i, (icon, number) in enumerate(zip(self._relay_icons, self._relay_numbers)):
            tooltip = tr("Relais {index}", index=i + 1)
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
    """Eine Zeile je 12V-Ausgang: Punkt (Enable-Zustand) + Kanalnummer,
    durch ein Strom-Icon abgesetzt, dann die Stromangabe.

    WICHTIG (Absprache): driver.get_current_sense_mv() liefert die ROHE
    Sense-Spannung in mV, keine echten mA -- der Shunt-/Verstaerkungsfaktor
    fehlt noch in der Firmware (siehe microHIL-Roadmap, "CURR? in mA
    umrechnen"). Die Beschriftung hier zeigt trotzdem bewusst "mA" statt
    "mV", auf ausdruecklichen Wunsch -- der intern durchgereichte Wert
    bleibt bis zur Firmware-Umrechnung die rohe mV-Zahl, nur mit falscher
    Einheit beschriftet. Bei der GUI-Integration (device_worker.py) auf
    keinen Fall vergessen, das zu korrigieren, sobald die Firmware echte
    mA liefert."""

    def __init__(self, count: int, prefix: str = "") -> None:
        super().__init__()
        self._icons: list[QLabel] = []
        self._numbers: list[QLabel] = []
        self._current_icons: list[QLabel] = []
        self._value_labels: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        if prefix:
            # Nur in der Kompaktansicht gesetzt (siehe MicroHilPanel.
            # __init__): dort steht -- anders als in der Normalansicht --
            # keine "12V-OUT"-Bereichsueberschrift mehr davor, seit Relais
            # und 12V-OUT zu einer Zeile zusammengefasst wurden.
            prefix_label = QLabel(prefix)
            prefix_label.setMinimumWidth(28)
            layout.addWidget(prefix_label)
        for i in range(count):
            cell = no_own_background(QWidget())
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(4)
            icon = QLabel()
            number = QLabel(str(i + 1))
            current_icon = QLabel()
            value = QLabel("-- mA")
            cell_layout.addWidget(icon)
            cell_layout.addWidget(number)
            cell_layout.addSpacing(4)
            cell_layout.addWidget(current_icon)
            cell_layout.addWidget(value)
            layout.addWidget(cell)
            self._icons.append(icon)
            self._numbers.append(number)
            self._current_icons.append(current_icon)
            self._value_labels.append(value)
        layout.addStretch()
        self.retranslate()
        self.set_states([False] * count)
        self.apply_palette(current_palette())

    def set_states(self, enabled: list[bool]) -> None:
        palette = current_palette()
        for icon, on in zip(self._icons, enabled):
            icon.setPixmap(_dot_pixmap(on, palette))

    def apply_palette(self, palette: Palette) -> None:
        pixmap = qta.icon(CURRENT_ICON_NAME, color=palette.text_muted).pixmap(
            CURRENT_ICON_SIZE, CURRENT_ICON_SIZE
        )
        for current_icon in self._current_icons:
            current_icon.setPixmap(pixmap)

    def retranslate(self) -> None:
        for i, (icon, number) in enumerate(zip(self._icons, self._numbers)):
            tooltip = tr("12V-Ausgang {index}", index=i + 1)
            icon.setToolTip(tooltip)
            number.setToolTip(tooltip)

    def set_values(self, values_mv: list[int]) -> None:
        # Zahl bleibt der rohe mV-Wert vom Geraet (siehe Klassendocstring) --
        # nur die Beschriftung sagt "mA".
        for label, value in zip(self._value_labels, values_mv):
            label.setText(f"{value} mA")

    def clear_values(self) -> None:
        for label in self._value_labels:
            label.setText("-- mA")


class MicroHilPanel(QGroupBox):
    def __init__(self, device_id: str, label: str) -> None:
        super().__init__()
        self._device_id = device_id
        self._online = True
        self._compact = False
        self._color_key: str | None = None
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

        # -- Normalansicht: vier Bereiche untereinander, mit Trennlinien --
        self._normal_widget = no_own_background(QWidget())
        normal_layout = QVBoxLayout(self._normal_widget)
        normal_layout.setContentsMargins(0, 0, 0, 0)

        self._in_array = _DotArray("IN", IN_COUNT, "Digitaleingang {index}")
        self._out_array = _DotArray("OUT", OUT_COUNT, "Digitalausgang {index}")
        self._ain_grid = _ValueGrid([f"AIN{i}" for i in range(1, AIN_COUNT + 1)])
        self._aout_grid = _ValueGrid([f"AOUT{i}" for i in range(1, AOUT_COUNT + 1)])
        self._relay_array = _DotArray("", RELAY_COUNT, "Relais {index}")
        self._pwr12_row = _Pwr12Row(PWR12_COUNT)

        normal_layout.addWidget(self._section_title(SECTION_TITLES[0]))
        normal_layout.addWidget(self._in_array)
        normal_layout.addWidget(self._out_array)
        normal_layout.addWidget(self._new_divider())
        normal_layout.addWidget(self._section_title(SECTION_TITLES[1]))
        normal_layout.addWidget(self._ain_grid)
        normal_layout.addWidget(self._aout_grid)
        normal_layout.addWidget(self._new_divider())
        normal_layout.addWidget(self._section_title(SECTION_TITLES[2]))
        normal_layout.addWidget(self._relay_array)
        normal_layout.addWidget(self._new_divider())
        normal_layout.addWidget(self._section_title(SECTION_TITLES[3]))
        normal_layout.addWidget(self._pwr12_row)

        outer.addWidget(self._normal_widget)

        # -- Kompaktansicht: alle Bereiche nebeneinander in einer Zeile --
        # eigene Widget-Instanzen (siehe Modul-Docstring) statt der obigen,
        # gefuellt ueber dieselben update_*()-Aufrufe wie die Normalansicht.
        # Keine Bereichs-Ueberschriften mehr (spart die dafuer noetige
        # eigene Zeile) -- die Praefixe (IN/OUT/REL/12V) und Feldnamen
        # (AIN1:/AOUT1:) tragen die Bedeutung stattdessen direkt, Tooltips
        # bleiben zusaetzlich erreichbar.
        self._compact_widget = no_own_background(QWidget())
        compact_layout = QHBoxLayout(self._compact_widget)
        compact_layout.setContentsMargins(0, 0, 0, 0)
        compact_layout.setSpacing(14)

        # Digital IO: einzige Gruppe, die zweizeilig bleibt (IN + OUT
        # uebereinander) -- bestimmt damit die Hoehe der gesamten Zeile.
        digital_col = no_own_background(QWidget())
        digital_col_layout = QVBoxLayout(digital_col)
        digital_col_layout.setContentsMargins(0, 0, 0, 0)
        digital_col_layout.setSpacing(2)
        self._compact_in_array = _DotArray("IN", IN_COUNT, "Digitaleingang {index}")
        self._compact_out_array = _DotArray("OUT", OUT_COUNT, "Digitalausgang {index}")
        digital_col_layout.addWidget(self._compact_in_array)
        digital_col_layout.addWidget(self._compact_out_array)
        compact_layout.addWidget(digital_col)

        compact_layout.addWidget(self._new_divider(vertical=True))

        # Analog IO: AIN+AOUT in einem 3x2-Raster statt einer Zeile mit 6
        # Eintraegen -- spart Breite, ohne die Zeilenhoehe zu erhoehen (die
        # gibt ohnehin schon die zweizeilige Digital-IO-Gruppe vor).
        self._compact_analog_grid = _ValueGrid(
            [f"AIN{i}" for i in range(1, AIN_COUNT + 1)] + [f"AOUT{i}" for i in range(1, AOUT_COUNT + 1)],
            columns=3,
        )
        compact_layout.addWidget(self._compact_analog_grid)

        compact_layout.addWidget(self._new_divider(vertical=True))

        # Relais + 12V-OUT: zu einer Gruppe zusammengefasst UND als
        # 3x2-Raster statt einer Zeile (Absprache) -- aus demselben
        # Breitenspar-Grund wie Analog IO oben.
        self._compact_relay_pwr12 = _RelayPwr12Grid(RELAY_COUNT, PWR12_COUNT)
        compact_layout.addWidget(self._compact_relay_pwr12)

        compact_layout.addStretch()
        outer.addWidget(self._compact_widget)
        self._compact_widget.hide()

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

    def _new_divider(self, vertical: bool = False) -> _Divider:
        divider = _Divider(vertical=vertical)
        divider.apply_palette(current_palette())
        self._dividers.append(divider)
        return divider

    def _retranslate(self) -> None:
        for title, text in zip(self._section_titles, SECTION_TITLES):
            title.setText(tr(text))
        for array in (
            self._in_array, self._out_array, self._relay_array,
            self._compact_in_array, self._compact_out_array,
        ):
            array.retranslate()
        self._compact_relay_pwr12.retranslate_relays()
        self._pwr12_row.retranslate()
        self._compact_relay_pwr12.pwr12_row.retranslate()
        self._offline_icon.setToolTip(tr("Verbindung getrennt"))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._offline_icon.move(self.width() - OFFLINE_ICON_SIZE - OFFLINE_ICON_MARGIN, OFFLINE_ICON_MARGIN)

    def _on_theme_changed(self, palette: Palette) -> None:
        for title in self._section_titles:
            title.apply_palette(palette)
        for divider in self._dividers:
            divider.apply_palette(palette)
        self._pwr12_row.apply_palette(palette)
        self._compact_relay_pwr12.pwr12_row.apply_palette(palette)
        self._apply_style(palette)
        # Punkt-Pixmaps haengen an der Palette (check_pass/text_muted) --
        # mit den zuletzt bekannten Zustaenden neu zeichnen statt sie zu
        # verlieren.
        for in_array in (self._in_array, self._compact_in_array):
            in_array.set_states(self._last_in)
        for out_array in (self._out_array, self._compact_out_array):
            out_array.set_states(self._last_out)
        self._relay_array.set_states(self._last_relay)
        self._compact_relay_pwr12.set_relay_states(self._last_relay)
        for pwr12_row in (self._pwr12_row, self._compact_relay_pwr12.pwr12_row):
            pwr12_row.set_states(self._last_pwr12_enabled)

    def _apply_style(self, palette: Palette) -> None:
        # Gleiche Logik wie dashboard._DevicePanel._apply_style (Panel-
        # Farbauswahl, siehe panel_color.py) -- MUSS auch hier vorhanden
        # sein: DashboardWidget.set_panel_colors_enabled()/set_panel_color()
        # rufen panel.set_panel_color() unterschiedslos auf JEDEM Panel auf,
        # unabhaengig vom Geraetekind (kein "hil"-Sonderfall dort). Ohne
        # diese Methode wuerde das Aktivieren der Panel-Farben-Option
        # (Einstellungen-Tab) crashen, sobald ein microHIL-Panel existiert.
        if not self._online:
            self.setStyleSheet(
                f"QGroupBox {{ background-color: {OFFLINE_BACKGROUND}; "
                f"border: 1px solid {OFFLINE_BORDER}; border-radius: 6px; }}"
            )
            return
        border_rule = f"border: 1px solid {palette.text_muted}; border-radius: 6px;"
        if self._color_key is None:
            self.setStyleSheet(f"QGroupBox {{ {border_rule} }}")
            return
        hex_color = palette.panel_tints.get(self._color_key)
        bg_rule = f"background-color: {hex_color};" if hex_color else ""
        self.setStyleSheet(f"QGroupBox {{ {border_rule} {bg_rule} }}")

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_panel_color(self, color_key: str | None) -> None:
        self._color_key = color_key
        self._apply_style(current_palette())

    def set_online(self, online: bool) -> None:
        self._online = online
        if not online:
            self.clear_values()
        self._offline_icon.setVisible(not online)
        self.setVisible(True)
        self._apply_style(current_palette())

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self._normal_widget.setVisible(not compact)
        self._compact_widget.setVisible(compact)

    # -- Werte -----------------------------------------------------------------
    # Aktualisieren immer BEIDE Widget-Saetze (Normal- und Kompaktansicht)
    # gleichzeitig, unabhaengig davon, welche gerade sichtbar ist -- sonst
    # zeigt die Ansicht nach einem set_compact()-Umschalten kurzzeitig
    # veraltete Werte, bis der naechste Polling-Zyklus (device_worker.py)
    # durch ist.

    def update_inputs(self, states: list[bool]) -> None:
        self._last_in = list(states)
        self._in_array.set_states(states)
        self._compact_in_array.set_states(states)

    def update_outputs(self, states: list[bool]) -> None:
        self._last_out = list(states)
        self._out_array.set_states(states)
        self._compact_out_array.set_states(states)

    def update_relays(self, states: list[bool]) -> None:
        self._last_relay = list(states)
        self._relay_array.set_states(states)
        self._compact_relay_pwr12.set_relay_states(states)

    def update_analog_in(self, values_mv: list[int]) -> None:
        for i, value in enumerate(values_mv, start=1):
            self._ain_grid.set_value(f"AIN{i}", f"{value} mV")
            self._compact_analog_grid.set_value(f"AIN{i}", f"{value} mV")

    def update_analog_out(self, values_mv: list[int]) -> None:
        for i, value in enumerate(values_mv, start=1):
            self._aout_grid.set_value(f"AOUT{i}", f"{value} mV")
            self._compact_analog_grid.set_value(f"AOUT{i}", f"{value} mV")

    def update_pwr12(self, enabled: list[bool], current_sense_mv: list[int]) -> None:
        self._last_pwr12_enabled = list(enabled)
        self._pwr12_row.set_states(enabled)
        self._pwr12_row.set_values(current_sense_mv)
        self._compact_relay_pwr12.pwr12_row.set_states(enabled)
        self._compact_relay_pwr12.pwr12_row.set_values(current_sense_mv)

    def clear_values(self) -> None:
        self.update_inputs([False] * IN_COUNT)
        self.update_outputs([False] * OUT_COUNT)
        self.update_relays([False] * RELAY_COUNT)
        self._ain_grid.clear_values()
        self._aout_grid.clear_values()
        self._compact_analog_grid.clear_values()
        self._last_pwr12_enabled = [False] * PWR12_COUNT
        for pwr12_row in (self._pwr12_row, self._compact_relay_pwr12.pwr12_row):
            pwr12_row.set_states(self._last_pwr12_enabled)
            pwr12_row.clear_values()

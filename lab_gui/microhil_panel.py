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
hil_*-Signale/dashboard.update_hil_*()-Slots). AOUT1-2 sind ein Sonderfall:
es gibt kein `AOUT?`-Kommando zum Zuruecklesen (siehe microhil/driver.py),
device_worker._poll_hil() fragt sie deshalb nie ab. Ihr Wert kommt
stattdessen DIREKT vom Control-Tab (control_tab.HilControlGroup, siehe
set_analog_out_value() unten) -- ein GUI-Thread-zu-GUI-Thread-Signal am
Worker vorbei, sobald der Nutzer dort "Uebernehmen" klickt. Das ist der
zuletzt GESENDETE Sollwert, KEINE Hardware-Bestaetigung (die es ohne
`AOUT?` nicht geben kann) -- bleibt "--", bis der Control-Tab tatsaechlich
einmal einen Wert angewendet hat.

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
from microhil.driver import AIN_COUNT, AOUT_COUNT, IN_COUNT, OUT_COUNT, PWR12_COUNT, RELAY_COUNT, defects_for_device_id
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


def _dot_cell(number: int, label: str | None = None) -> tuple[QWidget, QLabel, QLabel]:
    """Ein einzelner Punkt+Nummer-Indikator als eigenstaendiges Widget --
    Baustein sowohl fuer _DotArray als auch _RelayPwr12Grid (siehe dort):
    gibt (Zelle, Icon-Label, Nummern-Label) zurueck, damit der Aufrufer
    Icon/Nummer selbst in seiner eigenen Icons-/Numbers-Liste fuer
    set_states()/retranslate() nachfuehren kann.

    `label` ueberschreibt den angezeigten Text (Default: die blosse Zahl) --
    z.B. "REL1" statt "1" in _RelayPwr12Grid, wo anders als bei _DotArray
    kein eigenes Praefix-Label vor den Zellen steht, das den Kanaltyp schon
    klarstellt.

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
    number_label = QLabel(label if label is not None else str(number))
    cell_layout.addWidget(icon)
    cell_layout.addWidget(number_label)
    return cell, icon, number_label


def _pwr12_cell(number: int, label: str | None = None) -> tuple[QWidget, QLabel, QLabel, QLabel, QLabel]:
    """Ein einzelner 12V-Ausgang-Indikator (Schaltzustand-Punkt+Nummer,
    per Strom-Icon abgesetzt von der Stromangabe) als eigenstaendiges Widget
    -- Baustein sowohl fuer _Pwr12Row (alle Kanaele in einer Zeile,
    Normalansicht) als auch _RelayPwr12Grid (je ein Kanal in einer eigenen
    Relais-Zeile, Kompaktansicht, siehe dort). Gibt (Zelle, Icon-Label,
    Nummern-Label, Strom-Icon-Label, Wert-Label) zurueck.

    `label` ueberschreibt den angezeigten Text (Default: die blosse Zahl) --
    siehe _dot_cell()-Docstring fuer die Begruendung (z.B. "OUT1" statt "1"
    in _RelayPwr12Grid)."""
    cell = no_own_background(QWidget())
    cell_layout = QHBoxLayout(cell)
    cell_layout.setContentsMargins(0, 0, 0, 0)
    cell_layout.setSpacing(4)
    icon = QLabel()
    number_label = QLabel(label if label is not None else str(number))
    current_icon = QLabel()
    value_label = QLabel("-- mA")
    cell_layout.addWidget(icon)
    cell_layout.addWidget(number_label)
    cell_layout.addSpacing(4)
    cell_layout.addWidget(current_icon)
    cell_layout.addWidget(value_label)
    return cell, icon, number_label, current_icon, value_label


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
    """Kompakte 2x3-Anordnung fuer Relais 1-4 + 12V-OUT 1-2 als EINE
    gemeinsame Gruppe (Absprache) -- Relais 1-2 + 12V-OUT 1 in Zeile 1,
    Relais 3-4 + 12V-OUT 2 in Zeile 2. Reine Kompaktansichts-Variante: die
    Normalansicht zeigt Relais/12V-OUT weiterhin als zwei eigene Bereiche
    (_relay_array/_pwr12_row), siehe MicroHilPanel.__init__.

    ZWEI EINFACHE ZEILEN (QHBoxLayout) statt eines echten QGridLayout mit
    spaltenuebergreifendem Widget: eine erste Fassung mit QGridLayout +
    _Pwr12Row per columnSpan() ueber 2 Spalten fuehrte zu unvorhersehbar
    breiten Spalten (Qt verteilt die vom spannenden Widget benoetigte
    Breite nicht gleichmaessig auf die ueberspannten Spalten, siehe
    Git-Historie) -- Relais 2/3 landeten dadurch mit riesigen Luecken
    dazwischen. Zwei unabhaengige Zeilen (wie _DotArray/_ValueGrid es
    bereits vormachen) sind dagegen von Natur aus vorhersehbar: jede
    Zeile bemisst sich nur an ihrem eigenen Inhalt.

    Feste "2 Relais + 1 12V-Kanal je Zeile"-Aufteilung fuer die konkrete
    microHIL-Hardware (4 Relais, 2 12V-Kanaele) statt einer allgemeinen
    Formel -- diese Zahlen aendern sich nicht, eine generische Berechnung
    waere hier nur unnoetige Indirektion (Layout-Wunsch: je 12V-Kanal in
    derselben Zeile wie sein "Partner"-Relaispaar, statt beide 12V-Kanaele
    zusammen in einer eigenen Zeile)."""

    RELAYS_PER_ROW = 2

    def __init__(self, relay_count: int, pwr12_count: int) -> None:
        super().__init__()
        self._relay_icons: list[QLabel] = []
        self._relay_numbers: list[QLabel] = []
        self._pwr12_icons: list[QLabel] = []
        self._pwr12_numbers: list[QLabel] = []
        self._pwr12_current_icons: list[QLabel] = []
        self._pwr12_value_labels: list[QLabel] = []
        self._defective_curr: set[int] = set()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        row_layouts: list[QHBoxLayout] = []
        for _ in range(2):
            row = no_own_background(QWidget())
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            row_layouts.append(row_layout)
            outer.addWidget(row)

        # Relais zuerst je Zeile einfuegen, danach den passenden 12V-Kanal
        # -- QHBoxLayout.addWidget() haengt hinten an, das ergibt direkt die
        # gewuenschte Reihenfolge "Rel, Rel, 12V" je Zeile. Beschriftung als
        # "REL{n}"/"OUT{n}" statt blosser Zahl (Layout-Wunsch): anders als
        # bei _DotArray (eigenes Praefix-Label "IN"/"OUT" vor den Zellen)
        # steht hier kein Bereichs-Praefix vor den einzelnen Zellen, eine
        # blosse Zahl waere sonst zwischen Relais- und 12V-Zellen mehrdeutig.
        for i in range(relay_count):
            cell, icon, number = _dot_cell(i + 1, label=f"REL{i + 1}")
            row_layouts[i // self.RELAYS_PER_ROW].addWidget(cell)
            self._relay_icons.append(icon)
            self._relay_numbers.append(number)

        for i in range(pwr12_count):
            cell, icon, number, current_icon, value = _pwr12_cell(i + 1, label=f"OUT{i + 1}")
            row_layouts[i].addWidget(cell)
            self._pwr12_icons.append(icon)
            self._pwr12_numbers.append(number)
            self._pwr12_current_icons.append(current_icon)
            self._pwr12_value_labels.append(value)

        for row_layout in row_layouts:
            row_layout.addStretch()

        self.apply_pwr12_palette(current_palette())

    def set_relay_states(self, states: list[bool]) -> None:
        palette = current_palette()
        for icon, on in zip(self._relay_icons, states):
            icon.setPixmap(_dot_pixmap(on, palette))

    def retranslate_relays(self) -> None:
        for i, (icon, number) in enumerate(zip(self._relay_icons, self._relay_numbers)):
            tooltip = tr("Relais {index}", index=i + 1)
            icon.setToolTip(tooltip)
            number.setToolTip(tooltip)

    def set_pwr12_states(self, enabled: list[bool]) -> None:
        palette = current_palette()
        for icon, on in zip(self._pwr12_icons, enabled):
            icon.setPixmap(_dot_pixmap(on, palette))

    def set_pwr12_values(self, values_ma: list[int]) -> None:
        # Siehe _Pwr12Row.set_values()-Kommentar: defekt markierte Kanaele
        # (set_defects()) werden hier bewusst nicht ueberschrieben.
        for i, (label, value) in enumerate(zip(self._pwr12_value_labels, values_ma), start=1):
            if i in self._defective_curr:
                continue
            label.setText(f"{value} mA")

    def clear_pwr12_values(self) -> None:
        for i, label in enumerate(self._pwr12_value_labels, start=1):
            if i in self._defective_curr:
                continue
            label.setText("-- mA")

    def set_defects(self, defects: frozenset[str]) -> None:
        """Siehe _Pwr12Row.set_defects() -- identisches Verhalten fuer die
        Kompaktansicht."""
        palette = current_palette()
        for i, label in enumerate(self._pwr12_value_labels, start=1):
            if f"curr:{i}" not in defects:
                continue
            self._defective_curr.add(i)
            label.setText(tr("n/v"))
            label.setStyleSheet(f"color: {palette.text_muted}; font-style: italic; background: transparent;")
            label.setToolTip(tr(
                "Bekannter Hardware-Defekt auf diesem Board (siehe docs/hardware-notes.md "
                "im microHIL-Repo) -- Messwert nicht verlaesslich."
            ))

    def apply_pwr12_palette(self, palette: Palette) -> None:
        pixmap = qta.icon(CURRENT_ICON_NAME, color=palette.text_muted).pixmap(
            CURRENT_ICON_SIZE, CURRENT_ICON_SIZE
        )
        for current_icon in self._pwr12_current_icons:
            current_icon.setPixmap(pixmap)

    def retranslate_pwr12(self) -> None:
        for i, (icon, number) in enumerate(zip(self._pwr12_icons, self._pwr12_numbers)):
            tooltip = tr("12V-Ausgang {index}", index=i + 1)
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

    driver.get_current_ma() liefert seit 2026-09-08 den kalibrierten,
    gegen Amperemeter verifizierten Laststrom in mA (siehe
    docs/calibration.md im microHIL-Repo) -- vorher lieferte das
    zugrundeliegende Kommando nur die rohe, unkalibrierte Sense-Spannung
    (weiterhin ueber get_current_sense_raw_mv() erreichbar), die
    Beschriftung hier zeigte aber schon vorher "mA" auf ausdruecklichen
    Wunsch."""

    def __init__(self, count: int, prefix: str = "") -> None:
        super().__init__()
        self._icons: list[QLabel] = []
        self._numbers: list[QLabel] = []
        self._current_icons: list[QLabel] = []
        self._value_labels: list[QLabel] = []
        self._defective_curr: set[int] = set()
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
            cell, icon, number, current_icon, value = _pwr12_cell(i + 1)
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

    def set_values(self, values_ma: list[int]) -> None:
        # Kanaele mit bekanntem Hardware-Defekt (siehe set_defects()) werden
        # NICHT ueberschrieben -- ihr Messwert ist nachweislich unzuverlaessig
        # (docs/hardware-notes.md im microHIL-Repo), ein Poll-Update wuerde
        # die "defekt"-Anzeige sonst bei jedem Zyklus wieder mit einer
        # scheinbar gueltigen Zahl ueberschreiben.
        for i, (label, value) in enumerate(zip(self._value_labels, values_ma), start=1):
            if i in self._defective_curr:
                continue
            label.setText(f"{value} mA")

    def clear_values(self) -> None:
        for i, label in enumerate(self._value_labels, start=1):
            if i in self._defective_curr:
                continue
            label.setText("-- mA")

    def set_defects(self, defects: frozenset[str]) -> None:
        """Markiert Kanaele mit bekanntem Hardware-Defekt (Tag "curr:<n>",
        siehe microhil/driver.py: KNOWN_HARDWARE_DEFECTS) dauerhaft als
        nicht verlaesslich, statt ihren (garantiert falschen) Messwert
        wie gewohnt anzuzeigen."""
        palette = current_palette()
        for i, label in enumerate(self._value_labels, start=1):
            if f"curr:{i}" not in defects:
                continue
            self._defective_curr.add(i)
            label.setText(tr("n/v"))
            label.setStyleSheet(f"color: {palette.text_muted}; font-style: italic; background: transparent;")
            label.setToolTip(tr(
                "Bekannter Hardware-Defekt auf diesem Board (siehe docs/hardware-notes.md "
                "im microHIL-Repo) -- Messwert nicht verlaesslich."
            ))


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
        self._section_titles: list[tuple[_SectionTitle, str]] = []
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
        # Jede Gruppe traegt (Layout-Wunsch) eine eigene Bereichsueberschrift
        # analog zur Normalansicht (siehe _compact_group) -- zusaetzlich zu,
        # nicht statt, der Praefixe (IN/OUT/REL/OUT) und Feldnamen (AIN1:/
        # AOUT1:), die die Bedeutung je Zelle weiterhin direkt tragen.
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
        compact_layout.addWidget(self._compact_group(SECTION_TITLES[0], digital_col))

        compact_layout.addWidget(self._new_divider(vertical=True))

        # Analog IO: AIN+AOUT in einem 3x2-Raster statt einer Zeile mit 6
        # Eintraegen -- spart Breite, ohne die Zeilenhoehe zu erhoehen (die
        # gibt ohnehin schon die zweizeilige Digital-IO-Gruppe vor).
        # Feste Reihenfolge (Layout-Wunsch) statt Formel aus AIN_COUNT/
        # AOUT_COUNT: AIN1/AIN2/AOUT1 in Zeile 1, AIN3/AIN4/AOUT2 in Zeile 2
        # -- setzt AIN_COUNT==4/AOUT_COUNT==2 voraus (aktuelle microHIL-
        # Hardware), analog zu _RelayPwr12Grid.RELAYS_PER_ROW.
        self._compact_analog_grid = _ValueGrid(
            ["AIN1", "AIN2", "AOUT1", "AIN3", "AIN4", "AOUT2"],
            columns=3,
        )
        compact_layout.addWidget(self._compact_group(SECTION_TITLES[1], self._compact_analog_grid))

        compact_layout.addWidget(self._new_divider(vertical=True))

        # Relais + 12V-OUT: zu einer Gruppe zusammengefasst UND als
        # 3x2-Raster statt einer Zeile (Absprache) -- aus demselben
        # Breitenspar-Grund wie Analog IO oben. Eine gemeinsame Ueberschrift
        # ("Relais / 12V-OUT") statt zwei eigenen (wie in der Normalansicht),
        # da beide Kanaltypen hier zeilenweise gemischt dargestellt werden.
        self._compact_relay_pwr12 = _RelayPwr12Grid(RELAY_COUNT, PWR12_COUNT)
        compact_layout.addWidget(
            self._compact_group("Relais / 12V-OUT", self._compact_relay_pwr12)
        )

        compact_layout.addStretch()
        outer.addWidget(self._compact_widget)
        self._compact_widget.hide()

        # Bekannte Hardware-Defekte fuer GENAU dieses Board (device_id
        # enthaelt seit device_worker._reconnect_hils bereits die USB-
        # Seriennummer, siehe microhil/driver.py: defects_for_device_id) --
        # betroffene CURR-Anzeigen in beiden Ansichten dauerhaft als nicht
        # verlaesslich markieren statt einen bekannt falschen Messwert zu
        # zeigen (siehe HilControlGroup in control_tab.py fuer das analoge
        # Deaktivieren des PWR12-Schalters selbst).
        self._hil_defects = defects_for_device_id(device_id)
        self._pwr12_row.set_defects(self._hil_defects)
        self._compact_relay_pwr12.set_defects(self._hil_defects)

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
        # Merkt sich (Titel, Rohtext) statt sich per zip() auf eine parallele
        # Textliste (SECTION_TITLES) zu verlassen -- die Kompaktansicht nutzt
        # inzwischen eigene, teils zusammengefasste Ueberschriften (z.B.
        # "Relais / 12V-OUT" fuer eine Gruppe, die in der Normalansicht zwei
        # eigene Ueberschriften hat), eine gemeinsame Positions-Zuordnung
        # ueber eine einzige Liste waere dafuer nicht mehr eindeutig.
        title = _SectionTitle(tr(text))
        title.apply_palette(current_palette())
        self._section_titles.append((title, text))
        return title

    def _compact_group(self, title_text: str, content: QWidget) -> QWidget:
        """Eine Spalte der Kompaktansicht (Digital IO/Analog IO/Relais+
        12V-OUT) als Bereichsueberschrift ueber dem jeweiligen Inhalt --
        Ueberschriften in der Kompaktansicht analog zur Normalansicht
        (Layout-Wunsch), anders als in der urspruenglichen Fassung (siehe
        Git-Historie), die bewusst ohne sie auskam."""
        group = no_own_background(QWidget())
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._section_title(title_text))
        layout.addWidget(content)
        return group

    def _new_divider(self, vertical: bool = False) -> _Divider:
        divider = _Divider(vertical=vertical)
        divider.apply_palette(current_palette())
        self._dividers.append(divider)
        return divider

    def _retranslate(self) -> None:
        for title, text in self._section_titles:
            title.setText(tr(text))
        for array in (
            self._in_array, self._out_array, self._relay_array,
            self._compact_in_array, self._compact_out_array,
        ):
            array.retranslate()
        self._compact_relay_pwr12.retranslate_relays()
        self._pwr12_row.retranslate()
        self._compact_relay_pwr12.retranslate_pwr12()
        self._offline_icon.setToolTip(tr("Verbindung getrennt"))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._offline_icon.move(self.width() - OFFLINE_ICON_SIZE - OFFLINE_ICON_MARGIN, OFFLINE_ICON_MARGIN)

    def _on_theme_changed(self, palette: Palette) -> None:
        for title, _ in self._section_titles:
            title.apply_palette(palette)
        for divider in self._dividers:
            divider.apply_palette(palette)
        self._pwr12_row.apply_palette(palette)
        self._compact_relay_pwr12.apply_pwr12_palette(palette)
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
        self._pwr12_row.set_states(self._last_pwr12_enabled)
        self._compact_relay_pwr12.set_pwr12_states(self._last_pwr12_enabled)
        # set_defects() faerbt die betroffenen Labels mit der zum
        # Konstruktionszeitpunkt aktuellen Palette (text_muted) -- erneut
        # aufrufen, damit die Farbe nach einem Theme-Wechsel nicht veraltet.
        self._pwr12_row.set_defects(self._hil_defects)
        self._compact_relay_pwr12.set_defects(self._hil_defects)

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

    def set_analog_out_value(self, channel: int, millivolts: int) -> None:
        """Zeigt den zuletzt im Control-Tab (control_tab.HilControlGroup)
        angewendeten AOUT-Sollwert eines EINZELNEN Kanals an -- anders als
        update_analog_out() (fuer eine spaetere echte Hardware-Rueckfrage
        gedacht, aktuell aber nie aufgerufen, siehe device_worker._poll_hil)
        kommt dieser Wert direkt vom Control-Tab-Signal, NICHT vom Geraet:
        er ist der unbestaetigte, zuletzt gesendete Sollwert, keine Messung.
        """
        self._aout_grid.set_value(f"AOUT{channel}", f"{millivolts} mV")
        self._compact_analog_grid.set_value(f"AOUT{channel}", f"{millivolts} mV")

    def update_pwr12(self, enabled: list[bool], current_ma: list[int]) -> None:
        self._last_pwr12_enabled = list(enabled)
        self._pwr12_row.set_states(enabled)
        self._pwr12_row.set_values(current_ma)
        self._compact_relay_pwr12.set_pwr12_states(enabled)
        self._compact_relay_pwr12.set_pwr12_values(current_ma)

    def clear_values(self) -> None:
        self.update_inputs([False] * IN_COUNT)
        self.update_outputs([False] * OUT_COUNT)
        self.update_relays([False] * RELAY_COUNT)
        self._ain_grid.clear_values()
        self._aout_grid.clear_values()
        self._compact_analog_grid.clear_values()
        self._last_pwr12_enabled = [False] * PWR12_COUNT
        self._pwr12_row.set_states(self._last_pwr12_enabled)
        self._pwr12_row.clear_values()
        self._compact_relay_pwr12.set_pwr12_states(self._last_pwr12_enabled)
        self._compact_relay_pwr12.clear_pwr12_values()

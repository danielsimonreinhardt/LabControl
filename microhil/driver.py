"""Treiber fuer den microHIL (STM32F446-basiertes Test-/HIL-Geraet).

Kommunikation ueber USB-CDC (virtueller COM-Port, Windows: COMx, Linux:
/dev/ttyACMx). Reines ASCII-Zeilenprotokoll: Anfragen werden mit "\\n"
abgeschlossen (laut Protokoll wird auch "\\r\\n" akzeptiert), Antworten
enden immer mit "\\r\\n" -- auch "OK" und "ERR ...". Die Baudrate wird vom
virtuellen COM-Port ignoriert, pyserial verlangt aber trotzdem einen Wert.
Kommandos sind case-sensitiv, nur Grossschreibung (siehe protocol.md).

Alle Werte sind Ganzzahlen in mV/mA bzw. Promille, nie Fliesskomma.

Quelle des Protokolls: microHIL-Repo, docs/protocol.md und docs/can-usb.md
(dort auch die Details zur USB-Composite-Struktur, siehe _interface_number()
unten). Dieser Treiber wurde NICHT gegen echte Hardware verifiziert (kein
Geraet verfuegbar) -- anders als hcs34xx/korad_kel102 ist hier nichts als
"gegen reale Hardware getestet" markiert.

Zweiter USB-COM-Port (CAN1/SLCAN) ist bewusst ausserhalb dieses Treibers,
siehe can_bus/driver.py fuer CAN-Interfaces allgemein.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import serial
from serial.tools import list_ports

USB_VID = 0x0483
USB_PID = 0x5740
DEFAULT_BAUDRATE = 115200

# microHIL meldet sich als USB-Composite-Device mit zwei CDC-ACM-Funktionen
# (gleiche VID:PID 0483:5740) -- dieses Protokoll liegt auf Interface 0,
# CAN1/SLCAN auf Interface 2 (siehe can-usb.md, "Zwei COM-Ports"). Der
# Composite-Builder vergibt beiden Funktionen denselben (leeren) Interface-
# String-Index, das pyserial-Feld ListPortInfo.interface (iInterface-String)
# taugt zur Unterscheidung also NICHT -- stattdessen wird die Interface-
# Nummer aus dem hwid-String extrahiert. can-usb.md nennt zwei Formen
# (Windows "...&MI_00"/"...&MI_02", Linux "-if00"/"-if02" bzw. LOCATION=),
# ABER auf realer Hardware/pyserial-Kombination beobachtet (dieses Projekt,
# Windows, zwei tatsaechlich angeschlossene microHIL-Ports):
#   Interface 0: hwid = "USB VID:PID=0483:5740 SER=..."           (GAR KEIN
#                MI_xx/LOCATION-Hinweis -- der erste Port bleibt unmarkiert)
#   Interface 2: hwid = "USB VID:PID=0483:5740 SER=... LOCATION=1-3.1.4.3:x.2"
#                (die Interface-Nummer steht ganz am Ende, ABER nicht direkt
#                nach einer Ziffernfolge wie erwartet -- ".x.2" statt ".0.2",
#                das "x" ist ein Platzhalter fuer die Konfigurationsnummer.
#                Ein Regex wie `LOCATION=[\d.\-]+:\d+\.(\d+)`, der Ziffern
#                direkt vor der Interface-Nummer verlangt, matcht das NICHT.)
# Deshalb hier bewusst robuster: die Interface-Nummer wird vom Ende des
# LOCATION-Strings her verankert (`[:.](\d+)$`) statt ein festes Format davor
# vorauszusetzen, und wenn gar keine Kennung vorhanden ist (wie bei Interface
# 0 oben), greift in discover() ein sortierbasierter Fallback (siehe dort).
_MI_RE = re.compile(r"MI_([0-9A-Fa-f]+)")
_LOCATION_TAIL_RE = re.compile(r"[:.](\d+)$")
_LOCATION_ATTR_RE = re.compile(r"LOCATION=(\S+)")

HIL_INTERFACE_NUMBER = 0
CAN_INTERFACE_NUMBER = 2

RELAY_COUNT = 4
OUT_COUNT = 8
IN_COUNT = 8
AOUT_COUNT = 2
# Rohbereich des DAC (0..3300mV), fuer AOUTRAW/set_analog_output_raw().
AOUT_RAW_MAX_MV = 3300
# Nominaler KALIBRIERTER AOUT-Bereich (siehe docs/calibration.md im
# microHIL-Repo) -- seit 2026-09-08 durchkalibriert, `AOUT` erwartet jetzt
# die gewuenschte physikalische Ausgangsspannung statt eines rohen
# DAC-Sollwerts. Rechnerisch aus Verstaerkung 3.7x auf den vollen
# 0..3300mV-DAC-Bereich (siehe calibration.md, "Schaltungs-Herleitung"),
# AENDERT SICH mit einer Neukalibrierung -- nur ein Richtwert fuer GUI-
# Eingabefelder, keine harte Spezifikation.
AOUT_MAX_MV = 12210
AIN_COUNT = 4
PWR12_COUNT = 2
CURR_COUNT = 2
PWM_COUNT = 4
PWM_MAX_PERMILLE = 1000

# PWR12FLT?-Bitmaske (siehe protocol.md, "Strombegrenzung PWR12-1/2").
PWR12_FAULT_OWN_LIMIT = 0b01     # ILIM-Grenzwert dieses Kanals ausgeloest
PWR12_FAULT_TOTAL_BUDGET = 0b10  # Gemeinsames 1500mA-Budget (Polyfuse F1) ausgeloest

# PWM1-4 (TIM3, PC6-PC9) und OUT1-4 (PA10/PA15/PC10/PC11) treiben laut
# Schaltplan dieselbe Endstufe und werden von der Firmware gegenseitig
# verriegelt (siehe protocol.md, "Verriegelung PWM1-4 / OUT1-4"): OUT<n> 1
# schaltet PWM-Kanal n zwangsweise auf 0, PWM <n> >0 schaltet OUT<n> zwangs-
# weise aus. Keine eigene Fehlermeldung -- wirkt still auf dem jeweils
# anderen Kanal. Betrifft nur die Kanaele 1-4 (OUT hat 8, PWM nur 4 Kanaele).
INTERLOCKED_CHANNELS = 4

# Bekannte Hardware-Defekte je physischem Board, identifiziert ueber dessen
# eindeutige Seriennummer (STM32-UID, seit dem *IDN?-SN=-Feld auch ueber das
# Kommandoprotokoll abfragbar -- siehe MicroHIL.identify()/get_serial() unten
# -- und identisch mit der USB-Seriennummer, aus der device_worker.py bereits
# die device_id "hil:<serial>" bildet). Quelle/Details je Eintrag: microHIL-
# Repo, docs/hardware-notes.md. Zweck: GUI (control_tab.HilControlGroup,
# microhil_panel.MicroHilPanel) kann bekannt kaputte Kanaele fuer GENAU dieses
# Exemplar deaktivieren/ausgrauen, statt sie als scheinbar funktionierend
# anzuzeigen -- ein anderes microHIL-Board (andere Seriennummer) ist davon
# nicht betroffen.
#
# Tags: "pwr12:<1-2>" (12V-Ausgang liefert keine/keine verlaessliche Spannung),
# "curr:<1-2>" (Stromsense liefert keine verlaesslichen Werte).
KNOWN_HARDWARE_DEFECTS: dict[str, frozenset[str]] = {
    # Board "2065386A5631" hatte 2026-09-06 bis 2026-09-08 einen Eintrag hier
    # (PWR12-1: defekte Q22+Q28; CURR1/CURR2: U18/U19 nicht bestueckt) --
    # alle drei Punkte sind repariert und real durchkalibriert (siehe
    # microHIL-Repo, docs/hardware-notes.md/calibration.md), Eintrag deshalb
    # entfernt. Neue Defekte hier nach demselben Muster eintragen, sobald sie
    # an einem konkreten Board gefunden UND per docs/hardware-notes.md
    # dokumentiert sind -- nicht spekulativ.
}


def defects_for_serial(serial: str | None) -> frozenset[str]:
    """Bekannte Hardware-Defekte fuer ein Board mit dieser Seriennummer.

    Leeres frozenset bei unbekannter/fehlender Seriennummer (z.B. simuliertes
    Geraet, oder eine Firmware ohne SN=-Feld in *IDN?) -- kein Fehler, einfach
    "keine bekannten Defekte fuer dieses Exemplar".
    """
    if not serial:
        return frozenset()
    return KNOWN_HARDWARE_DEFECTS.get(serial, frozenset())


def defects_for_device_id(device_id: str) -> frozenset[str]:
    """Wie defects_for_serial(), akzeptiert aber direkt die device_id
    ("hil:<serial>", siehe device_worker._reconnect_hils) statt der nackten
    Seriennummer -- Komfort fuer GUI-Code, der ohnehin nur die device_id
    kennt (z.B. HilControlGroup.__init__, MicroHilPanel.__init__)."""
    serial = device_id.split(":", 1)[1] if device_id.startswith("hil:") else device_id
    return defects_for_serial(serial)


class HilError(RuntimeError):
    """Fehler bei der Kommunikation mit dem microHIL.

    Deckt sowohl Verbindungsprobleme (Timeout, SerialException) als auch
    vom Geraet gemeldete `ERR <Grund>`-Antworten ab. Anders als bei
    hcs34xx (PowerSupplyValueError) gibt es hier bewusst KEINE eigene
    Exception-Unterklasse fuer Geraete-Fehler: alle drei moeglichen Gruende
    (ARGS, RANGE, UNKNOWN, siehe protocol.md) zeigen einen Programmier-
    fehler im Aufrufer bzw. Treiber an (falscher Kanalindex, fehlendes
    Argument, Tippfehler im Kommando) -- kein Geraetezustand, auf den ein
    Aufrufer sinnvoll unterschiedlich reagieren wuerde (im Gegensatz zu
    OVP/OCP bei hcs34xx, das ein echter, zur Laufzeit veraenderlicher
    Geraetezustand ist). Der Grund steht trotzdem in .reason zur
    Verfuegung, u.a. fuer Logging/Diagnose.
    """

    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.reason = reason  # "ARGS" | "RANGE" | "UNKNOWN" | None


@dataclass
class Pwr12Channel:
    enabled: bool
    current_ma: int  # kalibrierter Laststrom, siehe get_current_ma()
    fault: int = 0    # PWR12FLT?-Bitmaske, siehe PWR12_FAULT_*


def _interface_number(info) -> int | None:
    """Extrahiert die USB-Interface-Nummer aus einem ListPortInfo.

    Liefert None, wenn sie sich nicht bestimmen laesst -- auf Windows war das
    beim ersten Interface (0) im Test durchaus der Fall (siehe Kommentar bei
    den Regex-Definitionen oben); discover() hat dafuer einen Fallback.
    """
    hwid = getattr(info, "hwid", "") or ""

    match = _MI_RE.search(hwid)
    if match:
        return int(match.group(1), 16)

    device = getattr(info, "device", "") or ""
    match = re.search(r"-if([0-9A-Fa-f]+)", device) or re.search(r"-if([0-9A-Fa-f]+)", hwid)
    if match:
        return int(match.group(1), 16)

    # ListPortInfo.location ist nicht auf allen Plattformen/Versionen befuellt;
    # Fallback auf "LOCATION=..." innerhalb der hwid. Die Interface-Nummer
    # steht so oder so ganz am Ende nach dem letzten ":" oder "." -- deshalb
    # vom Stringende her verankert statt ein festes Format davor anzunehmen
    # (siehe Kommentar oben zum beobachteten "...:x.2"-Fall).
    location = getattr(info, "location", None) or ""
    if not location:
        match = _LOCATION_ATTR_RE.search(hwid)
        if match:
            location = match.group(1)
    match = _LOCATION_TAIL_RE.search(location)
    if match:
        return int(match.group(1))

    return None


def parse_idn_fields(idn: str) -> dict[str, str]:
    """Zerlegt eine `*IDN?`-Antwort wie "microHIL,fw=0.1.0,SN=2065386A5631"
    in ihre "key=value"-Felder (z.B. {"fw": "0.1.0", "SN": "2065386A5631"}).
    Felder ohne "=" (der fuehrende Geraetename "microHIL") werden ignoriert."""
    fields: dict[str, str] = {}
    for part in idn.split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key] = value
    return fields


def _device_sort_key(device: str):
    """Natuerliche Sortierung, damit COM9 vor COM10 kommt (rein lexikalisch
    waere es umgekehrt) -- relevant fuer den Fallback in discover()."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", device)]


class MicroHIL:
    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0):
        self._ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        self._ser.reset_input_buffer()

    # -- discovery -------------------------------------------------------

    @classmethod
    def discover_ports(cls) -> list:
        """Alle COM-Ports mit microHIL-VID/PID (0483:5740) -- HIL- UND
        CAN-Port, falls beide sichtbar. Volle ListPortInfo-Objekte
        (u.a. .hwid, .serial_number) fuer eigene Auswahllogik."""
        return [
            info for info in list_ports.comports()
            if info.vid == USB_VID and info.pid == USB_PID
        ]

    @classmethod
    def discover(cls) -> str | None:
        """Sucht den HIL-Protokoll-Port (Interface 0) unter allen Ports mit
        VID:PID 0483:5740. Gibt None zurueck, wenn keiner gefunden wird.

        microHIL meldet zwei COM-Ports mit identischer VID:PID (siehe
        Modul-Docstring) -- ein simpler VID/PID-Filter wie in
        hcs34xx.discover()/korad_kel102.discover() waere hier also
        mehrdeutig. Strategie:
          1. Nur ein passender Port sichtbar -> diesen nehmen. Deckt sowohl
             aeltere Single-Port-Firmware als auch den Fall ab, dass der
             CAN-Port (noch) nicht enumeriert ist -- die Interface-Nummer
             ist dann irrelevant, es gibt ja keine Verwechslungsgefahr.
          2. Mehrere passende Ports, Interface-Nummer bei mindestens einem
             bestimmbar -> den mit Interface 0 nehmen.
          3. Bleiben danach Ports ohne bestimmbare Interface-Nummer uebrig
             (auf Windows beobachtet: Interface 0 traegt teils gar keine
             MI_xx/LOCATION-Kennung, siehe _interface_number()) -> die
             unbestimmten Ports nach Portname sortieren und der Reihe nach
             auf die noch offenen Interface-Slots (0, dann 2) verteilen,
             ohne bereits eindeutig zugeordnete Ports zu ueberschreiben.
             Funktioniert, weil es nur zwei Interfaces gibt und Windows sie
             in der Regel in Interface-Reihenfolge als COM-Ports vergibt.
          4. Bleibt danach immer noch Mehrdeutigkeit (mehr als ein Port pro
             Interface-Nummer, oder mehr unbestimmte Ports als offene Slots)
             -> HilError statt zu raten, da ein falscher Griff den CAN-Port
             statt des HIL-Ports oeffnen wuerde (falsches Protokoll auf dem
             Draht, verwirrende Fehlermeldungen). Port dann explizit angeben
             (MicroHIL(port=...)).
        """
        candidates = cls.discover_ports()
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].device

        found: dict[int, str] = {}
        unresolved: list[str] = []
        for info in candidates:
            iface = _interface_number(info)
            if iface is None:
                unresolved.append(info.device)
            elif iface in found:
                raise HilError(
                    f"Mehrere Ports melden Interface {iface}: "
                    f"{found[iface]!r} und {info.device!r}. Bitte Port explizit "
                    "angeben (MicroHIL(port=...))."
                )
            else:
                found[iface] = info.device

        if unresolved:
            open_slots = [n for n in (HIL_INTERFACE_NUMBER, CAN_INTERFACE_NUMBER) if n not in found]
            if len(unresolved) > len(open_slots):
                raise HilError(
                    f"{len(candidates)} Ports mit VID:PID={USB_VID:04x}:{USB_PID:04x} "
                    f"gefunden, davon {len(unresolved)} ohne bestimmbare USB-Interface-"
                    "Nummer (weder MI_xx noch LOCATION=... im hwid) -- ohne das ist "
                    "nicht sicher unterscheidbar, welcher Port das HIL-Protokoll spricht "
                    "und welcher der CAN/SLCAN-Port ist. Bitte Port explizit angeben "
                    f"(MicroHIL(port=...)); Kandidaten: "
                    f"{[(info.device, info.hwid) for info in candidates]}."
                )
            for slot, device in zip(open_slots, sorted(unresolved, key=_device_sort_key)):
                found[slot] = device

        if HIL_INTERFACE_NUMBER not in found:
            raise HilError(
                f"Kein Port mit Interface {HIL_INTERFACE_NUMBER} (HIL-Protokoll) "
                f"unter den gefundenen microHIL-Ports: "
                f"{[(info.device, info.hwid) for info in candidates]}. "
                "Bitte Port explizit angeben (MicroHIL(port=...))."
            )
        return found[HIL_INTERFACE_NUMBER]

    @classmethod
    def open_first(cls, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 1.0) -> "MicroHIL":
        port = cls.discover()
        if port is None:
            raise HilError(
                f"Kein Geraet mit VID={USB_VID:04x} PID={USB_PID:04x} gefunden. "
                "Ist der microHIL per USB angeschlossen?"
            )
        return cls(port, baudrate=baudrate, timeout=timeout)

    def close(self) -> None:
        self._ser.close()

    def __enter__(self) -> "MicroHIL":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- low level ---------------------------------------------------------

    def _write(self, cmd: str) -> None:
        # pyserial wirft bei einem ungueltig gewordenen Port-Handle (z.B. nach
        # Windows-Standby/Wakeup: "ClearCommError failed" o.ae.) ein rohes
        # SerialException/OSError statt eines HilError -- ohne diesen Fang
        # wuerde device_worker.py (faengt nur HilError) das Geraet weiterhin
        # als verbunden fuehren, obwohl die Verbindung tot ist.
        try:
            self._ser.write((cmd + "\n").encode("ascii"))
        except (serial.SerialException, OSError) as exc:
            raise HilError(f"Schreibfehler auf Port {self._ser.port}: {exc}") from exc

    def _read_line(self) -> str:
        # readline() liest bis "\n" (Standard-EOL von pyserial) -- das
        # trailing "\r" der geraeteseitigen "\r\n"-Terminierung bleibt dabei
        # im Puffer und wird per strip() entfernt.
        try:
            raw = self._ser.readline()
        except (serial.SerialException, OSError) as exc:
            raise HilError(f"Lesefehler auf Port {self._ser.port}: {exc}") from exc
        if not raw:
            raise HilError(f"Keine Antwort vom Geraet (Timeout) auf Port {self._ser.port}")
        return raw.decode("ascii", errors="replace").strip()

    def _query(self, cmd: str) -> str:
        """Sendet cmd, liefert die rohe Antwortzeile (ohne \\r\\n).

        Wirft HilError mit gesetztem .reason, wenn das Geraet mit
        `ERR <Grund>` antwortet (ARGS/RANGE/UNKNOWN, siehe protocol.md).
        """
        self._write(cmd)
        line = self._read_line()
        if line.startswith("ERR"):
            parts = line.split(None, 1)
            reason = parts[1] if len(parts) > 1 else None
            raise HilError(f"{cmd!r} -> {line}", reason=reason)
        return line

    def _command(self, cmd: str) -> None:
        """Fuer Set-Befehle, die nur mit `OK` antworten."""
        line = self._query(cmd)
        if line != "OK":
            raise HilError(f"Unerwartete Antwort auf {cmd!r} (OK erwartet): {line!r}")

    @staticmethod
    def _check_channel(channel: int, count: int, name: str) -> None:
        # Client-seitig geprueft statt dem Geraet zu ueberlassen: ein
        # falscher Kanalindex ist ein Programmierfehler im Aufruf, kein
        # Geraetezustand -- ValueError statt der Umweg ueber `ERR RANGE`
        # vom Geraet (siehe HilError-Docstring). Ausserdem so schneller
        # erkennbar als ein Roundtrip zum Geraet.
        if not (1 <= channel <= count):
            raise ValueError(f"{name}-Kanal {channel} ausserhalb 1..{count}")

    # -- identification ------------------------------------------------------

    def identify(self) -> str:
        """z.B. "microHIL,fw=0.1.0,SN=2065386A5631"."""
        return self._query("*IDN?")

    def get_serial(self) -> str | None:
        """Eindeutige Board-ID aus dem SN=-Feld von *IDN? (STM32-UID-basiert,
        identisch mit der USB-Seriennummer). None bei einer Firmware ohne
        dieses Feld (aeltere fw=0.1.0-Builds vor dem SN=-Zusatz) statt eines
        Fehlers -- siehe KNOWN_HARDWARE_DEFECTS/defects_for_serial() oben,
        die einen fehlenden Wert ebenfalls tolerieren."""
        return parse_idn_fields(self.identify()).get("SN")

    def get_firmware_version(self) -> str | None:
        """Firmwareversion aus dem fw=-Feld von *IDN? (z.B. "0.1.0") -- fuer
        die "Geraete-Info"-Anzeige im Settings-Tab (device_worker.py:
        hil_info). None bei einer Antwort ohne dieses Feld statt eines
        Fehlers, analog zu get_serial()."""
        return parse_idn_fields(self.identify()).get("fw")

    # -- relays (1-4) --------------------------------------------------------

    def set_relay(self, channel: int, on: bool) -> None:
        self._check_channel(channel, RELAY_COUNT, "RELAY")
        self._command(f"RELAY {channel} {1 if on else 0}")

    def get_relay(self, channel: int) -> bool:
        self._check_channel(channel, RELAY_COUNT, "RELAY")
        return self._query(f"RELAY? {channel}").strip() != "0"

    # -- digital outputs (1-8) -----------------------------------------------

    def set_output(self, channel: int, on: bool) -> None:
        """Setzt Digitalausgang OUT<channel>.

        Fuer channel 1-4 gilt die Verriegelung mit PWM<channel> (siehe
        INTERLOCKED_CHANNELS/Modul-Docstring): `set_output(n, True)`
        deaktiviert PWM-Kanal n (n=1-4) zwangsweise und ohne Fehlermeldung,
        wenn dieser gerade aktiv war.
        """
        self._check_channel(channel, OUT_COUNT, "OUT")
        self._command(f"OUT {channel} {1 if on else 0}")

    def get_output(self, channel: int) -> bool:
        self._check_channel(channel, OUT_COUNT, "OUT")
        return self._query(f"OUT? {channel}").strip() != "0"

    # -- digital inputs (1-8, read-only) --------------------------------------

    def get_input(self, channel: int) -> bool:
        self._check_channel(channel, IN_COUNT, "IN")
        return self._query(f"IN? {channel}").strip() != "0"

    def get_inputs(self) -> list[bool]:
        """Alle 8 Digitaleingaenge auf einmal (`IN?` ohne Kanalnummer).

        Liefert eine Liste [IN1, IN2, ..., IN8] -- das Geraet antwortet mit
        einem Bitstring wie "10110001", IN1 zuerst (siehe protocol.md).
        """
        bits = self._query("IN?").strip()
        if len(bits) != IN_COUNT or any(c not in "01" for c in bits):
            raise HilError(f"Unerwartete Antwort auf 'IN?': {bits!r}")
        return [c == "1" for c in bits]

    # -- analog output / DAC (1-2) --------------------------------------------

    def set_analog_output(self, channel: int, millivolts: int) -> None:
        """Setzt AOUT<channel> auf die gewuenschte physikalische
        Ausgangsspannung in mV -- seit 2026-09-08 kalibriert (siehe
        docs/calibration.md im microHIL-Repo, `cal_aout`), vorher war dies
        der rohe DAC-Sollwert (0..3300mV). Fuer den rohen DAC-Sollwert
        direkt (Diagnose/Kalibrierprozedur) siehe set_analog_output_raw().

        Die Firmware klemmt einen ausserhalb liegenden Wert still auf den
        aktuell kalibrierten Ausgangsbereich, statt ihn abzulehnen -- `OK`
        bestaetigt nur einen gueltigen Kanalindex, NICHT den exakten
        uebernommenen Wert (siehe protocol.md, "Wertebereiche: Index vs.
        Nutzwert"). Ein `AOUT?` zum Zuruecklesen existiert nicht. Der
        Treiber klemmt den Wert deshalb bereits hier client-seitig auf
        AOUT_MAX_MV, damit der hier sichtbare Sollwert mit dem
        tatsaechlich angewendeten grob uebereinstimmt -- AOUT_MAX_MV ist
        aber nur ein Richtwert (aendert sich mit der Kalibrierung), die
        Firmware bleibt fuer die exakte Grenze massgeblich.
        """
        self._check_channel(channel, AOUT_COUNT, "AOUT")
        clamped = max(0, min(AOUT_MAX_MV, millivolts))
        self._command(f"AOUT {channel} {clamped}")

    def set_analog_output_raw(self, channel: int, millivolts: int) -> None:
        """Setzt AOUT<channel> direkt auf einen rohen DAC-Sollwert
        (0..3300mV), OHNE die `cal_aout`-Kalibrierung anzuwenden --
        `AOUTRAW`, siehe docs/calibration.md im microHIL-Repo. Fuer die
        Kalibrierprozedur selbst und Diagnosezwecke, im Normalbetrieb
        set_analog_output() (kalibrierte physikalische Spannung) nutzen.
        """
        self._check_channel(channel, AOUT_COUNT, "AOUT")
        clamped = max(0, min(AOUT_RAW_MAX_MV, millivolts))
        self._command(f"AOUTRAW {channel} {clamped}")

    # -- analog inputs (1-4, read-only) ---------------------------------------

    def get_analog_input(self, channel: int) -> int:
        """Liest AIN<channel> in mV -- kalibriert (siehe docs/calibration.md
        im microHIL-Repo, `cal_ain`) seit 2026-09-08.

        Loest im Geraet eine Single-Conversion-ADC-Messung aus und kann
        laut protocol.md bis zu ~10 ms dauern (HAL_ADC_PollForConversion-
        Timeout) -- deutlich langsamer als die sonst ueblichen GPIO-
        Register-Befehle. Bei taktratigem Polling mehrerer Analogkanaele
        entsprechend einplanen.
        """
        self._check_channel(channel, AIN_COUNT, "AIN")
        return int(self._query(f"AIN? {channel}").strip())

    def get_analog_input_raw(self, channel: int) -> int:
        """Liest den rohen ADC-Wert von AIN<channel> in mV (`raw*3300/4095`),
        OHNE die `cal_ain`-Kalibrierung -- `AINRAW?`, siehe
        docs/calibration.md im microHIL-Repo. Fuer die Kalibrierprozedur
        selbst und Diagnosezwecke, im Normalbetrieb get_analog_input()
        (kalibrierter Wert) nutzen. Gleiche ~10ms-ADC-Anmerkung wie bei
        get_analog_input()."""
        self._check_channel(channel, AIN_COUNT, "AIN")
        return int(self._query(f"AINRAW? {channel}").strip())

    # -- switchable 12V outputs with current sense (1-2) ----------------------

    def set_pwr12(self, channel: int, on: bool) -> None:
        self._check_channel(channel, PWR12_COUNT, "PWR12")
        self._command(f"PWR12 {channel} {1 if on else 0}")

    def get_pwr12(self, channel: int) -> bool:
        self._check_channel(channel, PWR12_COUNT, "PWR12")
        return self._query(f"PWR12? {channel}").strip() != "0"

    def get_current_ma(self, channel: int) -> int:
        """Liest den kalibrierten Laststrom von PWR12<channel> in mA
        (`CURR?`) -- seit 2026-09-08 real durchkalibriert und gegen
        Amperemeter verifiziert (siehe docs/calibration.md im
        microHIL-Repo, `cal_curr`; vorher lieferte dieses Kommando nur die
        rohe, unkalibrierte Sense-Spannung, siehe get_current_sense_raw_mv()
        fuer das weiterhin vorhandene rohe Gegenstueck).
        Dieselbe ~10ms-ADC-Anmerkung wie bei get_analog_input() gilt auch
        hier.
        """
        self._check_channel(channel, CURR_COUNT, "CURR")
        return int(self._query(f"CURR? {channel}").strip())

    def get_current_sense_raw_mv(self, channel: int) -> int:
        """Liest die rohe, unkalibrierte Sense-Spannung von PWR12<channel>
        in mV (`CURRRAW?`, siehe docs/calibration.md im microHIL-Repo) --
        OHNE die `cal_curr`-Kalibrierung. Fuer die Kalibrierprozedur selbst
        und Diagnosezwecke, im Normalbetrieb get_current_ma() (kalibrierter
        Strom) nutzen."""
        self._check_channel(channel, CURR_COUNT, "CURR")
        return int(self._query(f"CURRRAW? {channel}").strip())

    def get_pwr12_fault(self, channel: int) -> int:
        """Liest die PWR12FLT?-Bitmaske fuer PWR12<channel> (siehe
        PWR12_FAULT_OWN_LIMIT/PWR12_FAULT_TOTAL_BUDGET, protocol.md
        "Strombegrenzung PWR12-1/2"): 0 = kein Fault, sonst Grund, warum
        der Kanal trotz anstehender Schaltanforderung aus ist. Eine
        Verriegelung erlischt erst nach `PWR12 <n> 0` (eigenes Limit) bzw.
        wenn BEIDE Kanaele zurueckgenommen wurden (Gesamtbudget) --
        `set_pwr12(channel, True)` allein reicht danach nicht."""
        self._check_channel(channel, PWR12_COUNT, "PWR12FLT")
        return int(self._query(f"PWR12FLT? {channel}").strip())

    def get_pwr12_channel(self, channel: int) -> Pwr12Channel:
        """Komfort: Schaltzustand + kalibrierter Strom + Fault-Status von
        PWR12<channel> in einem Aufruf."""
        return Pwr12Channel(
            enabled=self.get_pwr12(channel),
            current_ma=self.get_current_ma(channel),
            fault=self.get_pwr12_fault(channel),
        )

    def set_current_limit(self, channel: int, milliamps: int) -> None:
        """Setzt die Ueberstrom-Abschaltschwelle fuer PWR12<channel>
        (`ILIM`, Default nach Reset: 1200mA) -- seit 2026-09-08
        firmwareseitig implementiert (`protocol.c`, `pwr12_guard_poll()`):
        wird der Grenzwert ununterbrochen laenger als 100ms ueberschritten,
        schaltet die Firmware den Kanal selbststaendig ab, unabhaengig von
        der zuletzt per set_pwr12() gesetzten Schaltanforderung (siehe
        get_pwr12_fault()). Ein simples set_pwr12(channel, True) hebt diese
        Verriegelung NICHT auf -- siehe get_pwr12_fault()-Docstring.
        Zusaetzlich gibt es ein gemeinsames 1500mA-Budget beider Kanaele
        (Polyfuse F1), unabhaengig von den Einzellimits.
        """
        self._check_channel(channel, PWR12_COUNT, "ILIM")
        self._command(f"ILIM {channel} {milliamps}")

    def get_current_limit(self, channel: int) -> int:
        """Liest die aktuell gesetzte Ueberstrom-Abschaltschwelle von
        PWR12<channel> in mA (`ILIM?`)."""
        self._check_channel(channel, PWR12_COUNT, "ILIM")
        return int(self._query(f"ILIM? {channel}").strip())

    # -- PWM (1-4) -------------------------------------------------------------

    def set_pwm(self, channel: int, permille: int) -> None:
        """Setzt PWM<channel> auf permille/1000 Duty-Cycle (0 = aus, 1000 = 100%).

        Verriegelung mit OUT<channel> (channel 1-4, siehe
        INTERLOCKED_CHANNELS/Modul-Docstring): ein `permille > 0` schaltet
        OUT<channel> zwangsweise und ohne Fehlermeldung ab, wenn dieser
        gerade aktiv war.

        Wie bei set_analog_output() klemmt die Firmware den Wert still auf
        0..1000, statt ihn abzulehnen (`OK` bestaetigt nur den Kanalindex).
        Der Treiber klemmt deshalb auch hier bereits client-seitig -- anders
        als bei AOUT existiert mit `PWM?`/get_pwm() aber zusaetzlich ein
        Weg, den tatsaechlich uebernommenen Wert vom Geraet zu bestaetigen.
        """
        self._check_channel(channel, PWM_COUNT, "PWM")
        clamped = max(0, min(PWM_MAX_PERMILLE, permille))
        self._command(f"PWM {channel} {clamped}")

    def get_pwm(self, channel: int) -> int:
        self._check_channel(channel, PWM_COUNT, "PWM")
        return int(self._query(f"PWM? {channel}").strip())


if __name__ == "__main__":
    with MicroHIL.open_first() as hil:
        print("Verbunden:", hil.identify())
        print("Relays:", [hil.get_relay(ch) for ch in range(1, RELAY_COUNT + 1)])
        print("Outputs:", [hil.get_output(ch) for ch in range(1, OUT_COUNT + 1)])
        print("Inputs:", hil.get_inputs())
        print("Analog in (mV):", [hil.get_analog_input(ch) for ch in range(1, AIN_COUNT + 1)])
        for ch in range(1, PWR12_COUNT + 1):
            print(f"PWR12 {ch}:", hil.get_pwr12_channel(ch))
        print("PWM (permille):", [hil.get_pwm(ch) for ch in range(1, PWM_COUNT + 1)])

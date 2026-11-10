"""Hintergrund-Worker fuer die serielle Kommunikation mit Last(en) und Netzteil(en).

Laeuft in einem eigenen QThread, damit blockierende Seriell-I/O (Timeouts
bei Verbindungsabbruch) die GUI nicht einfrieren laesst. Alle Zugriffe auf
die Geraete laufen ausschliesslich hier; die GUI kommuniziert nur ueber
Qt-Signale/Slots (automatisch thread-sicher als Queued Connections).

Seit der Netzwerk-Freigabe hat die App drei Threads statt zwei: GUI,
dieser Worker und der HTTP-Server (samt einem Thread je Verbindung, siehe
share_server.py). Der Server-Thread fasst weder Geraete noch Qt-Objekte an
-- er liest ausschliesslich einen Schnappschuss aus live_state.LiveState;
die vollstaendigen Regeln fuer diese Grenze stehen dort im Docstring.

Unterstuetzt mehrere gleichzeitig angeschlossene Geraete desselben Typs
(z.B. zwei baugleiche HCS-34xx-Netzteile). Jede Instanz bekommt eine
Device-ID (siehe _resolve_device_ids), unter der sie in allen Signalen/
Slots referenziert wird.
"""
from __future__ import annotations

import logging
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from korad_kel102.driver import KoradKEL102, LoadError
from korad_kel102.mock import MockKoradKEL102
from jds66xx.driver import (
    FunctionGeneratorError,
    FunctionGeneratorValueError,
    JDS66xx,
    WAVE_DC,
    WAVE_PULSE,
    WAVE_SINE,
    WAVE_SQUARE,
    WAVE_TRIANGLE,
)
from jds66xx.mock import MockJDS66xx
from hcs34xx.driver import MIN_VOLTAGE as PSU_MIN_VOLTAGE, HCS34xx, PowerSupplyError, PowerSupplyValueError
from hcs34xx.mock import MockHCS34xx
from can_bus.driver import CanBus, CanError, CanConnectionError, DEFAULT_BITRATE as CAN_DEFAULT_BITRATE
from can_bus.mock import MockCanBus
from can_bus.dbc import DbcError, decode_frame, load_dbc
from can_bus.node import CanNode, CanScheduler
from battsim.driver import (
    CELL_COUNT as BATTSIM_CELL_COUNT,
    DEFAULT_VOLTAGE_LIMIT_V as BATTSIM_DEFAULT_LIMIT_V,
    KIND as BATTSIM_KIND,
    BattSim,
    BattSimError,
    BattSimValueError,
)
from battsim.mock import SimulatedBattSim
from microhil.driver import (
    AIN_COUNT as HIL_AIN_COUNT,
    AOUT_COUNT as HIL_AOUT_COUNT,
    HilError,
    IN_COUNT as HIL_IN_COUNT,
    MicroHIL,
    OUT_COUNT as HIL_OUT_COUNT,
    PWR12_COUNT as HIL_PWR12_COUNT,
    RELAY_COUNT as HIL_RELAY_COUNT,
)
from microhil.mock import MockMicroHIL
from picoscope2000.common import VOLTAGE_RANGE_CODES as PICO_VOLTAGE_RANGE_CODES
from picoscope2000.driver import PicoScope2000, PicoScope2000Error, usb_present as picoscope_usb_present
from picoscope2000.guard import GUARD as PICOSCOPE_GUARD, OWNER_SERVICE, OWNER_WORKER
from picoscope2000.mock import MockPicoScope2000
from unit_mso1000hd import guard as scpi_guard
from unit_mso1000hd.driver import (
    KIND as SCPISCOPE_KIND,
    SIM_DEVICE_ID as SIM_SCPISCOPE_ID,
    SIM_RESOURCE as SCPI_SIM_RESOURCE,
    MSO1000HDError,
    device_id_for as scpi_device_id_for,
    normalize_resource as scpi_normalize_resource,
    probe as scpi_probe,
    register_resource as scpi_register_resource,
)

logger = logging.getLogger(__name__)

# Meldungen, die sich bei jedem Verbindungsversuch wiederholen (fremder CH340-Port, von einer
# anderen App gehaltener Port), hoechstens alle 10 min je Port. Vorher bestand das Log zu ~80 %
# aus einer Zeile alle 33 s (gemessen 30.10.-10.11.2026); auf dem Raspberry Pi sind das
# unnoetige Schreibvorgaenge (SD-Karte bzw. Netzlaufwerk).
LOG_REPEAT_INTERVAL_S = 600.0
_last_logged: dict[str, float] = {}


def _log_repeating(key: str, level: int, msg: str, *args) -> None:
    now = time.monotonic()
    last = _last_logged.get(key)
    if last is not None and now - last < LOG_REPEAT_INTERVAL_S:
        return
    _last_logged[key] = now
    logger.log(level, msg, *args)

# War 500ms (2Hz) -- sichtbar grob fuer die Verlaufs-Diagramme, deren
# Repaint-Rate (siehe timeline_tab.REPAINT_INTERVAL_MS) inzwischen auf ~30Hz
# angehoben wurde: schnelleres Neuzeichnen allein bringt nichts, wenn die
# zugrundeliegenden Messwerte weiterhin nur alle 500ms neu eintreffen.
# 100ms (10Hz) ist ein klarer Sprung (5x), bleibt aber bewusst konservativ
# statt die Repaint-Rate voll zu erreichen: pro Zyklus werden ALLE Geraete
# sequentiell (nicht parallel) abgefragt, eine Last macht dabei bereits 5
# Kommandos hintereinander (measure_voltage/current/power + get_input +
# get_function, siehe _poll unten). Das HCS-34xx haengt an einem CP210x-USB-UART-Wandler (9600
# Baud) -- solche VCP-Treiber haben unter Windows oft einen Default-
# Latenz-Timer von ~16ms PRO Read-Aufruf, und die Geraete-Firmware selbst ist
# nicht als schnelles Interface dokumentiert. Eine deutlich hoehere Rate
# (z.B. die vollen 30Hz) waere ungetestetes Neuland und riskiert, dass ein
# Geraet Kommandos nicht mehr rechtzeitig verarbeitet (Timeouts, die faelsch-
# lich als Verbindungsabbruch gewertet werden). Nach einem Wechsel hier: an
# echter Hardware auf haeufigere "getrennt"-Log-Eintraege pruefen und im
# Zweifel wieder erhoehen.
POLL_INTERVAL_MS = 100
RECONNECT_INTERVAL_MS = 3000

# Netzteile (HCS-34xx) werden seltener abgefragt als der gemeinsame Takt: jeder Befehl kostet am
# Geraet ~16 ms, zwei Netzteile haetten sonst einen grossen Teil des 100-ms-Takts des gemeinsamen
# Worker-Threads belegt. Alle PSU_OUTPUT_POLL_EVERY Abfragen kommt der Ausgangszustand (GOUT) dazu:
# so folgt die Kachel auch einem von aussen geschalteten Ausgang.
PSU_POLL_INTERVAL_S = 0.2
PSU_OUTPUT_POLL_EVERY = 5

# Grund von all_outputs_off beim Beenden der App (siehe MainWindow.closeEvent): Netzteile werden dann
# nicht ausgeschaltet, sondern zur Handbedienung freigegeben (siehe DeviceWorker._release_psu).
REASON_WINDOW_CLOSE = "window close"

# Eigenes, deutlich langsameres Poll-Intervall fuer den microHIL statt ihn in
# denselben POLL_INTERVAL_MS-Zyklus wie Last/Netzteil/CAN zu haengen: ein
# voller microHIL-Zyklus braucht ca. 21 Kommandos (IN? + 8x OUT? + 4x RELAY?
# + 4x AIN? + 2x PWR12? + 2x CURR?), davon 6 mit bis zu ~10ms ADC-Latenz
# (siehe microhil/driver.py) -- macht bis zu ~135ms allein fuer den microHIL,
# klar mehr als die 100ms, die POLL_INTERVAL_MS den anderen Geraeten fuer
# ihre gesamte sequentielle Abfrage einraeumt (siehe Kommentar dort). Eine
# Dashboard-Anzeige aus LED-Punkten braucht ausserdem keine 10Hz-Aktualisierung
# -- 1x/Sekunde reicht fuers Auge, verhindert aber, dass ein zusaetzliches,
# eigentlich fuer Last/Netzteil-Reaktionsfaehigkeit ausgelegtes Geraet den
# gemeinsamen Poll-Zyklus aller anderen Geraete verlangsamt.
HIL_POLL_INTERVAL_MS = 1000

# Eigenes, sehr viel langsameres Reconnect-Intervall fuer das PicoScope statt
# des gemeinsamen RECONNECT_INTERVAL_MS (3s): anders als bei Last/Netzteil/CAN
# (reines Portoeffnen + kurze Abfrage, siehe RECONNECT_INTERVAL_MS-Kommentar)
# braucht ein einzelner PicoScope-Reconnect-Versuch (open+close) ca. 4,5s
# real gemessen (siehe picoscope2000/README.md) -- BLOCKIEREND, da alles im
# selben DeviceWorker-Thread laeuft wie POLL_INTERVAL_MS/HIL_POLL_INTERVAL_MS.
# Bei 3s wuerde der naechste Tick praktisch immer mitten in den vorherigen
# hineinlaufen: der Worker-Thread waere de facto dauerhaft mit dem PicoScope
# beschaeftigt, PSU/Last/CAN/HIL wuerden regelmaessig einfrieren (an echter
# Hardware reproduziert). 30s haelt das Dashboard trotzdem "praktisch live"
# fuer eine reine Statusanzeige, senkt die Blockierfrequenz aber auf ein
# vertretbares Mass. Siehe auch set_test_running(): waehrend eines
# Testlaufs pausiert dieser Timer zusaetzlich komplett (siehe
# _reconnect_picoscope), sonst kollidiert er mit PICO_*-Testschritten um das
# exklusive Handle (an echter Hardware reproduziert: ein Testschritt scheitert
# dann faelschlich mit "belegt", obwohl gar keine externe App im Weg ist).
PICOSCOPE_RECONNECT_INTERVAL_MS = 30000

# SCPI-Oszilloskope ueber LAN (UNI-T MSO1000HD, seit 0.21.0): Erkennung =
# kurz oeffnen, *IDN?, schliessen -- in einem eigenen Thread (siehe
# _scpi_executor), weil eine nicht erreichbare Adresse bis zum Timeout
# blockiert und sonst den Poll-Zyklus von Netzteil/Last einfrieren wuerde.
SCPISCOPE_PROBE_INTERVAL_MS = 10000
SCPISCOPE_PROBE_TIMEOUT_S = 2.0

# Eigenes Poll-Intervall fuer den Funktionsgenerator: ein voller Zustand sind
# 13 Registerabfragen (Ausgaenge, je Kanal Form/Frequenz/Amplitude/Offset/
# Tastverhaeltnis, Phase). Der Generator hat keine Messwerte, die sich von
# selbst aendern -- nur ein Handeingriff am Geraet ist zu erkennen, dafuer
# reicht ein langsamer Takt und er nimmt dem gemeinsamen Poll-Zyklus von
# Last/Netzteil keine Zeit weg. Nach jedem eigenen Schreibbefehl wird der
# Zustand ohnehin sofort neu gelesen (siehe _guard_fg/_emit_fg_state).
FG_POLL_INTERVAL_MS = 2000

# Ein CH340-Port (siehe jds66xx.driver.USB_VID) kann ein FREMDES Geraet tragen.
# Ein Port, der den Handshake nicht besteht, wird deshalb nicht alle
# RECONNECT_INTERVAL_MS erneut angesprochen (jedes Mal landet eine unverstandene
# Zeile beim fremden Geraet), sondern erst nach dieser Frist wieder. Ein
# ausgeschalteter Generator hat den Port trotzdem schon -- die Frist bestimmt,
# wie lange es nach dem Einschalten bis zur Erkennung dauern kann.
FG_REPROBE_INTERVAL_S = 30.0

# Mindestabstand (s) zwischen einem ps2000_close_unit() und dem naechsten
# ps2000_open_unit() -- an echter Hardware beobachtet: ein Reopen direkt
# (~0s) nach dem Schliessen schlaegt mit Status 0 fehl (nicht von "belegt
# durch andere App" unterscheidbar, siehe picoscope2000/README.md), ab ca.
# 0,5-1s Abstand klappt es zuverlaessig. Doppelte Sicherheitsmarge, da nicht
# hardware-/firmwareuebergreifend verifiziert. Betrifft nur
# _execute_picoscope_action (das eigentliche Oeffnen fuer einen Testschritt);
# _reconnect_picoscope() selbst braucht keine Wartezeit gegen sich selbst, da
# zwischen zwei Ticks ohnehin PICOSCOPE_RECONNECT_INTERVAL_MS liegt.
PICOSCOPE_SETTLE_S = 2.0

# Zeitbudget (s), das pro Poll-Zyklus UND Interface fuers Leeren der CAN-
# Empfangs-Queue zur Verfuegung steht (siehe _poll) -- verhindert, dass ein
# Interface mit sehr hoher Buslast die anderen Geraete im selben Zyklus
# verhungern laesst. Bei Ueberschreiten bleiben weitere Frames einfach bis
# zum naechsten Zyklus in der Queue (python-can puffert selbst). Frueher eine
# feste Frameanzahl (32): war ein Zyklus einmal laenger als POLL_INTERVAL_MS,
# holte der Poll danach je Lauf nur 32 Frames ab und der Rueckstand wuchs
# (BUGS_OFFEN #38). Ein Zeitbudget skaliert mit der tatsaechlichen Rechenzeit.
CAN_DRAIN_BUDGET_S = 0.05

# Dauer (s), ab der ein einzelner _poll()-Lauf als zu langsam geloggt wird
# (mit der Dauer je Geraet), hoechstens alle POLL_SLOW_LOG_EVERY_S Sekunden.
# Alle Geraete teilen sich den Worker-Thread: ein einzelnes langsames Geraet
# verzoegert alle Kacheln (BUGS_OFFEN #38).
POLL_SLOW_S = 0.5
POLL_SLOW_LOG_EVERY_S = 10.0

# Feste Device-IDs fuer die simulierten Geraete (siehe set_simulation_mode) --
# im Gegensatz zu echten Geraeten gibt es hier keine USB-Seriennummer/COM-Port,
# aus der sich eine ID ableiten liesse.
SIM_PSU_ID = "psu:SIM"
SIM_LOAD_ID = "load:SIM"
SIM_CAN_ID = "can:mock:SIM"
SIM_HIL_ID = "hil:SIM"
SIM_PICOSCOPE_ID = "picoscope:SIM"
SIM_FG_ID = "fg:SIM"

# Geraete, die UEBER ein CAN-Interface sprechen (Spalte "Geraet am Bus" in der
# CAN-Tabelle der Einstellungen, settings.py::can_configs Schluessel "device").
# (Code, Anzeigename) -- "" = kein Geraet, nur Rohdaten.
CAN_NODE_DEVICES = (("", "—"), (BATTSIM_KIND, "Batterie-Simulator"))


def battsim_device_id(can_id: str) -> str:
    """Device-ID eines Batterie-Simulators aus der ID seines CAN-Interfaces:
    "can:vector:75816:0" -> "battsim:vector:75816:0". Ein Simulator je Bus
    (feste CAN-IDs), die Interface-ID ist also eindeutig und stabil."""
    return f"{BATTSIM_KIND}:{can_id.split(':', 1)[1]}"


SIM_BATTSIM_ID = battsim_device_id(SIM_CAN_ID)

# FG_*-Aktionscode (testcase_model.FG_ACTIONS) -> Wellenformcode.
FG_WAVE_ACTIONS = {
    "FG_WAVE_SINE": WAVE_SINE,
    "FG_WAVE_SQUARE": WAVE_SQUARE,
    "FG_WAVE_PULSE": WAVE_PULSE,
    "FG_WAVE_TRIANGLE": WAVE_TRIANGLE,
    "FG_WAVE_DC": WAVE_DC,
}

# Die ps2000-API kennt (anders als bei Last/Netzteil/microHIL) keine
# Geraete-Enumeration/Seriennummer-basierte Unterscheidung mehrerer
# angeschlossener Einheiten (siehe picoscope2000/README.md) -- deshalb hier
# eine feste ID statt einer aus USB-Seriennummer/COM-Port abgeleiteten wie
# bei _resolve_device_ids. Nur EIN echtes PicoScope gleichzeitig unterstuetzt.
PICOSCOPE_ID = "picoscope:default"

# PICO_*-Aktionscode (testcase_model.PICO_ACTIONS) -> Feldname auf
# picoscope2000.common.Measurement (siehe _execute_picoscope_action).
PICO_ACTION_FIELDS = {
    "PICO_VMAX": "vmax", "PICO_VMIN": "vmin", "PICO_VPP": "vpp", "PICO_VRMS": "vrms",
}
# Kehrwert von picoscope2000.common.VOLTAGE_RANGE_CODES -- execute_action hat
# kein eigenes Range-Feld, der Zahlencode kommt ueber `value` an (siehe
# testcase_model.TestStep.value-Docstring).
PICO_RANGE_BY_CODE = {code: name for name, code in PICO_VOLTAGE_RANGE_CODES.items()}

# Sentinel-Fehlermeldung von _execute_picoscope_action: signalisiert
# testcase_runner.TestRunner, dass NICHT das Geraet/die Verbindung das
# Problem war, sondern eine erkannte Verschachtelung mit einem noch
# laufenden Reconnect-Tick (siehe _picoscope_busy-Docstring in __init__) --
# der Runner soll den Schritt automatisch neu versuchen statt den Testlauf
# mit einem irrefuehrenden "belegt" abzubrechen. Bewusst KEIN Warten in
# _execute_picoscope_action selbst: der aeussere (verschachtelnde) Aufruf
# kann strukturell nicht fertig werden, waehrend WIR (der verschachtelte
# Aufruf) blockieren -- das waere ein garantierter Deadlock/Timeout statt
# einer Loesung (an echter Hardware verifiziert: selbst 20s Wartezeit halfen
# nicht). Der Retry muss stattdessen als NEUER, nicht verschachtelter Aufruf
# erfolgen, erst nachdem der aeussere Aufruf laengst zurueckgekehrt ist --
# das uebernimmt testcase_runner.py mit einer verzoegerten erneuten
# execute_action-Emission.
PICOSCOPE_RETRY_MESSAGE = "picoscope_retry"


def can_device_id(cfg: dict) -> str:
    """Oeffentlich (kein fuehrender Unterstrich), weil main_window.py sie
    ebenfalls braucht: um beim Loeschen einer Zeile in der Einstellungen-
    CAN-Tabelle das zugehoerige device_id zu bestimmen und das Geraet
    komplett zu vergessen statt es (wie bei Last/Netzteil/HIL) nur
    ausgegraut als "getrennt" stehen zu lassen -- siehe
    main_window.MainWindow._on_can_configs_changed."""
    return f"can:{cfg['interface']}:{cfg['channel']}"


def _resolve_device_ids(kind: str, infos: list) -> dict[str, object]:
    """Bildet device_id -> ListPortInfo fuer aktuell erkannte Kandidaten.

    Nutzt die USB-Seriennummer als ID, sofern sie unter den aktuell
    sichtbaren Kandidaten eindeutig ist -- sie bleibt dann stabil ueber
    Neustarts und Portwechsel. Manche billigen USB-Seriell-Chips liefern
    aber keine oder fuer mehrere Einheiten identische Seriennummern; in dem
    Fall (oder wenn keine Seriennummer vorhanden ist) faellt die ID auf den
    COM-Port zurueck (funktional, aber weniger stabil).
    """
    serial_counts = Counter(info.serial_number for info in infos if info.serial_number)
    result: dict[str, object] = {}
    for info in infos:
        if info.serial_number and serial_counts[info.serial_number] == 1:
            device_id = f"{kind}:{info.serial_number}"
        else:
            device_id = f"{kind}:{_port_fallback_id(info)}"
        result[device_id] = info
    return result


def _port_fallback_id(info) -> str:
    """Ersatz-ID ohne eindeutige Seriennummer. Windows: COM-Name (bleibt je
    Geraet und USB-Buchse gleich, z.B. "psu:COM6"). Linux (Raspberry Pi,
    10.11.2026): /dev/ttyUSBn haengt von der Steckreihenfolge ab und wechselt
    nach Neustart oder Umstecken -- stabil ist der USB-Pfad (pyserial
    `location`, z.B. "1-1.2:1.0" -> "psu:usb-1-1.2:1.0"), solange das Geraet
    an derselben Buchse bleibt."""
    location = getattr(info, "location", None)
    if sys.platform != "win32" and location:
        return f"usb-{location}"
    return info.device


class DeviceWorker(QObject):
    device_added = Signal(str, str)          # kind ("load"/"psu"), device_id -- (wieder) verbunden
    device_removed = Signal(str, str)        # kind, device_id -- Verbindung verloren
    load_connected = Signal(str, bool)       # device_id, online
    psu_connected = Signal(str, bool)        # device_id, online
    load_measurement = Signal(str, float, float, float)  # device_id, voltage, current, power
    psu_measurement = Signal(str, float, float, bool)     # device_id, voltage, current, constant_current
    load_input_state = Signal(str, bool)     # device_id, Eingang ein/aus (Hardware-Rueckfrage)
    load_function_state = Signal(str, str)   # device_id, aktiver SCPI-Funktionscode (siehe korad_kel102.driver.FUNCTIONS)
    # device_id, Ausgang ein/aus. Anders als load_input_state KEINE echte
    # Hardware-Rueckfrage (GOUT) bei jedem PSU_OUTPUT_POLL_EVERY-ten Poll, beim
    # (Wieder-)Verbinden, nach dem Rueckwechsel aus der Handbedienung und wenn der Worker den
    # Ausgang SELBST (ausserhalb eines direkten GUI-Klicks in PsuControlGroup) schaltet:
    # all_outputs_off (Alle-Aus-Button, Safety-Watchdog-Trip) und die Freigabe zur
    # Handbedienung -- sonst bliebe der EIN/AUS-Schalter im Control-Tab auf dem alten Stand.
    psu_output_state = Signal(str, bool)
    # device_id, Sollspannung (V), Sollstrom (A) laut Geraet: beim Verbinden und nach der
    # Handbedienung, damit die Felder der Kachel nicht veraltete Werte zeigen.
    psu_setpoints = Signal(str, float, float)
    # device_id, Handbedienung an/aus (siehe set_psu_manual)
    psu_manual_state = Signal(str, bool)
    psu_limits = Signal(str, float, float)   # device_id, OVP (V), OCP (A) -- siehe _emit_psu_limits
    # device_id, maximale Ausgangsspannung (V), maximaler Ausgangsstrom (A) --
    # die vom Geraet selbst gemeldeten Nennwerte (GMAX), siehe _emit_psu_ratings.
    # Ohne sie rechnen GUI und Fernsteuerung mit festen 60 V / 10 A -- falsch fuer
    # jedes Netzteil, das davon abweicht (z.B. 16 V / 30 A).
    psu_ratings = Signal(str, float, float)
    # fuer Testablauf-Schritte: success, error, gelesener Wert (nur bei einer
    # microHIL-Lese-Aktion befuellt, siehe HIL_READ_ACTIONS/_dispatch_action;
    # 0.0 bei allen anderen Aktionen ohne Bedeutung).
    action_completed = Signal(bool, str, float)
    # Ergebnis einer Aktion aus der Netzwerk-Fernsteuerung (siehe
    # execute_remote_action): request_id, success, error, gelesener Wert.
    # Bewusst NICHT action_completed -- das gehoert dem TestRunner.
    remote_action_completed = Signal(int, bool, str, float)
    all_off_finished = Signal(str)           # Semikolon-Liste fehlgeschlagener Geraete, "" = alles ok

    can_connected = Signal(str, bool)        # device_id, online
    # device_id, Fehlertext -- ein konfiguriertes CAN-Interface konnte (noch)
    # nicht verbunden werden bzw. wurde waehrend des Betriebs getrennt (siehe
    # _reconnect_can()/_poll()). Vorher landete das AUSSCHLIESSLICH im
    # Log (logger.warning), die Kachel/Sektion blieb ohne jede sichtbare
    # Erklaerung einfach weg -- fuer den Nutzer ununterscheidbar von "Config
    # nie angekommen"/"Bug in der Anbindung" (Nutzerfeedback, siehe
    # settings_tab.SettingsTab.on_can_connect_error).
    can_connect_error = Signal(str, str)     # device_id, Fehlertext
    # device_id, arbitration_id, data (Hex-String z.B. "01 A2 FF"), extended, timestamp (s)
    can_frame_received = Signal(str, int, str, bool, float)
    can_stats = Signal(str, int, int)        # device_id, tx_count, rx_count -- fuers Dashboard
    # device_id, arbitration_id, can_bus.dbc.DecodedFrame -- NUR emittiert,
    # wenn fuer dieses Interface eine DBC-Datei hinterlegt ist (siehe
    # settings.py::can_configs, Schluessel "dbc_path") UND diese eine
    # Message-Definition fuer die arbitration_id enthaelt (siehe
    # can_bus/dbc.py::decode_frame). can_frame_received (Rohdaten) wird
    # davon UNABHAENGIG immer weiter emittiert -- Rohdaten bleiben wichtig
    # fuers Debugging, siehe FEATURES.md Punkt 3. Bewusst ein eigenes
    # Signal statt eines zusaetzlichen Parameters an can_frame_received:
    # ein spaeterer weiterer Abnehmer (z.B. testcase_runner fuer Solange/
    # Wenn-Bedingungen, siehe can_bus/dbc.py-Docstring) kann sich hier
    # unabhaengig von der GUI-Rohdaten-Anzeige einklinken.
    can_signals_decoded = Signal(str, int, object)

    hil_connected = Signal(str, bool)          # device_id, online
    # device_id, Firmwareversion aus *IDN? (microhil.driver.MicroHIL.
    # get_firmware_version(), z.B. "0.1.0") -- fuer die "Geraete-Info"-
    # Sektion im Settings-Tab (settings_tab.SettingsTab.set_hil_firmware_version).
    hil_info = Signal(str, str)
    hil_digital_state = Signal(str, list, list)  # device_id, inputs (IN1-8), outputs (OUT1-8)
    hil_relay_state = Signal(str, list)        # device_id, relays (RELAY1-4)
    hil_analog_input = Signal(str, list)       # device_id, AIN1-4 in mV
    # device_id, PWR12-Enable (1-2), Stromsense (1-2) -- siehe microhil_panel.
    # _Pwr12Row: rohe mV vom Geraet, im Dashboard bewusst als "mA" beschriftet.
    hil_pwr12_state = Signal(str, list, list)

    # device_id, online -- rein physische USB-Praesenz (siehe
    # picoscope2000.driver.usb_present), UNABHAENGIG davon, ob das Geraet
    # gerade von LabControl selbst angesprochen werden kann (siehe
    # picoscope_state: "busy" vs "free").
    picoscope_connected = Signal(str, bool)
    # device_id, status ("free" = LabControl konnte oeffnen, "busy" = Geraet
    # angeschlossen aber nicht oeffenbar, vermutlich PicoScope-7-App offen),
    # variant, serial -- variant/serial bleiben im "busy"-Fall auf dem
    # zuletzt bekannten Stand stehen (siehe _reconnect_picoscope).
    picoscope_state = Signal(str, str, str, str)

    # SCPI-Oszilloskop (Geraeteart "scpiscope"): device_id, online -- erreichbar
    # unter der eingetragenen Adresse und meldet sich mit *IDN? als UNI-T.
    scpiscope_connected = Signal(str, bool)
    # device_id, status ("free"), Modell, Seriennummer -- gleiche Form wie
    # picoscope_state, damit Kachel, LiveState und ScopeService beide bedienen.
    scpiscope_state = Signal(str, str, str, str)
    # intern: Ergebnis einer Erkennung aus dem Probe-Thread zurueck in den
    # Worker-Thread (device_id, erreichbar, Modell, Seriennummer).
    _scpi_probe_done = Signal(str, bool, str, str)
    # Intern: Ergebnis eines Verbindungsversuchs aus dem Probe-Thread (siehe
    # _reconnect_executor): kind ("load"/"psu"/"fg"), device_id, verbundenes
    # Geraeteobjekt oder None, Port (nur bei kind "fg_rejected": Port, der den
    # Handshake nicht bestand).
    _reconnect_done = Signal(str, str, object, str)

    fg_connected = Signal(str, bool)         # device_id, online
    # device_id, Zustand als dict: {"outputs": [bool, bool], "channels":
    # [{"waveform", "frequency", "amplitude", "offset", "duty"} x2], "phase"}.
    # Ein dict statt vieler Einzelsignale, weil der Generator kein schnell
    # veraenderlicher Messwert ist, sondern ein Satz Einstellungen, der nur
    # als Ganzes gelesen wird (siehe FG_POLL_INTERVAL_MS).
    fg_state = Signal(str, object)

    # Batterie-Simulator (Geraeteart "battsim", CAN-Knoten an einem
    # konfigurierten Interface, seit 0.22.0): device_id, online -- online heisst,
    # das Geraet meldet sich mit 0x120 (nicht nur: das Interface ist offen).
    battsim_connected = Signal(str, bool)
    # device_id, Zustand als dict (siehe _battsim_state_dict), alle POLL_INTERVAL_MS.
    battsim_state = Signal(str, object)

    def __init__(self, simulation_mode: bool = False, can_configs: list[dict] | None = None,
                 scpi_scopes: list[str] | None = None) -> None:
        super().__init__()
        # SCPI-Oszilloskope: device_id -> VISA-Ressource aus den Einstellungen
        # (settings.scpi_scopes), Erreichbarkeit und Geraeteinfo.
        self._scpi_resources: dict[str, str] = {}
        self._scpi_present: dict[str, bool] = {}
        self._scpi_info: dict[str, tuple[str, str]] = {}
        self._scpi_pending: set[str] = set()
        self._scpi_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scpi-probe")
        # Verbindungsversuche zu seriellen Geraeten (Netzteil/Last/Generator) laufen
        # NICHT im Worker-Thread: ein Port, der in Windows gelistet ist, hinter dem
        # aber ein ausgeschaltetes Geraet haengt, blockiert bis zum Lese-Timeout
        # (1 s je Port) -- alle 3 s, und in der Zeit stand der Poll aller Geraete
        # still (BUGS_OFFEN #38).
        self._reconnect_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="reconnect-probe")
        self._reconnect_pending: set[str] = set()
        # Netzteil-Abfrage (siehe PSU_POLL_INTERVAL_S): naechster Zeitpunkt, Zaehler und zuletzt
        # gemeldeter Ausgangszustand je Geraet.
        self._psu_next_poll: dict[str, float] = {}
        self._psu_poll_count: dict[str, int] = {}
        self._psu_output_seen: dict[str, bool] = {}
        # Netzteile in Handbedienung: LabControl spricht sie nicht an (siehe set_psu_manual).
        self._psu_manual: set[str] = set()
        self._last_slow_poll_log = 0.0
        self._mock_scpiscope_active = False
        self._set_scpi_addresses(scpi_scopes or [])
        self._fgs: dict[str, JDS66xx] = {}
        # Port -> monotonic()-Zeitpunkt, vor dem er nicht erneut geprobt wird
        # (siehe FG_REPROBE_INTERVAL_S).
        self._fg_probe_after: dict[str, float] = {}
        self._loads: dict[str, KoradKEL102] = {}
        self._psus: dict[str, HCS34xx] = {}
        self._can_buses: dict[str, CanBus] = {}
        self._can_stats: dict[str, list[int]] = {}  # device_id -> [tx_count, rx_count]
        self._can_configs: list[dict] = list(can_configs or [])
        # device_id -> geladene cantools-Datenbank, nur fuer Interfaces mit
        # hinterlegter "dbc_path" (siehe _reload_can_dbcs). Getrennt von
        # _can_buses gehalten: die DBC-Datei soll unabhaengig vom aktuellen
        # Verbindungsstatus geladen bleiben (z.B. bereits vor dem ersten
        # erfolgreichen Connect).
        self._can_dbcs: dict[str, object] = {}
        # device_id -> zuletzt geladener dbc_path, verhindert unnoetiges
        # Neuparsen bei jedem _reconnect_can()-Tick, wenn sich nur ein
        # anderes Feld (z.B. Bezeichnung) geaendert hat.
        self._can_dbc_paths: dict[str, str] = {}
        # CAN-Knoten (siehe can_bus/node.py): je Interface ein Sende-Thread,
        # angelegt beim ersten Knoten. Der Batterie-Simulator wird angehaengt,
        # sobald sein Interface offen ist, und gilt erst als verbunden, wenn er
        # antwortet (_battsim_online).
        self._can_schedulers: dict[str, CanScheduler] = {}
        self._battsims: dict[str, BattSim] = {}
        self._battsim_bus: dict[str, str] = {}      # battsim-ID -> CAN-Interface-ID
        self._battsim_online: dict[str, bool] = {}
        # Grenzwert je Simulator (Einstellungen, settings.battsim_voltage_limits).
        self._battsim_limits: dict[str, float] = {}
        # Nachgebildeter Simulator am simulierten Bus (Simulationsmodus).
        self._sim_battsim: SimulatedBattSim | None = None
        self._hils: dict[str, MicroHIL] = {}
        # Kein dict wie bei den anderen Geraeten: es wird nie ein offenes
        # Handle gehalten (siehe _reconnect_picoscope), daher reicht reiner
        # Zustand statt eines Treiber-Objekts.
        self._picoscope_present = False
        self._picoscope_variant = ""
        self._picoscope_serial = ""
        self._mock_picoscope_active = False
        # monotonic()-Zeitpunkt des letzten ps2000_close_unit() (egal ob aus
        # _reconnect_picoscope() oder _execute_picoscope_action()) -- siehe
        # PICOSCOPE_SETTLE_S.
        self._picoscope_last_close = 0.0
        # Reentranz-Schutz: an echter Hardware beobachtet, dass waehrend
        # ps2000_open_unit() (blockierend, ~3,5s) eine ZWEITE, verschachtelte
        # Ausfuehrung von execute_action auf demselben Worker-Thread moeglich
        # ist (vermutlich pumpt die Vendor-DLL waehrend des Wartens intern
        # Windows-Messages, wodurch Qts ueber PostMessage zugestellte Queued-
        # Connection-Events verschachtelt zum Zug kommen -- per Log bestaetigt:
        # eine execute_action-Ausfuehrung lief nachweislich VOR dem Rueckgabe-
        # Zeitpunkt des noch laufenden ps2000_open_unit()-Aufrufs der
        # Reconnect-Probe). set_test_running() allein reicht deshalb NICHT
        # (verhindert nur NEUE Reconnect-Ticks, nicht die Verschachtelung in
        # einem bereits laufenden). Ein simples bool reicht trotz Verschach-
        # telung: das GIL serialisiert Python-Bytecode weiterhin, ein
        # verschachtelter Aufruf sieht das vom AEUSSEREN Aufruf gesetzte Flag
        # zuverlaessig.
        self._picoscope_busy = False
        # Waehrend eines Testablaufs offen gehaltene Verbindung(en) -- device_id
        # -> PicoScope2000/MockPicoScope2000-Instanz (siehe open_picoscope_
        # session/close_picoscope_sessions). Anders als bei _reconnect_
        # picoscope()/dem Fallback in _execute_picoscope_action wird hier
        # bewusst NICHT nach jeder Aktion getrennt: ein Testablauf mit
        # mehreren PICO_*-Schritten wuerde sonst bei JEDEM Schritt erneut die
        # vollen ~4,5s Verbinden/Trennen zahlen (siehe picoscope2000/README.md)
        # -- einmalig beim Laufstart oeffnen und erst am Laufende schliessen
        # spart das. Waehrenddessen pausiert _reconnect_picoscope() ohnehin
        # (siehe _test_running), das exklusive Handle bleibt also fuer die
        # Laufdauer bei uns.
        self._picoscope_sessions: dict[str, object] = {}
        # True waehrend eines laufenden Testablaufs (siehe set_test_running) --
        # pausiert _reconnect_picoscope() komplett, damit dessen periodisches
        # Verbinden nicht mit einer PICO_*-Testablauf-Aktion um das exklusive
        # ps2000-Handle konkurriert (an echter Hardware reproduziert, siehe
        # PICOSCOPE_RECONNECT_INTERVAL_MS-Kommentar).
        self._test_running = False
        self._simulation_mode = simulation_mode
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._try_reconnect)
        # Eigener, langsamerer Timer statt im selben Zyklus wie _poll() --
        # siehe HIL_POLL_INTERVAL_MS.
        self._hil_poll_timer = QTimer(self)
        self._hil_poll_timer.timeout.connect(self._poll_hils)
        # Eigener, NOCH langsamerer Timer als der gemeinsame _reconnect_timer
        # -- siehe PICOSCOPE_RECONNECT_INTERVAL_MS.
        self._picoscope_reconnect_timer = QTimer(self)
        self._picoscope_reconnect_timer.timeout.connect(self._reconnect_picoscope)
        self._fg_poll_timer = QTimer(self)
        self._fg_poll_timer.timeout.connect(self._poll_fgs)
        self._scpi_probe_timer = QTimer(self)
        self._scpi_probe_timer.timeout.connect(self._probe_scpiscopes)
        self._scpi_probe_done.connect(self._on_scpi_probe_done)
        self._reconnect_done.connect(self._on_reconnect_done)

    @Slot()
    def start(self) -> None:
        if self._simulation_mode:
            self._add_mock_psu()
            self._add_mock_load()
            self._add_mock_can()
            self._add_mock_hil()
            self._add_mock_picoscope()
            self._add_mock_fg()
            self._add_mock_scpiscope()
        self._try_reconnect()
        self._reconnect_picoscope()
        self._probe_scpiscopes()
        self._scpi_probe_timer.start(SCPISCOPE_PROBE_INTERVAL_MS)
        self._poll_timer.start(POLL_INTERVAL_MS)
        self._reconnect_timer.start(RECONNECT_INTERVAL_MS)
        self._hil_poll_timer.start(HIL_POLL_INTERVAL_MS)
        self._picoscope_reconnect_timer.start(PICOSCOPE_RECONNECT_INTERVAL_MS)
        self._fg_poll_timer.start(FG_POLL_INTERVAL_MS)

    @Slot()
    def stop_timers(self) -> None:
        """Haelt alle Timer des Workers in SEINEM Thread an (MainWindow.closeEvent,
        vor QThread.quit()). Sonst raeumt Python den Worker erst beim Prozessende
        im Haupt-Thread ab, und Qt meldet unter Linux "Timers cannot be stopped
        from another thread" (in WSL gesehen, 10.11.2026)."""
        for timer in self.findChildren(QTimer):
            timer.stop()

    @Slot(bool)
    def set_test_running(self, running: bool) -> None:
        self._test_running = running

    @Slot(str)
    def open_picoscope_session(self, device_id: str) -> None:
        """Oeffnet das PicoScope einmalig fuer die Dauer eines Testablaufs
        (siehe _picoscope_sessions-Docstring in __init__) -- vom
        Aufrufer (main_window._on_run_requested) nur emittiert, wenn die
        Schritte des Laufs tatsaechlich eine PICO_*-Aktion mit diesem
        device_id enthalten.

        Best-Effort: schlaegt das Oeffnen fehl (Geraet nicht da, belegt durch
        PicoScope 7, oder gerade eine verschachtelte Reconnect-Probe aktiv,
        siehe _picoscope_busy), bleibt einfach keine Session fuer diese
        device_id bestehen -- _execute_picoscope_action faellt dann pro
        Aktion auf den alten Oeffnen+Schliessen-Pfad zurueck (inkl. dessen
        eigenem Retry-Mechanismus, siehe PICOSCOPE_RETRY_MESSAGE), es gibt
        also keinen Grund, hier selbst zu warten/erneut zu versuchen."""
        if device_id in self._picoscope_sessions:
            return
        if device_id == SIM_PICOSCOPE_ID:
            if self._mock_picoscope_active:
                scope = MockPicoScope2000.open_first()
                self._picoscope_sessions[device_id] = scope
                info = scope.get_info()
                self.picoscope_state.emit(device_id, "test", info.variant, info.serial)
            return
        if not self._picoscope_present or self._picoscope_busy:
            return
        # Handle-Waechter (picoscope2000/guard.py): haelt gerade der ScopeService
        # das Geraet, gibt es keine Session -- main_window._on_run_requested
        # laesst ihn vorher freigeben (ScopeService.begin_test), das ist hier
        # also nur der Rest-Fall. Der Waechter bleibt bis close_picoscope_sessions
        # belegt.
        if not PICOSCOPE_GUARD.try_acquire(OWNER_WORKER):
            return
        PICOSCOPE_GUARD.settle()
        elapsed = time.monotonic() - self._picoscope_last_close
        if elapsed < PICOSCOPE_SETTLE_S:
            time.sleep(PICOSCOPE_SETTLE_S - elapsed)
        self._picoscope_busy = True
        try:
            scope = PicoScope2000.open_first()
        except PicoScope2000Error:
            PICOSCOPE_GUARD.release(closed=False)
            return
        finally:
            self._picoscope_busy = False
        self._picoscope_sessions[device_id] = scope
        self.picoscope_state.emit(device_id, "test", self._picoscope_variant, self._picoscope_serial)

    @Slot()
    def close_picoscope_sessions(self) -> None:
        """Schliesst alle per open_picoscope_session() offen gehaltenen
        Verbindungen -- am Laufende (fertig/gestoppt/fehlgeschlagen) immer
        aufgerufen, auch wenn nie eine Session geoeffnet wurde (dann No-Op)."""
        if not self._picoscope_sessions:
            return
        for device_id, scope in self._picoscope_sessions.items():
            try:
                scope.close()
            except Exception:  # noqa: BLE001 -- Aufraeumen darf nie fehlschlagen
                logger.exception("PicoScope-Session %s: Fehler beim Schliessen", device_id)
            if device_id != SIM_PICOSCOPE_ID:
                self._picoscope_last_close = time.monotonic()
                PICOSCOPE_GUARD.release()
        self._picoscope_sessions.clear()
        # Sofort einen frischen Status statt auf die naechste tatsaechliche
        # An-/Absteck-Erkennung zu warten (seit der Entfernung der
        # periodischen Probe aus _reconnect_picoscope() gibt es sonst gar
        # keinen automatischen Trigger mehr dafuer) -- _probe_picoscope()
        # direkt statt _reconnect_picoscope(), das nur noch bei einer
        # Praesenz-Transition probt. Nur wenn tatsaechlich ein reales Geraet
        # angeschlossen ist (reine Simulation ohne echtes PicoScope soll
        # nicht versuchen, eines zu oeffnen).
        if self._picoscope_present:
            self._probe_picoscope()

    # -- Simulationsmodus ----------------------------------------------------

    @Slot(bool)
    def set_simulation_mode(self, enabled: bool) -> None:
        if enabled == self._simulation_mode:
            return
        self._simulation_mode = enabled
        if enabled:
            self._add_mock_psu()
            self._add_mock_load()
            self._add_mock_can()
            self._add_mock_hil()
            self._add_mock_picoscope()
            self._add_mock_fg()
            self._add_mock_scpiscope()
        else:
            self._remove_mock_psu()
            self._remove_mock_load()
            self._close_can(SIM_CAN_ID)
            self._remove_mock_hil()
            self._remove_mock_picoscope()
            self._remove_mock_fg()
            self._remove_mock_scpiscope()

    # -- CAN-Interface-Konfiguration (siehe settings.py: can_configs) --------
    # Anders als Last/Netzteil kein Hotplug: welche Interfaces ueberhaupt
    # verbunden werden sollen, kommt ausschliesslich aus dieser Liste (siehe
    # can_bus/README.md: keine sichere Autodiscovery ohne bekannte Bitrate).

    @Slot(list)
    def set_can_configs(self, configs: list) -> None:
        self._can_configs = list(configs)
        wanted_ids = {can_device_id(cfg) for cfg in self._can_configs}
        for device_id in list(self._can_buses):
            if device_id != SIM_CAN_ID and device_id not in wanted_ids:
                self._close_can(device_id)
        self._reconnect_can()
        # Spalte "Geraet am Bus" kann sich bei offenem Interface geaendert haben.
        for device_id in list(self._can_buses):
            self._sync_can_nodes(device_id)

    def _add_mock_psu(self) -> None:
        if SIM_PSU_ID in self._psus:
            return
        self._psus[SIM_PSU_ID] = MockHCS34xx()
        self.device_added.emit("psu", SIM_PSU_ID)
        self.psu_connected.emit(SIM_PSU_ID, True)
        self._emit_psu_ratings(SIM_PSU_ID)
        self._emit_psu_limits(SIM_PSU_ID)

    def _remove_mock_psu(self) -> None:
        psu = self._psus.pop(SIM_PSU_ID, None)
        self._psu_manual.discard(SIM_PSU_ID)
        if psu is not None:
            psu.close()
            self.psu_connected.emit(SIM_PSU_ID, False)
            self.device_removed.emit("psu", SIM_PSU_ID)

    def _add_mock_load(self) -> None:
        if SIM_LOAD_ID in self._loads:
            return
        load = MockKoradKEL102()
        self._loads[SIM_LOAD_ID] = load
        self.device_added.emit("load", SIM_LOAD_ID)
        self.load_connected.emit(SIM_LOAD_ID, True)
        self.load_input_state.emit(SIM_LOAD_ID, False)
        self.load_function_state.emit(SIM_LOAD_ID, load.get_function())

    def _remove_mock_load(self) -> None:
        load = self._loads.pop(SIM_LOAD_ID, None)
        if load is not None:
            load.close()
            self.load_connected.emit(SIM_LOAD_ID, False)
            self.device_removed.emit("load", SIM_LOAD_ID)

    def _add_mock_scpiscope(self) -> None:
        if self._mock_scpiscope_active:
            return
        from unit_mso1000hd.mock import SIM_MODEL, SIM_SERIAL
        self._mock_scpiscope_active = True
        scpi_register_resource(SIM_SCPISCOPE_ID, SCPI_SIM_RESOURCE)
        self.device_added.emit(SCPISCOPE_KIND, SIM_SCPISCOPE_ID)
        self.scpiscope_connected.emit(SIM_SCPISCOPE_ID, True)
        self.scpiscope_state.emit(SIM_SCPISCOPE_ID, "free", SIM_MODEL, SIM_SERIAL)

    def _remove_mock_scpiscope(self) -> None:
        if not self._mock_scpiscope_active:
            return
        self._mock_scpiscope_active = False
        self.scpiscope_connected.emit(SIM_SCPISCOPE_ID, False)
        self.device_removed.emit(SCPISCOPE_KIND, SIM_SCPISCOPE_ID)

    # -- SCPI-Oszilloskope (settings.scpi_scopes) ---------------------------

    def _set_scpi_addresses(self, addresses: list[str]) -> dict[str, str]:
        resources: dict[str, str] = {}
        for address in addresses:
            try:
                resource = scpi_normalize_resource(str(address))
            except ValueError:
                continue
            if resource == SCPI_SIM_RESOURCE:
                continue   # die Simulation kommt ueber den Simulationsmodus
            device_id = scpi_device_id_for(resource)
            resources[device_id] = resource
            scpi_register_resource(device_id, resource)
        old = self._scpi_resources
        self._scpi_resources = resources
        return old

    @Slot(list)
    def set_scpi_scopes(self, addresses: list) -> None:
        """Adressliste aus den Einstellungen geaendert: entfernte Geraete
        abmelden, neue sofort pruefen."""
        old = self._set_scpi_addresses(list(addresses))
        for device_id in set(old) - set(self._scpi_resources):
            if self._scpi_present.pop(device_id, False):
                self.scpiscope_connected.emit(device_id, False)
                self.device_removed.emit(SCPISCOPE_KIND, device_id)
            self._scpi_info.pop(device_id, None)
        self._probe_scpiscopes()

    @Slot()
    def _probe_scpiscopes(self) -> None:
        for device_id, resource in self._scpi_resources.items():
            if device_id in self._scpi_pending:
                continue
            # Haelt der ScopeService die Verbindung, ist das Geraet erreichbar
            # -- nicht dazwischenfunken (siehe unit_mso1000hd/guard.py).
            if scpi_guard.guard_for(device_id).owner == scpi_guard.OWNER_SERVICE:
                continue
            self._scpi_pending.add(device_id)
            self._scpi_executor.submit(self._scpi_probe_job, device_id, resource)

    def _scpi_probe_job(self, device_id: str, resource: str) -> None:
        """Laeuft im Probe-Thread, meldet ueber _scpi_probe_done zurueck."""
        guard = scpi_guard.guard_for(device_id)
        if not guard.try_acquire(scpi_guard.OWNER_WORKER):
            # ScopeService hat gerade geoeffnet: erreichbar, Info unveraendert.
            model, serial = self._scpi_info.get(device_id, ("", ""))
            self._scpi_probe_done.emit(device_id, True, model, serial)
            return
        try:
            info = scpi_probe(resource, SCPISCOPE_PROBE_TIMEOUT_S)
            self._scpi_probe_done.emit(device_id, True, info.model, info.serial)
        except MSO1000HDError as exc:
            logger.debug("SCPI-Oszilloskop %s (%s) nicht erreichbar: %s", device_id, resource, exc)
            self._scpi_probe_done.emit(device_id, False, "", "")
        except Exception:  # noqa: BLE001 -- der Probe-Thread darf nie still sterben
            logger.exception("SCPI-Oszilloskop %s: Erkennung gescheitert", device_id)
            self._scpi_probe_done.emit(device_id, False, "", "")
        finally:
            guard.release()

    @Slot(str, bool, str, str)
    def _on_scpi_probe_done(self, device_id: str, ok: bool, model: str, serial: str) -> None:
        self._scpi_pending.discard(device_id)
        if device_id not in self._scpi_resources:
            return   # inzwischen aus den Einstellungen entfernt
        was_present = self._scpi_present.get(device_id, False)
        if ok:
            if not was_present:
                self._scpi_present[device_id] = True
                logger.info("SCPI-Oszilloskop %s erreichbar (%s %s)", device_id, model, serial)
                self.device_added.emit(SCPISCOPE_KIND, device_id)
                self.scpiscope_connected.emit(device_id, True)
            # Nur bei Erstkontakt oder geaenderter Info -- sonst wuerde ein
            # spaetes Ergebnis einen inzwischen "mcp"-Zustand ueberschreiben.
            if model and (not was_present or self._scpi_info.get(device_id) != (model, serial)):
                self._scpi_info[device_id] = (model, serial)
                self.scpiscope_state.emit(device_id, "free", model, serial)
        elif was_present:
            self._scpi_present[device_id] = False
            logger.info("SCPI-Oszilloskop %s nicht mehr erreichbar", device_id)
            self.scpiscope_connected.emit(device_id, False)
            self.device_removed.emit(SCPISCOPE_KIND, device_id)

    def _add_mock_fg(self) -> None:
        if SIM_FG_ID in self._fgs:
            return
        self._fgs[SIM_FG_ID] = MockJDS66xx()
        self.device_added.emit("fg", SIM_FG_ID)
        self.fg_connected.emit(SIM_FG_ID, True)
        self._emit_fg_state(SIM_FG_ID)

    def _remove_mock_fg(self) -> None:
        fg = self._fgs.pop(SIM_FG_ID, None)
        if fg is not None:
            fg.close()
            self.fg_connected.emit(SIM_FG_ID, False)
            self.device_removed.emit("fg", SIM_FG_ID)

    def _add_mock_can(self) -> None:
        if SIM_CAN_ID in self._can_buses:
            return
        bus = MockCanBus()
        # Am simulierten Bus haengt immer ein nachgebildeter Batterie-Simulator
        # (battsim/mock.py) -- der echte Treiber laeuft dagegen.
        self._sim_battsim = SimulatedBattSim(firmware="v2")   # neue Firmware (BattSimFW), mit 0x121
        bus.attach_peer(self._sim_battsim)
        self._can_buses[SIM_CAN_ID] = bus
        self._can_stats[SIM_CAN_ID] = [0, 0]
        self.device_added.emit("can", SIM_CAN_ID)
        self.can_connected.emit(SIM_CAN_ID, True)
        self._sync_can_nodes(SIM_CAN_ID)

    def _close_can(self, device_id: str) -> None:
        self._detach_can_nodes(device_id, send_shutdown=True)
        bus = self._can_buses.pop(device_id, None)
        self._can_stats.pop(device_id, None)
        if bus is not None:
            bus.close()
            self.can_connected.emit(device_id, False)
            self.device_removed.emit("can", device_id)
        if device_id == SIM_CAN_ID:
            self._sim_battsim = None

    # -- CAN-Knoten (Geraete, die ueber ein Interface sprechen) --------------

    def _wanted_can_device(self, can_id: str) -> str:
        """Geraeteart am Bus laut Einstellungen ("" = keins). Am simulierten
        Bus immer der Batterie-Simulator."""
        if can_id == SIM_CAN_ID:
            return BATTSIM_KIND
        for cfg in self._can_configs:
            if can_device_id(cfg) == can_id:
                return cfg.get("device", "") or ""
        return ""

    def _sync_can_nodes(self, can_id: str) -> None:
        """Haengt den laut Einstellungen gewuenschten Knoten an ein offenes
        Interface an bzw. entfernt einen nicht mehr gewuenschten."""
        if can_id not in self._can_buses:
            return
        battsim_id = battsim_device_id(can_id)
        wanted = self._wanted_can_device(can_id) == BATTSIM_KIND
        if wanted and battsim_id not in self._battsims:
            limit = self._battsim_limits.get(battsim_id, BATTSIM_DEFAULT_LIMIT_V)
            node = BattSim(voltage_limit_v=limit)
            self._battsims[battsim_id] = node
            self._battsim_bus[battsim_id] = can_id
            self._battsim_online[battsim_id] = False
            scheduler = self._can_schedulers.get(can_id)
            if scheduler is None:
                scheduler = CanScheduler(self._can_buses[can_id], name=can_id)
                self._can_schedulers[can_id] = scheduler
            scheduler.attach(node)
            logger.info("Batterie-Simulator %s an %s angehaengt", battsim_id, can_id)
        elif not wanted and battsim_id in self._battsims:
            self._drop_battsim(battsim_id, send_shutdown=True)

    def _drop_battsim(self, battsim_id: str, send_shutdown: bool) -> None:
        node = self._battsims.pop(battsim_id, None)
        can_id = self._battsim_bus.pop(battsim_id, "")
        if node is None:
            return
        scheduler = self._can_schedulers.get(can_id)
        if scheduler is not None:
            scheduler.detach(node, send_shutdown=send_shutdown)
            if not scheduler.nodes:
                scheduler.stop(send_shutdown=False)
                del self._can_schedulers[can_id]
        if self._battsim_online.pop(battsim_id, False):
            self.battsim_connected.emit(battsim_id, False)
            self.device_removed.emit(BATTSIM_KIND, battsim_id)

    def _detach_can_nodes(self, can_id: str, send_shutdown: bool) -> None:
        """Alle Knoten eines Interfaces abhaengen (Interface wird geschlossen
        oder ist ausgefallen). Abschiedsframes nur, wenn der Bus noch geht."""
        for battsim_id in [b for b, c in self._battsim_bus.items() if c == can_id]:
            self._drop_battsim(battsim_id, send_shutdown=send_shutdown)
        scheduler = self._can_schedulers.pop(can_id, None)
        if scheduler is not None:
            scheduler.stop(send_shutdown=send_shutdown)

    def _nodes_for(self, can_id: str) -> list[CanNode]:
        return [self._battsims[b] for b, c in self._battsim_bus.items() if c == can_id]

    def _update_battsims(self, can_id: str) -> None:
        """Nach dem Empfang eines Poll-Zyklus: Verbindungswechsel melden und den
        Zustand an Kachel/LiveState geben."""
        for battsim_id in [b for b, c in self._battsim_bus.items() if c == can_id]:
            node = self._battsims[battsim_id]
            online = node.online()
            if online != self._battsim_online.get(battsim_id, False):
                self._battsim_online[battsim_id] = online
                logger.info("Batterie-Simulator %s %s", battsim_id, "antwortet" if online else "antwortet nicht mehr")
                if online:
                    self.device_added.emit(BATTSIM_KIND, battsim_id)
                    self.battsim_connected.emit(battsim_id, True)
                else:
                    self.battsim_connected.emit(battsim_id, False)
                    self.device_removed.emit(BATTSIM_KIND, battsim_id)
            if online:
                self.battsim_state.emit(battsim_id, self._battsim_state_dict(node))

    @staticmethod
    def _battsim_state_dict(node: BattSim) -> dict:
        st = node.get_state()
        return {
            "status": st.status,
            "status_name": st.status_name,
            "failed": st.failed,
            "output_on": st.output_on,
            "remote": st.remote_requested,
            "output_requested": st.output_requested,
            "voltage_limit": st.voltage_limit_v,
            "stack_voltage": st.stack_voltage_v,
            "tx_error": st.last_tx_error,
            # Statusbotschaft 0x121 (nur neue Firmware, sonst None)
            "fail_reason": st.fail_reason,
            "fail_reason_name": st.fail_reason_name,
            "controller_on": st.controller_on,
            "overload": st.overload,
            "limit_active": st.limit_active,
            "supply_current": st.supply_current_a,
            "fw_version": st.fw_version,
            "cells": [
                {"index": c.index, "setpoint": c.setpoint_v, "enable": c.enable_requested,
                 "voltage": c.voltage_v, "enabled": c.enabled, "error": c.setpoint_error,
                 "status": c.status, "status_name": c.status_name, "below_floor": c.below_floor}
                for c in st.cells
            ],
        }

    @Slot()
    def shutdown_can_nodes(self) -> None:
        """Geordnetes Ende beim Schliessen der App: jeder Knoten sendet seine
        Abschiedsframes (Batterie-Simulator: Ausgang sperren, Fernsteuerung
        abgeben), dann enden die Sende-Threads."""
        for can_id in list(self._can_schedulers):
            self._detach_can_nodes(can_id, send_shutdown=True)

    def _add_mock_hil(self) -> None:
        if SIM_HIL_ID in self._hils:
            return
        self._hils[SIM_HIL_ID] = MockMicroHIL()
        self.device_added.emit("hil", SIM_HIL_ID)
        self.hil_connected.emit(SIM_HIL_ID, True)
        self.hil_info.emit(SIM_HIL_ID, self._hils[SIM_HIL_ID].get_firmware_version() or "")
        self._poll_hil(SIM_HIL_ID, self._hils[SIM_HIL_ID])

    def _remove_mock_hil(self) -> None:
        hil = self._hils.pop(SIM_HIL_ID, None)
        if hil is not None:
            hil.close()
            self.hil_connected.emit(SIM_HIL_ID, False)
            self.device_removed.emit("hil", SIM_HIL_ID)

    def _add_mock_picoscope(self) -> None:
        if self._mock_picoscope_active:
            return
        self._mock_picoscope_active = True
        info = MockPicoScope2000.open_first().get_info()
        self.device_added.emit("picoscope", SIM_PICOSCOPE_ID)
        self.picoscope_connected.emit(SIM_PICOSCOPE_ID, True)
        self.picoscope_state.emit(SIM_PICOSCOPE_ID, "free", info.variant, info.serial)

    def _remove_mock_picoscope(self) -> None:
        if not self._mock_picoscope_active:
            return
        self._mock_picoscope_active = False
        self.picoscope_connected.emit(SIM_PICOSCOPE_ID, False)
        self.device_removed.emit("picoscope", SIM_PICOSCOPE_ID)

    def _try_reconnect(self) -> None:
        # Ein passender COM-Port (USB-VID/PID) kann existieren, ohne dass
        # dahinter tatsaechlich ein antwortendes Geraet haengt (z.B. wenn der
        # Wandlerchip von Windows noch gelistet wird, das Geraet aber aus
        # oder abgezogen ist). Deshalb hier zusaetzlich zum Portoeffnen eine
        # echte Abfrage als Handshake -- sonst wuerde "verbunden" faelschlich
        # kurz aufblitzen, bis der naechste Poll (bis zu 500ms spaeter) es
        # wieder korrigiert.
        self._reconnect_loads()
        self._reconnect_psus()
        self._reconnect_can()
        self._reconnect_hils()
        self._reconnect_fgs()
        # PicoScope NICHT hier: eigener, viel langsamerer Timer (siehe
        # PICOSCOPE_RECONNECT_INTERVAL_MS / _picoscope_reconnect_timer).

    def _submit_reconnect(self, kind: str, device_id: str, job, *args) -> None:
        """Startet einen Verbindungsversuch im Probe-Thread (je Geraet nur einer
        gleichzeitig). Ergebnis kommt ueber _reconnect_done in _on_reconnect_done."""
        key = f"{kind}/{device_id}"
        if key in self._reconnect_pending:
            return
        self._reconnect_pending.add(key)
        self._reconnect_executor.submit(job, device_id, *args)

    def _reconnect_loads(self) -> None:
        candidates = _resolve_device_ids("load", KoradKEL102.discover_ports())
        for device_id, info in candidates.items():
            if device_id in self._loads:
                continue
            self._submit_reconnect("load", device_id, self._load_connect_job, info.device)

    def _load_connect_job(self, device_id: str, port: str) -> None:
        """Laeuft im Probe-Thread (siehe _reconnect_executor)."""
        candidate = None
        try:
            candidate = KoradKEL102(port)
            candidate.identify()
        except LoadError:
            if candidate is not None:
                candidate.close()
            candidate = None
        except OSError:
            # KoradKEL102(port) oeffnet den seriellen Port direkt und wirft dabei
            # KEIN LoadError, sondern pyserial's rohe SerialException
            # (Unterklasse von OSError) -- z.B. wenn der Port gerade von einem
            # anderen Prozess/einer zweiten App-Instanz gehalten wird ("Zugriff
            # verweigert"). Naechster RECONNECT_INTERVAL_MS-Tick versucht es
            # einfach erneut, analog zum LoadError-Fall.
            _log_repeating(f"open/{port}", logging.WARNING, "Last-Port %s konnte nicht geoeffnet werden", port)
            if candidate is not None:
                candidate.close()
            candidate = None
        except Exception:  # noqa: BLE001 -- der Probe-Thread darf nie still sterben
            logger.exception("Last %s: Verbindungsversuch gescheitert", device_id)
            if candidate is not None:
                candidate.close()
            candidate = None
        self._reconnect_done.emit("load", device_id, candidate, "")

    def _reconnect_fgs(self) -> None:
        """Verbindet Funktionsgeneratoren. Kandidaten sind alle CH340-Ports,
        bestaetigt wird per Handshake (JDS66xx.probe) -- die VID/PID allein
        beweist nichts (siehe jds66xx.driver.USB_VID und FG_REPROBE_INTERVAL_S).

        Anders als beim Netzteil werden die Ausgaenge beim Verbinden NICHT
        veraendert: das Geraet meldet seinen Zustand wahrheitsgemaess zurueck
        (Register 20), die Anzeige folgt also dem echten Zustand statt ihn zu
        raten -- ein gezieltes Abschalten beim Verbinden waere ein Eingriff in
        einen Aufbau, den der Nutzer vielleicht gerade betreibt."""
        infos = JDS66xx.discover_ports()
        present = {info.device for info in infos}
        for port in [p for p in self._fg_probe_after if p not in present]:
            del self._fg_probe_after[port]
        now = time.monotonic()
        candidates = _resolve_device_ids("fg", infos)
        for device_id, info in candidates.items():
            if device_id in self._fgs:
                continue
            if self._fg_probe_after.get(info.device, 0.0) > now:
                continue
            self._submit_reconnect("fg", device_id, self._fg_connect_job, info.device)

    def _fg_connect_job(self, device_id: str, port: str) -> None:
        """Laeuft im Probe-Thread (siehe _reconnect_executor)."""
        candidate = None
        try:
            candidate = JDS66xx(port)
            if not candidate.probe():
                candidate.close()
                # Die Sperrfrist (FG_REPROBE_INTERVAL_S) setzt der Worker selbst.
                self._reconnect_done.emit("fg_rejected", device_id, None, port)
                return
        except OSError:
            # Port wird von einem anderen Prozess gehalten -- naechster Tick
            # versucht es erneut (analog zu _load_connect_job).
            _log_repeating(f"open/{port}", logging.WARNING, "Funktionsgenerator-Port %s konnte nicht geoeffnet werden", port)
            if candidate is not None:
                candidate.close()
            candidate = None
        except Exception:  # noqa: BLE001 -- der Probe-Thread darf nie still sterben
            logger.exception("Funktionsgenerator %s: Verbindungsversuch gescheitert", device_id)
            if candidate is not None:
                candidate.close()
            candidate = None
        self._reconnect_done.emit("fg", device_id, candidate, "")

    def _emit_fg_state(self, device_id: str) -> None:
        """Liest den vollen Zustand und meldet ihn per fg_state. Ein Fehler wird
        wie bei jedem anderen Zugriff behandelt (Verbindung tot -> entfernen)."""
        fg = self._fgs.get(device_id)
        if fg is None:
            return
        try:
            state = fg.get_state()
        except FunctionGeneratorError as exc:
            self._drop_fg(device_id, str(exc))
            return
        self.fg_state.emit(device_id, {
            "outputs": list(state.outputs),
            "channels": [
                {"waveform": c.waveform, "frequency": c.frequency, "amplitude": c.amplitude,
                 "offset": c.offset, "duty": c.duty}
                for c in state.channels
            ],
            "phase": state.phase,
        })

    def _drop_fg(self, device_id: str, reason: str) -> None:
        fg = self._fgs.pop(device_id, None)
        if fg is None:
            return
        logger.warning("Funktionsgenerator %s getrennt: %s", device_id, reason)
        fg.close()
        self.fg_connected.emit(device_id, False)
        self.device_removed.emit("fg", device_id)

    def _poll_fgs(self) -> None:
        for device_id in list(self._fgs):
            self._emit_fg_state(device_id)

    def _reconnect_psus(self) -> None:
        candidates = _resolve_device_ids("psu", HCS34xx.discover_ports())
        for device_id, info in candidates.items():
            if device_id in self._psus:
                continue
            self._submit_reconnect("psu", device_id, self._psu_connect_job, info.device)

    def _psu_connect_job(self, device_id: str, port: str) -> None:
        """Laeuft im Probe-Thread (siehe _reconnect_executor)."""
        candidate = None
        try:
            candidate = HCS34xx(port)
            candidate.get_display()
            # Der Ausgang wird beim Verbinden NICHT veraendert: das Geraet hat keine Ein/Aus-Taste,
            # ausgeschaltet waere es von Hand nicht mehr zu benutzen. Der wirkliche Zustand wird
            # in _on_reconnect_done gelesen und angezeigt, damit Anzeige und Ausgang nicht
            # auseinanderlaufen (frueher: Ausgang beim Verbinden ausschalten).
        except PowerSupplyError:
            if candidate is not None:
                candidate.close()
            candidate = None
        except OSError:
            # HCS34xx(port) oeffnet den seriellen Port direkt und wirft dabei
            # KEIN PowerSupplyError, sondern pyserial's rohe SerialException --
            # analog zum Last-Fall und zum microHIL-Fall (siehe
            # _reconnect_hils). Naechster RECONNECT_INTERVAL_MS-Tick versucht
            # es einfach erneut.
            _log_repeating(f"open/{port}", logging.WARNING, "Netzteil-Port %s konnte nicht geoeffnet werden", port)
            if candidate is not None:
                candidate.close()
            candidate = None
        except Exception:  # noqa: BLE001 -- der Probe-Thread darf nie still sterben
            logger.exception("Netzteil %s: Verbindungsversuch gescheitert", device_id)
            if candidate is not None:
                candidate.close()
            candidate = None
        self._reconnect_done.emit("psu", device_id, candidate, "")

    @Slot(str, str, object, str)
    def _on_reconnect_done(self, kind: str, device_id: str, candidate: object, port: str) -> None:
        """Uebernimmt das Ergebnis eines Verbindungsversuchs im Worker-Thread --
        ab hier gehoert das Geraeteobjekt wieder allein diesem Thread."""
        pending_kind = "fg" if kind == "fg_rejected" else kind
        self._reconnect_pending.discard(f"{pending_kind}/{device_id}")
        if kind == "fg_rejected":
            self._fg_probe_after[port] = time.monotonic() + FG_REPROBE_INTERVAL_S
            _log_repeating(f"fg_rejected/{port}", logging.INFO,
                           "CH340-Port %s ist kein Funktionsgenerator (Handshake fehlgeschlagen)", port)
            return
        if candidate is None:
            return
        registry = {"load": self._loads, "psu": self._psus, "fg": self._fgs}[kind]
        if device_id in registry:
            candidate.close()   # inzwischen anderweitig verbunden
            return
        registry[device_id] = candidate
        if kind == "load":
            logger.info("Last verbunden: %s", device_id)
            self.device_added.emit("load", device_id)
            self.load_connected.emit(device_id, True)
            # Sofort abfragen statt auf den naechsten Poll-Zyklus zu warten
            # (bis zu POLL_INTERVAL_MS spaeter) -- der EIN/AUS-Button im
            # Control-Tab soll den echten Zustand so frueh wie moeglich zeigen.
            try:
                self.load_input_state.emit(device_id, candidate.get_input())
                self.load_function_state.emit(device_id, candidate.get_function())
            except LoadError:
                pass  # naechster Poll-Zyklus liefert den Status ohnehin nach
        elif kind == "psu":
            logger.info("Netzteil verbunden: %s", device_id)
            self.device_added.emit("psu", device_id)
            self.psu_connected.emit(device_id, True)
            self._psu_manual.discard(device_id)
            self.psu_manual_state.emit(device_id, False)
            self._adopt_psu_state(device_id)
        else:
            logger.info("Funktionsgenerator verbunden: %s (%s)", device_id, candidate.identify())
            self.device_added.emit("fg", device_id)
            self.fg_connected.emit(device_id, True)
            self._emit_fg_state(device_id)

    def _reconnect_can(self) -> None:
        """Verbindet noch nicht verbundene, konfigurierte CAN-Interfaces.

        Anders als bei Load/PSU keine Discovery ueber VID/PID -- die Liste
        der zu verbindenden Interfaces kommt ausschliesslich aus
        self._can_configs (siehe set_can_configs/settings.py). Ein
        Verbindungsfehler (z.B. Kabel nicht gesteckt, Kanal aktuell von
        einer anderen Anwendung belegt) wird hier verschluckt und beim
        naechsten RECONNECT_INTERVAL_MS-Tick erneut versucht, analog zu
        _reconnect_psus/_reconnect_loads.
        """
        self._reload_can_dbcs()
        for cfg in self._can_configs:
            device_id = can_device_id(cfg)
            if device_id in self._can_buses:
                continue
            try:
                bus = CanBus(
                    cfg["interface"], cfg["channel"], cfg.get("bitrate", CAN_DEFAULT_BITRATE),
                    serial_baudrate=cfg.get("serial_baudrate"),
                )
            except CanConnectionError as exc:
                logger.warning("CAN-Interface %s nicht erreichbar: %s", device_id, exc)
                self.can_connect_error.emit(device_id, str(exc))
                continue
            self._can_buses[device_id] = bus
            self._can_stats[device_id] = [0, 0]
            logger.info("CAN-Interface verbunden: %s", device_id)
            self.device_added.emit("can", device_id)
            self.can_connected.emit(device_id, True)
            self._sync_can_nodes(device_id)

    def _reload_can_dbcs(self) -> None:
        """Haelt self._can_dbcs/self._can_dbc_paths mit der aktuellen
        self._can_configs-Liste synchron (siehe set_can_configs/
        settings.py::can_configs, Schluessel "dbc_path").

        Laedt/parst eine DBC-Datei nur, wenn sich ihr Pfad seit dem letzten
        Aufruf geaendert hat (siehe _can_dbc_paths-Docstring in __init__) --
        _reconnect_can() (und damit dieser Aufruf) laeuft alle
        RECONNECT_INTERVAL_MS erneut, ein wiederholtes Neuparsen bei
        unveraendertem Pfad waere unnoetige Arbeit im selben Worker-Thread,
        der auch Last/Netzteil/microHIL bedient.

        Ein Parse-Fehler (fehlende/kaputte Datei) wird nur geloggt, nicht
        als Exception weitergereicht -- das CAN-Interface bleibt trotzdem
        nutzbar, es fehlt dann lediglich die Signal-Decodierung
        (can_signals_decoded), die Rohdaten-Anzeige (can_frame_received)
        ist davon unberuehrt.
        """
        wanted_ids = {can_device_id(cfg) for cfg in self._can_configs}
        for device_id in list(self._can_dbcs):
            if device_id not in wanted_ids:
                del self._can_dbcs[device_id]
                self._can_dbc_paths.pop(device_id, None)
        for cfg in self._can_configs:
            device_id = can_device_id(cfg)
            dbc_path = cfg.get("dbc_path") or None
            if dbc_path is None:
                if device_id in self._can_dbcs:
                    del self._can_dbcs[device_id]
                    self._can_dbc_paths.pop(device_id, None)
                continue
            if self._can_dbc_paths.get(device_id) == dbc_path:
                continue
            try:
                self._can_dbcs[device_id] = load_dbc(dbc_path)
                self._can_dbc_paths[device_id] = dbc_path
                logger.info("DBC-Datei fuer %s geladen: %s", device_id, dbc_path)
            except DbcError as exc:
                logger.warning("CAN-Interface %s: %s", device_id, exc)
                self._can_dbcs.pop(device_id, None)
                self._can_dbc_paths.pop(device_id, None)

    def _reconnect_hils(self) -> None:
        """Verbindet den microHIL, falls noch nicht verbunden.

        Anders als _reconnect_loads/_reconnect_psus (die ueber
        _resolve_device_ids beliebig viele baugleiche Geraete gleichzeitig
        unterstuetzen) wird hier bewusst nur EIN microHIL gleichzeitig
        unterstuetzt: MicroHIL.discover() (siehe microhil/driver.py) loest
        selbst schon die Mehrdeutigkeit zwischen den zwei COM-Ports EINES
        Geraets auf (HIL-Protokoll vs. CAN1/SLCAN, gleiche VID:PID), kennt
        aber keine Gruppierung ueber mehrere PHYSISCHE microHIL-Einheiten
        hinweg -- der microHIL ist eine Eigenentwicklung, fuer die (anders
        als bei den Last-/Netzteil-Modellen) kein Mehrfach-Einsatz vorgesehen
        ist. Discover_ports() wird trotzdem geloggt statt zu crashen, falls
        doch einmal mehrere Einheiten auftauchen -- discover() wirft dann
        HilError statt zu raten (siehe dessen Docstring).
        """
        try:
            port = MicroHIL.discover()
        except HilError as exc:
            _log_repeating("hil/ambiguous", logging.WARNING, "microHIL-Port nicht eindeutig bestimmbar: %s", exc)
            return
        if port is None:
            return
        info = next((i for i in MicroHIL.discover_ports() if i.device == port), None)
        if info is not None and info.serial_number:
            device_id = f"hil:{info.serial_number}"
        else:
            device_id = f"hil:{_port_fallback_id(info) if info is not None else port}"
        if device_id in self._hils:
            return
        candidate = None
        try:
            candidate = MicroHIL(port)
            candidate.identify()
        except HilError:
            if candidate is not None:
                candidate.close()
            return
        except OSError:
            # MicroHIL(port) oeffnet den seriellen Port direkt (siehe
            # microhil/driver.py) und wirft dabei KEIN HilError, sondern
            # pyserial's rohe SerialException (Unterklasse von OSError) --
            # z.B. wenn der Port gerade von einem anderen Prozess/einer
            # zweiten App-Instanz gehalten wird ("Zugriff verweigert").
            # Ungefangen wuerde das hier den kompletten _try_reconnect()-
            # Aufruf abbrechen und damit AUCH die Wiederverbindung von
            # Last/Netzteil/CAN im selben Zyklus verhindern (an echter
            # Hardware reproduziert). Naechster RECONNECT_INTERVAL_MS-Tick
            # versucht es einfach erneut, analog zum HilError-Fall oben.
            _log_repeating(f"open/{port}", logging.WARNING, "microHIL-Port %s konnte nicht geoeffnet werden", port)
            if candidate is not None:
                candidate.close()
            return
        self._hils[device_id] = candidate
        logger.info("microHIL verbunden: %s", device_id)
        self.device_added.emit("hil", device_id)
        self.hil_connected.emit(device_id, True)
        self.hil_info.emit(device_id, candidate.get_firmware_version() or "")
        # Sofort abfragen statt auf den naechsten HIL_POLL_INTERVAL_MS-Zyklus
        # zu warten (analog zu _reconnect_loads/_reconnect_psus).
        self._poll_hil(device_id, candidate)

    def _reconnect_picoscope(self) -> None:
        """Aktualisiert die reine USB-Praesenz des PicoScope, OHNE dafuer zu
        oeffnen (siehe _probe_picoscope() fuer den einmaligen Open/Close-
        Vorgang bei tatsaechlichem An-/Abstecken).

        Frueher wurde HIER bei JEDEM Tick (alle PICOSCOPE_RECONNECT_INTERVAL_MS)
        neu verbunden und sofort wieder getrennt, um "frei" von "belegt" zu
        unterscheiden -- an echter Hardware loeste das bei jedem Tick ein
        hoerbares Relaisklicken im Geraet aus (Nutzerfeedback), unabhaengig
        davon, ob sich am Belegt-Status ueberhaupt etwas geaendert hatte.
        `usb_present()` (Windows-SetupAPI, siehe driver.py) liefert die reine
        physische Praesenz dagegen OHNE das Geraet zu beruehren -- reicht fuer
        die laufende An-/Abstecken-Erkennung, das tatsaechliche Oeffnen (und
        damit variant/serial + der initiale Frei/Belegt-Status) passiert nur
        noch EINMAL beim Erkennen (siehe _probe_picoscope()), nicht mehr
        periodisch. Ein waehrend des Betriebs von aussen (PicoScope-7-App)
        belegtes Geraet zeigt die Kachel dadurch bewusst optimistisch weiter
        als zuletzt bekannt an, bis zum naechsten Ab-/Anstecken oder einer
        echten Testablauf-Aktion -- der Trade-off war ausdruecklich gewuenscht.

        Pausiert waehrend eines laufenden Testablaufs (siehe
        set_test_running/_test_running) UND wenn eine PICO_*-Aktion oder ein
        anderer Reconnect-Tick laeuft (siehe _picoscope_busy), analog zu
        _probe_picoscope().
        """
        if self._test_running or self._picoscope_busy:
            return
        present = picoscope_usb_present()
        if not present:
            if self._picoscope_present:
                self._picoscope_present = False
                self._picoscope_variant = ""
                self._picoscope_serial = ""
                self.picoscope_connected.emit(PICOSCOPE_ID, False)
                self.device_removed.emit("picoscope", PICOSCOPE_ID)
            return

        if not self._picoscope_present:
            self._picoscope_present = True
            logger.info("PicoScope erkannt (USB)")
            self.device_added.emit("picoscope", PICOSCOPE_ID)
            self.picoscope_connected.emit(PICOSCOPE_ID, True)
            self._probe_picoscope()

    def _probe_picoscope(self) -> None:
        """Oeffnet das PicoScope EINMALIG (nicht periodisch, siehe
        _reconnect_picoscope-Docstring), um Frei/Belegt-Status + variant/
        serial zu ermitteln -- ps2000_open_unit() belegt das Geraet exklusiv,
        ein dauerhaft offenes LabControl-Handle wuerde also verhindern, dass
        die PicoScope-7-App (Start-Button im Dashboard, siehe
        picoscope2000.driver.launch_app) das Geraet je selbst oeffnen koennte,
        daher wird sofort wieder geschlossen.

        An echter Hardware beobachtet: ps2000_open_unit() (blockierend,
        ~3,5s) pumpt intern Windows-Messages und liesse dadurch eine ZWEITE,
        verschachtelte Ausfuehrung eines Testablauf-Schritts auf demselben
        Worker-Thread zu, WAEHREND dieser Aufruf noch laeuft (per Log
        bestaetigt) -- _picoscope_busy schuetzt davor, indem parallele
        PICO_*-Aktionen currently busy sehen und auf ihren eigenen
        Retry-Mechanismus zurueckfallen (siehe testcase_runner.py).
        """
        # Haelt der ScopeService (Erfassung ueber Netzwerk/MCP) das Geraet, ist
        # der Status bekannt: "mcp". Oeffnen ginge ohnehin nicht (exklusiv).
        if not PICOSCOPE_GUARD.try_acquire(OWNER_WORKER):
            if PICOSCOPE_GUARD.owner == OWNER_SERVICE:
                self.picoscope_state.emit(PICOSCOPE_ID, "mcp", self._picoscope_variant, self._picoscope_serial)
            return
        self._picoscope_busy = True
        opened = False
        try:
            PICOSCOPE_GUARD.settle()
            scope = PicoScope2000.open_first()
            opened = True
            try:
                info = scope.get_info()
            finally:
                scope.close()
                self._picoscope_last_close = time.monotonic()
        except PicoScope2000Error:
            self.picoscope_state.emit(PICOSCOPE_ID, "busy", self._picoscope_variant, self._picoscope_serial)
            return
        finally:
            self._picoscope_busy = False
            PICOSCOPE_GUARD.release(closed=opened)
        self._picoscope_variant = info.variant
        self._picoscope_serial = info.serial
        self.picoscope_state.emit(PICOSCOPE_ID, "free", info.variant, info.serial)

    def _adopt_psu_state(self, device_id: str) -> None:
        """Uebernimmt den Zustand des Geraets in die GUI, ohne ihn zu aendern: Nennwerte, OVP/OCP,
        Sollwerte und Ausgang. Schlaegt das Lesen fehl, folgt die Kachel dem naechsten Abgleich in
        _poll_devices.

        Nennwerte VOR OVP/OCP und Sollwerten: die GUI setzt damit erst die Bereiche der Felder, dann
        passen die Werte hinein (Signale desselben Threads kommen in Sendereihenfolge an)."""
        self._emit_psu_ratings(device_id)
        self._emit_psu_limits(device_id)
        self._guard_psu(device_id, lambda psu: self.psu_setpoints.emit(device_id, *psu.get_setpoint()))
        self._guard_psu(device_id, lambda psu: self._emit_psu_output(device_id, psu.get_output()))

    def _emit_psu_ratings(self, device_id: str) -> None:
        """Meldet die Nennwerte (GMAX) des Netzteils an die GUI.

        Einmal beim (Wieder-)Verbinden -- sie aendern sich zur Laufzeit nicht.
        Ein Fehler hier ist unkritisch: die GUI behaelt dann ihre statischen
        Rahmen (siehe testcase_model.ACTION_VALUE_RANGE), und ein echter
        Verbindungsabbruch faellt im naechsten Poll-Zyklus ohnehin auf.
        """
        psu = self._psus.get(device_id)
        if psu is None:
            return
        try:
            max_voltage, max_current = psu.get_max()
        except PowerSupplyError:
            return
        if max_voltage > 0 and max_current > 0:
            self.psu_ratings.emit(device_id, max_voltage, max_current)

    def _emit_psu_limits(self, device_id: str) -> None:
        """Fragt OVP/OCP ab und meldet sie per psu_limits an die GUI.

        Wird beim (Wieder-)Verbinden und nach jedem erfolgreichen Setzen von
        OVP/OCP aufgerufen -- die GUI zeigt damit den zuletzt bekannten Stand
        in den Steuer-/Testablauf-Feldern und kann davor warnen, wenn ein
        Sollwert die Schwelle ueberschreitet (das Geraet ignoriert solche
        Werte sonst kommentarlos, siehe hcs34xx/driver.py). Kein Live-Polling
        bei jedem Zyklus, damit eine laufende Nutzereingabe im OVP/OCP-Feld
        nicht durch einen Poll ueberschrieben wird; bei manueller Aenderung
        direkt am Geraet bleibt der GUI-Stand daher bis zum naechsten
        Verbindungsaufbau ggf. veraltet.
        """
        psu = self._psus.get(device_id)
        if psu is None:
            return
        try:
            self.psu_limits.emit(device_id, psu.get_ovp(), psu.get_ocp())
        except PowerSupplyError:
            pass  # naechster erfolgreicher Poll-Zyklus deckt einen echten Verbindungsabbruch ohnehin auf

    def _poll(self) -> None:
        t_start = time.monotonic()
        timings: dict[str, float] = {}
        self._poll_devices(timings)
        total = time.monotonic() - t_start
        if total > POLL_SLOW_S and t_start - self._last_slow_poll_log >= POLL_SLOW_LOG_EVERY_S:
            self._last_slow_poll_log = t_start
            detail = ", ".join(f"{d} {t * 1000:.0f} ms" for d, t in sorted(timings.items(), key=lambda kv: -kv[1]))
            logger.warning("Poll-Zyklus langsam: %.0f ms gesamt (%s)", total * 1000, detail)

    def _poll_devices(self, timings: dict[str, float]) -> None:
        for device_id, load in list(self._loads.items()):
            t0 = time.monotonic()
            try:
                m = load.measure()
                self.load_measurement.emit(device_id, m.voltage, m.current, m.power)
                self.load_input_state.emit(device_id, load.get_input())
                self.load_function_state.emit(device_id, load.get_function())
            except Exception as exc:  # noqa: BLE001 -- ein Geraet darf den Zyklus fuer die anderen nie abbrechen
                if isinstance(exc, LoadError):
                    logger.warning("Last %s getrennt: %s", device_id, exc)
                else:
                    logger.exception("Last %s: unerwarteter Fehler, gilt als getrennt", device_id)
                try:
                    load.close()
                except Exception:  # noqa: BLE001
                    pass
                del self._loads[device_id]
                self.load_connected.emit(device_id, False)
                self.device_removed.emit("load", device_id)
            timings[device_id] = time.monotonic() - t0

        now = time.monotonic()
        for device_id, psu in list(self._psus.items()):
            if device_id in self._psu_manual or now < self._psu_next_poll.get(device_id, 0.0):
                continue
            self._psu_next_poll[device_id] = now + PSU_POLL_INTERVAL_S
            t0 = time.monotonic()
            try:
                d = psu.get_display()
                self.psu_measurement.emit(device_id, d.voltage, d.current, d.constant_current)
                count = self._psu_poll_count.get(device_id, 0) + 1
                self._psu_poll_count[device_id] = count
                if count % PSU_OUTPUT_POLL_EVERY == 0:
                    try:
                        on = psu.get_output()
                    except PowerSupplyValueError:
                        on = self._psu_output_seen.get(device_id)   # unverstaendliche Antwort: nichts melden
                    if on is not None and self._psu_output_seen.get(device_id) != on:
                        self._emit_psu_output(device_id, on)
            except Exception as exc:  # noqa: BLE001 -- ein Geraet darf den Zyklus fuer die anderen nie abbrechen
                if isinstance(exc, PowerSupplyError):
                    logger.warning("Netzteil %s getrennt: %s", device_id, exc)
                else:   # z. B. termios.error unter Linux vor 0.31.1: stand nur auf stderr, CAN blieb stehen
                    logger.exception("Netzteil %s: unerwarteter Fehler, gilt als getrennt", device_id)
                try:
                    psu.close()
                except Exception:  # noqa: BLE001
                    pass
                del self._psus[device_id]
                self.psu_connected.emit(device_id, False)
                self.device_removed.emit("psu", device_id)
            timings[device_id] = time.monotonic() - t0

        for device_id, bus in list(self._can_buses.items()):
            t0 = time.monotonic()
            nodes = self._nodes_for(device_id)
            try:
                drain_until = time.monotonic() + CAN_DRAIN_BUDGET_S
                while time.monotonic() < drain_until:
                    frame = bus.recv(timeout=0.0)
                    if frame is None:
                        break
                    self._can_stats[device_id][1] += 1
                    for node in nodes:
                        if frame.arbitration_id in node.rx_ids:
                            node.on_frame(frame)
                    self.can_frame_received.emit(
                        device_id, frame.arbitration_id, frame.data.hex(" ").upper(),
                        frame.extended, frame.timestamp,
                    )
                    db = self._can_dbcs.get(device_id)
                    if db is not None:
                        decoded = decode_frame(db, frame)
                        if decoded is not None:
                            self.can_signals_decoded.emit(device_id, frame.arbitration_id, decoded)
            except CanError as exc:
                logger.warning("CAN-Interface %s getrennt: %s", device_id, exc)
                self._detach_can_nodes(device_id, send_shutdown=False)
                bus.close()
                del self._can_buses[device_id]
                self._can_stats.pop(device_id, None)
                self.can_connected.emit(device_id, False)
                self.can_connect_error.emit(device_id, str(exc))
                self.device_removed.emit("can", device_id)
                continue
            scheduler = self._can_schedulers.get(device_id)
            if scheduler is not None:
                self._can_stats[device_id][0] += scheduler.take_sent_count()
            self._update_battsims(device_id)
            tx, rx = self._can_stats[device_id]
            self.can_stats.emit(device_id, tx, rx)
            timings[device_id] = time.monotonic() - t0

    def _poll_hils(self) -> None:
        for device_id, hil in list(self._hils.items()):
            self._poll_hil(device_id, hil)

    def _poll_hil(self, device_id: str, hil: MicroHIL) -> None:
        """Fragt einen kompletten microHIL-Zustand ab und meldet ihn ueber
        die hil_*-Signale ans Dashboard (siehe microhil_panel.MicroHilPanel).

        AOUT1-2 (Analogausgaenge) werden bewusst NICHT abgefragt: es gibt
        kein `AOUT?`-Kommando (siehe microhil/driver.py:
        set_analog_output()-Docstring), hier gaebe es also nichts
        Verlaessliches abzufragen. Das Dashboard zeigt AOUT1-2 stattdessen
        ueber einen direkten GUI-Thread-zu-GUI-Thread-Weg vom Control-Tab
        (control_tab.HilControlGroup.set_analog_output ->
        dashboard.set_hil_analog_out, siehe main_window._on_control_
        section_created) -- am Worker/Poll-Zyklus hier komplett vorbei, da
        es sich um den zuletzt GESENDETEN Sollwert handelt, keine
        Hardware-Bestaetigung. PWM1-4 ebenfalls nicht abgefragt, aus
        demselben Grund (kein `PWM?`-Aequivalent OHNE Kanalnummer wie bei
        IN?, und PWM ist im Dashboard weiterhin nicht dargestellt) -- die
        Control-Tab-Sektion (HilControlGroup) zeigt dort ebenfalls nur den
        zuletzt gesendeten Sollwert, kein Poll-Readback.
        """
        try:
            inputs = hil.get_inputs()
            outputs = [hil.get_output(ch) for ch in range(1, HIL_OUT_COUNT + 1)]
            relays = [hil.get_relay(ch) for ch in range(1, HIL_RELAY_COUNT + 1)]
            ain_mv = [hil.get_analog_input(ch) for ch in range(1, HIL_AIN_COUNT + 1)]
            pwr12_enabled = [hil.get_pwr12(ch) for ch in range(1, HIL_PWR12_COUNT + 1)]
            pwr12_current_ma = [hil.get_current_ma(ch) for ch in range(1, HIL_PWR12_COUNT + 1)]
        except HilError as exc:
            logger.warning("microHIL %s getrennt: %s", device_id, exc)
            hil.close()
            del self._hils[device_id]
            self.hil_connected.emit(device_id, False)
            self.device_removed.emit("hil", device_id)
            return
        self.hil_digital_state.emit(device_id, inputs, outputs)
        self.hil_relay_state.emit(device_id, relays)
        self.hil_analog_input.emit(device_id, ain_mv)
        self.hil_pwr12_state.emit(device_id, pwr12_enabled, pwr12_current_ma)

    # -- gemeinsame Fehlerbehandlung ------------------------------------------

    def _guard_load(self, device_id: str, action: Callable[[KoradKEL102], None]) -> tuple[bool, str]:
        load = self._loads.get(device_id)
        if load is None:
            return False, "Last nicht verbunden"
        try:
            action(load)
            return True, ""
        except LoadError as exc:
            load.close()
            del self._loads[device_id]
            self.load_connected.emit(device_id, False)
            self.device_removed.emit("load", device_id)
            return False, str(exc)

    def _guard_psu(self, device_id: str, action: Callable[[HCS34xx], None]) -> tuple[bool, str]:
        psu = self._psus.get(device_id)
        if psu is None:
            return False, "Netzteil nicht verbunden"
        if device_id in self._psu_manual:
            return False, "Netzteil in Handbedienung"
        try:
            action(psu)
            return True, ""
        except PowerSupplyValueError as exc:
            # Ungueltiger Wert (z.B. < 1V) -- kein Verbindungsproblem, Port bleibt offen.
            return False, str(exc)
        except PowerSupplyError as exc:
            psu.close()
            del self._psus[device_id]
            self.psu_connected.emit(device_id, False)
            self.device_removed.emit("psu", device_id)
            return False, str(exc)

    def _guard_fg(self, device_id: str, action: Callable[[JDS66xx], None]) -> tuple[bool, str]:
        fg = self._fgs.get(device_id)
        if fg is None:
            return False, "Funktionsgenerator nicht verbunden"
        try:
            action(fg)
        except FunctionGeneratorValueError as exc:
            # Wert abgelehnt bzw. nicht quittiert -- kein Verbindungsproblem.
            return False, str(exc)
        except FunctionGeneratorError as exc:
            self._drop_fg(device_id, str(exc))
            return False, str(exc)
        # Sofort den echten Zustand zuruecklesen, damit Dashboard und Control-Tab
        # zeigen, was das Geraet tatsaechlich angenommen hat (z.B. gerundet).
        self._emit_fg_state(device_id)
        return True, ""

    def _guard_can(self, device_id: str, action: Callable[[CanBus], None]) -> tuple[bool, str]:
        bus = self._can_buses.get(device_id)
        if bus is None:
            return False, "CAN-Interface nicht verbunden"
        try:
            action(bus)
            return True, ""
        except CanError as exc:
            self._detach_can_nodes(device_id, send_shutdown=False)
            bus.close()
            del self._can_buses[device_id]
            self._can_stats.pop(device_id, None)
            self.can_connected.emit(device_id, False)
            self.device_removed.emit("can", device_id)
            return False, str(exc)

    def _guard_hil(self, device_id: str, action: Callable[[MicroHIL], None]) -> tuple[bool, str]:
        hil = self._hils.get(device_id)
        if hil is None:
            return False, "microHIL nicht verbunden"
        try:
            action(hil)
            return True, ""
        except HilError as exc:
            hil.close()
            del self._hils[device_id]
            self.hil_connected.emit(device_id, False)
            self.device_removed.emit("hil", device_id)
            return False, str(exc)

    # -- Sicherheitsabschaltung (Watchdog, siehe safety.py) -------------------

    @Slot(str)
    def all_outputs_off(self, reason: str) -> None:
        """Schaltet ALLE bekannten Geraete sofort ab, unabhaengig von einem
        laufenden Testablauf.

        Last: echtes Ausgang-AUS (set_input(False)). Netzteil: Ausgang aus
        (SOUT1, mit Rueckkontrolle per GOUT, wie PSU_OUT_OFF im Testablauf,
        siehe _dispatch_action) -- ausser beim Beenden der App (REASON_WINDOW_CLOSE): dann wird es
        zur Handbedienung freigegeben (_release_psu), und ein Netzteil in Handbedienung wird nie
        angefasst.

        Ein haengendes/fehlerhaftes Geraet darf die anderen nicht blockieren:
        jedes Geraet bekommt einen Versuch + einen Retry, danach wird es wie
        bei einem normalen Verbindungsabbruch fallengelassen und mit dem
        naechsten weitergemacht -- kein except darf diese Schleife verlassen.

        microHIL (self._hils) SEIT dem Control-Tab-Abschnitt (HilControlGroup,
        siehe control_tab.py) MIT dabei: Digitalausgaenge, Relais, 12V-OUT und
        Analogausgaenge sind von dort aus steuerbar, also genau die Zustaende,
        fuer die diese App jetzt verantwortlich ist (siehe _kill_hil).
        """
        logger.info("ALL OFF angefordert (reason=%s)", reason)
        failures: list[str] = []
        for device_id, load in list(self._loads.items()):
            try:
                if not self._kill_load(device_id, load):
                    failures.append(device_id)
            except Exception:  # noqa: BLE001 -- Watchdog darf nie haengenbleiben
                logger.exception("ALL OFF: unerwarteter Fehler bei Last %s", device_id)
                failures.append(device_id)
        release_psus = reason == REASON_WINDOW_CLOSE
        for device_id, psu in list(self._psus.items()):
            try:
                if not self._kill_psu(device_id, psu, release=release_psus):
                    failures.append(device_id)
            except Exception:  # noqa: BLE001 -- Watchdog darf nie haengenbleiben
                logger.exception("ALL OFF: unerwarteter Fehler bei Netzteil %s", device_id)
                failures.append(device_id)
        for device_id, hil in list(self._hils.items()):
            try:
                if not self._kill_hil(device_id, hil):
                    failures.append(device_id)
            except Exception:  # noqa: BLE001 -- Watchdog darf nie haengenbleiben
                logger.exception("ALL OFF: unerwarteter Fehler bei microHIL %s", device_id)
                failures.append(device_id)
        for device_id, fg in list(self._fgs.items()):
            try:
                if not self._kill_fg(device_id, fg):
                    failures.append(device_id)
            except Exception:  # noqa: BLE001 -- Watchdog darf nie haengenbleiben
                logger.exception("ALL OFF: unerwarteter Fehler bei Funktionsgenerator %s", device_id)
                failures.append(device_id)
        for device_id, node in list(self._battsims.items()):
            try:
                self._kill_battsim(device_id, node)
            except Exception:  # noqa: BLE001 -- Watchdog darf nie haengenbleiben
                logger.exception("ALL OFF: unerwarteter Fehler bei Batterie-Simulator %s", device_id)
                failures.append(device_id)
        self.all_off_finished.emit(";".join(failures))

    def _kill_battsim(self, device_id: str, node: BattSim) -> None:
        """Ausgang sperren (OUTPUT=1 -> REMOTE/STOP, alle Zellen gebrueckt).
        Die Fernsteuerung bleibt an und es wird weiter zyklisch gesendet --
        sonst liefe der Simulator nach 4,5-6 s in FAIL. Ohne Fernsteuerung
        ueber LabControl gibt es nichts abzuschalten: dann bedient jemand das
        Geraet von Hand (STANDALONE) oder es steht in STANDBY. Kein Fehlschlag
        moeglich -- der Frame geht beim naechsten Sendetakt (<= 10 ms) raus;
        bestaetigt wird ueber BATTSIM_OUTPUT_STATUS in der Kachel."""
        if node.get_state().remote_requested:
            node.set_output(False)
            logger.info("ALL OFF: Batterie-Simulator %s -> Ausgang gesperrt", device_id)

    def _kill_load(self, device_id: str, load: KoradKEL102) -> bool:
        for attempt in (1, 2):
            try:
                load.set_input(False)
                logger.info("ALL OFF: Last %s -> Ausgang AUS", device_id)
                return True
            except LoadError as exc:
                if attempt == 1:
                    continue
                logger.error("ALL OFF: Last %s nicht erreichbar: %s", device_id, exc)
                load.close()
                del self._loads[device_id]
                self.load_connected.emit(device_id, False)
                self.device_removed.emit("load", device_id)
                return False
        return False

    def _kill_fg(self, device_id: str, fg: JDS66xx) -> bool:
        """Beide Ausgaenge AUS. Die eingestellten Signalparameter bleiben
        erhalten -- nur der Ausgang wird getrennt, wie bei der Last."""
        for attempt in (1, 2):
            try:
                fg.set_outputs(False, False)
                logger.info("ALL OFF: Funktionsgenerator %s -> beide Ausgaenge AUS", device_id)
                self._emit_fg_state(device_id)
                return True
            except FunctionGeneratorValueError as exc:
                logger.error("ALL OFF: Funktionsgenerator %s quittierte nicht: %s", device_id, exc)
                return False
            except FunctionGeneratorError as exc:
                if attempt == 1:
                    continue
                logger.error("ALL OFF: Funktionsgenerator %s nicht erreichbar: %s", device_id, exc)
                self._drop_fg(device_id, str(exc))
                return False
        return False

    def _release_psu(self, psu: HCS34xx) -> None:
        """Gibt das Netzteil zur Handbedienung frei: Strom 0 A, Spannung auf das Minimum, Ausgang EIN,
        Reglersperre aufheben. Das Geraet hat keine Ausgangstaste, es muss also eingeschaltet
        uebergeben werden; 0 V sind nicht einstellbar (MIN_VOLTAGE), mit 0 A Strombegrenzung liegt
        aber trotzdem nichts an, bis jemand von Hand aufdreht."""
        psu.set_current(0.0)
        psu.set_voltage(PSU_MIN_VOLTAGE)
        psu.set_output(True)
        psu.release_keyboard()

    def _kill_psu(self, device_id: str, psu: HCS34xx, release: bool = False) -> bool:
        if device_id in self._psu_manual:
            logger.info("ALL OFF: Netzteil %s in Handbedienung -> nicht angefasst", device_id)
            return True
        for attempt in (1, 2):
            try:
                if release:
                    self._release_psu(psu)
                    logger.info("Beenden: Netzteil %s -> 1 V / 0 A, Ausgang EIN, zur Handbedienung freigegeben", device_id)
                    self._emit_psu_output(device_id, True)
                    return True
                psu.set_output(False)
                logger.info("ALL OFF: Netzteil %s -> Ausgang aus", device_id)
                self._emit_psu_output(device_id, False)
                return True
            except PowerSupplyValueError as exc:
                # Schalter wirkte nicht, keine Verbindungsstoerung -- Retry hilft nicht.
                logger.error("ALL OFF: Netzteil %s: Ausgang liess sich nicht ausschalten: %s", device_id, exc)
                return False
            except PowerSupplyError as exc:
                if attempt == 1:
                    continue
                logger.error("ALL OFF: Netzteil %s nicht erreichbar: %s", device_id, exc)
                psu.close()
                del self._psus[device_id]
                self.psu_connected.emit(device_id, False)
                self.device_removed.emit("psu", device_id)
                return False
        return False

    def _kill_hil(self, device_id: str, hil: MicroHIL) -> bool:
        """Schaltet alle vom Control-Tab aus steuerbaren microHIL-Ausgaenge ab:
        Digitalausgaenge (OUT1-8), Relais (REL1-4), 12V-OUT (PWR12 1-2) und
        Analogausgaenge (AOUT1-2, auf 0mV). NICHT PWM (kein Control-Tab-
        Abschnitt dafuer, siehe microhil_panel.py-Modul-Docstring) -- die App
        setzt dort ohnehin nie einen Zustand, den sie zuruecknehmen muesste.

        Ein einzelner fehlgeschlagener Kanal bricht NICHT die uebrigen Kanaele
        desselben Geraets ab (anders als bei Last/Netzteil, wo ein Fehler
        gleichbedeutend mit Verbindungsverlust ist) -- ein HilError auf einem
        Kanal ist typischerweise `ERR RANGE`/`ERR UNKNOWN` (Programmierfehler)
        oder ein echter Verbindungsabbruch; im zweiten Fall schlagen ohnehin
        alle nachfolgenden Kanaele ebenso fehl und der Retry-Mechanismus
        greift wie bei Last/Netzteil."""
        for attempt in (1, 2):
            try:
                for ch in range(1, HIL_OUT_COUNT + 1):
                    hil.set_output(ch, False)
                for ch in range(1, HIL_RELAY_COUNT + 1):
                    hil.set_relay(ch, False)
                for ch in range(1, HIL_PWR12_COUNT + 1):
                    hil.set_pwr12(ch, False)
                for ch in range(1, HIL_AOUT_COUNT + 1):
                    hil.set_analog_output(ch, 0)
                logger.info("ALL OFF: microHIL %s -> alle Ausgaenge AUS", device_id)
                return True
            except HilError as exc:
                if attempt == 1:
                    continue
                logger.error("ALL OFF: microHIL %s nicht erreichbar: %s", device_id, exc)
                hil.close()
                del self._hils[device_id]
                self.hil_connected.emit(device_id, False)
                self.device_removed.emit("hil", device_id)
                return False
        return False

    # -- Last: Steuerbefehle ------------------------------------------------

    @Slot(str, str)
    def set_load_function(self, device_id: str, mode: str) -> None:
        self._guard_load(device_id, lambda load: load.set_function(mode))

    @Slot(str, float)
    def set_load_current(self, device_id: str, amps: float) -> None:
        self._guard_load(device_id, lambda load: load.set_current(amps))

    @Slot(str, float)
    def set_load_voltage(self, device_id: str, volts: float) -> None:
        self._guard_load(device_id, lambda load: load.set_voltage(volts))

    @Slot(str, float)
    def set_load_resistance(self, device_id: str, ohms: float) -> None:
        self._guard_load(device_id, lambda load: load.set_resistance(ohms))

    @Slot(str, float)
    def set_load_power(self, device_id: str, watts: float) -> None:
        self._guard_load(device_id, lambda load: load.set_power(watts))

    @Slot(str, bool)
    def set_load_input(self, device_id: str, on: bool) -> None:
        self._guard_load(device_id, lambda load: load.set_input(on))

    @Slot(str, str, float)
    def set_load_setpoint(self, device_id: str, mode_code: str, value: float) -> None:
        setters = {
            "CURR": self.set_load_current,
            "VOLT": self.set_load_voltage,
            "RES": self.set_load_resistance,
            "POW": self.set_load_power,
        }
        setter = setters.get(mode_code)
        if setter is not None:
            setter(device_id, value)

    # -- Netzteil: Steuerbefehle ---------------------------------------------

    @Slot(str, float)
    def set_psu_voltage(self, device_id: str, volts: float) -> None:
        self._guard_psu(device_id, lambda psu: psu.set_voltage(volts))

    @Slot(str, float)
    def set_psu_current(self, device_id: str, amps: float) -> None:
        self._guard_psu(device_id, lambda psu: psu.set_current(amps))

    @Slot(str, bool)
    def set_psu_manual(self, device_id: str, manual: bool) -> None:
        """Schaltet ein Netzteil zwischen Handbedienung und Kontrolle durch LabControl um.

        Handbedienung: Das Geraet hat keine Ausgangstaste und jeder Befehl sperrt die Regler, ein
        gleichzeitiges Bedienen von Hand und per Software ist also nicht sinnvoll. LabControl gibt es
        deshalb frei (_release_psu: 1 V / 0 A, Ausgang EIN, Regler entsperrt) und spricht es danach
        nicht mehr an -- keine Abfrage, keine Befehle (_guard_psu lehnt sie ab), "Alle aus" laesst
        es aus. Zurueck: der Zustand des Geraets wird uebernommen (_adopt_psu_state)."""
        psu = self._psus.get(device_id)
        if psu is None:
            self.psu_manual_state.emit(device_id, False)
            return
        if manual:
            if device_id not in self._psu_manual:
                ok, message = self._guard_psu(device_id, self._release_psu)
                if not ok:
                    logger.warning("Netzteil %s: Freigabe zur Handbedienung gescheitert: %s", device_id, message)
                    self.psu_manual_state.emit(device_id, False)
                    return
                self._psu_manual.add(device_id)
                logger.info("Netzteil %s: Handbedienung (1 V / 0 A, Ausgang EIN)", device_id)
                self._emit_psu_output(device_id, True)
            self.psu_manual_state.emit(device_id, True)
        else:
            if device_id in self._psu_manual:
                self._psu_manual.discard(device_id)
                logger.info("Netzteil %s: Kontrolle durch LabControl", device_id)
                self._psu_poll_count[device_id] = 0
                self._adopt_psu_state(device_id)
            self.psu_manual_state.emit(device_id, device_id in self._psu_manual)

    @Slot(str, bool)
    def set_psu_output(self, device_id: str, on: bool) -> None:
        ok, _ = self._guard_psu(device_id, lambda psu: psu.set_output(on))
        if ok:
            self._emit_psu_output(device_id, on)
        else:
            # Schalter wirkte nicht: der EIN/AUS-Schalter der Kachel hat sich schon umgestellt,
            # also den wirklichen Zustand nachmelden.
            self._guard_psu(device_id, lambda psu: self._emit_psu_output(device_id, psu.get_output()))

    def _emit_psu_output(self, device_id: str, on: bool) -> None:
        """Meldet den Ausgangszustand an die GUI und merkt ihn fuer _poll_devices."""
        self._psu_output_seen[device_id] = on
        self.psu_output_state.emit(device_id, on)

    @Slot(str, float)
    def set_psu_ovp(self, device_id: str, volts: float) -> None:
        ok, _ = self._guard_psu(device_id, lambda psu: psu.set_ovp(volts))
        if ok:
            self._emit_psu_limits(device_id)

    @Slot(str, float)
    def set_psu_ocp(self, device_id: str, amps: float) -> None:
        ok, _ = self._guard_psu(device_id, lambda psu: psu.set_ocp(amps))
        if ok:
            self._emit_psu_limits(device_id)

    @Slot(str, int)
    def recall_psu_memory(self, device_id: str, index: int) -> None:
        self._guard_psu(device_id, lambda psu: psu.recall_memory(index))

    # -- Funktionsgenerator: Steuerbefehle (siehe control_tab.FgControlGroup) ------

    @Slot(str, int, bool)
    def set_fg_output(self, device_id: str, channel: int, on: bool) -> None:
        self._guard_fg(device_id, lambda fg: fg.set_output(channel, on))

    @Slot(str, int, int)
    def set_fg_waveform(self, device_id: str, channel: int, code: int) -> None:
        self._guard_fg(device_id, lambda fg: fg.set_waveform(channel, code))

    @Slot(str, int, float)
    def set_fg_frequency(self, device_id: str, channel: int, hertz: float) -> None:
        self._guard_fg(device_id, lambda fg: fg.set_frequency(channel, hertz))

    @Slot(str, int, float)
    def set_fg_amplitude(self, device_id: str, channel: int, volts: float) -> None:
        self._guard_fg(device_id, lambda fg: fg.set_amplitude(channel, volts))

    @Slot(str, int, float)
    def set_fg_offset(self, device_id: str, channel: int, volts: float) -> None:
        self._guard_fg(device_id, lambda fg: fg.set_offset(channel, volts))

    @Slot(str, int, float)
    def set_fg_duty(self, device_id: str, channel: int, percent: float) -> None:
        self._guard_fg(device_id, lambda fg: fg.set_duty(channel, percent))

    @Slot(str, float)
    def set_fg_phase(self, device_id: str, degrees: float) -> None:
        self._guard_fg(device_id, lambda fg: fg.set_phase(degrees))

    # -- CAN-Bus: Steuerbefehle -----------------------------------------------
    # Nutzdaten bewusst als Hex-String statt bytes uebergeben, da PySide6-
    # Signale ueber die Thread-Grenze hinweg fuer str/int/float/bool
    # zuverlaessig funktionieren (siehe uebrige Signale in dieser Datei),
    # fuer bytes aber nicht garantiert getestet ist.

    def _send_can(self, device_id: str, arbitration_id: int, data_hex: str, extended: bool) -> tuple[bool, str]:
        try:
            data = bytes.fromhex(data_hex.replace(" ", ""))
        except ValueError as exc:
            return False, f"Ungueltige CAN-Nutzdaten '{data_hex}': {exc}"
        ok, message = self._guard_can(device_id, lambda bus: bus.send(arbitration_id, data, extended))
        if ok:
            self._can_stats[device_id][0] += 1
        return ok, message

    @Slot(str, int, str, bool)
    def send_can_frame(self, device_id: str, arbitration_id: int, data_hex: str, extended: bool) -> None:
        """Manuelles Senden aus dem Control-Tab -- meldet Fehler NICHT ueber
        action_completed (das ist reserviert fuer den Testablauf-Dispatch,
        siehe execute_can_send), analog zu set_load_current/set_psu_voltage
        etc., die ihr _guard_*-Ergebnis ebenfalls nicht zurueckmelden."""
        self._send_can(device_id, arbitration_id, data_hex, extended)

    @Slot(str, int, str, bool)
    def execute_can_send(self, device_id: str, arbitration_id: int, data_hex: str, extended: bool) -> None:
        """Senden aus einem Testablauf-Schritt (step_type "can_send", siehe
        testcase_runner.py) -- im Gegensatz zu send_can_frame() wird das
        Ergebnis ueber action_completed gemeldet, damit der TestRunner auf
        den Abschluss warten kann (gleiches Muster wie execute_action/
        _dispatch_action fuer Last/Netzteil)."""
        ok, message = self._send_can(device_id, arbitration_id, data_hex, extended)
        self.action_completed.emit(ok, message, 0.0)

    # -- microHIL: Steuerbefehle (siehe control_tab.HilControlGroup) ----------
    # Melden ihr _guard_hil-Ergebnis nicht zurueck, analog zu set_load_current/
    # set_psu_voltage etc. -- Fehler landen im Log, kein Testablauf-Dispatch
    # (siehe _guard_hil/all_outputs_off fuer die Verbindungsabbruch-Behandlung).

    @Slot(str, int, bool)
    def set_hil_output(self, device_id: str, channel: int, on: bool) -> None:
        self._guard_hil(device_id, lambda hil: hil.set_output(channel, on))

    @Slot(str, int, int)
    def set_hil_analog_output(self, device_id: str, channel: int, millivolts: int) -> None:
        self._guard_hil(device_id, lambda hil: hil.set_analog_output(channel, millivolts))

    @Slot(str, int, bool)
    def set_hil_relay(self, device_id: str, channel: int, on: bool) -> None:
        self._guard_hil(device_id, lambda hil: hil.set_relay(channel, on))

    @Slot(str, int, bool)
    def set_hil_pwr12(self, device_id: str, channel: int, on: bool) -> None:
        self._guard_hil(device_id, lambda hil: hil.set_pwr12(channel, on))

    @Slot(str, int, int)
    def set_hil_pwm(self, device_id: str, channel: int, permille: int) -> None:
        self._guard_hil(device_id, lambda hil: hil.set_pwm(channel, permille))

    @Slot(str, int, int)
    def set_hil_current_limit(self, device_id: str, channel: int, milliamps: int) -> None:
        # Siehe microhil/driver.py: set_current_limit()-Docstring -- gegen
        # echte, noch nicht aktualisierte Firmware schlaegt das mit
        # HilError("ERR UNKNOWN") fehl; _guard_hil behandelt das wie jeden
        # anderen Verbindungsfehler (Verbindung wird geschlossen). Auf dem
        # simulierten Geraet (microhil.mock) funktioniert es bereits.
        self._guard_hil(device_id, lambda hil: hil.set_current_limit(channel, milliamps))

    # -- Batterie-Simulator: Steuerung ----------------------------------------

    def _guard_battsim(self, device_id: str, action: Callable[[BattSim], None]) -> tuple[bool, str]:
        """Steuerbefehle gehen nur an einen antwortenden Simulator. Ein
        abgelehnter Wert (Bereich, Grenzwert, Zustand) laesst die Verbindung
        unberuehrt -- getrennt wird ein CAN-Knoten nur ueber sein Interface
        bzw. wenn 0x120 ausbleibt (_update_battsims)."""
        node = self._battsims.get(device_id)
        if node is None or not self._battsim_online.get(device_id, False):
            return False, "Batterie-Simulator nicht verbunden"
        try:
            action(node)
        except BattSimError as exc:   # inkl. BattSimValueError
            return False, str(exc)
        self.battsim_state.emit(device_id, self._battsim_state_dict(node))
        return True, ""

    def _tile_battsim(self, device_id: str, action_name: str, action: Callable[[BattSim], None],
                      value: float = 0.0, channel: int = 0) -> None:
        """Bedienung ueber die Kachel im Fenster: wie _guard_battsim, aber mit Logzeile.
        Fernsteuerung und Testablauf stehen ohnehin im Log; die Kachel bisher nicht, damit
        liess sich ein gesendeter Zustand nicht mehr einer Quelle zuordnen (07.11.2026)."""
        ok, message = self._guard_battsim(device_id, action)
        self._log_battsim_write(device_id, action_name, value, channel, "Kachel", ok, message)

    @staticmethod
    def _log_battsim_write(device_id: str, action_name: str, value: float, channel: int, source: str,
                           ok: bool, message: str) -> None:
        logger.info("Batterie-Simulator %s: %s (wert=%s, kanal=%s) per %s -> %s%s", device_id, action_name,
                    value, channel, source, "ok" if ok else "FEHLER", f" [{message}]" if message else "")

    @Slot(str, bool)
    def set_battsim_remote(self, device_id: str, on: bool) -> None:
        self._tile_battsim(device_id, "BS_REMOTE_ON" if on else "BS_REMOTE_OFF", lambda n: n.set_remote(on))

    @Slot(str, bool)
    def set_battsim_output(self, device_id: str, on: bool) -> None:
        self._tile_battsim(device_id, "BS_OUT_ON" if on else "BS_OUT_OFF", lambda n: n.set_output(on))

    @Slot(str, int, float)
    def set_battsim_cell_voltage(self, device_id: str, cell: int, volts: float) -> None:
        self._tile_battsim(device_id, "BS_CELL_VOLT", lambda n: n.set_cell_voltage(cell, volts), volts, cell)

    @Slot(str, float)
    def set_battsim_all_voltages(self, device_id: str, volts: float) -> None:
        self._tile_battsim(device_id, "BS_ALL_VOLT", lambda n: n.set_all_voltages(volts), volts)

    @Slot(str, int, bool)
    def set_battsim_cell_enabled(self, device_id: str, cell: int, on: bool) -> None:
        self._tile_battsim(device_id, "BS_CELL_ON" if on else "BS_CELL_OFF",
                           lambda n: n.set_cell_enabled(cell, on), 0.0, cell)

    @Slot(str, bool)
    def set_battsim_all_enabled(self, device_id: str, on: bool) -> None:
        self._tile_battsim(device_id, "BS_ALL_ON" if on else "BS_ALL_OFF", lambda n: n.set_all_enabled(on))

    @Slot(str)
    def reset_battsim_fail(self, device_id: str) -> None:
        self._tile_battsim(device_id, "BS_RESET_FAIL", lambda n: n.reset_fail())

    @Slot(str, float)
    def set_battsim_voltage_limit(self, device_id: str, volts: float) -> None:
        """Grenzwert je Zelle (Einstellung, wirkt auch, solange das Geraet noch
        nicht antwortet -- er gilt dann ab dem Anhaengen)."""
        self._battsim_limits[device_id] = volts
        node = self._battsims.get(device_id)
        if node is not None:
            try:
                node.set_voltage_limit(volts)
            except BattSimValueError as exc:
                logger.warning("Grenzwert fuer %s abgelehnt: %s", device_id, exc)

    @Slot(object)
    def set_battsim_voltage_limits(self, limits: object) -> None:
        for device_id, volts in dict(limits or {}).items():
            self.set_battsim_voltage_limit(device_id, float(volts))

    # -- Testablauf: generischer Dispatch fuer einen Testschritt -------------

    @Slot(str, str, str, float, int)
    def execute_action(self, device_id: str, kind: str, action: str, value: float, channel: int) -> None:
        ok, message, read_value = self._dispatch_action(device_id, kind, action, value, channel)
        if kind == "battsim" and action not in ("BS_CELL_READ", "BS_STACK_READ"):
            self._log_battsim_write(device_id, action, value, channel, "Testablauf", ok, message)
        self.action_completed.emit(ok, message, read_value)

    @Slot(int, str, str, str, float, int)
    def execute_remote_action(
        self, request_id: int, device_id: str, kind: str, action: str, value: float, channel: int
    ) -> None:
        """Aktion aus der Netzwerk-Fernsteuerung (siehe share_remote.py).

        Dieselbe _dispatch_action wie beim Testablauf -- damit dieselbe
        Geraetelogik und dieselben Fehlermeldungen --, aber mit EIGENEM
        Ergebnissignal und einer Anfrage-ID. Ueber action_completed zu melden
        wuerde das Ergebnis dem TestRunner unterschieben (siehe
        send_can_frame), und ohne ID liesse sich die Antwort nicht der
        wartenden HTTP-Anfrage zuordnen.

        Die Erlaubnis (Token, Freigabe, Hauptschalter, Wertebereich) ist zu
        diesem Zeitpunkt bereits geprueft; hier wird nur ausgefuehrt.
        """
        ok, message, read_value = self._dispatch_action(device_id, kind, action, value, channel)
        logger.info("Fernsteuerung: %s %s (wert=%s, kanal=%s) -> %s%s", device_id, action, value,
                    channel, "ok" if ok else "FEHLER", f" [{message}]" if message else "")
        self.remote_action_completed.emit(request_id, ok, message, read_value)

    def _dispatch_action(
        self, device_id: str, kind: str, action: str, value: float, channel: int
    ) -> tuple[bool, str, float]:
        if kind == "load":
            if action in ("CURR", "VOLT", "RES", "POW"):
                ok, message = self._guard_load(device_id, lambda load: load.set_function(action))
                if not ok:
                    return ok, message, 0.0
                setter_name = {
                    "CURR": "set_current",
                    "VOLT": "set_voltage",
                    "RES": "set_resistance",
                    "POW": "set_power",
                }[action]
                ok, message = self._guard_load(device_id, lambda load: getattr(load, setter_name)(value))
                return ok, message, 0.0
            if action == "OUT_ON":
                ok, message = self._guard_load(device_id, lambda load: load.set_input(True))
                return ok, message, 0.0
            if action == "OUT_OFF":
                ok, message = self._guard_load(device_id, lambda load: load.set_input(False))
                return ok, message, 0.0
            return False, f"Unbekannte Aktion '{action}' fuer Last", 0.0

        if kind == "psu":
            if action == "PSU_VOLT":
                ok, message = self._guard_psu(device_id, lambda psu: psu.set_voltage(value))
                return ok, message, 0.0
            if action == "PSU_CURR":
                ok, message = self._guard_psu(device_id, lambda psu: psu.set_current(value))
                return ok, message, 0.0
            if action in ("PSU_OUT_ON", "PSU_OUT_OFF"):
                # Reine Schaltaktion (siehe BUGS.md #17): Spannungs- und Stromsollwert bleiben
                # unangetastet. Der Schalter wird mit GOUT zurueckgelesen (siehe set_output).
                # Der EIN/AUS-Schalter im Control-Tab kennt nur, was er selbst oder dieser
                # Worker meldet -- deshalb hier nachmelden (Fernsteuerung, Testablauf).
                on = action == "PSU_OUT_ON"
                ok, message = self._guard_psu(device_id, lambda psu: psu.set_output(on))
                if ok:
                    self._emit_psu_output(device_id, on)
                return ok, message, 0.0
            return False, f"Unbekannte Aktion '{action}' fuer Netzteil", 0.0

        if kind == "hil":
            if action == "HIL_OUT_ON":
                ok, message = self._guard_hil(device_id, lambda hil: hil.set_output(channel, True))
                return ok, message, 0.0
            if action == "HIL_OUT_OFF":
                ok, message = self._guard_hil(device_id, lambda hil: hil.set_output(channel, False))
                return ok, message, 0.0
            if action == "HIL_RELAY_ON":
                ok, message = self._guard_hil(device_id, lambda hil: hil.set_relay(channel, True))
                return ok, message, 0.0
            if action == "HIL_RELAY_OFF":
                ok, message = self._guard_hil(device_id, lambda hil: hil.set_relay(channel, False))
                return ok, message, 0.0
            if action == "HIL_AOUT":
                ok, message = self._guard_hil(
                    device_id, lambda hil: hil.set_analog_output(channel, int(value))
                )
                return ok, message, 0.0
            if action == "HIL_IN_READ":
                # Mutable Zwischenspeicher statt Rueckgabewert, da die an
                # _guard_hil uebergebene Aktion (siehe deren Signatur) nichts
                # zurueckgeben kann -- dieselbe Technik wie ueberall sonst in
                # dieser Methode fuer reine Set-Kommandos, hier aber mit dem
                # zusaetzlichen Zweck, den GELESENEN Wert aus der Closure
                # herauszutragen.
                result: dict[str, bool] = {}

                def _read_in(hil: MicroHIL) -> None:
                    result["v"] = hil.get_input(channel)

                ok, message = self._guard_hil(device_id, _read_in)
                return ok, message, (1.0 if result.get("v") else 0.0)
            if action == "HIL_AIN_READ":
                result: dict[str, int] = {}

                def _read_ain(hil: MicroHIL) -> None:
                    result["v"] = hil.get_analog_input(channel)

                ok, message = self._guard_hil(device_id, _read_ain)
                return ok, message, float(result.get("v", 0))
            return False, f"Unbekannte Aktion '{action}' fuer microHIL", 0.0

        if kind == "fg":
            if action in FG_WAVE_ACTIONS:
                code = FG_WAVE_ACTIONS[action]
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_waveform(channel, code))
            elif action == "FG_FREQ":
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_frequency(channel, value))
            elif action == "FG_AMPL":
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_amplitude(channel, value))
            elif action == "FG_OFFS":
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_offset(channel, value))
            elif action == "FG_DUTY":
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_duty(channel, value))
            elif action == "FG_PHASE":
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_phase(value))
            elif action == "FG_OUT_ON":
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_output(channel, True))
            elif action == "FG_OUT_OFF":
                ok, message = self._guard_fg(device_id, lambda fg: fg.set_output(channel, False))
            else:
                return False, f"Unbekannte Aktion '{action}' fuer Funktionsgenerator", 0.0
            return ok, message, 0.0

        if kind == "picoscope":
            return self._execute_picoscope_action(device_id, action, value, channel)

        if kind == BATTSIM_KIND:
            return self._dispatch_battsim(device_id, action, value, channel)

        return False, f"Unbekanntes Geraet '{kind}'", 0.0

    def _dispatch_battsim(self, device_id: str, action: str, value: float, channel: int) -> tuple[bool, str, float]:
        """BS_*-Aktionen (testcase_model.BATTSIM_ACTIONS, remote_actions).
        Lese-Aktionen liefern den zuletzt gemeldeten Istwert."""
        setters: dict[str, Callable[[BattSim], None]] = {
            "BS_REMOTE_ON": lambda n: n.set_remote(True),
            "BS_REMOTE_OFF": lambda n: n.set_remote(False),
            "BS_OUT_ON": lambda n: n.set_output(True),
            "BS_OUT_OFF": lambda n: n.set_output(False),
            "BS_CELL_VOLT": lambda n: n.set_cell_voltage(channel, value),
            "BS_ALL_VOLT": lambda n: n.set_all_voltages(value),
            "BS_CELL_ON": lambda n: n.set_cell_enabled(channel, True),
            "BS_CELL_OFF": lambda n: n.set_cell_enabled(channel, False),
            "BS_ALL_ON": lambda n: n.set_all_enabled(True),
            "BS_ALL_OFF": lambda n: n.set_all_enabled(False),
            "BS_RESET_FAIL": lambda n: n.reset_fail(),
        }
        if action in setters:
            ok, message = self._guard_battsim(device_id, setters[action])
            return ok, message, 0.0
        if action in ("BS_CELL_READ", "BS_STACK_READ"):
            node = self._battsims.get(device_id)
            if node is None or not self._battsim_online.get(device_id, False):
                return False, "Batterie-Simulator nicht verbunden", 0.0
            st = node.get_state()
            if action == "BS_STACK_READ":
                if st.stack_voltage_v is None:
                    return False, "Stapelspannung noch nicht gemeldet", 0.0
                return True, "", float(st.stack_voltage_v)
            if not 1 <= channel <= len(st.cells):
                return False, f"Zelle {channel} gibt es nicht (1..{len(st.cells)})", 0.0
            volts = st.cells[channel - 1].voltage_v
            if volts is None:
                return False, f"Zelle {channel}: noch kein Istwert gemeldet", 0.0
            return True, "", float(volts)
        return False, f"Unbekannte Aktion '{action}' fuer Batterie-Simulator", 0.0

    def _execute_picoscope_action(
        self, device_id: str, action: str, value: float, channel: int
    ) -> tuple[bool, str, float]:
        """Fuehrt eine einzelne PICO_*-Aktion aus (siehe testcase_model.
        PICO_ACTIONS).

        Ist fuer diesen device_id bereits eine Session offen (siehe
        open_picoscope_session, main_window._on_run_requested -- der
        Normalfall waehrend eines Testlaufs mit PICO_*-Schritten), wird sie
        direkt fuer die Messung wiederverwendet: kein Oeffnen/Schliessen pro
        Aktion mehr noetig. Ohne Session (Fallback, z.B. Session-Oeffnen ist
        selbst an einer verschachtelten Reconnect-Probe gescheitert, siehe
        open_picoscope_session-Docstring) wird -- wie bisher -- pro Aktion
        verbunden, gemessen und sofort wieder getrennt, genau wie
        _reconnect_picoscope() (siehe dessen Docstring -- ein dauerhaftes
        Handle wuerde sonst die PicoScope-7-App aussperren). Das macht jede
        einzelne PICO_*-Aktion in diesem Fallback-Fall ca. 4,5s teuer (siehe
        picoscope2000/README.md) -- blockiert fuer diese Dauer den
        DeviceWorker-Thread, exakt wie jede andere synchron ausgefuehrte
        Testablauf-Aktion auch, nur laenger."""
        field = PICO_ACTION_FIELDS.get(action)
        if field is None:
            return False, f"Unbekannte Aktion '{action}' fuer Oszilloskop", 0.0

        pico_channel = "B" if channel == 2 else "A"
        voltage_range = PICO_RANGE_BY_CODE.get(int(value), "2V")

        session = self._picoscope_sessions.get(device_id)
        if session is not None:
            try:
                measurement = session.measure(channel=pico_channel, voltage_range=voltage_range)
            except PicoScope2000Error as exc:
                return False, str(exc), 0.0
            return True, "", getattr(measurement, field)

        is_mock = device_id == SIM_PICOSCOPE_ID
        if is_mock:
            if not self._mock_picoscope_active:
                return False, "Oszilloskop (Simulation) nicht verbunden", 0.0
            driver_cls = MockPicoScope2000
        else:
            if not self._picoscope_present:
                return False, "Oszilloskop nicht verbunden", 0.0
            driver_cls = PicoScope2000

        if is_mock:
            try:
                scope = driver_cls.open_first()
                measurement = scope.measure(channel=pico_channel, voltage_range=voltage_range)
            except PicoScope2000Error as exc:
                return False, str(exc), 0.0
            finally:
                scope.close()
            return True, "", getattr(measurement, field)

        # Normalerweise verhindert set_test_running() (siehe main_window.
        # _on_run_requested) neue Reconnect-Ticks waehrend eines Testlaufs --
        # ein knapp VOR Testlaufstart bereits gestarteter Tick kann aber noch
        # in-flight sein. Bewusst KEIN Warten hier (siehe PICOSCOPE_RETRY_
        # MESSAGE-Kommentar oben: strukturell unmoeglich, der aeussere Aufruf
        # kann nicht fertig werden, waehrend wir blockieren) -- stattdessen
        # sofort mit einer fuer testcase_runner.py erkennbaren Retry-Meldung
        # zurueckkehren.
        if self._picoscope_busy:
            return False, PICOSCOPE_RETRY_MESSAGE, 0.0
        if not PICOSCOPE_GUARD.try_acquire(OWNER_WORKER):
            return False, "Oszilloskop belegt (Erfassung über Netzwerk/MCP)", 0.0
        PICOSCOPE_GUARD.settle()
        # Siehe PICOSCOPE_SETTLE_S: ein Reopen zu kurz nach dem letzten
        # Schliessen (egal ob von _reconnect_picoscope() oder einer
        # vorherigen Aktion) schlaegt an echter Hardware fehl.
        elapsed = time.monotonic() - self._picoscope_last_close
        if elapsed < PICOSCOPE_SETTLE_S:
            time.sleep(PICOSCOPE_SETTLE_S - elapsed)

        self._picoscope_busy = True
        opened = False
        try:
            try:
                scope = driver_cls.open_first()
            except PicoScope2000Error:
                return False, "Oszilloskop belegt (vermutlich PicoScope-7-App offen)", 0.0
            opened = True
            try:
                measurement = scope.measure(channel=pico_channel, voltage_range=voltage_range)
            except PicoScope2000Error as exc:
                return False, str(exc), 0.0
            finally:
                scope.close()
                self._picoscope_last_close = time.monotonic()
        finally:
            self._picoscope_busy = False
            PICOSCOPE_GUARD.release(closed=opened)
        return True, "", getattr(measurement, field)

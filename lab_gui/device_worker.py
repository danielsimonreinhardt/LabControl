"""Hintergrund-Worker fuer die serielle Kommunikation mit Last(en) und Netzteil(en).

Laeuft in einem eigenen QThread, damit blockierende Seriell-I/O (Timeouts
bei Verbindungsabbruch) die GUI nicht einfrieren laesst. Alle Zugriffe auf
die Geraete laufen ausschliesslich hier; die GUI kommuniziert nur ueber
Qt-Signale/Slots (automatisch thread-sicher als Queued Connections).

Unterstuetzt mehrere gleichzeitig angeschlossene Geraete desselben Typs
(z.B. zwei baugleiche HCS-34xx-Netzteile). Jede Instanz bekommt eine
Device-ID (siehe _resolve_device_ids), unter der sie in allen Signalen/
Slots referenziert wird.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from korad_kel102.driver import KoradKEL102, LoadError
from korad_kel102.mock import MockKoradKEL102
from hcs34xx.driver import HCS34xx, PowerSupplyError, PowerSupplyValueError
from hcs34xx.mock import MockHCS34xx
from can_bus.driver import CanBus, CanError, CanConnectionError, DEFAULT_BITRATE as CAN_DEFAULT_BITRATE
from can_bus.mock import MockCanBus
from can_bus.dbc import DbcError, decode_frame, load_dbc
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
from picoscope2000.mock import MockPicoScope2000

logger = logging.getLogger(__name__)

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

# Obergrenze empfangener CAN-Frames, die pro Poll-Zyklus UND Interface aus
# der Empfangs-Queue geleert werden (siehe _poll) -- verhindert, dass ein
# Interface mit sehr hoher Buslast die anderen Geraete im selben Zyklus
# verhungern laesst. Bei Ueberschreiten bleiben weitere Frames einfach bis
# zum naechsten Zyklus in der Queue (python-can puffert selbst).
CAN_DRAIN_LIMIT = 32

# Feste Device-IDs fuer die simulierten Geraete (siehe set_simulation_mode) --
# im Gegensatz zu echten Geraeten gibt es hier keine USB-Seriennummer/COM-Port,
# aus der sich eine ID ableiten liesse.
SIM_PSU_ID = "psu:SIM"
SIM_LOAD_ID = "load:SIM"
SIM_CAN_ID = "can:mock:SIM"
SIM_HIL_ID = "hil:SIM"
SIM_PICOSCOPE_ID = "picoscope:SIM"

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


def _can_device_id(cfg: dict) -> str:
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
            device_id = f"{kind}:{info.device}"
        result[device_id] = info
    return result


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
    # Hardware-Rueckfrage (das HCS-34xx-Protokoll kennt keine, siehe
    # hcs34xx/driver.py) -- wird nur emittiert, wenn der Worker den Ausgang
    # SELBST (ausserhalb eines direkten GUI-Klicks in PsuControlGroup) auf
    # AUS setzt: beim (Wieder-)Verbinden (Sicherheits-Fix, siehe
    # _reconnect_psus) und bei all_outputs_off (Alle-Aus-Button, Safety-
    # Watchdog-Trip, Fensterschliessen) -- sonst bliebe der EIN/AUS-Schalter
    # im Control-Tab faelschlich auf "EIN" stehen, obwohl der Ausgang laengst
    # abgeschaltet wurde.
    psu_output_state = Signal(str, bool)
    psu_limits = Signal(str, float, float)   # device_id, OVP (V), OCP (A) -- siehe _emit_psu_limits
    # fuer Testablauf-Schritte: success, error, gelesener Wert (nur bei einer
    # microHIL-Lese-Aktion befuellt, siehe HIL_READ_ACTIONS/_dispatch_action;
    # 0.0 bei allen anderen Aktionen ohne Bedeutung).
    action_completed = Signal(bool, str, float)
    all_off_finished = Signal(str)           # Semikolon-Liste fehlgeschlagener Geraete, "" = alles ok

    can_connected = Signal(str, bool)        # device_id, online
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

    def __init__(self, simulation_mode: bool = False, can_configs: list[dict] | None = None) -> None:
        super().__init__()
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

    @Slot()
    def start(self) -> None:
        if self._simulation_mode:
            self._add_mock_psu()
            self._add_mock_load()
            self._add_mock_can()
            self._add_mock_hil()
            self._add_mock_picoscope()
        self._try_reconnect()
        self._reconnect_picoscope()
        self._poll_timer.start(POLL_INTERVAL_MS)
        self._reconnect_timer.start(RECONNECT_INTERVAL_MS)
        self._hil_poll_timer.start(HIL_POLL_INTERVAL_MS)
        self._picoscope_reconnect_timer.start(PICOSCOPE_RECONNECT_INTERVAL_MS)

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
        elapsed = time.monotonic() - self._picoscope_last_close
        if elapsed < PICOSCOPE_SETTLE_S:
            time.sleep(PICOSCOPE_SETTLE_S - elapsed)
        self._picoscope_busy = True
        try:
            scope = PicoScope2000.open_first()
        except PicoScope2000Error:
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
        else:
            self._remove_mock_psu()
            self._remove_mock_load()
            self._close_can(SIM_CAN_ID)
            self._remove_mock_hil()
            self._remove_mock_picoscope()

    # -- CAN-Interface-Konfiguration (siehe settings.py: can_configs) --------
    # Anders als Last/Netzteil kein Hotplug: welche Interfaces ueberhaupt
    # verbunden werden sollen, kommt ausschliesslich aus dieser Liste (siehe
    # can_bus/README.md: keine sichere Autodiscovery ohne bekannte Bitrate).

    @Slot(list)
    def set_can_configs(self, configs: list) -> None:
        self._can_configs = list(configs)
        wanted_ids = {_can_device_id(cfg) for cfg in self._can_configs}
        for device_id in list(self._can_buses):
            if device_id != SIM_CAN_ID and device_id not in wanted_ids:
                self._close_can(device_id)
        self._reconnect_can()

    def _add_mock_psu(self) -> None:
        if SIM_PSU_ID in self._psus:
            return
        self._psus[SIM_PSU_ID] = MockHCS34xx()
        self.device_added.emit("psu", SIM_PSU_ID)
        self.psu_connected.emit(SIM_PSU_ID, True)
        self._emit_psu_limits(SIM_PSU_ID)

    def _remove_mock_psu(self) -> None:
        psu = self._psus.pop(SIM_PSU_ID, None)
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

    def _add_mock_can(self) -> None:
        if SIM_CAN_ID in self._can_buses:
            return
        self._can_buses[SIM_CAN_ID] = MockCanBus()
        self._can_stats[SIM_CAN_ID] = [0, 0]
        self.device_added.emit("can", SIM_CAN_ID)
        self.can_connected.emit(SIM_CAN_ID, True)

    def _close_can(self, device_id: str) -> None:
        bus = self._can_buses.pop(device_id, None)
        self._can_stats.pop(device_id, None)
        if bus is not None:
            bus.close()
            self.can_connected.emit(device_id, False)
            self.device_removed.emit("can", device_id)

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
        # PicoScope NICHT hier: eigener, viel langsamerer Timer (siehe
        # PICOSCOPE_RECONNECT_INTERVAL_MS / _picoscope_reconnect_timer).

    def _reconnect_loads(self) -> None:
        candidates = _resolve_device_ids("load", KoradKEL102.discover_ports())
        for device_id, info in candidates.items():
            if device_id in self._loads:
                continue
            candidate = None
            try:
                candidate = KoradKEL102(info.device)
                candidate.identify()
            except LoadError:
                if candidate is not None:
                    candidate.close()
                continue
            except OSError:
                # KoradKEL102(info.device) oeffnet den seriellen Port direkt
                # und wirft dabei KEIN LoadError, sondern pyserial's rohe
                # SerialException (Unterklasse von OSError) -- z.B. wenn der
                # Port gerade von einem anderen Prozess/einer zweiten
                # App-Instanz gehalten wird ("Zugriff verweigert"). Ungefangen
                # wuerde das hier die Schleife ueber ALLE Last-Kandidaten
                # abbrechen (nicht nur die Wiederverbindung von PSU/CAN/HIL im
                # selben Zyklus wie beim analogen microHIL-Fix -- siehe
                # _reconnect_hils -- sondern zusaetzlich auch die noch nicht
                # geprueften Last-Kandidaten in DIESER Schleife). Naechster
                # RECONNECT_INTERVAL_MS-Tick versucht es einfach erneut,
                # analog zum LoadError-Fall oben.
                logger.warning("Last-Port %s konnte nicht geoeffnet werden", info.device)
                if candidate is not None:
                    candidate.close()
                continue
            self._loads[device_id] = candidate
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

    def _reconnect_psus(self) -> None:
        candidates = _resolve_device_ids("psu", HCS34xx.discover_ports())
        for device_id, info in candidates.items():
            if device_id in self._psus:
                continue
            candidate = None
            try:
                candidate = HCS34xx(info.device)
                candidate.get_display()
                # SICHERHEITSKRITISCH: Das Geraet kennt kein echtes Ausgang-
                # AUS-Kommando (siehe hcs34xx/driver.py) -- "AUS" wird ueber
                # Strom 0A emuliert. Ohne diesen expliziten Befehl bliebe ein
                # von einer frueheren Sitzung/manuell eingeschalteter Ausgang
                # nach dem Verbinden real eingeschaltet, waehrend die GUI
                # (Standardzustand "Aus") das Gegenteil anzeigt. Deshalb hier
                # aktiv auf 0A setzen, BEVOR das Geraet als verbunden gilt --
                # so ist der reale Zustand garantiert mit der Anzeige synchron.
                candidate.set_current(0.0)
            except PowerSupplyError:
                if candidate is not None:
                    candidate.close()
                continue
            except OSError:
                # HCS34xx(info.device) oeffnet den seriellen Port direkt und
                # wirft dabei KEIN PowerSupplyError, sondern pyserial's rohe
                # SerialException (Unterklasse von OSError) -- analog zum
                # Last-Fall oben und zum bereits gefixten microHIL-Fall
                # (siehe _reconnect_hils). Naechster RECONNECT_INTERVAL_MS-
                # Tick versucht es einfach erneut.
                logger.warning("Netzteil-Port %s konnte nicht geoeffnet werden", info.device)
                if candidate is not None:
                    candidate.close()
                continue
            self._psus[device_id] = candidate
            logger.info("Netzteil verbunden: %s", device_id)
            self.device_added.emit("psu", device_id)
            self.psu_connected.emit(device_id, True)
            self.psu_output_state.emit(device_id, False)
            self._emit_psu_limits(device_id)

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
            device_id = _can_device_id(cfg)
            if device_id in self._can_buses:
                continue
            try:
                bus = CanBus(cfg["interface"], cfg["channel"], cfg.get("bitrate", CAN_DEFAULT_BITRATE))
            except CanConnectionError as exc:
                logger.warning("CAN-Interface %s nicht erreichbar: %s", device_id, exc)
                continue
            self._can_buses[device_id] = bus
            self._can_stats[device_id] = [0, 0]
            logger.info("CAN-Interface verbunden: %s", device_id)
            self.device_added.emit("can", device_id)
            self.can_connected.emit(device_id, True)

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
        wanted_ids = {_can_device_id(cfg) for cfg in self._can_configs}
        for device_id in list(self._can_dbcs):
            if device_id not in wanted_ids:
                del self._can_dbcs[device_id]
                self._can_dbc_paths.pop(device_id, None)
        for cfg in self._can_configs:
            device_id = _can_device_id(cfg)
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
            logger.warning("microHIL-Port nicht eindeutig bestimmbar: %s", exc)
            return
        if port is None:
            return
        info = next((i for i in MicroHIL.discover_ports() if i.device == port), None)
        device_id = f"hil:{info.serial_number}" if info is not None and info.serial_number else f"hil:{port}"
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
            logger.warning("microHIL-Port %s konnte nicht geoeffnet werden", port)
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
        self._picoscope_busy = True
        try:
            scope = PicoScope2000.open_first()
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
        self._picoscope_variant = info.variant
        self._picoscope_serial = info.serial
        self.picoscope_state.emit(PICOSCOPE_ID, "free", info.variant, info.serial)

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
        for device_id, load in list(self._loads.items()):
            try:
                m = load.measure()
                self.load_measurement.emit(device_id, m.voltage, m.current, m.power)
                self.load_input_state.emit(device_id, load.get_input())
                self.load_function_state.emit(device_id, load.get_function())
            except LoadError as exc:
                logger.warning("Last %s getrennt: %s", device_id, exc)
                load.close()
                del self._loads[device_id]
                self.load_connected.emit(device_id, False)
                self.device_removed.emit("load", device_id)

        for device_id, psu in list(self._psus.items()):
            try:
                d = psu.get_display()
                self.psu_measurement.emit(device_id, d.voltage, d.current, d.constant_current)
            except PowerSupplyError as exc:
                logger.warning("Netzteil %s getrennt: %s", device_id, exc)
                psu.close()
                del self._psus[device_id]
                self.psu_connected.emit(device_id, False)
                self.device_removed.emit("psu", device_id)

        for device_id, bus in list(self._can_buses.items()):
            try:
                drained = 0
                while drained < CAN_DRAIN_LIMIT:
                    frame = bus.recv(timeout=0.0)
                    if frame is None:
                        break
                    self._can_stats[device_id][1] += 1
                    self.can_frame_received.emit(
                        device_id, frame.arbitration_id, frame.data.hex(" ").upper(),
                        frame.extended, frame.timestamp,
                    )
                    db = self._can_dbcs.get(device_id)
                    if db is not None:
                        decoded = decode_frame(db, frame)
                        if decoded is not None:
                            self.can_signals_decoded.emit(device_id, frame.arbitration_id, decoded)
                    drained += 1
            except CanError as exc:
                logger.warning("CAN-Interface %s getrennt: %s", device_id, exc)
                bus.close()
                del self._can_buses[device_id]
                self._can_stats.pop(device_id, None)
                self.can_connected.emit(device_id, False)
                self.device_removed.emit("can", device_id)
                continue
            tx, rx = self._can_stats[device_id]
            self.can_stats.emit(device_id, tx, rx)

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

    def _guard_can(self, device_id: str, action: Callable[[CanBus], None]) -> tuple[bool, str]:
        bus = self._can_buses.get(device_id)
        if bus is None:
            return False, "CAN-Interface nicht verbunden"
        try:
            action(bus)
            return True, ""
        except CanError as exc:
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

        Last: echtes Ausgang-AUS (set_input(False)). Netzteil: kein
        Ausgang-Kommando vorhanden (siehe hcs34xx/README.md) -- Emulation
        ueber Stromsollwert 0A (dieselbe PSU_OUT_OFF-Konvention wie im
        Testablauf, siehe _dispatch_action).

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
        for device_id, psu in list(self._psus.items()):
            try:
                if not self._kill_psu(device_id, psu):
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
        self.all_off_finished.emit(";".join(failures))

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

    def _kill_psu(self, device_id: str, psu: HCS34xx) -> bool:
        for attempt in (1, 2):
            try:
                psu.set_current(0.0)
                logger.info("ALL OFF: Netzteil %s -> Strom 0A", device_id)
                self.psu_output_state.emit(device_id, False)
                return True
            except PowerSupplyValueError as exc:
                # Wert abgelehnt, keine Verbindungsstoerung -- Retry hilft nicht.
                logger.error("ALL OFF: Netzteil %s lehnte Strom 0A ab: %s", device_id, exc)
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

    # -- Testablauf: generischer Dispatch fuer einen Testschritt -------------

    @Slot(str, str, str, float, int)
    def execute_action(self, device_id: str, kind: str, action: str, value: float, channel: int) -> None:
        ok, message, read_value = self._dispatch_action(device_id, kind, action, value, channel)
        self.action_completed.emit(ok, message, read_value)

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
            if action == "PSU_OUT_ON":
                # Workaround (kein echtes Ausgang-Ein/Aus verfuegbar, siehe
                # hcs34xx/README.md): reine Schaltaktion (siehe BUGS.md #17)
                # -- laesst einen zuvor gesetzten Spannungs-Sollwert
                # unangetastet und hebt nur den Strom auf mindestens 0.1A an,
                # analog zu PSU_OUT_OFF (das nur den Strom auf 0A setzt).
                def _output_on(psu: HCS34xx) -> None:
                    _, current = psu.get_setpoint()
                    if current < 0.1:
                        psu.set_current(0.1)

                ok, message = self._guard_psu(device_id, _output_on)
                return ok, message, 0.0
            if action == "PSU_OUT_OFF":
                ok, message = self._guard_psu(device_id, lambda psu: psu.set_current(0.0))
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

        if kind == "picoscope":
            return self._execute_picoscope_action(device_id, action, value, channel)

        return False, f"Unbekanntes Geraet '{kind}'", 0.0

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
        # Siehe PICOSCOPE_SETTLE_S: ein Reopen zu kurz nach dem letzten
        # Schliessen (egal ob von _reconnect_picoscope() oder einer
        # vorherigen Aktion) schlaegt an echter Hardware fehl.
        elapsed = time.monotonic() - self._picoscope_last_close
        if elapsed < PICOSCOPE_SETTLE_S:
            time.sleep(PICOSCOPE_SETTLE_S - elapsed)

        self._picoscope_busy = True
        try:
            try:
                scope = driver_cls.open_first()
            except PicoScope2000Error:
                return False, "Oszilloskop belegt (vermutlich PicoScope-7-App offen)", 0.0
            try:
                measurement = scope.measure(channel=pico_channel, voltage_range=voltage_range)
            except PicoScope2000Error as exc:
                return False, str(exc), 0.0
            finally:
                scope.close()
                self._picoscope_last_close = time.monotonic()
        finally:
            self._picoscope_busy = False
        return True, "", getattr(measurement, field)

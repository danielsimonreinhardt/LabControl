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
from collections import Counter
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from korad_kel102.driver import KoradKEL102, LoadError
from korad_kel102.mock import MockKoradKEL102
from hcs34xx.driver import HCS34xx, PowerSupplyError, PowerSupplyValueError
from hcs34xx.mock import MockHCS34xx
from can_bus.driver import CanBus, CanError, CanConnectionError, DEFAULT_BITRATE as CAN_DEFAULT_BITRATE
from can_bus.mock import MockCanBus
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
    action_completed = Signal(bool, str)     # fuer Testablauf-Schritte: success, error
    all_off_finished = Signal(str)           # Semikolon-Liste fehlgeschlagener Geraete, "" = alles ok

    can_connected = Signal(str, bool)        # device_id, online
    # device_id, arbitration_id, data (Hex-String z.B. "01 A2 FF"), extended, timestamp (s)
    can_frame_received = Signal(str, int, str, bool, float)
    can_stats = Signal(str, int, int)        # device_id, tx_count, rx_count -- fuers Dashboard

    hil_connected = Signal(str, bool)          # device_id, online
    hil_digital_state = Signal(str, list, list)  # device_id, inputs (IN1-8), outputs (OUT1-8)
    hil_relay_state = Signal(str, list)        # device_id, relays (RELAY1-4)
    hil_analog_input = Signal(str, list)       # device_id, AIN1-4 in mV
    # device_id, PWR12-Enable (1-2), Stromsense (1-2) -- siehe microhil_panel.
    # _Pwr12Row: rohe mV vom Geraet, im Dashboard bewusst als "mA" beschriftet.
    hil_pwr12_state = Signal(str, list, list)

    def __init__(self, simulation_mode: bool = False, can_configs: list[dict] | None = None) -> None:
        super().__init__()
        self._loads: dict[str, KoradKEL102] = {}
        self._psus: dict[str, HCS34xx] = {}
        self._can_buses: dict[str, CanBus] = {}
        self._can_stats: dict[str, list[int]] = {}  # device_id -> [tx_count, rx_count]
        self._can_configs: list[dict] = list(can_configs or [])
        self._hils: dict[str, MicroHIL] = {}
        self._simulation_mode = simulation_mode
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._try_reconnect)
        # Eigener, langsamerer Timer statt im selben Zyklus wie _poll() --
        # siehe HIL_POLL_INTERVAL_MS.
        self._hil_poll_timer = QTimer(self)
        self._hil_poll_timer.timeout.connect(self._poll_hils)

    @Slot()
    def start(self) -> None:
        if self._simulation_mode:
            self._add_mock_psu()
            self._add_mock_load()
            self._add_mock_can()
            self._add_mock_hil()
        self._try_reconnect()
        self._poll_timer.start(POLL_INTERVAL_MS)
        self._reconnect_timer.start(RECONNECT_INTERVAL_MS)
        self._hil_poll_timer.start(HIL_POLL_INTERVAL_MS)

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
        else:
            self._remove_mock_psu()
            self._remove_mock_load()
            self._close_can(SIM_CAN_ID)
            self._remove_mock_hil()

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
        self._poll_hil(SIM_HIL_ID, self._hils[SIM_HIL_ID])

    def _remove_mock_hil(self) -> None:
        hil = self._hils.pop(SIM_HIL_ID, None)
        if hil is not None:
            hil.close()
            self.hil_connected.emit(SIM_HIL_ID, False)
            self.device_removed.emit("hil", SIM_HIL_ID)

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
        # Sofort abfragen statt auf den naechsten HIL_POLL_INTERVAL_MS-Zyklus
        # zu warten (analog zu _reconnect_loads/_reconnect_psus).
        self._poll_hil(device_id, candidate)

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
        Hardware-Bestaetigung. Aus demselben Grund auch PWM1-4 nicht (kein
        Control-Tab-Abschnitt dafuer, ohnehin nicht im Dashboard, siehe
        microhil_panel.py).
        """
        try:
            inputs = hil.get_inputs()
            outputs = [hil.get_output(ch) for ch in range(1, HIL_OUT_COUNT + 1)]
            relays = [hil.get_relay(ch) for ch in range(1, HIL_RELAY_COUNT + 1)]
            ain_mv = [hil.get_analog_input(ch) for ch in range(1, HIL_AIN_COUNT + 1)]
            pwr12_enabled = [hil.get_pwr12(ch) for ch in range(1, HIL_PWR12_COUNT + 1)]
            pwr12_current_mv = [hil.get_current_sense_mv(ch) for ch in range(1, HIL_PWR12_COUNT + 1)]
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
        self.hil_pwr12_state.emit(device_id, pwr12_enabled, pwr12_current_mv)

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
        self.action_completed.emit(ok, message)

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
    def set_hil_current_limit(self, device_id: str, channel: int, milliamps: int) -> None:
        # Siehe microhil/driver.py: set_current_limit()-Docstring -- gegen
        # echte, noch nicht aktualisierte Firmware schlaegt das mit
        # HilError("ERR UNKNOWN") fehl; _guard_hil behandelt das wie jeden
        # anderen Verbindungsfehler (Verbindung wird geschlossen). Auf dem
        # simulierten Geraet (microhil.mock) funktioniert es bereits.
        self._guard_hil(device_id, lambda hil: hil.set_current_limit(channel, milliamps))

    # -- Testablauf: generischer Dispatch fuer einen Testschritt -------------

    @Slot(str, str, str, float)
    def execute_action(self, device_id: str, kind: str, action: str, value: float) -> None:
        ok, message = self._dispatch_action(device_id, kind, action, value)
        self.action_completed.emit(ok, message)

    def _dispatch_action(self, device_id: str, kind: str, action: str, value: float) -> tuple[bool, str]:
        if kind == "load":
            if action in ("CURR", "VOLT", "RES", "POW"):
                ok, message = self._guard_load(device_id, lambda load: load.set_function(action))
                if not ok:
                    return ok, message
                setter_name = {
                    "CURR": "set_current",
                    "VOLT": "set_voltage",
                    "RES": "set_resistance",
                    "POW": "set_power",
                }[action]
                return self._guard_load(device_id, lambda load: getattr(load, setter_name)(value))
            if action == "OUT_ON":
                return self._guard_load(device_id, lambda load: load.set_input(True))
            if action == "OUT_OFF":
                return self._guard_load(device_id, lambda load: load.set_input(False))
            return False, f"Unbekannte Aktion '{action}' fuer Last"

        if kind == "psu":
            if action == "PSU_VOLT":
                return self._guard_psu(device_id, lambda psu: psu.set_voltage(value))
            if action == "PSU_CURR":
                return self._guard_psu(device_id, lambda psu: psu.set_current(value))
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

                return self._guard_psu(device_id, _output_on)
            if action == "PSU_OUT_OFF":
                return self._guard_psu(device_id, lambda psu: psu.set_current(0.0))
            return False, f"Unbekannte Aktion '{action}' fuer Netzteil"

        return False, f"Unbekanntes Geraet '{kind}'"

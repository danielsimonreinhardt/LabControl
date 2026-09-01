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

# Feste Device-IDs fuer die simulierten Geraete (siehe set_simulation_mode) --
# im Gegensatz zu echten Geraeten gibt es hier keine USB-Seriennummer/COM-Port,
# aus der sich eine ID ableiten liesse.
SIM_PSU_ID = "psu:SIM"
SIM_LOAD_ID = "load:SIM"


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

    def __init__(self, simulation_mode: bool = False) -> None:
        super().__init__()
        self._loads: dict[str, KoradKEL102] = {}
        self._psus: dict[str, HCS34xx] = {}
        self._simulation_mode = simulation_mode
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

    @Slot()
    def start(self) -> None:
        if self._simulation_mode:
            self._add_mock_psu()
            self._add_mock_load()
        self._try_reconnect()
        self._poll_timer.start(POLL_INTERVAL_MS)
        self._reconnect_timer.start(RECONNECT_INTERVAL_MS)

    # -- Simulationsmodus ----------------------------------------------------

    @Slot(bool)
    def set_simulation_mode(self, enabled: bool) -> None:
        if enabled == self._simulation_mode:
            return
        self._simulation_mode = enabled
        if enabled:
            self._add_mock_psu()
            self._add_mock_load()
        else:
            self._remove_mock_psu()
            self._remove_mock_load()

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
                # hcs34xx/README.md): Spannung setzen, Strom dabei auf
                # mindestens 0.1A anheben statt einen bestehenden hoeheren
                # Sollwert zu ueberschreiben.
                def _output_on(psu: HCS34xx) -> None:
                    psu.set_voltage(value)
                    _, current = psu.get_setpoint()
                    if current < 0.1:
                        psu.set_current(0.1)

                return self._guard_psu(device_id, _output_on)
            if action == "PSU_OUT_OFF":
                return self._guard_psu(device_id, lambda psu: psu.set_current(0.0))
            return False, f"Unbekannte Aktion '{action}' fuer Netzteil"

        return False, f"Unbekanntes Geraet '{kind}'"

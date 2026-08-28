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

from collections import Counter
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from korad_kel102.driver import KoradKEL102, LoadError
from hcs34xx.driver import HCS34xx, PowerSupplyError, PowerSupplyValueError
from hcs34xx.mock import MockHCS34xx

POLL_INTERVAL_MS = 500
RECONNECT_INTERVAL_MS = 3000

# Feste Device-ID fuer das simulierte Netzteil (siehe set_simulation_mode) --
# im Gegensatz zu echten Geraeten gibt es hier keine USB-Seriennummer/COM-Port,
# aus der sich eine ID ableiten liesse.
SIM_PSU_ID = "psu:SIM"


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
    action_completed = Signal(bool, str)     # fuer Testablauf-Schritte: success, error

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
        else:
            self._remove_mock_psu()

    def _add_mock_psu(self) -> None:
        if SIM_PSU_ID in self._psus:
            return
        self._psus[SIM_PSU_ID] = MockHCS34xx()
        self.device_added.emit("psu", SIM_PSU_ID)
        self.psu_connected.emit(SIM_PSU_ID, True)

    def _remove_mock_psu(self) -> None:
        psu = self._psus.pop(SIM_PSU_ID, None)
        if psu is not None:
            psu.close()
            self.psu_connected.emit(SIM_PSU_ID, False)
            self.device_removed.emit("psu", SIM_PSU_ID)

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
            self.device_added.emit("load", device_id)
            self.load_connected.emit(device_id, True)

    def _reconnect_psus(self) -> None:
        candidates = _resolve_device_ids("psu", HCS34xx.discover_ports())
        for device_id, info in candidates.items():
            if device_id in self._psus:
                continue
            candidate = None
            try:
                candidate = HCS34xx(info.device)
                candidate.get_display()
            except PowerSupplyError:
                if candidate is not None:
                    candidate.close()
                continue
            self._psus[device_id] = candidate
            self.device_added.emit("psu", device_id)
            self.psu_connected.emit(device_id, True)

    def _poll(self) -> None:
        for device_id, load in list(self._loads.items()):
            try:
                m = load.measure()
                self.load_measurement.emit(device_id, m.voltage, m.current, m.power)
                self.load_input_state.emit(device_id, load.get_input())
            except LoadError:
                load.close()
                del self._loads[device_id]
                self.load_connected.emit(device_id, False)
                self.device_removed.emit("load", device_id)

        for device_id, psu in list(self._psus.items()):
            try:
                d = psu.get_display()
                self.psu_measurement.emit(device_id, d.voltage, d.current, d.constant_current)
            except PowerSupplyError:
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

    # -- Netzteil: Steuerbefehle ---------------------------------------------

    @Slot(str, float)
    def set_psu_voltage(self, device_id: str, volts: float) -> None:
        self._guard_psu(device_id, lambda psu: psu.set_voltage(volts))

    @Slot(str, float)
    def set_psu_current(self, device_id: str, amps: float) -> None:
        self._guard_psu(device_id, lambda psu: psu.set_current(amps))

    @Slot(str, float)
    def set_psu_ovp(self, device_id: str, volts: float) -> None:
        self._guard_psu(device_id, lambda psu: psu.set_ovp(volts))

    @Slot(str, float)
    def set_psu_ocp(self, device_id: str, amps: float) -> None:
        self._guard_psu(device_id, lambda psu: psu.set_ocp(amps))

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
            if action in ("PSU_P1", "PSU_P2", "PSU_P3"):
                index = {"PSU_P1": 0, "PSU_P2": 1, "PSU_P3": 2}[action]
                return self._guard_psu(device_id, lambda psu: psu.recall_memory(index))
            return False, f"Unbekannte Aktion '{action}' fuer Netzteil"

        return False, f"Unbekanntes Geraet '{kind}'"

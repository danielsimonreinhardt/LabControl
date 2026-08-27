"""Hintergrund-Worker fuer die serielle Kommunikation mit Last und Netzteil.

Laeuft in einem eigenen QThread, damit blockierende Seriell-I/O (Timeouts
bei Verbindungsabbruch) die GUI nicht einfrieren laesst. Alle Zugriffe auf
die Geraete laufen ausschliesslich hier; die GUI kommuniziert nur ueber
Qt-Signale/Slots (automatisch thread-sicher als Queued Connections).
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from korad_kel102.driver import KoradKEL102, LoadError
from hcs34xx.driver import HCS34xx, PowerSupplyError, PowerSupplyValueError

POLL_INTERVAL_MS = 500
RECONNECT_INTERVAL_MS = 3000


class DeviceWorker(QObject):
    load_connected = Signal(bool)
    psu_connected = Signal(bool)
    load_measurement = Signal(float, float, float)  # voltage, current, power
    psu_measurement = Signal(float, float, bool)     # voltage, current, constant_current
    action_completed = Signal(bool, str)             # fuer Testablauf-Schritte: success, error

    def __init__(self) -> None:
        super().__init__()
        self._load: KoradKEL102 | None = None
        self._psu: HCS34xx | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._try_reconnect)

    @Slot()
    def start(self) -> None:
        self._try_reconnect()
        self._poll_timer.start(POLL_INTERVAL_MS)
        self._reconnect_timer.start(RECONNECT_INTERVAL_MS)

    def _try_reconnect(self) -> None:
        if self._load is None:
            try:
                self._load = KoradKEL102.open_first()
                self.load_connected.emit(True)
            except LoadError:
                self._load = None

        if self._psu is None:
            try:
                self._psu = HCS34xx.open_first()
                self.psu_connected.emit(True)
            except PowerSupplyError:
                self._psu = None

    def _poll(self) -> None:
        if self._load is not None:
            try:
                m = self._load.measure()
                self.load_measurement.emit(m.voltage, m.current, m.power)
            except LoadError:
                self._load.close()
                self._load = None
                self.load_connected.emit(False)

        if self._psu is not None:
            try:
                d = self._psu.get_display()
                self.psu_measurement.emit(d.voltage, d.current, d.constant_current)
            except PowerSupplyError:
                self._psu.close()
                self._psu = None
                self.psu_connected.emit(False)

    # -- gemeinsame Fehlerbehandlung ------------------------------------------

    def _guard_load(self, action: Callable[[KoradKEL102], None]) -> tuple[bool, str]:
        if self._load is None:
            return False, "Last nicht verbunden"
        try:
            action(self._load)
            return True, ""
        except LoadError as exc:
            self._load.close()
            self._load = None
            self.load_connected.emit(False)
            return False, str(exc)

    def _guard_psu(self, action: Callable[[HCS34xx], None]) -> tuple[bool, str]:
        if self._psu is None:
            return False, "Netzteil nicht verbunden"
        try:
            action(self._psu)
            return True, ""
        except PowerSupplyValueError as exc:
            # Ungueltiger Wert (z.B. < 1V) -- kein Verbindungsproblem, Port bleibt offen.
            return False, str(exc)
        except PowerSupplyError as exc:
            self._psu.close()
            self._psu = None
            self.psu_connected.emit(False)
            return False, str(exc)

    # -- Last: Steuerbefehle ------------------------------------------------

    @Slot(str)
    def set_load_function(self, mode: str) -> None:
        self._guard_load(lambda load: load.set_function(mode))

    @Slot(float)
    def set_load_current(self, amps: float) -> None:
        self._guard_load(lambda load: load.set_current(amps))

    @Slot(float)
    def set_load_voltage(self, volts: float) -> None:
        self._guard_load(lambda load: load.set_voltage(volts))

    @Slot(float)
    def set_load_resistance(self, ohms: float) -> None:
        self._guard_load(lambda load: load.set_resistance(ohms))

    @Slot(float)
    def set_load_power(self, watts: float) -> None:
        self._guard_load(lambda load: load.set_power(watts))

    @Slot(bool)
    def set_load_input(self, on: bool) -> None:
        self._guard_load(lambda load: load.set_input(on))

    # -- Netzteil: Steuerbefehle ---------------------------------------------

    @Slot(float)
    def set_psu_voltage(self, volts: float) -> None:
        self._guard_psu(lambda psu: psu.set_voltage(volts))

    @Slot(float)
    def set_psu_current(self, amps: float) -> None:
        self._guard_psu(lambda psu: psu.set_current(amps))

    @Slot(float)
    def set_psu_ovp(self, volts: float) -> None:
        self._guard_psu(lambda psu: psu.set_ovp(volts))

    @Slot(float)
    def set_psu_ocp(self, amps: float) -> None:
        self._guard_psu(lambda psu: psu.set_ocp(amps))

    @Slot(int)
    def recall_psu_memory(self, index: int) -> None:
        self._guard_psu(lambda psu: psu.recall_memory(index))

    # -- Testablauf: generischer Dispatch fuer einen Testschritt -------------

    @Slot(str, str, float)
    def execute_action(self, device: str, action: str, value: float) -> None:
        ok, message = self._dispatch_action(device, action, value)
        self.action_completed.emit(ok, message)

    def _dispatch_action(self, device: str, action: str, value: float) -> tuple[bool, str]:
        if device == "Last":
            if action in ("CURR", "VOLT", "RES", "POW"):
                ok, message = self._guard_load(lambda load: load.set_function(action))
                if not ok:
                    return ok, message
                setter_name = {
                    "CURR": "set_current",
                    "VOLT": "set_voltage",
                    "RES": "set_resistance",
                    "POW": "set_power",
                }[action]
                return self._guard_load(lambda load: getattr(load, setter_name)(value))
            if action == "OUT_ON":
                return self._guard_load(lambda load: load.set_input(True))
            if action == "OUT_OFF":
                return self._guard_load(lambda load: load.set_input(False))
            return False, f"Unbekannte Aktion '{action}' fuer Last"

        if device == "Netzteil":
            if action == "PSU_VOLT":
                return self._guard_psu(lambda psu: psu.set_voltage(value))
            if action == "PSU_CURR":
                return self._guard_psu(lambda psu: psu.set_current(value))
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

                return self._guard_psu(_output_on)
            if action == "PSU_OUT_OFF":
                return self._guard_psu(lambda psu: psu.set_current(0.0))
            if action in ("PSU_P1", "PSU_P2", "PSU_P3"):
                index = {"PSU_P1": 0, "PSU_P2": 1, "PSU_P3": 2}[action]
                return self._guard_psu(lambda psu: psu.recall_memory(index))
            return False, f"Unbekannte Aktion '{action}' fuer Netzteil"

        return False, f"Unbekanntes Geraet '{device}'"

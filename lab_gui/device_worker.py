"""Hintergrund-Worker fuer die serielle Kommunikation mit Last und Netzteil.

Laeuft in einem eigenen QThread, damit blockierende Seriell-I/O (Timeouts
bei Verbindungsabbruch) die GUI nicht einfrieren laesst. Alle Zugriffe auf
die Geraete laufen ausschliesslich hier; die GUI kommuniziert nur ueber
Qt-Signale/Slots (automatisch thread-sicher als Queued Connections).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from korad_kel102.driver import KoradKEL102, LoadError
from hcs34xx.driver import HCS34xx, PowerSupplyError

POLL_INTERVAL_MS = 500
RECONNECT_INTERVAL_MS = 3000


class DeviceWorker(QObject):
    load_connected = Signal(bool)
    psu_connected = Signal(bool)
    load_measurement = Signal(float, float, float)  # voltage, current, power
    psu_measurement = Signal(float, float, bool)     # voltage, current, constant_current

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

    # -- Last: Steuerbefehle ------------------------------------------------

    @Slot(str)
    def set_load_function(self, mode: str) -> None:
        if self._load is not None:
            try:
                self._load.set_function(mode)
            except LoadError:
                pass

    @Slot(float)
    def set_load_current(self, amps: float) -> None:
        if self._load is not None:
            try:
                self._load.set_current(amps)
            except LoadError:
                pass

    @Slot(float)
    def set_load_voltage(self, volts: float) -> None:
        if self._load is not None:
            try:
                self._load.set_voltage(volts)
            except LoadError:
                pass

    @Slot(float)
    def set_load_resistance(self, ohms: float) -> None:
        if self._load is not None:
            try:
                self._load.set_resistance(ohms)
            except LoadError:
                pass

    @Slot(float)
    def set_load_power(self, watts: float) -> None:
        if self._load is not None:
            try:
                self._load.set_power(watts)
            except LoadError:
                pass

    @Slot(bool)
    def set_load_input(self, on: bool) -> None:
        if self._load is not None:
            try:
                self._load.set_input(on)
            except LoadError:
                pass

    # -- Netzteil: Steuerbefehle ---------------------------------------------

    @Slot(float)
    def set_psu_voltage(self, volts: float) -> None:
        if self._psu is not None:
            try:
                self._psu.set_voltage(volts)
            except PowerSupplyError:
                pass

    @Slot(float)
    def set_psu_current(self, amps: float) -> None:
        if self._psu is not None:
            try:
                self._psu.set_current(amps)
            except PowerSupplyError:
                pass

    @Slot(float)
    def set_psu_ovp(self, volts: float) -> None:
        if self._psu is not None:
            try:
                self._psu.set_ovp(volts)
            except PowerSupplyError:
                pass

    @Slot(float)
    def set_psu_ocp(self, amps: float) -> None:
        if self._psu is not None:
            try:
                self._psu.set_ocp(amps)
            except PowerSupplyError:
                pass

    @Slot(int)
    def recall_psu_memory(self, index: int) -> None:
        if self._psu is not None:
            try:
                self._psu.recall_memory(index)
            except PowerSupplyError:
                pass

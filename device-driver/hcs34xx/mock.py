"""Simulierter Ersatz fuer HCS34xx zum Testen der GUI ohne angeschlossene Hardware.

Bildet dieselbe oeffentliche Schnittstelle wie HCS34xx nach (siehe driver.py),
haelt Sollwerte/Schutzschwellen aber nur im Speicher statt sie ueber eine
serielle Verbindung an ein reales Geraet zu senden. CC/CV-Verhalten unter Last
wird nicht simuliert -- die "Anzeige" liefert schlicht die Sollwerte zurueck.
"""
from __future__ import annotations

from hcs34xx.driver import Display, MIN_VOLTAGE, PowerSupplyValueError

DEFAULT_MAX_VOLTAGE = 60.0
DEFAULT_MAX_CURRENT = 10.0


class MockHCS34xx:
    def __init__(self, max_voltage: float = DEFAULT_MAX_VOLTAGE, max_current: float = DEFAULT_MAX_CURRENT):
        self._max_voltage = max_voltage
        self._max_current = max_current
        self._voltage_set = 0.0
        self._current_set = 0.0
        self._ovp = max_voltage
        self._ocp = max_current
        self._memory: list[tuple[float, float]] = [(0.0, 0.0)] * 3

    def close(self) -> None:
        pass

    def __enter__(self) -> "MockHCS34xx":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- limits ------------------------------------------------------------

    def get_max(self) -> tuple[float, float]:
        return self._max_voltage, self._max_current

    # -- setpoints -----------------------------------------------------------

    def set_voltage(self, volts: float) -> None:
        if volts < MIN_VOLTAGE:
            raise PowerSupplyValueError(
                f"Spannung {volts}V unterschreitet das Minimum von {MIN_VOLTAGE}V."
            )
        self._voltage_set = volts

    def set_current(self, amps: float) -> None:
        self._current_set = amps

    def get_setpoint(self) -> tuple[float, float]:
        return self._voltage_set, self._current_set

    # -- live display -----------------------------------------------------

    def get_display(self) -> Display:
        return Display(voltage=self._voltage_set, current=self._current_set, constant_current=False)

    # -- protection thresholds --------------------------------------------

    def get_ovp(self) -> float:
        return self._ovp

    def set_ovp(self, volts: float) -> None:
        if volts < MIN_VOLTAGE:
            raise PowerSupplyValueError(
                f"OVP-Schwelle {volts}V unterschreitet das Minimum von {MIN_VOLTAGE}V."
            )
        self._ovp = volts

    def get_ocp(self) -> float:
        return self._ocp

    def set_ocp(self, amps: float) -> None:
        self._ocp = amps

    # -- memory presets (P1/P2/P3) ------------------------------------------

    def get_memory(self) -> list[tuple[float, float]]:
        return list(self._memory)

    def set_memory(self, presets: list[tuple[float, float]]) -> None:
        if len(presets) != 3:
            raise ValueError("Es muessen genau 3 Presets angegeben werden")
        self._memory = list(presets)

    def recall_memory(self, index: int) -> None:
        if index not in (0, 1, 2):
            raise ValueError("index muss 0, 1 oder 2 sein")
        self._voltage_set, self._current_set = self._memory[index]

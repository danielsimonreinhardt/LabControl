"""Simulierter Ersatz fuer KoradKEL102 zum Testen der GUI ohne angeschlossene
Hardware (siehe hcs34xx/mock.py fuer dasselbe Prinzip beim Netzteil).

Bildet dieselbe oeffentliche Schnittstelle wie KoradKEL102 nach (siehe
driver.py), haelt Sollwerte aber nur im Speicher statt sie ueber eine
serielle Verbindung an ein reales Geraet zu senden. Reales
Entladeverhalten (Spannungsabfall eines angeschlossenen Akkus etc.) wird
nicht simuliert -- measure() liefert schlicht die zuletzt gesetzten
Sollwerte zurueck, unabhaengig vom aktiven Modus. Das reicht aus, um im
Testcase-Editor While/If-Bedingungen auf Last-Messwerte (siehe
condition_dialog.py) ohne angeschlossene Last durchzuspielen.
"""
from __future__ import annotations

from korad_kel102.driver import FUNCTIONS, Measurement


class MockKoradKEL102:
    def __init__(self) -> None:
        self._function = "CURR"
        self._input_on = False
        self._voltage_set = 0.0
        self._current_set = 0.0
        self._resistance_set = 0.0
        self._power_set = 0.0

    def close(self) -> None:
        pass

    def __enter__(self) -> "MockKoradKEL102":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def identify(self) -> str:
        return "MOCK,KEL102,SIMULATION,1.0"

    # -- input on/off ------------------------------------------------------

    def set_input(self, on: bool) -> None:
        self._input_on = on

    def get_input(self) -> bool:
        return self._input_on

    # -- function / mode ---------------------------------------------------

    def set_function(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in FUNCTIONS:
            raise ValueError(f"Unbekannter Modus {mode!r}, erlaubt: {FUNCTIONS}")
        self._function = mode

    def get_function(self) -> str:
        return self._function

    # -- setpoints -----------------------------------------------------------

    def set_voltage(self, volts: float) -> None:
        self._voltage_set = volts

    def get_voltage_setpoint(self) -> float:
        return self._voltage_set

    def set_current(self, amps: float) -> None:
        self._current_set = amps

    def get_current_setpoint(self) -> float:
        return self._current_set

    def set_resistance(self, ohms: float) -> None:
        self._resistance_set = ohms

    def get_resistance_setpoint(self) -> float:
        return self._resistance_set

    def set_power(self, watts: float) -> None:
        self._power_set = watts

    def get_power_setpoint(self) -> float:
        return self._power_set

    # -- live measurement ------------------------------------------------------

    def measure_voltage(self) -> float:
        return self._voltage_set

    def measure_current(self) -> float:
        return self._current_set

    def measure_power(self) -> float:
        return self._voltage_set * self._current_set

    def measure(self) -> Measurement:
        return Measurement(
            voltage=self.measure_voltage(),
            current=self.measure_current(),
            power=self.measure_power(),
        )

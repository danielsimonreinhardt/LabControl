"""Simulierter Ersatz fuer MicroHIL zum Testen der GUI ohne angeschlossene
Hardware (siehe hcs34xx/mock.py und korad_kel102/mock.py fuer dasselbe
Prinzip bei den anderen Geraeten).

Bildet dieselbe oeffentliche Schnittstelle wie MicroHIL nach (siehe
driver.py), haelt alle Zustaende aber nur im Speicher statt sie ueber eine
serielle Verbindung an ein reales Geraet zu senden. Digitaleingaenge (IN1-8)
sind rein simulatorseitig -- am realen Geraet werden sie von aussen
angelegt, hier gibt es dafuer set_input() zum manuellen Vorgeben in Tests/
GUI-Vorschau. Die PWM/OUT-Verriegelung (siehe driver.INTERLOCKED_CHANNELS)
wird nachgebildet, das Klemmverhalten von AOUT/PWM ebenfalls (driver.py
klemmt bereits client-seitig, hier zusaetzlich zur Sicherheit falls direkt
gegen den Mock statt den Treiber getestet wird).
"""
from __future__ import annotations

from microhil.driver import (
    AIN_COUNT,
    AOUT_COUNT,
    AOUT_MAX_MV,
    AOUT_RAW_MAX_MV,
    CURR_COUNT,
    INTERLOCKED_CHANNELS,
    IN_COUNT,
    OUT_COUNT,
    PWM_COUNT,
    PWM_MAX_PERMILLE,
    PWR12_COUNT,
    RELAY_COUNT,
    Pwr12Channel,
    parse_idn_fields,
)


class MockMicroHIL:
    def __init__(self) -> None:
        self._relays = [False] * RELAY_COUNT
        self._outputs = [False] * OUT_COUNT
        self._inputs = [False] * IN_COUNT
        self._analog_out = [0] * AOUT_COUNT
        self._analog_in = [0] * AIN_COUNT
        self._pwr12 = [False] * PWR12_COUNT
        self._current_ma = [0] * CURR_COUNT
        self._current_limits = [1200] * PWR12_COUNT
        self._pwr12_fault = [0] * PWR12_COUNT
        self._pwm = [0] * PWM_COUNT

    def close(self) -> None:
        pass

    def __enter__(self) -> "MockMicroHIL":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def identify(self) -> str:
        return "microHIL,fw=MOCK"

    def get_firmware_version(self) -> str | None:
        return parse_idn_fields(self.identify()).get("fw")

    # -- relays (1-4) --------------------------------------------------------

    def set_relay(self, channel: int, on: bool) -> None:
        self._relays[channel - 1] = on

    def get_relay(self, channel: int) -> bool:
        return self._relays[channel - 1]

    # -- digital outputs (1-8) -----------------------------------------------

    def set_output(self, channel: int, on: bool) -> None:
        self._outputs[channel - 1] = on
        if on and channel <= INTERLOCKED_CHANNELS:
            self._pwm[channel - 1] = 0

    def get_output(self, channel: int) -> bool:
        return self._outputs[channel - 1]

    # -- digital inputs (1-8, read-only am echten Geraet) ---------------------

    def set_input(self, channel: int, high: bool) -> None:
        """Nur im Mock vorhanden: Digitaleingang von aussen vorgeben (am
        realen Geraet legt das die angeschlossene Beschaltung fest)."""
        self._inputs[channel - 1] = high

    def get_input(self, channel: int) -> bool:
        return self._inputs[channel - 1]

    def get_inputs(self) -> list[bool]:
        return list(self._inputs)

    # -- analog output / DAC (1-2) --------------------------------------------

    def set_analog_output(self, channel: int, millivolts: int) -> None:
        self._analog_out[channel - 1] = max(0, min(AOUT_MAX_MV, millivolts))

    def set_analog_output_raw(self, channel: int, millivolts: int) -> None:
        self._analog_out[channel - 1] = max(0, min(AOUT_RAW_MAX_MV, millivolts))

    # -- analog inputs (1-4, read-only am echten Geraet) -----------------------

    def set_analog_input(self, channel: int, millivolts: int) -> None:
        """Nur im Mock vorhanden, analog zu set_input()."""
        self._analog_in[channel - 1] = millivolts

    def get_analog_input(self, channel: int) -> int:
        return self._analog_in[channel - 1]

    def get_analog_input_raw(self, channel: int) -> int:
        return self._analog_in[channel - 1]

    # -- switchable 12V outputs with current sense (1-2) ----------------------

    def set_pwr12(self, channel: int, on: bool) -> None:
        self._pwr12[channel - 1] = on

    def get_pwr12(self, channel: int) -> bool:
        return self._pwr12[channel - 1]

    def set_current_ma(self, channel: int, milliamps: int) -> None:
        """Nur im Mock vorhanden, analog zu set_input()."""
        self._current_ma[channel - 1] = milliamps

    def get_current_ma(self, channel: int) -> int:
        return self._current_ma[channel - 1]

    def get_current_sense_raw_mv(self, channel: int) -> int:
        """Nur im Mock: liefert denselben Wert wie get_current_ma() zurueck
        -- der Mock simuliert keine eigene Roh-/Kalibrierabweichung."""
        return self._current_ma[channel - 1]

    def set_pwr12_fault(self, channel: int, fault: int) -> None:
        """Nur im Mock vorhanden, analog zu set_input() -- erlaubt, einen
        PWR12FLT?-Zustand fuer GUI-Tests vorzugeben."""
        self._pwr12_fault[channel - 1] = fault

    def get_pwr12_fault(self, channel: int) -> int:
        return self._pwr12_fault[channel - 1]

    def get_pwr12_channel(self, channel: int) -> Pwr12Channel:
        return Pwr12Channel(
            enabled=self.get_pwr12(channel),
            current_ma=self.get_current_ma(channel),
            fault=self.get_pwr12_fault(channel),
        )

    def set_current_limit(self, channel: int, milliamps: int) -> None:
        """Haelt den Wert nur im Speicher -- der Mock bildet KEINE echte
        Ueberstrom-Abschaltlogik nach (siehe driver.py:
        set_current_limit()-Docstring fuer das reale Verhalten inkl.
        Verriegelung), erlaubt aber die GUI-Anbindung (Eingabefeld ->
        Signal -> Treiber) im Simulationsmodus durchzuklicken. Fuer einen
        simulierten Fault-Zustand siehe set_pwr12_fault()."""
        self._current_limits[channel - 1] = milliamps

    def get_current_limit(self, channel: int) -> int:
        return self._current_limits[channel - 1]

    # -- PWM (1-4) -------------------------------------------------------------

    def set_pwm(self, channel: int, permille: int) -> None:
        clamped = max(0, min(PWM_MAX_PERMILLE, permille))
        self._pwm[channel - 1] = clamped
        if clamped > 0 and channel <= INTERLOCKED_CHANNELS:
            self._outputs[channel - 1] = False

    def get_pwm(self, channel: int) -> int:
        return self._pwm[channel - 1]

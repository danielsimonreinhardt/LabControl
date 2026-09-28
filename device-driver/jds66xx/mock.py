"""Simulierter Ersatz fuer JDS66xx zum Testen der GUI ohne angeschlossene
Hardware (siehe korad_kel102/mock.py fuer dasselbe Prinzip bei der Last).

Bildet dieselbe oeffentliche Schnittstelle wie JDS66xx nach (siehe driver.py),
haelt den Zustand aber nur im Speicher. Werte werden mit denselben check_*-
Funktionen wie im echten Treiber geprueft, damit der Mock dieselben Eingaben
ablehnt (FunctionGeneratorValueError) -- ein Mock, der alles schluckt, wuerde
Fehler verstecken, die erst an echter Hardware auffallen. Nicht simuliert wird
die waveformabhaengige Frequenzklemmung des Geraets.
"""
from __future__ import annotations

from jds66xx.driver import (
    CHANNELS,
    WAVE_SINE,
    ChannelState,
    State,
    check_amplitude,
    check_channel,
    check_duty,
    check_frequency,
    check_offset,
    check_phase,
    check_waveform,
)


class MockJDS66xx:
    def __init__(self) -> None:
        self._outputs = [False, False]
        self._channels = {
            ch: ChannelState(waveform=WAVE_SINE, frequency=1000.0, amplitude=1.0, offset=0.0, duty=50.0)
            for ch in CHANNELS
        }
        self._phase = 0.0

    def close(self) -> None:
        pass

    def __enter__(self) -> "MockJDS66xx":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def probe(self) -> bool:
        return True

    def identify(self) -> str:
        return "MOCK JDS2915 SIMULATION"

    # -- Ausgaenge ---------------------------------------------------------------

    def get_outputs(self) -> tuple[bool, bool]:
        return self._outputs[0], self._outputs[1]

    def set_outputs(self, ch1: bool, ch2: bool) -> None:
        self._outputs = [bool(ch1), bool(ch2)]

    def get_output(self, channel: int) -> bool:
        check_channel(channel)
        return self._outputs[channel - 1]

    def set_output(self, channel: int, on: bool) -> None:
        check_channel(channel)
        self._outputs[channel - 1] = bool(on)

    # -- Signalparameter ---------------------------------------------------------

    def get_waveform(self, channel: int) -> int:
        check_channel(channel)
        return self._channels[channel].waveform

    def set_waveform(self, channel: int, code: int) -> None:
        check_channel(channel)
        check_waveform(code)
        self._channels[channel].waveform = code

    def get_frequency(self, channel: int) -> float:
        check_channel(channel)
        return self._channels[channel].frequency

    def set_frequency(self, channel: int, hertz: float) -> None:
        check_channel(channel)
        check_frequency(hertz)
        # Wie das Geraet auf 0,01 Hz gerundet.
        self._channels[channel].frequency = round(hertz * 100) / 100.0

    def get_amplitude(self, channel: int) -> float:
        check_channel(channel)
        return self._channels[channel].amplitude

    def set_amplitude(self, channel: int, volts: float) -> None:
        check_channel(channel)
        check_amplitude(volts)
        self._channels[channel].amplitude = round(volts * 1000) / 1000.0

    def get_offset(self, channel: int) -> float:
        check_channel(channel)
        return self._channels[channel].offset

    def set_offset(self, channel: int, volts: float) -> None:
        check_channel(channel)
        check_offset(volts)
        self._channels[channel].offset = round(volts * 100) / 100.0

    def get_duty(self, channel: int) -> float:
        check_channel(channel)
        return self._channels[channel].duty

    def set_duty(self, channel: int, percent: float) -> None:
        check_channel(channel)
        check_duty(percent)
        self._channels[channel].duty = round(percent * 10) / 10.0

    def get_phase(self) -> float:
        return self._phase

    def set_phase(self, degrees: float) -> None:
        check_phase(degrees)
        self._phase = (round(degrees * 10) % 3600) / 10.0

    # -- Gesamtzustand -----------------------------------------------------------

    def get_channel_state(self, channel: int) -> ChannelState:
        check_channel(channel)
        c = self._channels[channel]
        return ChannelState(c.waveform, c.frequency, c.amplitude, c.offset, c.duty)

    def get_state(self) -> State:
        return State(
            outputs=self.get_outputs(),
            channels=(self.get_channel_state(1), self.get_channel_state(2)),
            phase=self._phase,
        )

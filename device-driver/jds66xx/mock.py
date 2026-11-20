"""Simulierter Ersatz fuer JDS66xx zum Testen der GUI ohne angeschlossene
Hardware (siehe korad_kel102/mock.py fuer dasselbe Prinzip bei der Last).

Bildet dieselbe oeffentliche Schnittstelle wie JDS66xx nach (siehe driver.py),
haelt den Zustand aber nur im Speicher. Werte werden mit denselben check_*-
Funktionen wie im echten Treiber geprueft, damit der Mock dieselben Eingaben
ablehnt (FunctionGeneratorValueError) -- ein Mock, der alles schluckt, wuerde
Fehler verstecken, die erst an echter Hardware auffallen. Nicht simuliert wird
die waveformabhaengige Frequenzklemmung des Geraets.

Nachgebildet (seit 1.0.1, PI-75 a/PI-80 a, Messung am JDS2915 vom 17.11.2026):
was am Ausgang anliegt -- output_dc_voltage() in DC (Offset + Amplitude/2,
bei Amplitude genau 0 nur etwa 1/10 des Offsets) und effective_duty() (der
Tastgrad wirkt nur bei Puls, Rechteck bleibt 50 %). Wie das Geraet nimmt der
Mock jeden Tastgrad an, auch wenn er nicht wirkt.
"""
from __future__ import annotations

from jds66xx.driver import (
    CHANNELS,
    WAVE_DC,
    WAVE_PULSE,
    WAVE_SINE,
    WAVE_SQUARE,
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

    # -- Was am Ausgang anliegt (Messung 17.11.2026, nur Mock) ----------------------

    # Amplitude genau 0 in DC: Offset 2,5 V ergab 0,22 V
    DC_ZERO_AMPLITUDE_FACTOR = 0.22 / 2.5

    def output_dc_voltage(self, channel: int) -> float | None:
        """Gleichspannung am Ausgang (Leerlauf) in DC, None in anderen Formen. Wie gemessen: Offset +
        Amplitude/2, bei Amplitude genau 0 kommt der Offset nur zu etwa 1/10 an."""
        check_channel(channel)
        c = self._channels[channel]
        if c.waveform != WAVE_DC:
            return None
        if c.amplitude == 0.0:
            return round(c.offset * self.DC_ZERO_AMPLITUDE_FACTOR, 4)
        return round(c.offset + c.amplitude / 2.0, 4)

    def effective_duty(self, channel: int) -> float | None:
        """Tastgrad am Ausgang: der eingestellte nur bei Puls, Rechteck immer 50 %, sonst None."""
        check_channel(channel)
        c = self._channels[channel]
        if c.waveform == WAVE_PULSE:
            return c.duty
        if c.waveform == WAVE_SQUARE:
            return 50.0
        return None

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

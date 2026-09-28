"""Gemeinsame Typen/Konstanten fuer driver.py und mock.py.

Bewusst ausgelagert: driver.py loest beim Import eine DLL-Suche aus (siehe
_ensure_dll_on_path()) -- mock.py muss aber auch auf Systemen ohne
installierte PicoScope-Software funktionieren (globaler Simulationsmodus).
"""
from __future__ import annotations

from dataclasses import dataclass, field

CHANNEL_MAP = {"A": 0, "B": 1}
VOLTAGE_RANGE_CODES = {
    "20mV": 1, "50mV": 2, "100mV": 3, "200mV": 4, "500mV": 5,
    "1V": 6, "2V": 7, "5V": 8, "10V": 9, "20V": 10,
}


# Vollausschlag +-V je Bereichscode (ps2000.PICO_VOLTAGE_RANGE ohne DLL-Import).
RANGE_VOLTS = {
    1: 0.02, 2: 0.05, 3: 0.1, 4: 0.2, 5: 0.5, 6: 1.0, 7: 2.0, 8: 5.0, 9: 10.0, 10: 20.0,
}
# ps2000.h: PS2000_CHANNEL_A..D = 0..3, PS2000_EXTERNAL = 4, PS2000_NONE = 5.
TRIGGER_SOURCE_NONE = 5
# ps2000.h: PS2000_RISING = 0, PS2000_FALLING = 1.
DIRECTION_CODES = {"rising": 0, "falling": 1}
MAX_ADC = 32767


class PicoScope2000Error(RuntimeError):
    """Fehler bei der Kommunikation mit dem PicoScope."""


class PicoScope2000TriggerTimeout(PicoScope2000Error):
    """Innerhalb der Wartezeit kam kein Trigger (Erfassung abgebrochen)."""


@dataclass
class ChannelConfig:
    enabled: bool
    dc: bool
    range_code: int


@dataclass
class TriggerConfig:
    """Parameter von ps2000_set_trigger in Gerätecodes.

    delay_pct: Lage des Triggers im Block, -100..0 (negativ = Pre-Trigger:
    -20 heisst, 20 % des Blocks liegen vor dem Trigger).
    auto_trigger_ms: 0 = unbegrenzt auf Trigger warten, sonst nach dieser
    Zeit trotzdem erfassen (max. 32767, int16).
    """
    source: int
    threshold_adc: int = 0
    direction: int = 0
    delay_pct: int = 0
    auto_trigger_ms: int = 0


@dataclass
class RawBlock:
    sample_interval_ns: float
    # ADC-Werte je aktivem Kanal ("A"/"B"), Bereich -MAX_ADC..+MAX_ADC.
    adc: dict = field(default_factory=dict)
    # Übersteuerung je Kanal laut Gerät.
    overflow: dict = field(default_factory=dict)


@dataclass
class UnitInfo:
    variant: str
    serial: str


@dataclass
class BlockCapture:
    channel: str
    voltage_range: str
    sample_interval_ns: float
    millivolts: list = field(default_factory=list)

    @property
    def time_ns(self) -> list:
        return [i * self.sample_interval_ns for i in range(len(self.millivolts))]


@dataclass
class Measurement:
    """Aus einer BlockCapture abgeleitete Kennwerte (mV) -- fuer den
    Testablauf (PICO_VMAX/PICO_VMIN/PICO_VPP/PICO_VRMS, siehe
    testcase_model.PICO_ACTIONS): eine einzelne Erfassung reicht fuer alle
    vier Kennwerte, PicoScope2000.measure() berechnet sie deshalb gemeinsam
    statt pro Aktion neu zu erfassen (waere sonst 4x ~4,5s Verbinden/Trennen
    fuer einen einzigen Testschritt mit mehreren Pruefungen)."""
    channel: str
    voltage_range: str
    vmax: float
    vmin: float
    vpp: float
    vrms: float

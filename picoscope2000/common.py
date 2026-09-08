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


class PicoScope2000Error(RuntimeError):
    """Fehler bei der Kommunikation mit dem PicoScope."""


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

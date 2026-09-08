"""Simulierter Ersatz fuer PicoScope2000 (driver.py) -- gleiche oeffentliche
Schnittstelle, keine echte Hardware/DLL noetig. Erzeugt eine synthetische
Sinuskurve statt echter Messwerte, fuer GUI-Entwicklung/-Tests und den
globalen Simulationsmodus.
"""
from __future__ import annotations

import math

from picoscope2000.common import (
    VOLTAGE_RANGE_CODES,
    BlockCapture,
    Measurement,
    PicoScope2000Error,
    UnitInfo,
)

SIM_SERIAL = "SIM-2204A-0001"
SIM_VARIANT = "2204A"


class MockPicoScope2000:
    def __init__(self):
        self._closed = False

    @classmethod
    def discover(cls) -> list:
        return [SIM_SERIAL]

    @classmethod
    def open_first(cls) -> "MockPicoScope2000":
        return cls()

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "MockPicoScope2000":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def get_info(self) -> UnitInfo:
        return UnitInfo(variant=SIM_VARIANT, serial=SIM_SERIAL)

    def capture_block(
        self,
        channel: str = "A",
        voltage_range: str = "2V",
        num_samples: int = 2000,
        timebase: int = 8,
    ) -> BlockCapture:
        if voltage_range not in VOLTAGE_RANGE_CODES:
            raise PicoScope2000Error(f"Unbekannter Spannungsbereich: {voltage_range!r}")

        sample_interval_ns = 2.0 ** timebase  # grobe Naeherung, reicht fuer Simulation
        amplitude_mv = 500.0
        frequency_hz = 1_000.0
        millivolts = [
            amplitude_mv * math.sin(2 * math.pi * frequency_hz * (i * sample_interval_ns * 1e-9))
            for i in range(num_samples)
        ]
        return BlockCapture(
            channel=channel,
            voltage_range=voltage_range,
            sample_interval_ns=sample_interval_ns,
            millivolts=millivolts,
        )

    def measure(
        self,
        channel: str = "A",
        voltage_range: str = "2V",
        num_samples: int = 2000,
        timebase: int = 8,
    ) -> Measurement:
        capture = self.capture_block(
            channel=channel, voltage_range=voltage_range,
            num_samples=num_samples, timebase=timebase,
        )
        values = capture.millivolts
        vmax = max(values)
        vmin = min(values)
        vrms = (sum(v * v for v in values) / len(values)) ** 0.5
        return Measurement(
            channel=channel,
            voltage_range=voltage_range,
            vmax=vmax,
            vmin=vmin,
            vpp=vmax - vmin,
            vrms=vrms,
        )

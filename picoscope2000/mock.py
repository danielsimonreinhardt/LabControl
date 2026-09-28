"""Simulierter Ersatz fuer PicoScope2000 (driver.py) -- gleiche oeffentliche
Schnittstelle, keine echte Hardware/DLL noetig. Erzeugt eine synthetische
Sinuskurve statt echter Messwerte, fuer GUI-Entwicklung/-Tests und den
globalen Simulationsmodus.
"""
from __future__ import annotations

import math

from picoscope2000.common import (
    MAX_ADC,
    RANGE_VOLTS,
    TRIGGER_SOURCE_NONE,
    VOLTAGE_RANGE_CODES,
    BlockCapture,
    ChannelConfig,
    Measurement,
    PicoScope2000Error,
    PicoScope2000TriggerTimeout,
    RawBlock,
    TriggerConfig,
    UnitInfo,
)

SIM_SERIAL = "SIM-2204A-0001"
SIM_VARIANT = "2204A"

# Nachbildung des 2204A-Speichers fuer get_timebase(): 8 kS, von den aktiven
# Kanaelen geteilt. Das echte Geraet meldet seinen Wert selbst (max_samples).
SIM_MEMORY = 8000
SIM_MAX_TIMEBASE = 23


class SquareWave:
    """Rechteck mit linearen Flanken endlicher Dauer (Spannung am Scope-Eingang)."""

    def __init__(self, frequency_hz=1_000.0, low_v=0.0, high_v=3.3, duty=0.5, edge_s=50e-9):
        self.frequency_hz = frequency_hz
        self.low_v, self.high_v = low_v, high_v
        self.duty, self.edge_s = duty, edge_s

    def __call__(self, t):
        import numpy as np
        phase = np.mod(t * self.frequency_hz, 1.0)
        w = self.edge_s * self.frequency_hz
        swing = self.high_v - self.low_v
        v = np.full(phase.shape, self.low_v, dtype=float)
        v[phase < w] = self.low_v + swing * phase[phase < w] / w
        high = (phase >= w) & (phase < self.duty)
        v[high] = self.high_v
        fall = (phase >= self.duty) & (phase < self.duty + w)
        v[fall] = self.high_v - swing * (phase[fall] - self.duty) / w
        return v


class SineWave:
    def __init__(self, frequency_hz=1_000.0, amplitude_v=1.0, offset_v=0.0, phase_deg=-90.0):
        self.frequency_hz, self.amplitude_v = frequency_hz, amplitude_v
        self.offset_v, self.phase_deg = offset_v, phase_deg

    def __call__(self, t):
        import numpy as np
        return self.offset_v + self.amplitude_v * np.sin(
            2 * np.pi * self.frequency_hz * t + np.radians(self.phase_deg))


class Constant:
    def __init__(self, value_v=0.0):
        self.value_v = value_v

    def __call__(self, t):
        import numpy as np
        return np.full(np.shape(t), self.value_v, dtype=float)


def default_signals() -> dict:
    """Kanal A: 1-kHz-Rechteck 0..3,3 V, 50 ns Flanken. Kanal B: 1-kHz-Sinus +-1 V."""
    return {"A": SquareWave(), "B": SineWave()}


class MockPicoScope2000:
    # Fuer Pruefskripte austauschbar: Kanal -> Funktion t[s] -> V am Eingang.
    signals: dict = default_signals()
    noise_v: float = 0.005
    # Bereich, in dem nach einer Trigger-Flanke gesucht wird (2 Perioden bei 1 kHz).
    trigger_search_s: float = 2e-3

    def __init__(self):
        self._closed = False
        self._channels: dict[str, ChannelConfig] = {}
        self._rng = None

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

    # -- scope_api-Schnittstelle (siehe driver.py) --------------------------------

    def configure_channels(self, channels: dict[str, ChannelConfig]) -> None:
        self._channels = {name: cfg for name, cfg in channels.items() if cfg.enabled}

    def get_timebase(self, timebase: int, num_samples: int) -> tuple[float, int] | None:
        n_enabled = max(1, len(self._channels))
        if timebase < 0 or timebase > SIM_MAX_TIMEBASE:
            return None
        if timebase == 0 and n_enabled > 1:
            return None  # wie beim echten Geraet: 100 MS/s nur mit einem Kanal
        max_samples = SIM_MEMORY // n_enabled
        if num_samples > max_samples:
            return None
        return 10.0 * 2 ** timebase, max_samples

    def run_block_raw(self, trigger: TriggerConfig, timebase: int, num_samples: int,
                      wait_s: float) -> RawBlock:
        import numpy as np

        if not self._channels:
            raise PicoScope2000Error("Kein Kanal aktiv (configure_channels() vorher aufrufen).")
        timing = self.get_timebase(timebase, num_samples)
        if timing is None:
            raise PicoScope2000Error(f"Timebase {timebase} mit {num_samples} Samples ungueltig.")
        dt = timing[0] * 1e-9
        if self._rng is None:
            self._rng = np.random.default_rng(2204)

        trigger_index = int(round(num_samples * -trigger.delay_pct / 100.0))
        t_trigger = None
        names = {0: "A", 1: "B"}
        if trigger.source != TRIGGER_SOURCE_NONE:
            source = names.get(trigger.source)
            cfg = self._channels.get(source)
            if cfg is None:
                raise PicoScope2000Error(f"Trigger-Quelle {source!r} ist nicht aktiv.")
            level = trigger.threshold_adc / MAX_ADC * RANGE_VOLTS[cfg.range_code]
            grid = np.linspace(0.0, self.trigger_search_s, 200_001)
            v = self._ideal(source, grid, cfg)
            if trigger.direction == 0:
                hits = np.nonzero((v[:-1] <= level) & (v[1:] > level))[0]
            else:
                hits = np.nonzero((v[:-1] >= level) & (v[1:] < level))[0]
            if hits.size:
                i = int(hits[0])
                t_trigger = grid[i] + (level - v[i]) / (v[i + 1] - v[i]) * (grid[i + 1] - grid[i])
            elif trigger.auto_trigger_ms == 0:
                raise PicoScope2000TriggerTimeout(f"Kein Trigger innerhalb von {wait_s:.1f} s.")
        t0 = (t_trigger if t_trigger is not None else 1.234e-4) - trigger_index * dt
        t = t0 + np.arange(num_samples) * dt

        adc, overflow = {}, {}
        for name, cfg in self._channels.items():
            full_scale = RANGE_VOLTS[cfg.range_code]
            v = self._ideal(name, t, cfg) + self._rng.normal(0.0, self.noise_v, num_samples)
            overflow[name] = bool(np.any(np.abs(v) > full_scale))
            adc[name] = np.clip(np.round(v / full_scale * MAX_ADC), -MAX_ADC, MAX_ADC).astype(int).tolist()
        return RawBlock(sample_interval_ns=timing[0], adc=adc, overflow=overflow)

    def _ideal(self, name: str, t, cfg: ChannelConfig):
        signal = self.signals.get(name) or Constant(0.0)
        v = signal(t)
        if not cfg.dc:
            # AC-Kopplung grob: Gleichanteil ueber eine Periode des Suchbereichs entfernen
            import numpy as np
            v = v - float(np.mean(signal(np.linspace(0.0, self.trigger_search_s, 20_001))))
        return v

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

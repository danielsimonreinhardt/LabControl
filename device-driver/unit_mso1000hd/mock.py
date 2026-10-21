"""Simuliertes UNI-T MSO1000HD auf SCPI-Ebene.

Anders als die übrigen Mocks (eigene Klasse mit gleicher Schnittstelle) ist
hier das *Gerät* nachgebildet: ``SimulatedInstrument`` versteht die Teilmenge
der SCPI-Befehle, die driver.py benutzt, und antwortet wie das Handbuch es
beschreibt (Datenblöcke ``#9...``, Trigger-Status, Speicher in Stücken). So
läuft der echte Treiber- und Adaptercode auch im Simulationsmodus und in den
Prüfskripten mit -- nur Transport und Messwerte sind erfunden.

Zeitmodell (Annahme, am Gerät zu prüfen, siehe README.md):
- Bildschirm 10 Teilungen breit, Fenster um TIMebase:OFFSet zentriert;
  t = 0 ist der Trigger-Zeitpunkt.
- Abtastrate = min(Speichertiefe / Fensterdauer, 2,5 GS/s geteilt durch die
  Zahl der aktiven Kanäle, gerundet auf 1/2/4).
- Vertikal 8 Teilungen (±4), CHANnel:OFFSet verschiebt die Kurve: die
  Bildschirmmitte liegt bei -OFFSet. Eingang begrenzt bei ±5 Teilungen.
"""
from __future__ import annotations

import math
import re

import numpy as np

SIM_MODEL = "MSO1154HD-S"
SIM_SERIAL = "SIM-MSO1154HD-0001"
SIM_FIRMWARE = "1.00.0009"
SIM_IDN = f"UNI-T Technologies,{SIM_MODEL},{SIM_SERIAL},{SIM_FIRMWARE}"

MAX_RATE = 2.5e9
DEPTHS = {"AUTO": 250_000, "25K": 25_000, "250K": 250_000, "500K": 500_000,
          "5M": 5_000_000, "50M": 50_000_000, "100M": 100_000_000}
# Volt/Teilung am Eingang (Tastkopf 1:1), 1-2-5.
V_SCALES = tuple(round(m * 10.0 ** e, 12) for e in range(-3, 2) for m in (1, 2, 5) if m * 10.0 ** e <= 10)
T_SCALES = tuple(round(m * 10.0 ** e, 15) for e in range(-9, 3) for m in (1, 2, 5) if m * 10.0 ** e <= 1000)
NOISE_DIV = 0.002   # Rauschen in Teilungen (1 sigma)


class SquareWave:
    def __init__(self, frequency_hz=1_000.0, low_v=0.0, high_v=3.3, duty=0.5, edge_s=5e-9):
        self.frequency_hz, self.low_v, self.high_v = frequency_hz, low_v, high_v
        self.duty, self.edge_s = duty, edge_s

    def __call__(self, t):
        phase = np.mod(t * self.frequency_hz, 1.0)
        w = self.edge_s * self.frequency_hz
        swing = self.high_v - self.low_v
        v = np.full(np.shape(t), self.low_v, dtype=float)
        rise = phase < w
        v[rise] = self.low_v + swing * phase[rise] / w
        v[(phase >= w) & (phase < self.duty)] = self.high_v
        fall = (phase >= self.duty) & (phase < self.duty + w)
        v[fall] = self.high_v - swing * (phase[fall] - self.duty) / w
        return v


class SineWave:
    def __init__(self, frequency_hz=1_000.0, amplitude_v=1.0, offset_v=0.0, phase_deg=-90.0):
        self.frequency_hz, self.amplitude_v, self.offset_v, self.phase_deg = (
            frequency_hz, amplitude_v, offset_v, phase_deg)

    def __call__(self, t):
        return self.offset_v + self.amplitude_v * np.sin(2 * np.pi * self.frequency_hz * t
                                                         + np.radians(self.phase_deg))


class Constant:
    def __init__(self, value_v=0.0):
        self.value_v = value_v

    def __call__(self, t):
        return np.full(np.shape(t), self.value_v, dtype=float)


def default_signals() -> dict:
    """CH1: 1-kHz-Rechteck 0..3,3 V. CH2: 1-kHz-Sinus ±1 V. CH3: 12 V Gleichspannung
    mit 50 mV 100-kHz-Welligkeit. CH4: 0 V."""
    return {
        "CH1": SquareWave(),
        "CH2": SineWave(),
        "CH3": lambda t: 12.0 + 0.025 * np.sin(2 * np.pi * 100e3 * t),
        "CH4": Constant(0.0),
    }


def _snap_up(value: float, steps: tuple[float, ...]) -> float:
    for step in steps:
        if step >= value * (1 - 1e-9):
            return step
    return steps[-1]


class _Channel:
    def __init__(self) -> None:
        self.display = False
        self.coupling = "DC"
        self.probe = 1.0
        self.scale = 1.0       # V/Teilung an der Tastkopfspitze
        self.offset = 0.0


class SimulatedInstrument:
    """Transport-Ersatz für driver.MSO1000HD: write / read_line / read_exact."""

    # Testskripte setzen das, wie MockPicoScope2000.signals.
    signals: dict = default_signals()
    # Zum Prüfen von Fehlerfällen: wird nach Erfassung "STOP" nie erreicht?
    hang_after_run = False

    def __init__(self) -> None:
        self.channels = {n: _Channel() for n in range(1, 5)}
        self.channels[1].display = True
        self.tb_scale = 1e-3
        self.tb_offset = 0.0
        self.depth = "AUTO"
        self.trig_source = 1
        self.trig_level = 0.0
        self.trig_rising = True
        self.sweep = "AUTO"
        self.status = "AUTO"
        self.wav_mode = "NORMal"
        self.wav_format = "WORD"
        self.wav_source = 1
        self.wav_start = 1
        self.wav_stop = 0
        self.errors: list[tuple[int, str]] = []
        self.captured: dict[int, np.ndarray] = {}
        self.captured_xinc = 1e-6
        self.captured_xorigin = 0.0
        self._out = bytearray()
        self._arm_polls = 0
        self._pending_trigger = False
        self._rng = np.random.default_rng(1)
        self.commands: list[str] = []
        self.closed = False

    # -- Transport-Schnittstelle --------------------------------------------------

    def set_timeout(self, _seconds: float) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def write(self, command: str) -> None:
        self.commands.append(command)
        self._handle(command.strip())

    def read_line(self) -> str:
        idx = self._out.find(b"\n")
        if idx < 0:
            from unit_mso1000hd.driver import MSO1000HDError
            raise MSO1000HDError("Timeout: keine Antwort (Simulation)")
        line = bytes(self._out[:idx])
        del self._out[: idx + 1]
        return line.decode("ascii")

    def read_exact(self, count: int) -> bytes:
        if len(self._out) < count:
            from unit_mso1000hd.driver import MSO1000HDError
            raise MSO1000HDError("Timeout: zu wenige Bytes (Simulation)")
        data = bytes(self._out[:count])
        del self._out[:count]
        return data

    # -- Befehle ------------------------------------------------------------------

    def _reply(self, text: str) -> None:
        self._out += text.encode("ascii") + b"\n"

    def _reply_block(self, payload: bytes) -> None:
        self._out += f"#9{len(payload):09d}".encode("ascii") + payload + b"\n"

    def _error(self, code: int, text: str) -> None:
        self.errors.append((code, text))

    def _handle(self, command: str) -> None:
        header, _, arg = command.partition(" ")
        arg = arg.strip()
        query = header.endswith("?")
        tokens = [t for t in header.rstrip("?").lstrip(":").upper().split(":") if t]
        if header.upper() == "*IDN?":
            return self._reply(SIM_IDN)
        if header.upper() == "*OPC?":
            return self._reply("1")
        try:
            handled = self._dispatch(tokens, arg, query)
        except (ValueError, KeyError, IndexError):
            self._error(-224, f"Illegal parameter value: {command}")
            return
        if not handled:
            self._error(-113, f"Undefined header; command cannot be found: {command}")

    def _dispatch(self, tok: list[str], arg: str, query: bool) -> bool:
        def kw(i: int, full: str) -> bool:
            return i < len(tok) and _matches(tok[i], full)

        if len(tok) == 1 and kw(0, "RUN") and not query:
            self._start_run()
            return True
        if len(tok) == 1 and kw(0, "STOP") and not query:
            self.status = "STOP"
            self._pending_trigger = False
            return True
        if kw(0, "SYSTem") and kw(1, "ERRor"):
            if query:
                code, text = self.errors.pop() if self.errors else (0, "No error")
                self._reply(f'{code},"{text}"')
            else:
                self.errors.clear()
            return True

        m = re.fullmatch(r"(CHAN|CHANNEL)(\d)", tok[0]) if tok else None
        if m and len(tok) == 2:
            ch = self.channels[int(m.group(2))]
            return self._channel(ch, tok[1], arg, query)

        if kw(0, "TIMebase"):
            if len(tok) == 2 and kw(1, "SCALe"):
                if query:
                    self._reply(f"{self.tb_scale:e}")
                else:
                    self.tb_scale = _snap_up(float(arg), T_SCALES)
                return True
            if len(tok) == 2 and kw(1, "OFFSet"):
                if query:
                    self._reply(f"{self.tb_offset:e}")
                else:
                    self.tb_offset = float(arg)
                return True
            if len(tok) in (2, 3) and (kw(1, "TYPe") or kw(1, "ROLL") or kw(1, "EXTend")):
                return True
            return False

        if kw(0, "ACQuire"):
            if len(tok) == 2 and kw(1, "TYPe"):
                return True
            if len(tok) == 3 and kw(1, "MEMory") and kw(2, "DEPTh"):
                if query:
                    self._reply(self.depth)
                else:
                    if arg.upper() not in DEPTHS:
                        raise ValueError(arg)
                    self.depth = arg.upper()
                return True
            return False

        if kw(0, "TRIGger"):
            return self._trigger(tok, arg, query)

        if kw(0, "WAVeform"):
            return self._waveform(tok, arg, query)
        return False

    def _channel(self, ch: _Channel, key: str, arg: str, query: bool) -> bool:
        if _matches(key, "DISPlay"):
            if query:
                self._reply("1" if ch.display else "0")
            else:
                ch.display = arg.upper() in ("1", "ON")
            return True
        if _matches(key, "COUPling"):
            if query:
                self._reply(ch.coupling)
            else:
                ch.coupling = arg.upper()
            return True
        if _matches(key, "PROBe"):
            if query:
                self._reply(f"{ch.probe:e}")
            else:
                old = ch.probe
                ch.probe = float(arg.upper().rstrip("X"))
                ch.scale *= ch.probe / old   # Skala hängt am Tastkopf, wie am Gerät üblich
            return True
        if _matches(key, "SCALe"):
            if query:
                self._reply(f"{ch.scale:e}")
            else:
                ch.scale = _snap_up(float(arg) / ch.probe, V_SCALES) * ch.probe
            return True
        if _matches(key, "OFFSet"):
            if query:
                self._reply(f"{ch.offset:e}")
            else:
                ch.offset = float(arg)
            return True
        return any(_matches(key, k) for k in ("BWLimit", "INVert", "UNITs"))

    def _trigger(self, tok: list[str], arg: str, query: bool) -> bool:
        key = tok[1:] if len(tok) > 1 else []
        if key and _matches(key[0], "STATus") and query:
            self._reply(self._poll_status())
            return True
        if key and _matches(key[0], "FORCe") and not query:
            if self.status in ("WAIT", "ARMED", "READY") and not self.hang_after_run:
                self._capture(trigger_time=None)
            return True
        if key and _matches(key[0], "SWEep"):
            if query:
                self._reply(self.sweep)
            else:
                self.sweep = {"AUTO": "AUTO", "NORM": "NORMal", "NORMAL": "NORMal",
                              "SING": "SINGle", "SINGLE": "SINGle"}[arg.upper()]
            return True
        if key and (_matches(key[0], "MODe") or _matches(key[0], "COUPling")):
            return True
        if len(key) == 2 and _matches(key[0], "EDGe"):
            if _matches(key[1], "SOURce"):
                m = re.fullmatch(r"(CHAN|CHANNEL)(\d)", arg.upper())
                if not m:
                    raise ValueError(arg)
                self.trig_source = int(m.group(2))
                return True
            if _matches(key[1], "LEVel"):
                if query:
                    self._reply(f"{self.trig_level:e}")
                else:
                    self.trig_level = float(arg)
                return True
            if _matches(key[1], "POLarity"):
                self.trig_rising = arg.upper().startswith("POS")
                return True
        return False

    def _waveform(self, tok: list[str], arg: str, query: bool) -> bool:
        key = tok[1] if len(tok) == 2 else ""
        if _matches(key, "MODE"):
            self.wav_mode = arg
            return True
        if _matches(key, "FORMat"):
            self.wav_format = arg.upper()
            return True
        if _matches(key, "SOURce"):
            m = re.fullmatch(r"(CHAN|CHANNEL)(\d)", arg.upper())
            if not m:
                raise ValueError(arg)
            self.wav_source = int(m.group(2))
            self.wav_start, self.wav_stop = 1, 0
            return True
        if _matches(key, "STARt"):
            if query:
                self._reply(str(self.wav_start))
            else:
                self.wav_start = int(arg)
            return True
        if _matches(key, "STOP"):
            if query:
                self._reply(str(self.wav_stop))
            else:
                self.wav_stop = int(arg)
            return True
        if _matches(key, "POINts") and query:
            self._reply(str(self._points_available()))
            return True
        if _matches(key, "PREamble") and query:
            n = self._points_available()
            pre = (f"{self.wav_format},NORMal,{n},1,{self.captured_xinc:e},{self.captured_xorigin:e},0,"
                   f"{self.channels[self.wav_source].scale / 25:e},0,128")
            self._reply_block(pre.encode("ascii"))
            return True
        if _matches(key, "DATA") and query:
            data = self.captured.get(self.wav_source)
            if data is None or self.status != "STOP":
                self._error(-221, "Settings conflict: no stopped acquisition")
                self._reply_block(b"")
                return True
            stop = self.wav_stop if self.wav_stop > 0 else data.size
            part = data[self.wav_start - 1: stop]
            self._reply_block(",".join(f"{v:.6e}" for v in part).encode("ascii"))
            return True
        return False

    # -- Erfassung ------------------------------------------------------------------

    def _points_available(self) -> int:
        data = self.captured.get(self.wav_source)
        return 0 if data is None else int(data.size)

    def _active(self) -> list[int]:
        return [n for n, ch in self.channels.items() if ch.display]

    def _start_run(self) -> None:
        if self.sweep != "SINGle":
            self.status = "AUTO"
            return
        self.status = "WAIT"
        self._arm_polls = 1   # erste Statusabfrage sieht noch WAIT
        self._pending_trigger = True

    def _poll_status(self) -> str:
        if self.status == "WAIT" and self._pending_trigger:
            if self._arm_polls > 0:
                self._arm_polls -= 1
                return "WAIT"
            if not self.hang_after_run:
                t_cross = self._find_trigger()
                if t_cross is not None:
                    self._capture(trigger_time=t_cross)
        return self.status

    def _window(self) -> tuple[int, float]:
        active = max(1, len(self._active()))
        rate_max = MAX_RATE / (1 if active == 1 else 2 if active == 2 else 4)
        duration = 10.0 * self.tb_scale
        points = int(min(DEPTHS[self.depth], max(1000, math.floor(rate_max * duration))))
        return points, duration / points

    def _find_trigger(self) -> float | None:
        """Erste Kreuzung des Pegels in der gewünschten Richtung ab t = 0."""
        signal = self.signals.get(f"CH{self.trig_source}")
        if signal is None:
            return None
        points, xinc = self._window()
        span = max(10 * self.tb_scale, 2e-3)
        t = np.linspace(0.0, span, 200_001)
        v = signal(t)
        above = v > self.trig_level
        edges = np.nonzero(~above[:-1] & above[1:])[0] if self.trig_rising else np.nonzero(above[:-1] & ~above[1:])[0]
        if edges.size == 0:
            return None
        return float(t[edges[0] + 1])

    def _capture(self, trigger_time: float | None) -> None:
        points, xinc = self._window()
        first = self.tb_offset - 5.0 * self.tb_scale
        t = first + np.arange(points) * xinc
        shift = trigger_time if trigger_time is not None else 0.0
        self.captured = {}
        for n in self._active():
            ch = self.channels[n]
            signal = self.signals.get(f"CH{n}", Constant(0.0))
            v = np.asarray(signal(t + shift), dtype=float)
            if ch.coupling == "AC":
                v = v - float(np.mean(v))
            v = v + self._rng.normal(0.0, NOISE_DIV * ch.scale, v.size)
            center = -ch.offset
            v = np.clip(v, center - 5 * ch.scale, center + 5 * ch.scale)
            self.captured[n] = v
        self.captured_xinc = xinc
        # HARDWARE-PRÜFUNG: Bedeutung von XORigin am Gerät; hier Zeit des ersten Punkts.
        self.captured_xorigin = first
        self.status = "STOP"
        self._pending_trigger = False


def _matches(token: str, full: str) -> bool:
    """SCPI-Schlüsselwort: Kurzform (Großbuchstaben) oder Langform, Groß/klein egal."""
    short = "".join(c for c in full if c.isupper() or c.isdigit())
    return token.upper() in (short.upper(), full.upper())


class MockMSO1000HD:
    """Für Aufrufer, die eine Mock-Klasse erwarten: öffnet driver.MSO1000HD mit
    simuliertem Gerät."""

    @staticmethod
    def open_first():
        from unit_mso1000hd.driver import MSO1000HD
        return MSO1000HD(SimulatedInstrument())

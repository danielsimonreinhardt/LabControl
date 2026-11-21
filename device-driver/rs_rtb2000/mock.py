"""Simuliertes R&S RTB2000 (RTB2002) auf SCPI-Ebene.

Wie beim UNI-T MSO1000HD ist hier das *Gerät* nachgebildet:
``SimulatedRTB2000`` versteht die Teilmenge der SCPI-Befehle, die driver.py
benutzt, und antwortet in der Form des Handbuchs (Datenblöcke ``#<n><Länge>``
mit REAL,32, Kopf XStart/XStop/Punkte, ACQuire:STATe). So läuft der echte
Treiber- und Adaptercode im Simulationsmodus und in den Prüfskripten mit --
nur Transport und Messwerte sind erfunden.

Zeitmodell (wie am RTB2002 gemessen, siehe README.md):
- Bildschirm 12 Teilungen breit (TIMebase:DIVisions?), Referenzpunkt in der
  Mitte, TIMebase:POSition = Zeit der Bildschirmmitte relativ zum Trigger.
- Abtastrate = min(Aufzeichnungslänge / Fensterdauer, 2,5 GS/s mit einem
  aktiven Kanal bzw. 1,25 GS/s je Kanal mit mehreren). Das echte Gerät nimmt
  teils weniger Punkte als die Aufzeichnungslänge (500k -> 375 008 bei 6 ms).
- Vertikal 10 Teilungen (RANGe = 10 * SCALe). CHANnel:OFFSet wird vom Signal
  abgezogen: die Bildschirmmitte liegt bei +OFFSet. Werte begrenzt bei
  99,9 % des Vollausschlags (±4,995 Teilungen), wie am Gerät.
"""
from __future__ import annotations

import math
import re

import numpy as np

SIM_MODEL = "RTB2002"
SIM_SERIAL = "1333.1005k02/SIM001"
SIM_FIRMWARE = "01.210"
SIM_IDN = f"Rohde&Schwarz,{SIM_MODEL},{SIM_SERIAL},{SIM_FIRMWARE}"
SIM_OPTIONS = "0"   # wie das RTB2002 ohne Lizenzen

SIM_CHANNELS = 2

MAX_RATE = 2.5e9
H_DIVS = 12
V_DIVS = 10
CLIP_DIVS = 4.995
# Aufzeichnungslängen laut ACQuire:POINts (S. 261).
RECORD_LENGTHS = (10_000, 20_000, 50_000, 100_000, 200_000, 500_000,
                  1_000_000, 2_000_000, 5_000_000, 10_000_000, 20_000_000)
V_SCALES = tuple(round(m * 10.0 ** e, 12) for e in range(-3, 2) for m in (1, 2, 5) if m * 10.0 ** e <= 10)
T_SCALES = tuple(round(m * 10.0 ** e, 15) for e in range(-9, 2) for m in (1, 2, 5) if m * 10.0 ** e <= 50)
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
    """CH1: 1-kHz-Rechteck 0..3,3 V. CH2: 1-kHz-Sinus ±1 V."""
    return {"CH1": SquareWave(), "CH2": SineWave()}


def _snap_up(value: float, steps: tuple[float, ...]) -> float:
    for step in steps:
        if step >= value * (1 - 1e-9):
            return step
    return steps[-1]


def _matches(token: str, full: str) -> bool:
    """SCPI-Schlüsselwort: Kurzform (Großbuchstaben) oder Langform, Groß/klein egal."""
    short = "".join(c for c in full if c.isupper() or c.isdigit())
    return token.upper() in (short.upper(), full.upper())


def _split(token: str) -> tuple[str, int | None]:
    """"CHAN1" -> ("CHAN", 1), "LEV" -> ("LEV", None)."""
    m = re.fullmatch(r"(.*?)(\d*)", token)
    return m.group(1), (int(m.group(2)) if m.group(2) else None)


class _Channel:
    def __init__(self) -> None:
        self.state = False
        self.coupling = "DCLIMIT"
        self.probe = 1.0
        self.scale = 5e-3      # V/Teilung an der Tastkopfspitze (*RST, S. 253)
        self.offset = 0.0


class SimulatedRTB2000:
    """Transport-Ersatz für driver.RTB2000: write / read_line / read_exact."""

    # Testskripte setzen das, wie MockPicoScope2000.signals.
    signals: dict = default_signals()
    # Zum Prüfen von Fehlerfällen: wird eine Einzelerfassung nie fertig?
    hang_after_run = False

    def __init__(self) -> None:
        self.channels = {n: _Channel() for n in range(1, SIM_CHANNELS + 1)}
        self.channels[1].state = True
        self.tb_scale = 100e-6
        self.tb_position = 0.0
        self.tb_reference = 50.0
        self.record_length = 10_000
        self.trig_source = 1
        self.trig_level = 0.0
        self.trig_rising = True
        self.trig_mode = "AUTO"
        self.state = "STOP"
        self.data_format = "ASC"
        self.byte_order = "MSBF"
        self.errors: list[tuple[int, str]] = []
        self.captured: dict[int, np.ndarray] = {}
        self.captured_xstart = 0.0
        self.captured_xinc = 1e-6
        self._out = bytearray()
        self._arm_polls = 0
        self._pending = False
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
            from rs_rtb2000.driver import RTB2000Error
            raise RTB2000Error("Timeout: keine Antwort (Simulation)")
        line = bytes(self._out[:idx])
        del self._out[: idx + 1]
        return line.decode("ascii")

    def read_exact(self, count: int) -> bytes:
        if len(self._out) < count:
            from rs_rtb2000.driver import RTB2000Error
            raise RTB2000Error("Timeout: zu wenige Bytes (Simulation)")
        data = bytes(self._out[:count])
        del self._out[:count]
        return data

    # -- Befehle ------------------------------------------------------------------

    def _reply(self, text: str) -> None:
        self._out += text.encode("ascii") + b"\n"

    def _reply_block(self, payload: bytes) -> None:
        length = str(len(payload))
        self._out += f"#{len(length)}{length}".encode("ascii") + payload + b"\n"

    def _error(self, code: int, text: str) -> None:
        self.errors.append((code, text))

    def _handle(self, command: str) -> None:
        header, _, arg = command.partition(" ")
        arg = arg.strip()
        query = header.endswith("?")
        upper = header.upper()
        if upper.startswith("*"):
            return self._common(upper, query)
        tokens = [t for t in header.rstrip("?").lstrip(":").upper().split(":") if t]
        try:
            handled = self._dispatch(tokens, arg, query)
        except (ValueError, KeyError, IndexError):
            self._error(-224, f"Illegal parameter value;{command}")
            return
        if not handled:
            self._error(-113, f"Undefined header;{command}")

    def _common(self, header: str, query: bool) -> None:
        if header == "*IDN?":
            self._reply(SIM_IDN)
        elif header == "*OPT?":
            self._reply(SIM_OPTIONS)
        elif header == "*OPC?":
            self._reply("1")
        elif header == "*CLS":
            self.errors.clear()
        elif header == "*TRG":
            if self.state == "RUN" and self._pending and not self.hang_after_run:
                self._capture(trigger_time=None)
        else:
            self._error(-113, f"Undefined header;{header}")

    def _dispatch(self, tok: list[str], arg: str, query: bool) -> bool:
        if not tok:
            return False
        name, suffix = _split(tok[0])

        if len(tok) == 1 and not query:
            if _matches(name, "SINGle") or _matches(name, "RUNSingle"):
                self._start_single()
                return True
            if _matches(name, "RUN") or _matches(name, "RUNContinous"):
                self.state = "RUN"
                self._pending = False
                return True
            if _matches(name, "STOP"):
                self.state = "STOP"
                self._pending = False
                return True

        if _matches(name, "SYSTem") and len(tok) >= 2 and _matches(tok[1], "ERRor") and query:
            code, text = self.errors.pop(0) if self.errors else (0, "No error")
            self._reply(f'{code},"{text}"')
            return True

        if _matches(name, "CHANnel"):
            return self._channel(suffix or 1, tok[1:], arg, query)
        if _matches(name, "PROBe") and len(tok) == 4 and _matches(tok[1], "SETup") and _matches(tok[2], "ATTenuation"):
            ch = self.channels[suffix or 1]
            if _matches(tok[3], "MANual"):
                if query:
                    self._reply(f"{ch.probe:e}")
                else:
                    old = ch.probe
                    ch.probe = float(arg)
                    ch.scale *= ch.probe / old   # Skala hängt am Tastkopf
                return True
            return _matches(tok[3], "UNIT")
        if _matches(name, "TIMebase"):
            return self._timebase(tok[1:], arg, query)
        if _matches(name, "ACQuire"):
            return self._acquire(tok[1:], arg, query)
        if _matches(name, "TRIGger") and len(tok) >= 3 and _matches(tok[1], "A"):
            return self._trigger(tok[2:], arg, query)
        if _matches(name, "FORMat"):
            if len(tok) == 1 or (len(tok) == 2 and _matches(tok[1], "DATA")):
                if query:
                    self._reply("REAL,32" if self.data_format == "REAL" else "ASC,0")
                else:
                    fmt = arg.split(",")[0].strip().upper()
                    if fmt.startswith("REAL"):
                        self.data_format = "REAL"
                    elif fmt.startswith("ASC"):
                        self.data_format = "ASC"
                    else:
                        raise ValueError(arg)
                return True
            if len(tok) == 2 and _matches(tok[1], "BORDer"):
                self.byte_order = "LSBF" if arg.upper().startswith("LSB") else "MSBF"
                return True
        return False

    def _channel(self, n: int, rest: list[str], arg: str, query: bool) -> bool:
        if not rest:
            return False
        key = rest[0]
        # Befehle, die für alle Kanäle gelten (Suffix egal).
        if _matches(key, "TYPE") or _matches(key, "ARIThmetics"):
            return True
        if _matches(key, "DATA"):
            return self._data(n, rest[1:], query)
        ch = self.channels.get(n)
        if ch is None:
            raise KeyError(n)
        if _matches(key, "STATe"):
            if query:
                self._reply("1" if ch.state else "0")
            else:
                ch.state = arg.upper() in ("1", "ON")
            return True
        if _matches(key, "COUPling"):
            if query:
                self._reply(ch.coupling[:3])
            else:
                value = arg.upper()
                if not any(_matches(value, k) for k in ("DCLimit", "ACLimit", "GND")):
                    raise ValueError(arg)
                ch.coupling = "ACLIMIT" if value.startswith("AC") else "GND" if value == "GND" else "DCLIMIT"
            return True
        if _matches(key, "SCALe"):
            if query:
                self._reply(f"{ch.scale:e}")
            else:
                ch.scale = _snap_up(float(arg) / ch.probe, V_SCALES) * ch.probe
            return True
        if _matches(key, "RANGe"):
            if query:
                self._reply(f"{ch.scale * V_DIVS:e}")
            else:
                ch.scale = _snap_up(float(arg) / V_DIVS / ch.probe, V_SCALES) * ch.probe
            return True
        if _matches(key, "OFFSet"):
            if query:
                self._reply(f"{ch.offset:e}")
            else:
                ch.offset = float(arg)
            return True
        return any(_matches(key, k) for k in ("BANDwidth", "POLarity"))

    def _data(self, n: int, rest: list[str], query: bool) -> bool:
        if rest and _matches(rest[0], "POINts"):
            if query:
                data = self.captured.get(n)
                self._reply(str(0 if data is None else data.size))
            return True   # DEF/MAX/DMAX: die Simulation hat nur das Bildschirmfenster
        if rest and _matches(rest[0], "HEADer") and query:
            data = self.captured.get(n)
            count = 0 if data is None else data.size
            xstop = self.captured_xstart + max(0, count - 1) * self.captured_xinc
            self._reply(f"{self.captured_xstart:.6e},{xstop:.6e},{count},1")
            return True
        if not rest and query:
            data = self.captured.get(n)
            if data is None or self.state == "RUN":
                self._error(-221, "Settings conflict;no stopped acquisition")
                self._reply_block(b"")
                return True
            if self.data_format == "REAL":
                dtype = "<f4" if self.byte_order == "LSBF" else ">f4"
                self._reply_block(data.astype(dtype).tobytes())
            else:
                self._out += (",".join(f"{v:.6e}" for v in data)).encode("ascii") + b"\n"
            return True
        return False

    def _timebase(self, rest: list[str], arg: str, query: bool) -> bool:
        if not rest:
            return False
        key = rest[0]
        if _matches(key, "SCALe"):
            if query:
                self._reply(f"{self.tb_scale:e}")
            else:
                self.tb_scale = _snap_up(float(arg), T_SCALES)
            return True
        if _matches(key, "POSition"):
            if query:
                self._reply(f"{self.tb_position:e}")
            else:
                self.tb_position = float(arg)
            return True
        if _matches(key, "REFerence"):
            if query:
                self._reply(f"{self.tb_reference:g}")
            else:
                self.tb_reference = float(arg)
            return True
        if _matches(key, "DIVisions") and query:
            self._reply(str(H_DIVS))
            return True
        if _matches(key, "RANGe") or _matches(key, "ACQTime"):
            if query:
                self._reply(f"{self.tb_scale * H_DIVS:e}")
            else:
                self.tb_scale = _snap_up(float(arg) / H_DIVS, T_SCALES)
            return True
        return _matches(key, "ROLL") or _matches(key, "ZOOM")

    def _acquire(self, rest: list[str], arg: str, query: bool) -> bool:
        if not rest:
            return False
        key = rest[0]
        if _matches(key, "POINts") and (len(rest) == 1 or _matches(rest[1], "VALue")):
            if query:
                self._reply(str(self.record_length))
            else:
                wanted = float(arg)
                self.record_length = min(RECORD_LENGTHS, key=lambda r: abs(r - wanted))
            return True
        if _matches(key, "SRATe") and query:
            points, xinc = self._window()
            self._reply(f"{1.0 / xinc:e}")
            return True
        if _matches(key, "STATe") and query:
            self._reply(self._poll_state())
            return True
        return _matches(key, "TYPE") or _matches(key, "NSINgle")

    def _trigger(self, rest: list[str], arg: str, query: bool) -> bool:
        key, suffix = _split(rest[0])
        if _matches(key, "MODE"):
            if query:
                self._reply(self.trig_mode[:4])
            else:
                self.trig_mode = "NORMAL" if arg.upper().startswith("NORM") else "AUTO"
            return True
        if _matches(key, "TYPE"):
            return True
        if _matches(key, "SOURce"):
            m = re.fullmatch(r"CH(\d)", arg.upper())
            if not m or int(m.group(1)) not in self.channels:
                raise ValueError(arg)
            self.trig_source = int(m.group(1))
            return True
        if _matches(key, "EDGE") and len(rest) == 2:
            if _matches(rest[1], "SLOPe"):
                self.trig_rising = arg.upper().startswith("POS")
                return True
            return _matches(rest[1], "COUPling")
        if _matches(key, "LEVel") and (len(rest) == 1 or _matches(rest[1], "VALue")):
            if query:
                self._reply(f"{self.trig_level:e}")
            else:
                self.trig_level = float(arg)
            return True
        return False

    # -- Erfassung ------------------------------------------------------------------

    def _active(self) -> list[int]:
        return [n for n, ch in self.channels.items() if ch.state]

    def _start_single(self) -> None:
        self.state = "RUN"
        self._arm_polls = 1   # erste Statusabfrage sieht noch RUN
        self._pending = True

    def _poll_state(self) -> str:
        if self.state == "RUN" and self._pending:
            if self._arm_polls > 0:
                self._arm_polls -= 1
                return "RUN"
            if not self.hang_after_run:
                t_cross = self._find_trigger()
                if t_cross is not None:
                    self._capture(trigger_time=t_cross)
        return self.state

    def _window(self) -> tuple[int, float]:
        """Wie am RTB2002 gemessen: 2,5 GS/s nur mit einem Kanal und erst, wenn
        Länge / Fenster das hergibt, sonst höchstens 1,25 GS/s."""
        duration = H_DIVS * self.tb_scale
        wanted = self.record_length / duration
        rate = MAX_RATE if len(self._active()) <= 1 and wanted >= MAX_RATE else min(wanted, MAX_RATE / 2)
        points = int(max(1000, math.floor(rate * duration)))
        return points, duration / points

    def _find_trigger(self) -> float | None:
        """Erste Kreuzung des Pegels in der gewünschten Richtung ab t = 0."""
        signal = self.signals.get(f"CH{self.trig_source}")
        if signal is None or not self.channels[self.trig_source].state:
            return None
        span = max(H_DIVS * self.tb_scale, 2e-3)
        t = np.linspace(0.0, span, 200_001)
        v = signal(t)
        above = v > self.trig_level
        edges = (np.nonzero(~above[:-1] & above[1:])[0] if self.trig_rising
                 else np.nonzero(above[:-1] & ~above[1:])[0])
        if edges.size == 0:
            return None
        return float(t[edges[0] + 1])

    def _capture(self, trigger_time: float | None) -> None:
        points, xinc = self._window()
        duration = H_DIVS * self.tb_scale
        # Referenzpunkt (Prozent der Breite) liegt bei TIMebase:POSition.
        first = self.tb_position - duration * self.tb_reference / 100.0
        t = first + np.arange(points) * xinc
        shift = trigger_time if trigger_time is not None else 0.0
        self.captured = {}
        for n in self._active():
            ch = self.channels[n]
            signal = self.signals.get(f"CH{n}", Constant(0.0))
            v = np.asarray(signal(t + shift), dtype=float)
            if ch.coupling == "ACLIMIT":
                v = v - float(np.mean(v))
            elif ch.coupling == "GND":
                v = np.zeros_like(v)
            v = v + self._rng.normal(0.0, NOISE_DIV * ch.scale, v.size)
            v = np.clip(v, ch.offset - CLIP_DIVS * ch.scale, ch.offset + CLIP_DIVS * ch.scale)
            self.captured[n] = v
        self.captured_xstart = first
        self.captured_xinc = xinc
        self.state = "COMP"
        self._pending = False


class MockRTB2000:
    """Für Aufrufer, die eine Mock-Klasse erwarten: öffnet driver.RTB2000 mit
    simuliertem Gerät."""

    @staticmethod
    def open_first():
        from rs_rtb2000.driver import RTB2000
        return RTB2000(SimulatedRTB2000())

"""Simulierter Generator AWG1 eines UNI-T MSO1000HD-S auf SCPI-Ebene.

Wie unit_mso1000hd/mock.py ist hier das *Gerät* nachgebildet: ``SimulatedAwg`` ersetzt den
Transport (write/read_line/close) und versteht genau die Befehle, die driver.py sendet. So läuft
der echte Treiber im Simulationsmodus und in tools/check_unit_mso_awg.py mit.

Nachgebildet, wie am MSO1154HD-S gemessen (1.0.2):
- Antworten wie das Gerät ("1", "BASe", "RAMP", "1.000000e+03").
- Ein unbekannter Befehl gibt beim Senden einen I/O-Fehler, der Fehlerspeicher meldet danach
  ``-113,"Undefined header;..."``; die Verbindung bleibt.
- Werte außerhalb werden still geklemmt (Tastgrad 150 -> 99, Frequenz 0 -> 1 µHz, Last < 50 -> 50,
  Last > 1 MΩ -> 1 MΩ, Last in ganzen Ohm).
- Last: Amplitude und Offset werden an der angenommenen Last gezählt (Anzeige = Leerlaufwert *
  R/(R+50); 1 MΩ gilt als hochohmig), die Spannung am Ausgang bleibt.
- Eigenheit: AWG1:BASe:DUTY in einer anderen Form als Rechteck/Puls schaltet die Ausgabe auf ein
  Rechteck um, AWG1:BASe:WAVe? meldet weiter die alte Form (effective_waveform()).
- Mehrere Threads: jede Antwort gehört dem Thread, der gefragt hat (am Gerät sorgt dafür die
  Gerätesperre in unit_mso1000hd.driver.VisaTransport).
"""
from __future__ import annotations

import threading

from unit_mso1000hd.driver import MSO1000HDError

_IO_ERROR = "VI_ERROR_IO (-1073807298): Could not perform operation because of I/O error."


def _fmt(value: float) -> str:
    return f"{value:.6e}"


class SimulatedAwg:
    """Transport-Ersatz für driver.UnitMsoAwg."""

    def __init__(self, has_awg: bool = True) -> None:
        self.has_awg = has_awg
        self.dead = False             # Prüfungen: Verbindung weg (jeder Zugriff scheitert)
        self.output = True
        self.mode = "BASe"
        self.wave = "RAMP"
        self.frequency = 1000.0
        self.amplitude_oc = 1.0       # Leerlauf (hochohmig) Vss
        self.offset_oc = 0.0
        self.duty = 50.0
        self.load = 1_000_000.0
        self.duty_forces_square = False
        self.params = {
            "MODulate:WAVe": "SINe", "MODulate:FREQuency": 100.0, "AM:MODulate:DEPTh": 100.0,
            "FM:FREQuency:DEV": 1000.0, "FSK:RATio": 100.0, "FSK:FREQuency": 10000.0, "SWEep:TYPe": "LINe",
            "SWEep:FREQuency:STARt": 1000.0, "SWEep:FREQuency:STOP": 2000.0, "SWEep:TIMe": 1.0,
        }
        self.errors: list[str] = []
        self.log: list[str] = []      # gesendete Befehle (Prüfungen)
        self._state_lock = threading.RLock()
        self._local = threading.local()

    # -- Transport ---------------------------------------------------------------------

    def set_timeout(self, _seconds: float) -> None:
        pass

    def close(self) -> None:
        pass

    def write(self, command: str) -> None:
        if self.dead:
            raise MSO1000HDError(f"Senden von {command!r} gescheitert: verbindung weg")
        with self._state_lock:
            self.log.append(command)
            try:
                answer = self._handle(command.strip())
            except KeyError:
                self.errors.append(f'-113,"Undefined header;{command.strip()}"')
                raise MSO1000HDError(f"Senden von {command!r} gescheitert: {_IO_ERROR}") from None
        if answer is not None:
            self._local.answer = answer

    def read_line(self) -> str:
        if self.dead:
            raise MSO1000HDError("Lesen gescheitert: verbindung weg")
        answer = getattr(self._local, "answer", None)
        self._local.answer = None
        if answer is None:
            raise MSO1000HDError("Lesen gescheitert: VI_ERROR_TMO (-1073807339): Timeout expired before operation completed.")
        return answer

    # -- Gerät -----------------------------------------------------------------------------

    def _factor(self) -> float:
        return 1.0 if self.load >= 1_000_000 else self.load / (self.load + 50.0)

    def effective_waveform(self) -> str:
        """Was am Ausgang anliegt (Eigenheit Tastgrad, siehe Modul-Docstring)."""
        return "SQUare" if self.duty_forces_square else self.wave

    def amplitude_at_output(self) -> float:
        """Vss am hochohmigen Eingang (Oszilloskop) -- unabhängig von der eingestellten Last."""
        return 0.0 if not self.output else self.amplitude_oc

    def _handle(self, command: str):   # noqa: C901 -- eine Befehlstabelle
        upper = command.upper()
        if upper == "*IDN?":
            return "UNI-TREND TECHNOLOGY,MSO1154HD-S,SIM-AWG-0001,V1.01.0081"
        if upper == ":SYSTEM:ERROR?":
            return self.errors.pop(0) if self.errors else '0,"No error"'
        if not upper.startswith("AWG1:") or not self.has_awg:
            raise KeyError(command)
        head, _, arg = command[5:].partition(" ")
        key = head.upper()
        query = key.endswith("?")
        key = key.rstrip("?")
        arg = arg.strip()

        def number() -> float:
            try:
                return float(arg)
            except ValueError:
                raise KeyError(command) from None

        if key == "OUTPUT":
            if query:
                return "1" if self.output else "0"
            if arg.upper() not in ("ON", "OFF", "1", "0"):
                raise KeyError(command)
            self.output = arg.upper() in ("ON", "1")
            return None
        if key == "MODE":
            if query:
                return self.mode
            words = {"BASE": "BASe", "AM": "AM", "FM": "FM", "ASK": "ASK", "FSK": "FSK", "SWEEP": "SWEep"}
            self.mode = words[arg.upper()]
            return None
        if key == "BASE:WAVE":
            if query:
                return self.wave
            words = {"SINE": "SINe", "SQUARE": "SQUare", "PULSE": "PULSe", "RAMP": "RAMP", "NOISE": "NOISe",
                     "DC": "DC"}
            self.wave = words[arg.upper()]
            self.duty_forces_square = False
            return None
        if key == "BASE:FREQUENCY":
            if query:
                return _fmt(self.frequency)
            self.frequency = min(max(number(), 1e-6), 25e6)
            return None
        if key == "BASE:AMPLITUDE":
            if query:
                return _fmt(round(self.amplitude_oc * self._factor(), 6))
            self.amplitude_oc = min(max(number(), 0.002), 20.0) / self._factor()
            return None
        if key == "BASE:OFFSET":
            if query:
                return _fmt(round(self.offset_oc * self._factor(), 6))
            self.offset_oc = min(max(number(), -10.0), 10.0) / self._factor()
            return None
        if key == "BASE:DUTY":
            if query:
                return _fmt(self.duty)
            self.duty = min(max(number(), 1.0), 99.0)
            if self.wave not in ("SQUare", "PULSe"):
                self.duty_forces_square = True
            return None
        if key == "LOAD":
            if query:
                return _fmt(self.load)
            self.load = float(min(max(round(number()), 50), 1_000_000))
            return None
        for name in self.params:
            if key == name.upper():
                if query:
                    value = self.params[name]
                    return value if isinstance(value, str) else _fmt(value)
                if isinstance(self.params[name], str):
                    words = {"SINE": "SINe", "SQUARE": "SQUare", "UPRAMP": "UPRamp", "DNRAMP": "DNRamp",
                             "NOISE": "NOISe", "LINE": "LINe", "LOG": "LOG"}
                    self.params[name] = words[arg.upper()]
                else:
                    self.params[name] = number()
                return None
        raise KeyError(command)

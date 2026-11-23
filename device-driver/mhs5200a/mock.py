"""Simulierter MHS-5225A auf Ebene der seriellen Zeilen: MockMHS5200A ist der echte Treiber (driver.MHS5200A)
an einem nachgebildeten Port. So laeuft in der Simulation derselbe Code wie am Geraet (Skalierung, Daempfung,
Reihenfolge der Schreibbefehle, Handshake).

Nachgebildet, wie am MHS-5225A gemessen (README "Am Geraet gemessen"): jeder Schreibbefehl wird mit "ok"
quittiert, auch ein unbekannter; Werte ausserhalb begrenzt das Geraet still (Amplitude 2000, Frequenz
25 MHz, Tastgrad 999, Offset 0..240, Phase modulo 360); Arbitraer n wird als 100+n geschrieben und als
10+n gemeldet; ein unbekanntes Lesefeld liefert nur das Echo, eine Zeile ohne ":" gar nichts (Timeout).
Nicht nachgebildet: Signal am Ausgang (ausser output_volts() fuer die Pruefung), Phase.
"""
from __future__ import annotations

import re

from mhs5200a.driver import MHS5200A

SIM_IDENT = "5225A5040000"
_LINE_RE = re.compile(r"^:([sr])(\d)([a-z])(\d*)$")
_LIMITS = {"f": 2_500_000_000, "a": 2000, "d": 999, "o": 240, "y": 1, "b": 1}


class FakeMhsPort:
    """Ersatz fuer serial.Serial: write() nimmt Zeilen, readline() liefert die Antwort."""

    port = "SIM-MHS"

    def __init__(self) -> None:
        self.values = {ch: {"w": 0, "f": 100000, "a": 1000, "o": 120, "d": 500, "p": 0, "y": 0, "b": 0}
                       for ch in (1, 2)}
        self.log: list[str] = []
        self._reply = b""

    def reset_input_buffer(self) -> None:
        self._reply = b""

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> int:
        line = data.decode("ascii").strip()
        self.log.append(line)
        self._reply = self._answer(line)
        return len(data)

    def readline(self) -> bytes:
        reply, self._reply = self._reply, b""
        return reply

    def _answer(self, line: str) -> bytes:
        if line == ":r0c":
            return f":r0c{SIM_IDENT}\r\n".encode()
        match = _LINE_RE.match(line)
        if not match:
            return b"ok\r\n" if line.startswith(":s") else (f"{line}\r\n".encode() if line.startswith(":r") else b"")
        op, ch, field, value = match.group(1), int(match.group(2)), match.group(3), match.group(4)
        regs = self.values.get(ch)
        if regs is None or field not in regs:
            return b"ok\r\n" if op == "s" else f"{line}\r\n".encode()
        if op == "s":
            if value:
                self._store(ch, field, int(value))
            return b"ok\r\n"
        return f":r{ch}{field}{self._format(ch, field)}\r\n".encode()

    def _store(self, ch: int, field: str, raw: int) -> None:
        if field == "w":
            if 0 <= raw <= 4:
                self.values[ch]["w"] = raw
            elif 100 <= raw <= 115:
                self.values[ch]["w"] = 10 + raw - 100
            else:
                self.values[ch]["w"] = 10   # am Geraet: unbekannte Form -> Arbitraer 0
        elif field == "p":
            for regs in self.values.values():   # am Geraet gilt die Phase fuer beide Kanaele
                regs["p"] = raw % 360
        else:
            self.values[ch][field] = min(raw, _LIMITS[field])

    def _format(self, ch: int, field: str) -> str:
        raw = self.values[ch][field]
        width = {"f": 10, "a": 4, "o": 3, "d": 3, "p": 3, "w": 2}.get(field, 1)
        return f"{raw:0{width}d}"

    # -- fuer Pruefungen: was am Ausgang anliegt (Leerlauf, ideal) -----------------------------

    def output_volts(self, ch: int) -> tuple[float, float]:
        """(Spitze-Spitze, Gleichanteil) in V bei eingeschaltetem Ausgang, (0, 0) bei ausgeschaltetem."""
        regs = self.values[ch]
        if not regs["b"]:
            return 0.0, 0.0
        vpp = regs["a"] / (100.0 if regs["y"] else 1000.0)
        return vpp, (regs["o"] - 120) / 100.0 * vpp / 2.0


class MockMHS5200A(MHS5200A):
    """Der echte Treiber am FakeMhsPort (kein serieller Port noetig)."""

    def __init__(self) -> None:   # bewusst ohne super().__init__: oeffnet keinen Port
        self._ser = FakeMhsPort()
        self.model = "MHS-5200A"
        self.max_frequency_hz = 25e6
        self.probe()

    @property
    def sim(self) -> FakeMhsPort:
        return self._ser

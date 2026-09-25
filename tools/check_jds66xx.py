"""Pruefskript fuer den Funktionsgenerator-Treiber (jds66xx) OHNE Hardware.

Ersetzt den seriellen Port durch eine kleine Nachbildung des Geraete-Protokolls
(FakeSerial) und prueft, dass der Treiber die Telegramme so kodiert und die
Antworten so dekodiert, wie es das Joy-IT-Protokoll beschreibt. Prueft ausserdem
den Handshake gegen ein fremdes Board (Konsole bzw. reiner Echo-Port), damit ein
CH340-Nachbar nie als Funktionsgenerator durchgeht, und dass der Mock dieselben
Werte ablehnt wie der Treiber.

Aufruf:  python tools/check_jds66xx.py     (aus dem Repo-Stamm)

Das ist eine Pruefung gegen die DOKUMENTIERTE Kodierung, keine gegen ein echtes
Geraet -- Timing, Quittungsformat und Frequenzklemmung bleiben Hardware-Sache.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import serial  # noqa: E402

from jds66xx import driver  # noqa: E402
from jds66xx.driver import FunctionGeneratorError, FunctionGeneratorValueError, JDS66xx  # noqa: E402
from jds66xx.mock import MockJDS66xx  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        FAILURES.append(f"{name} {detail}".strip())
        print(f"  FEHLER  {name} {detail}")
    else:
        print(f"  ok      {name}")


class FakeGenerator:
    """Registerbank nach Protokoll; Antwort auf jede Zeile wie das Geraet."""

    def __init__(self) -> None:
        self.regs: dict[int, list[int]] = {
            20: [0, 0], 21: [0], 22: [0], 23: [100000, 0], 24: [100000, 0],
            25: [1000], 26: [1000], 27: [1000], 28: [1000], 29: [500], 30: [500], 31: [0],
        }
        self.sent: list[str] = []

    def reply(self, line: str) -> bytes:
        self.sent.append(line)
        m = re.match(r"^:([rw])(\d+)=(.*)\.$", line)
        if not m:
            return b"\r\n"
        op, reg, data = m.group(1), int(m.group(2)), m.group(3)
        if op == "r":
            if reg not in self.regs:
                return b":r%d=.\r\n" % reg
            return (":r%d=%s.\r\n" % (reg, ",".join(map(str, self.regs[reg])))).encode()
        self.regs[reg] = [int(x) for x in data.split(",")]
        return b":ok\r\n"


class EchoBoard:
    """Fremdes Board: spiegelt die Eingabe (mit/ohne Prompt), kennt das Protokoll nicht."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt

    def reply(self, line: str) -> bytes:
        return f"{self.prompt}{line}\r\n".encode()


class SilentBoard:
    def reply(self, line: str) -> bytes:
        return b""


class FakeSerial:
    device: object = None

    def __init__(self, port=None, baudrate=None, timeout=None) -> None:
        self.port = port
        self._pending = b""

    def reset_input_buffer(self) -> None:
        self._pending = b""

    def write(self, data: bytes) -> None:
        self._pending = type(self).device.reply(data.decode("ascii").strip())

    def readline(self) -> bytes:
        out, self._pending = self._pending, b""
        return out

    def close(self) -> None:
        pass


def open_with(device) -> JDS66xx:
    FakeSerial.device = device
    return JDS66xx("FAKE")


def main() -> int:
    serial.Serial = FakeSerial  # driver greift ueber das Modul serial zu
    driver.serial.Serial = FakeSerial

    print("Kodierung/Dekodierung")
    fake = FakeGenerator()
    gen = open_with(fake)
    check("probe erkennt Generator", gen.probe())

    gen.set_frequency(1, 257.86)
    check("Frequenz 257,86 Hz -> :w23=25786,0.", fake.sent[-1] == ":w23=25786,0.", fake.sent[-1])
    check("Frequenz zuruecklesen", abs(gen.get_frequency(1) - 257.86) < 1e-9)
    gen.set_frequency(2, 15_000_000)
    check("Frequenz Kanal 2 -> Register 24", fake.sent[-1] == ":w24=1500000000,0.", fake.sent[-1])

    fake.regs[23] = [80_000_000, 3]
    check("Multiplikator mHz wird gelesen", abs(gen.get_frequency(1) - 80_000.0) < 1e-6)
    fake.regs[23] = [80_000_000, 4]
    check("Multiplikator uHz wird gelesen", abs(gen.get_frequency(1) - 80.0) < 1e-9)

    gen.set_amplitude(1, 5.0)
    check("Amplitude 5 V -> :w25=5000.", fake.sent[-1] == ":w25=5000.", fake.sent[-1])
    check("Amplitude zuruecklesen", abs(gen.get_amplitude(1) - 5.0) < 1e-9)

    gen.set_offset(1, 0.0)
    check("Offset 0 V -> 1000", fake.sent[-1] == ":w27=1000.", fake.sent[-1])
    gen.set_offset(1, -2.5)
    check("Offset -2,5 V -> 750", fake.sent[-1] == ":w27=750.", fake.sent[-1])
    check("Offset negativ zuruecklesen", abs(gen.get_offset(1) + 2.5) < 1e-9)
    gen.set_offset(2, 9.99)
    check("Offset +9,99 V -> 1999", fake.sent[-1] == ":w28=1999.", fake.sent[-1])

    gen.set_duty(1, 37.5)
    check("Duty 37,5 % -> 375", fake.sent[-1] == ":w29=375.", fake.sent[-1])
    check("Duty zuruecklesen", abs(gen.get_duty(1) - 37.5) < 1e-9)

    gen.set_phase(90.5)
    check("Phase 90,5 Grad -> 905", fake.sent[-1] == ":w31=905.", fake.sent[-1])
    gen.set_phase(360.0)
    check("Phase 360 Grad wird zu 0", fake.sent[-1] == ":w31=0.", fake.sent[-1])

    gen.set_waveform(2, driver.WAVE_SQUARE)
    check("Wellenform Kanal 2 -> :w22=1.", fake.sent[-1] == ":w22=1.", fake.sent[-1])
    gen.set_waveform(1, 101)
    check("Arbitraer 01 (101) erlaubt", fake.sent[-1] == ":w21=101.", fake.sent[-1])

    print("Ausgaenge (Register 20 kennt nur das Paar)")
    gen.set_outputs(False, False)
    gen.set_output(1, True)
    check("Kanal 1 ein -> :w20=1,0.", fake.sent[-1] == ":w20=1,0.", fake.sent[-1])
    gen.set_output(2, True)
    check("Kanal 2 ein laesst Kanal 1 an -> :w20=1,1.", fake.sent[-1] == ":w20=1,1.", fake.sent[-1])
    gen.set_output(1, False)
    check("Kanal 1 aus laesst Kanal 2 an -> :w20=0,1.", fake.sent[-1] == ":w20=0,1.", fake.sent[-1])
    check("get_outputs", gen.get_outputs() == (False, True))

    print("Gesamtzustand")
    state = gen.get_state()
    check("get_state Kanal 2 Wellenform", state.channels[1].waveform == driver.WAVE_SQUARE)
    check("get_state Ausgaenge", state.outputs == (False, True))

    print("Wertepruefung: abgelehnt BEVOR gesendet wird, Verbindung bleibt")
    for label, call in {
        "Frequenz zu hoch": lambda: gen.set_frequency(1, 15_000_001),
        "Frequenz null": lambda: gen.set_frequency(1, 0),
        "Amplitude zu hoch": lambda: gen.set_amplitude(1, 20.01),
        "Amplitude negativ": lambda: gen.set_amplitude(1, -1),
        "Offset zu gross": lambda: gen.set_offset(1, 10),
        "Duty > 100": lambda: gen.set_duty(1, 100.5),
        "Kanal 3": lambda: gen.set_frequency(3, 100),
        "Wellenform 17": lambda: gen.set_waveform(1, 17),
    }.items():
        before = len(fake.sent)
        try:
            call()
            check(label, False, "(nicht abgelehnt)")
        except FunctionGeneratorValueError:
            check(label, len(fake.sent) == before, "(es wurde trotzdem gesendet)")

    print("Fehlerklassen")
    check("ValueError ist FunctionGeneratorError", issubclass(FunctionGeneratorValueError, FunctionGeneratorError))

    print("Handshake gegen fremde Boards (CH340-Nachbarn)")
    for label, board in {
        "Konsole mit Prompt": EchoBoard("> "),
        "reiner Echo-Port": EchoBoard(""),
        "stummer Port": SilentBoard(),
    }.items():
        other = open_with(board)
        check(f"{label}: probe() ist False", other.probe() is False)

    print("Schreiben ohne :ok")
    class NoAck(FakeGenerator):
        def reply(self, line: str) -> bytes:
            return b":er\r\n" if line.startswith(":w") else super().reply(line)
    other = open_with(NoAck())
    try:
        other.set_frequency(1, 100)
        check("fehlendes :ok -> ValueError", False)
    except FunctionGeneratorValueError:
        check("fehlendes :ok -> ValueError (Verbindung bleibt)", True)
    other = open_with(SilentBoard())
    try:
        other.set_frequency(1, 100)
        check("Timeout -> Error", False)
    except FunctionGeneratorValueError:
        check("Timeout ist KEIN ValueError", False)
    except FunctionGeneratorError:
        check("Timeout -> FunctionGeneratorError (Verbindung tot)", True)

    print("Mock: gleiche Ablehnung, gleiche Rundung")
    mock = MockJDS66xx()
    for label, call in {
        "Frequenz": lambda: mock.set_frequency(1, 20_000_000),
        "Amplitude": lambda: mock.set_amplitude(2, 25),
        "Offset": lambda: mock.set_offset(1, -11),
        "Duty": lambda: mock.set_duty(1, -1),
        "Phase": lambda: mock.set_phase(361),
        "Kanal": lambda: mock.set_output(0, True),
    }.items():
        try:
            call()
            check(f"Mock lehnt {label} ab", False)
        except FunctionGeneratorValueError:
            check(f"Mock lehnt {label} ab", True)
    mock.set_frequency(1, 257.864)
    check("Mock rundet auf 0,01 Hz", mock.get_frequency(1) == 257.86)
    mock.set_output(2, True)
    check("Mock: Ausgang 2 an, 1 unberuehrt", mock.get_outputs() == (False, True))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} Pruefung(en) fehlgeschlagen:")
        for line in FAILURES:
            print("  -", line)
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

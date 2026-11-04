"""Prueft den HCS34xx-Treiber gegen ein simuliertes Geraet (keine Hardware):
  * BUGS_OFFEN #37: set_current/set_voltage/set_output lesen den Zustand zurueck und wiederholen,
    wenn das Geraet trotz "OK" den Wert nicht uebernommen hat.
  * Ausgang: SOUT0/SOUT1 + GOUT ("0" = EIN, "1" = AUS), Sollwerte bleiben dabei unberuehrt.
  * Tastensperre: nach JEDEM Befehl wird ENDS gesendet; quittiert ein Geraet ENDS nicht, wird es
    abgeschaltet statt jeden Befehl in den Timeout laufen zu lassen.
Mock-Pruefung (simuliertes Geraet), keine Hardware."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device-driver"))
from hcs34xx.driver import HCS34xx, PowerSupplyError, PowerSupplyValueError  # noqa: E402


class FakeSerial:
    """Geraet, das die ersten `drop` Schreibbefehle quittiert, aber verwirft."""
    port = "FAKE"

    def __init__(self, drop, ends=True, output_on=True):
        self.drop, self.v, self.i, self.buf = drop, 120, 100, b""
        self.output_on = output_on
        self.answers_ends = ends
        self.log: list[str] = []

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, data):
        cmd = data.decode().strip()
        self.log.append(cmd)
        if cmd == "GETS":
            self.buf = f"{self.v:03d}{self.i:03d}".encode() + b"\rOK\r"
        elif cmd == "GOUT":
            self.buf = (b"0" if self.output_on else b"1") + b"\rOK\r"
        elif cmd == "ENDS":
            self.buf = b"OK\r" if self.answers_ends else b""
        elif cmd.startswith("SOUT"):
            if self.drop > 0:
                self.drop -= 1
            else:
                self.output_on = cmd[4:] == "0"
            self.buf = b"OK\r"
        elif cmd.startswith(("VOLT", "CURR")):
            if self.drop > 0:
                self.drop -= 1
            elif cmd.startswith("VOLT"):
                self.v = int(cmd[4:])
            else:
                self.i = int(cmd[4:])
            self.buf = b"OK\r"

    def read_until(self, _):
        out, self.buf = self.buf, b""
        return out


def make(drop=0, **kwargs):
    d = HCS34xx.__new__(HCS34xx)
    d._ser = FakeSerial(drop, **kwargs)
    return d


def expect_error(label, action):
    try:
        action()
    except PowerSupplyValueError as exc:
        print(f"erwarteter Fehler ({label}):", exc)
        return
    raise SystemExit(f"FEHLER: {label} wurde als ok gemeldet")


# --- Sollwerte lesen zurueck (BUGS_OFFEN #37)
d = make(1)
d.set_current(0.0)
assert d._ser.i == 0, "erster Befehl verloren, Retry muss ihn nachholen"
d = make(3)
expect_error("nicht uebernommener Sollwert", lambda: d.set_current(0.0))
d = make(0)
d.set_voltage(24.0)
assert d._ser.v == 240

# --- Ausgang
d = make(0, output_on=True)
assert d.get_output() is True
d.set_output(False)
assert d._ser.output_on is False and d.get_output() is False, "SOUT1 muss den Ausgang ausschalten"
assert (d._ser.v, d._ser.i) == (120, 100), "Sollwerte duerfen sich beim Schalten nicht aendern"
d.set_output(True)
assert d._ser.output_on is True and d.get_output() is True, "SOUT0 muss den Ausgang einschalten"
assert "SOUT1" in d._ser.log and "SOUT0" in d._ser.log, "SOUT1 = aus, SOUT0 = ein (laut sigrok und am Geraet geprueft)"
d = make(1, output_on=True)
d.set_output(False)
assert d._ser.output_on is False, "verworfener erster SOUT-Befehl muss nachgeholt werden"
d = make(3, output_on=True)
expect_error("nicht uebernommener Ausgang AUS", lambda: d.set_output(False))
assert d._ser.output_on is True

# --- Tastensperre aufheben: ENDS nach jedem Befehl
d = make(0)
d.get_setpoint()
d.get_output()
d.set_current(1.5)
log = d._ser.log
commands = [c for c in log if c != "ENDS"]
for i, c in enumerate(log):
    if c != "ENDS":
        assert i + 1 < len(log) and log[i + 1] == "ENDS", f"nach {c} fehlt ENDS: {log}"
assert log.count("ENDS") == len(commands) > 0

# --- Geraet, das ENDS nicht quittiert: einmal versuchen, dann nicht mehr
d = make(0, ends=False)
d.get_setpoint()
d.get_setpoint()
d.get_output()
assert log and d._ser.log.count("ENDS") == 1, f"ENDS nur einmal versuchen: {d._ser.log}"
assert d._release_keys is False

# --- Fehler beim ENDS bricht den eigentlichen Befehl nicht ab
d = make(0)
d._ser.write = lambda data, _orig=d._ser.write: (_ for _ in ()).throw(OSError("weg")) if data.strip() == b"ENDS" else _orig(data)
assert d.get_setpoint() == (12.0, 10.0), "ENDS-Fehler darf die Antwort des Befehls nicht verlieren"

# --- unerwartete GOUT-Antwort ist ein Wertfehler, kein Verbindungsabbruch
d = make(0)
d._ser.write = lambda data, _orig=d._ser.write: setattr(d._ser, "buf", b"7\rOK\r") if data.strip() == b"GOUT" else _orig(data)
try:
    d.get_output()
    raise SystemExit("FEHLER: unerwartete GOUT-Antwort wurde akzeptiert")
except PowerSupplyValueError:
    pass
assert issubclass(PowerSupplyValueError, PowerSupplyError)

print("check_hcs34xx_verify: OK")

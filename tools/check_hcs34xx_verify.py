"""Prueft BUGS_OFFEN #37: HCS34xx.set_current/set_voltage lesen den Sollwert zurueck
und wiederholen, wenn das Geraet trotz "OK" den Wert nicht uebernommen hat.
Mock-Pruefung (simuliertes Geraet), keine Hardware."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device-driver"))
from hcs34xx.driver import HCS34xx, PowerSupplyValueError  # noqa: E402


class FakeSerial:
    """Geraet, das die ersten `drop` Schreibbefehle quittiert, aber verwirft."""
    port = "FAKE"

    def __init__(self, drop):
        self.drop, self.v, self.i, self.buf = drop, 120, 100, b""

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, data):
        cmd = data.decode().strip()
        if cmd == "GETS":
            self.buf = f"{self.v:03d}{self.i:03d}".encode() + b"\rOK\r"
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


def make(drop):
    d = HCS34xx.__new__(HCS34xx)
    d._ser = FakeSerial(drop)
    return d


d = make(1)
d.set_current(0.0)
assert d._ser.i == 0, "erster Befehl verloren, Retry muss ihn nachholen"
d = make(3)
try:
    d.set_current(0.0)
    raise SystemExit("FEHLER: nicht uebernommener Sollwert wurde als ok gemeldet")
except PowerSupplyValueError as exc:
    print("erwarteter Fehler:", exc)
d = make(0)
d.set_voltage(24.0)
assert d._ser.v == 240
print("check_hcs34xx_verify: OK")

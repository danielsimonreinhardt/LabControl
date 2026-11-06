"""Prueft den HCS34xx-Treiber gegen ein simuliertes Geraet (keine Hardware):
  * BUGS_OFFEN #37: set_current/set_voltage/set_output lesen den Zustand zurueck und wiederholen,
    wenn das Geraet trotz "OK" den Wert nicht uebernommen hat.
  * Ausgang: SOUT0/SOUT1 + GOUT ("0" = EIN, "1" = AUS), Sollwerte bleiben dabei unberuehrt.
  * Tastensperre: Befehle senden KEIN ENDS mehr mit; release_keyboard() sendet genau ein ENDS.
  * Langsame Befehle werden geloggt.
Mock-Pruefung (simuliertes Geraet), keine Hardware."""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device-driver"))
from hcs34xx.driver import SLOW_EXCHANGE_S, HCS34xx, PowerSupplyError, PowerSupplyValueError  # noqa: E402


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

# --- Befehle senden kein ENDS mit; release_keyboard sendet genau eines
d = make(0)
d.get_setpoint()
d.get_output()
d.set_current(1.5)
assert "ENDS" not in d._ser.log, f"Befehle duerfen kein ENDS mitsenden: {d._ser.log}"
assert d.release_keyboard() is True
assert d._ser.log.count("ENDS") == 1 and d._ser.log[-1] == "ENDS"
d = make(0, ends=False)
assert d.release_keyboard() is False, "nicht quittiertes ENDS meldet False statt eines Fehlers"
d = make(0)
d._ser.write = lambda data: (_ for _ in ()).throw(OSError("weg"))
assert d.release_keyboard() is False, "Portfehler bei ENDS darf nicht werfen (Sperre faellt von selbst)"

# --- unerwartete GOUT-Antwort ist ein Wertfehler, kein Verbindungsabbruch
d = make(0)
d._ser.write = lambda data, _orig=d._ser.write: setattr(d._ser, "buf", b"7\rOK\r") if data.strip() == b"GOUT" else _orig(data)
try:
    d.get_output()
    raise SystemExit("FEHLER: unerwartete GOUT-Antwort wurde akzeptiert")
except PowerSupplyValueError:
    pass
assert issubclass(PowerSupplyValueError, PowerSupplyError)

# --- Langsamer Befehl wird mit Namen geloggt, hoechstens einmal je Intervall
records = []


class Grab(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


handler = Grab()
logging.getLogger("hcs34xx.driver").addHandler(handler)
d = make(0)
orig_write = d._ser.write


def slow_write(data, _orig=orig_write):
    if data.strip() == b"GETS":
        time.sleep(SLOW_EXCHANGE_S + 0.05)
    return _orig(data)


d._ser.write = slow_write
d.get_setpoint()
d.get_setpoint()
logging.getLogger("hcs34xx.driver").removeHandler(handler)
assert len(records) == 1 and "GETS" in records[0], f"langsamer Befehl: genau eine Meldung erwartet: {records}"

print("check_hcs34xx_verify: OK")

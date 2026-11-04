"""Prueft BUGS_OFFEN #38: Verbindungsversuche (Netzteil/Last/Generator) blockieren den
Worker-Thread nicht mehr, und der CAN-Abruf ist an ein Zeitbudget statt an 32 Frames
gekoppelt. Mock-Pruefung (simulierte Geraete), keine Hardware."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])
import device_worker as dw  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("OK   " if ok else "FEHLER ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def spin(seconds, until=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else False


SILENT_S = 1.0   # Lese-Timeout eines ausgeschalteten Netzteils (HCS34xx: timeout=1.0)
constructed = []
calls = []


class FakePsu:
    """Port ist da, Geraet antwortet je nach Port nicht (aus) oder sofort."""
    silent_ports = {"COM_SILENT_A", "COM_SILENT_B"}

    @classmethod
    def discover_ports(cls):
        return [SimpleNamespace(device=p, serial_number=None)
                for p in ("COM_SILENT_A", "COM_SILENT_B", "COM_OK")]

    def __init__(self, port):
        self.port = port
        constructed.append(port)

    def get_display(self):
        if self.port in self.silent_ports:
            time.sleep(SILENT_S)
            raise dw.PowerSupplyError("Timeout")
        return SimpleNamespace(voltage=12.0, current=0.1, constant_current=False)

    def set_output(self, on):
        calls.append(("set_output", self.port, on))

    def get_setpoint(self):
        return 12.0, 0.0

    def get_ovp(self):
        return 15.0

    def get_ocp(self):
        return 33.0

    def get_max(self):
        return 16.0, 30.0

    def close(self):
        calls.append(("close", self.port))


class FakeFg:
    probes = []

    @classmethod
    def discover_ports(cls):
        return [SimpleNamespace(device="COM_CH340", serial_number=None)]

    def __init__(self, port):
        self.port = port

    def probe(self):
        FakeFg.probes.append(self.port)
        return False

    def close(self):
        pass


class NoPorts:
    @classmethod
    def discover_ports(cls):
        return []


dw.HCS34xx = FakePsu
dw.JDS66xx = FakeFg
dw.KoradKEL102.discover_ports = classmethod(lambda cls: [])
dw.MicroHIL.discover = classmethod(lambda cls: None)

print("Reconnect blockiert den Worker nicht")
worker = dw.DeviceWorker()
events = []
worker.psu_connected.connect(lambda d, on: events.append(("psu_connected", d, on)))
# Ratings/Limits lesen weitere Werte vom Geraet -- hier nicht Gegenstand der Pruefung.
worker._emit_psu_ratings = lambda d: None
worker._emit_psu_limits = lambda d: None

t0 = time.monotonic()
worker._try_reconnect()
blocked = time.monotonic() - t0
check("_try_reconnect kehrt sofort zurueck (2 stumme Netzteile = 2 s Timeout)", blocked < 0.3, f"{blocked * 1000:.0f} ms")

worker._try_reconnect()   # zweiter Tick, waehrend die Probes noch laufen
check("Kein zweiter Probe auf einen noch laufenden Port",
      sorted(constructed) == ["COM_OK", "COM_SILENT_A", "COM_SILENT_B"], str(sorted(constructed)))

ok = spin(2.0, until=lambda: ("psu_connected", "psu:COM_OK", True) in events)
check("Antwortendes Netzteil wird uebernommen", ok)
check("Netzteil vor 'verbunden' ausgeschaltet (SOUT1)", ("set_output", "COM_OK", False) in calls)
check("Stumme Netzteile bleiben getrennt",
      "psu:COM_SILENT_A" not in worker._psus and "psu:COM_SILENT_B" not in worker._psus)
spin(1.5)
check("Pending-Markierung der stummen Netzteile nach dem Timeout freigegeben",
      not any(k.startswith("psu/psu:COM_SILENT") for k in worker._reconnect_pending), str(worker._reconnect_pending))

worker._try_reconnect()
spin(1.5, until=lambda: constructed.count("COM_SILENT_A") == 2)
check("Naechster Tick versucht die stummen Ports erneut", constructed.count("COM_SILENT_A") == 2)

print("Generator, der den Handshake nicht besteht")
FakeFg.probes.clear()
worker._fg_probe_after.clear()
worker._reconnect_pending.clear()
worker._reconnect_fgs()
spin(0.5, until=lambda: "COM_CH340" in worker._fg_probe_after)
check("Port erhaelt Sperrfrist", worker._fg_probe_after.get("COM_CH340", 0) > time.monotonic())
worker._reconnect_fgs()
spin(0.2)
check("Innerhalb der Sperrfrist kein zweiter Probe", FakeFg.probes == ["COM_CH340"], str(FakeFg.probes))

print("CAN-Abruf mit Zeitbudget")


class FakeBus:
    def __init__(self, n):
        self.frames = [SimpleNamespace(arbitration_id=0x100, data=bytes(8), extended=False, timestamp=0.0)
                       for _ in range(n)]

    def recv(self, timeout=0.0):
        return self.frames.pop() if self.frames else None


worker2 = dw.DeviceWorker()
worker2._can_buses["can:fake"] = FakeBus(1000)
worker2._can_stats["can:fake"] = [0, 0]
worker2._poll()
rx = worker2._can_stats["can:fake"][1]
check("Ein Zyklus holt mehr als die frueheren 32 Frames ab", rx > 32, f"{rx} von 1000")
worker2._poll()
worker2._poll()
check("Rueckstand wird in wenigen Zyklen abgebaut", worker2._can_stats["can:fake"][1] == 1000,
      str(worker2._can_stats["can:fake"][1]))

print("Warnung bei langsamem Poll-Zyklus")
worker3 = dw.DeviceWorker()
slow = SimpleNamespace(get_display=lambda: (time.sleep(dw.POLL_SLOW_S + 0.1),
                                            SimpleNamespace(voltage=1, current=1, constant_current=False))[1])
worker3._psus["psu:SLOW"] = slow
records = []


class Grab(__import__("logging").Handler):
    def emit(self, record):
        records.append(record.getMessage())


h = Grab()
dw.logger.addHandler(h)
worker3._poll()
worker3._poll()
dw.logger.removeHandler(h)
slow_msgs = [m for m in records if "Poll-Zyklus langsam" in m]
check("Langsamer Zyklus wird mit Geraetename geloggt", len(slow_msgs) == 1 and "psu:SLOW" in slow_msgs[0], str(slow_msgs))

print()
print("ALLE PRUEFUNGEN BESTANDEN" if not failures else f"{len(failures)} PRUEFUNG(EN) FEHLGESCHLAGEN: {failures}")
sys.exit(1 if failures else 0)

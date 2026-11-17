"""Prueft die Frische-Grenze je Geraet und die schnellere Abfrage des Funktionsgenerators (1.0.1, PI-78 a).

- Watchdog im Testablauf (safety.SafetyMonitor): Funktionsgenerator mit 3 s Luecke loest NICHT aus, ein
  Netzteil mit 3 s Luecke schon, Funktionsgenerator ueber 5 s loest aus; der Zustand des Generators
  (fg_state) ist sein Heartbeat (vorher: kein Heartbeat -> Abbruch nach ~2,5 s auch im Warteschritt),
  ebenso battsim_state; Fenster und Kommandozeile verdrahten beides
- Partnergeraete "@<rechner>/<id>": Grenze aus der Geraeteart; der Besitzer setzt "stale" seiner Kachel mit
  dieser Grenze (LiveState -> Freigabe -> partner_devices: Generator 3 s alt bleibt online, Netzteil nicht)
- Abfrage des Generators (device_worker._poll_fgs) gegen eine Nachbildung der seriellen Registerbank:
  je Zyklus nur Ausgaenge + 3 rollierende Felder (4 statt 12 Abfragen), alle Felder nach 4 Zyklen;
  nach einem Schreibbefehl wird genau das Feld (und was das Geraet anpasst) zurueckgelesen und gemeldet;
  langsamer Zyklus wird mit Geraetename geloggt

Qt ohne Fenster, keine Hardware, kein COM-Port.  Aufruf:  python tools/check_stale_per_device.py
Die Zeiten sind Pruefling-Zeiten (verstellte Uhr), keine Aussage ueber das echte Geraet.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class Clock:
    """Verstellbare Uhr fuer safety.time.monotonic."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


# -- Nachbildung des Generators an der seriellen Leitung (wie tools/check_jds66xx.py) ------------------

class FakeGenerator:
    def __init__(self) -> None:
        self.regs: dict[int, list[int]] = {
            20: [0, 0], 21: [0], 22: [0], 23: [100000, 0], 24: [100000, 0],
            25: [1000], 26: [1000], 27: [1000], 28: [1000], 29: [500], 30: [500], 31: [0],
        }
        self.sent: list[str] = []
        self.delay_s = 0.0

    def reply(self, line: str) -> bytes:
        self.sent.append(line)
        if self.delay_s:
            time.sleep(self.delay_s)
        m = re.match(r"^:([rw])(\d+)=(.*)\.$", line)
        if not m:
            return b"\r\n"
        op, reg, data = m.group(1), int(m.group(2)), m.group(3)
        if op == "r":
            return (":r%d=%s.\r\n" % (reg, ",".join(map(str, self.regs.get(reg, []))))).encode()
        self.regs[reg] = [int(x) for x in data.split(",")]
        return b":ok\r\n"

    def reads(self) -> list[int]:
        return [int(re.match(r"^:r(\d+)=", s).group(1)) for s in self.sent if s.startswith(":r")]

    def writes(self) -> list[int]:
        return [int(re.match(r"^:w(\d+)=", s).group(1)) for s in self.sent if s.startswith(":w")]


class FakeSerial:
    device: FakeGenerator | None = None

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


def check_watchdog() -> None:
    import safety
    from field_catalog import stale_timeout_s

    print("Grenze je Geraet")
    check("Funktionsgenerator 5 s, Netzteil/Last 2 s",
          stale_timeout_s("fg:COM21") == 5.0 and stale_timeout_s("psu:0001") == 2.0
          and stale_timeout_s("load:x") == 2.0, str((stale_timeout_s("fg:COM21"), stale_timeout_s("psu:0001"))))
    check("Partnergeraet: Grenze aus der Geraeteart",
          stale_timeout_s("@raspberrypi/fg:COM21") == 5.0 and stale_timeout_s("@raspberrypi/psu:0001") == 2.0)

    print("Watchdog im Testablauf")
    clock = Clock()
    real_time = safety.time
    safety.time = clock   # nur monotonic() wird benutzt
    try:
        def run(device_ids, beats, gap_s):
            mon = safety.SafetyMonitor({})
            trips: list[tuple[str, str]] = []
            mon.tripped.connect(lambda d, r: trips.append((d, r)))
            mon.begin_run_supervision(set(device_ids))
            for device_id in beats:
                beat(mon, device_id)
            clock.now += gap_s
            mon._check_stale()
            mon.end_run_supervision()
            return trips

        def beat(mon, device_id):
            if "fg:" in device_id:
                mon.on_fg_state(device_id, {})
            elif "battsim:" in device_id:
                mon.on_battsim_state(device_id, {})
            else:
                mon.on_psu_measurement(device_id, 12.0, 0.1, False)

        check("Funktionsgenerator 3 s ohne neuen Zustand: kein Abbruch", run(["fg:COM21"], ["fg:COM21"], 3.0) == [])
        trips = run(["psu:0001"], ["psu:0001"], 3.0)
        check("Netzteil 3 s ohne Messwert: Abbruch", [d for d, _ in trips] == ["psu:0001"], str(trips))
        trips = run(["fg:COM21"], ["fg:COM21"], 5.6)
        check("Funktionsgenerator ueber 5 s: Abbruch", [d for d, _ in trips] == ["fg:COM21"], str(trips))
        trips = run(["fg:COM21", "psu:0001"], ["fg:COM21", "psu:0001"], 3.0)
        check("gemischt: nur das Netzteil loest aus", [d for d, _ in trips] == ["psu:0001"], str(trips))
        check("Partner-Generator 3 s: kein Abbruch",
              run(["@raspberrypi/fg:COM21"], ["@raspberrypi/fg:COM21"], 3.0) == [])
        trips = run(["@raspberrypi/psu:0001"], ["@raspberrypi/psu:0001"], 3.0)
        check("Partner-Netzteil 3 s: Abbruch", [d for d, _ in trips] == ["@raspberrypi/psu:0001"], str(trips))
        check("Batterie-Simulator mit Zustand alle 0,1 s: kein Abbruch",
              run(["battsim:can0"], ["battsim:can0"], 1.0) == [])

        # Warteschritt ueber 10 s: der Generator meldet alle 0,5 s seinen Zustand (FG_POLL_INTERVAL_MS)
        mon = safety.SafetyMonitor({})
        trips = []
        mon.tripped.connect(lambda d, r: trips.append(d))
        mon.begin_run_supervision({"fg:COM21"})
        for _ in range(20):
            clock.now += 0.5
            mon.on_fg_state("fg:COM21", {})
            mon._check_stale()
        check("Warteschritt 10 s mit Generator-Zustand alle 0,5 s: kein Abbruch", trips == [], str(trips))
        mon.end_run_supervision()
        # Gegenprobe zum Fehlerbild: ohne Heartbeat bricht er ab (frueher schon nach 2 s)
        mon = safety.SafetyMonitor({})
        trips = []
        mon.tripped.connect(lambda d, r: trips.append(d))
        mon.begin_run_supervision({"fg:COM21"})
        clock.now += 5.6
        mon._check_stale()
        check("Gegenprobe: Generator ohne Zustand loest aus", trips == ["fg:COM21"], str(trips))
    finally:
        safety.time = real_time

    print("Verdrahtung")
    for name, worker, mon in (("main_window.py", "self._worker", "self._safety"),
                              ("headless.py", "self.worker", "self.safety")):
        text = (ROOT / "lab_gui" / name).read_text(encoding="utf-8")
        check(f"{name}: fg_state und battsim_state als Heartbeat am Watchdog",
              f"{worker}.fg_state.connect({mon}.on_fg_state)" in text
              and f"{worker}.battsim_state.connect({mon}.on_battsim_state)" in text)


def check_partner_tiles() -> None:
    import share_api
    from live_state import LiveState
    from partner_client import PartnerSnapshot
    from partner_devices import PartnerDevices
    from share_render import _tile_block

    print("Verbund: Grenze kommt mit der Kachel an")
    state = LiveState()
    fg = {"outputs": [True, False], "phase": 0.0,
          "channels": [{"waveform": 0, "frequency": 1000.0, "amplitude": 1.0, "offset": 0.0, "duty": 50.0}] * 2}
    for kind, device_id in (("fg", "fg:COM21"), ("psu", "psu:0001")):
        state.on_device_known(kind, device_id, device_id)
        state.set_online(device_id, True)
    state.on_fg_state("fg:COM21", fg)
    state.on_psu_measurement("psu:0001", 12.0, 0.5, False)
    for entry in state._devices.values():   # beide Werte 3 s alt
        entry["ts"] -= 3.0
    snap = state.snapshot()
    devices = snap["devices"]
    check("Besitzer: Generator 3 s alt nicht veraltet, Netzteil schon",
          devices["fg:COM21"]["stale"] is False and devices["psu:0001"]["stale"] is True,
          str({d: e["stale"] for d, e in devices.items()}))
    tiles = [share_api._tile_detail(d, devices[d], snap) for d in ("fg:COM21", "psu:0001")]
    for tile in tiles:
        tile.update({"control": True, "control_available": True})

    class Link:
        def name(self):
            return "Pi"

    from PySide6.QtCore import QObject, Signal

    class FakeLink(QObject, Link):
        snapshot_changed = Signal(object)
        action_finished = Signal(int, str, str, bool, str)

    pd = PartnerDevices(FakeLink())
    online: dict[str, bool] = {}
    pd.online_changed.connect(lambda _k, rid, on: online.__setitem__(rid, on))
    pd.on_snapshot(PartnerSnapshot(host="raspberrypi", remote_effective=True, lock="free", tiles=tiles))
    check("Partner: Generator online, Netzteil (veraltet) nicht",
          online.get("@raspberrypi/fg:COM21") is True and online.get("@raspberrypi/psu:0001") is False, str(online))
    for entry in state._devices.values():   # jetzt 5,6 s alt
        entry["ts"] -= 2.6
    check("Besitzer: Generator ueber 5 s veraltet", state.snapshot()["devices"]["fg:COM21"]["stale"] is True)
    html_fg = _tile_block({"kind": "fg", "label": "FG", "age_s": 4.0, "stale": False, "fields": []})
    html_psu = _tile_block({"kind": "psu", "label": "NT", "age_s": 4.0, "stale": False, "fields": []})
    check("Display: Generator 4 s nicht 'veraltet', Netzteil schon",
          "veraltet" not in html_fg and "veraltet" in html_psu)


def check_fg_poll() -> None:
    import device_worker as dw
    from jds66xx import driver

    print("Abfrage des Funktionsgenerators")
    driver.serial.Serial = FakeSerial
    fake = FakeGenerator()
    FakeSerial.device = fake
    gen = driver.JDS66xx("FAKE")
    worker = dw.DeviceWorker()
    states: list[dict] = []
    worker.fg_state.connect(lambda _d, st: states.append(st))
    worker._fgs["fg:FAKE"] = gen
    worker._emit_fg_state("fg:FAKE")
    check("Verbinden: voller Zustand (12 Abfragen)", len(fake.reads()) == 12 and len(states) == 1,
          str(fake.reads()))
    check("Intervall 0,5 s", dw.FG_POLL_INTERVAL_MS == 500)

    seen: set[int] = set()
    per_cycle: list[list[int]] = []
    for _ in range(4):
        fake.sent.clear()
        worker._poll_fgs()
        per_cycle.append(fake.reads())
        seen.update(fake.reads())
    check("je Zyklus 4 Abfragen (vorher 12), keine Schreibbefehle",
          all(len(c) == 4 for c in per_cycle) and not fake.writes(), str(per_cycle))
    check("je Zyklus zuerst die Ausgaenge (Register 20)", all(c[0] == 20 for c in per_cycle), str(per_cycle))
    check("nach 4 Zyklen alle 12 Register gelesen", seen == set(range(20, 32)), str(sorted(seen)))
    check("je Zyklus ein Zustand gemeldet (Heartbeat)", len(states) == 5, str(len(states)))

    fake.regs[20] = [1, 0]     # Handeingriff am Geraet: Ausgang 1 EIN
    fake.regs[27] = [1250]     # Offset Kanal 1 auf 2,5 V
    worker._poll_fgs()
    check("Ausgang am Geraet: im naechsten Zyklus", states[-1]["outputs"] == [True, False], str(states[-1]))
    for _ in range(4):
        worker._poll_fgs()
    check("Offset am Geraet: spaetestens nach 4 weiteren Zyklen",
          abs(states[-1]["channels"][0]["offset"] - 2.5) < 1e-9, str(states[-1]["channels"][0]))

    print("Nach eigenem Schreibbefehl")
    fake.sent.clear()
    states.clear()
    ok, _msg, _v = worker._dispatch_action("fg:FAKE", "fg", "FG_FREQ", 2500.0, 2)   # Testablauf-Schritt
    check("Testablauf FG_FREQ K2: schreiben + Frequenz und Amplitude K2 zuruecklesen",
          ok and fake.writes() == [24] and fake.reads() == [24, 26], str(fake.sent))
    check("Frequenz sofort gemeldet", states and abs(states[-1]["channels"][1]["frequency"] - 2500.0) < 1e-9,
          str(states[-1:]))
    fake.sent.clear()
    worker.set_fg_output("fg:FAKE", 2, True)
    check("Ausgang K2 EIN: Lesen-Aendern-Schreiben + 1 Abfrage",
          fake.writes() == [20] and fake.reads() == [20, 20], str(fake.sent))
    check("Ausgang sofort gemeldet", states[-1]["outputs"] == [True, True], str(states[-1]))
    fake.sent.clear()
    worker.set_fg_waveform("fg:FAKE", 1, 2)   # Puls: nur dort wirkt der Tastgrad (PI-80 a)
    check("Form K1: Form, Frequenz, Amplitude K1 zuruecklesen",
          fake.writes() == [21] and fake.reads() == [21, 23, 25], str(fake.sent))
    fake.sent.clear()
    worker.set_fg_duty("fg:FAKE", 1, 30.0)
    # PI-80 a: vor dem Schreiben die Form lesen (nur bei Puls), danach nur dieses Feld zurueck
    check("Tastgrad K1: Form pruefen, dann nur dieses Feld", fake.writes() == [29] and fake.reads() == [21, 29]
          and abs(states[-1]["channels"][0]["duty"] - 30.0) < 1e-9, str(fake.sent))
    fake.sent.clear()
    worker.set_fg_phase("fg:FAKE", 90.0)
    check("Phase: nur dieses Feld", fake.writes() == [31] and fake.reads() == [31]
          and abs(states[-1]["phase"] - 90.0) < 1e-9, str(fake.sent))
    check("uebrige Werte im Zustand erhalten", abs(states[-1]["channels"][1]["frequency"] - 2500.0) < 1e-9
          and states[-1]["outputs"] == [True, True], str(states[-1]))

    print("Langsamer Zyklus")
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Grab()
    dw.logger.addHandler(handler)
    fake.delay_s = (dw.POLL_SLOW_S + 0.1) / 4
    worker._poll_fgs()
    worker._poll_fgs()
    fake.delay_s = 0.0
    dw.logger.removeHandler(handler)
    slow = [m for m in records if "Poll-Zyklus langsam" in m]
    check("langsamer Generator-Zyklus einmal mit Geraetename geloggt", len(slow) == 1 and "fg:FAKE" in slow[0],
          str(slow))

    print("Simulation (Mock)")
    worker2 = dw.DeviceWorker()
    states2: list[dict] = []
    worker2.fg_state.connect(lambda _d, st: states2.append(st))
    worker2._add_mock_fg()
    for _ in range(3):
        worker2._poll_fgs()
    check("Mock: je Zyklus ein Zustand", len(states2) == 4, str(len(states2)))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    check_watchdog()
    check_partner_tiles()
    check_fg_poll()
    app.processEvents()
    print()
    print("ALLE PRUEFUNGEN BESTANDEN" if not FAILURES else f"{len(FAILURES)} FEHLGESCHLAGEN: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

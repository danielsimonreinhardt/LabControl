"""Prueft PI-76 a (Abbruch-Block) und PI-79 a (Abschaltung nur fuer geschaltete Geraete und den Ausloeser), 1.0.1.

- format   -- Format 7 (Bausteine 6) nur mit Abbruch-Block, Laden/Speichern verlustfrei, Format 6 lehnt ab;
              Regeln: nur Geraeteaktionen/Warten, keine Pruefung/Lese-Aktion, nur einer, nur oberste Ebene,
              Hoechstdauer; geschaltete Geraete (Lesen zaehlt nicht, auch Abbruch-Block)
- runner   -- TestRunner allein: Block laeuft in Reihenfolge bei Stopp, Schrittfehler, verletzter Pruefung,
              Abbrechen bei der Bestaetigung, Freigabe-Hoechstzeit; nicht bei normalem Ende; Fehler im Block ->
              Rest wird versucht; Hoechstdauer; noch unquittierte Aktion wird vor dem Block abgewartet;
              Sicherheitsabschaltung/ALLE AUS ohne Block bzw. bricht ihn ab; run_stopped/step_failed erst danach
- session  -- RunSession mit Attrappe des Workers: ohne Block Standard-Menge (geschaltete Geraete), nur gelesenes
              Netzteil bleibt an; Ueberwachen-Grenze schaltet die Quelle mit ab; Block fehlerfrei -> keine
              weitere Abschaltung; Fehler im Block -> Rueckfall; Watchdog im Lauf -> geschaltete + Ausloeser,
              kein ALLE AUS, kein Block; Watchdog ohne Lauf -> ALLE AUS wie bisher; Partnergeraete ueber
              partner_devices_off; nicht erreichbare Geraete; Bericht, run-result.json und Laufregister (get_run)
- partner  -- PartnerLink.devices_off (POST /devices-off, Rueckfall ALLE AUS bei aelterem Partner) und
              share_api /devices-off (Token, Pruefung, Fehlschlaege)
- editor   -- Menue "Abbruch-Block … Ende", Zeile/Ende-Beschriftung, Anzeige "Beim Abbruch aus" bzw.
              "Abbruch-Block" mit Tooltip, Fehler bei unzulaessigem Schritt, englische Texte
- window   -- echtes Hauptfenster (Simulation): Stopp per HTTP mit Block -> shutdown in GET /runs/{id};
              ALLE AUS (Knopf) waehrend des Laufs -> kein Block, ALLE AUS; POST /devices-off schaltet nur das Geraet

Offscreen, Simulation/Attrappen, keine Hardware.  Aufruf:  python tools/check_abort_block.py [abschnitt ...]
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-abort-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN = "abort-check-token"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return bool(until()) if until is not None else False


def psu(device_id: str, action: str, **kw):
    from testcase_model import TestStep
    return TestStep(device_kind="psu", device_id=device_id, action=action, **kw)


def fg(device_id: str, action: str, **kw):
    from testcase_model import TestStep
    return TestStep(device_kind="fg", device_id=device_id, action=action, hil_channel=1, **kw)


# -- format ----------------------------------------------------------------------------------------------

def section_format() -> None:
    print("format -- Dateiformat und Regeln")
    import testcase_model as tm
    TS = tm.TestStep

    folder = Path(tempfile.mkdtemp(prefix="lc-abort-format-"))
    steps = [psu("psu:A", "PSU_OUT_ON"), TS(step_type="wait", duration=1.0),
             TS(step_type="on_abort"), fg("fg:1", "FG_OUT_OFF"), TS(step_type="wait", duration=0.5),
             psu("psu:A", "PSU_OUT_OFF"), TS(step_type="end")]
    path = folder / "mit_block.json"
    tm.save_steps(steps, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    check("mit Abbruch-Block: Format 7", data["version"] == 7, str(data["version"]))
    loaded, _meta = tm.load_testcase(path)
    check("Laden: Block zurueck", [s.step_type for s in loaded] == [s.step_type for s in steps])
    tm.save_steps(loaded, folder / "mit_block2.json")
    check("Laden+Speichern verlustfrei", json.loads((folder / "mit_block2.json").read_text(encoding="utf-8")) == data)
    tm.save_steps([TS(step_type="await_release", duration=5.0)], folder / "v6.json")
    check("ohne Block bleibt Freigabe bei Format 6",
          json.loads((folder / "v6.json").read_text(encoding="utf-8"))["version"] == 6)
    tm.save_steps([psu("psu:A", "PSU_OUT_ON")], folder / "v2.json")
    check("ohne neue Schritte weiter Format 2", json.loads((folder / "v2.json").read_text(encoding="utf-8"))
          ["version"] == 2)
    old = tm.FILE_FORMAT_VERSION
    try:
        tm.FILE_FORMAT_VERSION = 6
        try:
            tm.load_testcase(path)
            refused = False
        except ValueError as exc:
            refused = "neueren Programmversion" in str(exc)
    finally:
        tm.FILE_FORMAT_VERSION = old
    check("Version mit Format 6 lehnt die Datei ab", refused)
    block = folder / "baustein.json"
    tm.save_block(steps[2:], "Abbruch", block)
    check("Baustein mit Abbruch-Block: Format 6", json.loads(block.read_text(encoding="utf-8"))["version"] == 6)
    name, block_steps = tm.load_block(block)
    check("Baustein laden", name == "Abbruch" and block_steps[0].step_type == "on_abort")

    _m, _d, errors = tm.validate_structure(steps)
    check("gueltiger Block ohne Fehler", not errors, str(errors))
    check("Indizes des Blocks", tm.abort_block_steps(steps) == [3, 4, 5], str(tm.abort_block_steps(steps)))
    check("Geraete des Blocks", tm.abort_block_devices(steps) == [("fg", "fg:1"), ("psu", "psu:A")])

    def errs(items):
        return [m for _i, m in tm.validate_structure(items)[2]]
    bad = errs([TS(step_type="on_abort"), TS(step_type="confirm"), TS(step_type="end")])
    check("Bestaetigung im Block abgelehnt", any("nur Geräteaktionen und Warten" in m for m in bad), str(bad))
    bad = errs([TS(step_type="on_abort"), TS(step_type="await_release"), TS(step_type="end")])
    check("Freigabe im Block abgelehnt", bool(bad), str(bad))
    bad = errs([TS(step_type="on_abort"), psu("psu:A", "PSU_OUT_OFF", check_enabled=True), TS(step_type="end")])
    check("Pruefung im Block abgelehnt", any("Prüfungen" in m for m in bad), str(bad))
    bad = errs([TS(step_type="on_abort"), TS(device_kind="hil", device_id="hil:1", action="HIL_IN_READ"),
                TS(step_type="end")])
    check("Lese-Aktion im Block abgelehnt", bool(bad), str(bad))
    bad = errs([TS(step_type="on_abort"), TS(device_kind="picoscope", device_id="picoscope:1",
                                             action="SCOPE_ARM"), TS(step_type="end")])
    check("Oszilloskop im Block abgelehnt", bool(bad), str(bad))
    bad = errs([TS(step_type="on_abort"), TS(device_kind="uds", device_id="uds:dut", action="UDS_RESET"),
                TS(step_type="end")])
    check("UDS im Block abgelehnt", bool(bad), str(bad))
    bad = errs([TS(step_type="on_abort"), TS(step_type="loop"), TS(step_type="end"), TS(step_type="end")])
    check("Block im Block abgelehnt", bool(bad), str(bad))
    bad = errs([TS(step_type="loop"), TS(step_type="on_abort"), TS(step_type="end"), TS(step_type="end")])
    check("Abbruch-Block nur auf oberster Ebene", any("oberster Ebene" in m for m in bad), str(bad))
    bad = errs([TS(step_type="on_abort"), TS(step_type="end"), TS(step_type="on_abort"), TS(step_type="end")])
    check("nur ein Abbruch-Block", any("Nur ein" in m for m in bad), str(bad))
    bad = errs([TS(step_type="on_abort"), TS(step_type="wait", duration=50.0), psu("psu:A", "PSU_OUT_OFF",
                                                                                     duration=20.0),
                TS(step_type="end")])
    check("Hoechstdauer 60 s", any("höchstens 60 s" in m for m in bad), str(bad))
    ok = errs([TS(step_type="on_abort"), TS(step_type="wait", duration=50.0, enabled=True),
               TS(step_type="wait", duration=50.0, enabled=False), TS(step_type="end")])
    check("deaktivierte Schritte zaehlen nicht zur Dauer", not ok, str(ok))
    can_ok = errs([TS(step_type="on_abort"), TS(device_kind="can", device_id="can:x", action="RESTBUS_STOP"),
                   TS(device_kind="battsim", device_id="battsim:x", action="BS_OUT_OFF"),
                   TS(device_kind="hil", device_id="hil:1", action="HIL_OUT_OFF"),
                   TS(device_kind="load", device_id="load:1", action="OUT_OFF"), TS(step_type="end")])
    check("Restbus, Batterie-Simulator, microHIL, Last erlaubt", not can_ok, str(can_ok))

    run = [psu("psu:A", "PSU_VOLT", value=12.0), psu("psu:A", "PSU_OUT_ON"),
           TS(step_type="monitor", cond_device_kind="psu", cond_device_id="psu:B", cond_field="current"),
           fg("fg:1", "FG_OUT_ON"), TS(step_type="end"),
           TS(step_type="if", cond_source="measurement", cond_device_kind="psu", cond_device_id="psu:C"),
           TS(step_type="end"),
           TS(device_kind="hil", device_id="hil:1", action="HIL_AIN_READ"),
           TS(step_type="loop", enabled=False), psu("psu:D", "PSU_OUT_ON"), TS(step_type="end"),
           TS(device_kind="can", device_id="can:x", action="RESTBUS_START"),
           TS(device_kind="can", device_id="can:x", action="CAN_SEND"),
           psu("", "PSU_OUT_OFF", enabled=False)]
    check("geschaltete Geraete: Lesen/Bedingung/Ueberwachen/deaktiviert zaehlen nicht, Restbus schon",
          tm.switched_devices(run) == [("psu", "psu:A"), ("fg", "fg:1"), ("can", "can:x")],
          str(tm.switched_devices(run)))
    check("Bezeichnung 'Abbruch-Block'", tm.CONTROL_STEP_LABELS["on_abort"] == "Abbruch-Block")


# -- runner ----------------------------------------------------------------------------------------------

class FakeDevices:
    """Quittiert Aktionen asynchron; Geraete in `fail` lehnen ab, in `hold` quittieren erst mit release()."""

    def __init__(self, runner) -> None:
        from PySide6.QtCore import QTimer
        self.done: list[tuple[str, str]] = []
        self.fail: set[str] = set()
        self.hold: set[str] = set()
        self._held = []
        self.runner = runner

        def execute(device_id, kind, action, value, channel):
            def ack():
                self.done.append((device_id, action))
                if device_id in self.fail:
                    runner.on_action_completed(False, f"{device_id} antwortet nicht", 0.0)
                else:
                    runner.on_action_completed(True, "", 0.0)
            if device_id in self.hold:
                self._held.append(ack)
            else:
                QTimer.singleShot(0, ack)
        runner.execute_action.connect(execute)

    def release(self) -> None:
        held, self._held = self._held, []
        for ack in held:
            ack()


def record(runner):
    events: list = []
    connections = [
        (runner.step_started, lambda i, _s: events.append(("started", i))),
        (runner.run_finished, lambda: events.append(("finished",))),
        (runner.run_stopped, lambda: events.append(("stopped",))),
        (runner.step_failed, lambda i, m: events.append(("failed", i, m))),
        (runner.abort_step_started, lambda i, _s: events.append(("abort_started", i))),
        (runner.abort_step_done, lambda i, ok, m: events.append(("abort_done", i, ok, m))),
    ]
    for signal, slot in connections:
        signal.connect(slot)

    def stop():
        for signal, slot in connections:
            signal.disconnect(slot)
    return events, stop


def abort_steps():
    from testcase_model import TestStep
    return [TestStep(step_type="on_abort"), fg("fg:1", "FG_OUT_OFF"), TestStep(step_type="wait", duration=0.2),
            psu("psu:A", "PSU_OUT_OFF"), psu("psu:12", "PSU_OUT_OFF"), TestStep(step_type="end")]


BLOCK_ORDER = [("fg:1", "FG_OUT_OFF"), ("psu:A", "PSU_OUT_OFF"), ("psu:12", "PSU_OUT_OFF")]


def section_runner(app) -> None:
    print("runner -- TestRunner allein")
    import testcase_runner
    from testcase_model import TestStep
    from testcase_runner import TestRunner

    runner = TestRunner()
    devices = FakeDevices(runner)
    infos: list = []
    runner.shutdown_handler = lambda info: infos.append(info) and False
    main = [psu("psu:A", "PSU_OUT_ON"), fg("fg:1", "FG_OUT_ON"), TestStep(step_type="wait", duration=5.0)]

    def run(steps, act=None, until=None, seconds=5.0):
        devices.done.clear()
        infos.clear()
        events, stop = record(runner)
        runner.start(steps)
        if until is not None:
            spin(app, 3.0, until)
        if act is not None:
            act()
        spin(app, seconds, lambda: not runner.is_running())
        stop()
        return events

    # Stopp
    events = run(main + abort_steps(), act=runner.stop,
                 until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
    order = [d for d in devices.done if d[1].endswith("_OFF")]
    check("Stopp: Abbruch-Block in Reihenfolge", order == BLOCK_ORDER, str(order))
    check("Stopp: run_stopped erst nach dem Block", events[-1] == ("stopped",)
          and events.index(("abort_started", 4)) < events.index(("stopped",)), str(events))
    check("Stopp: Block-Ergebnis ok, Abschaltung erfaehrt es", bool(infos) and infos[0]["block_ok"]
          and infos[0]["kind"] == "stopped" and [r["index"] for r in infos[0]["block"]] == [4, 5, 6, 7],
          str(infos))
    check("Stopp: Block-Schritte nicht als normale Schritte gemeldet",
          not any(e[0] == "started" and e[1] >= 3 for e in events), str(events))

    # normales Ende: kein Block
    events = run([psu("psu:A", "PSU_OUT_ON")] + abort_steps())
    check("normales Ende: Block laeuft nicht", ("finished",) in events and devices.done == [("psu:A", "PSU_OUT_ON")]
          and not infos, f"{events} {devices.done}")

    # Schrittfehler (Geraet lehnt ab) -> Block, danach step_failed
    devices.fail = {"psu:X"}
    events = run([psu("psu:A", "PSU_OUT_ON"), psu("psu:X", "PSU_OUT_ON")] + abort_steps())
    devices.fail = set()
    failed = [e for e in events if e[0] == "failed"]
    check("Schrittfehler: Block laeuft, dann step_failed", failed and failed[0][1] == 1
          and [d for d in devices.done if d[1].endswith("_OFF")] == BLOCK_ORDER and events[-1][0] == "failed",
          f"{events} {devices.done}")
    check("Schrittfehler: Ausloeser = Geraet des Schritts", infos and infos[0]["trigger"] == ("psu", "psu:X"),
          str(infos and infos[0]["trigger"]))

    # Pruefung mit Abbruch (Lese-Aktion ausserhalb der Grenzen)
    # die Attrappe liefert den Lesewert 0.0 -> ausserhalb 1..2
    read = TestStep(device_kind="hil", device_id="hil:1", action="HIL_AIN_READ", check_enabled=True,
                    check_min=1.0, check_max=2.0, check_abort=True)
    events = run([read] + abort_steps())
    check("verletzte Pruefung: Block laeuft, dann FAIL",
          [d for d in devices.done if d[1].endswith("_OFF")] == BLOCK_ORDER and events[-1][0] == "failed",
          f"{events} {devices.done}")
    check("verletzte Pruefung: Ausloeser = gepruefte Quelle", bool(infos) and infos[0]["trigger"] == ("hil", "hil:1"),
          str(infos))

    # Abbrechen bei der Bestaetigung
    runner.confirm_available = True
    events = run([psu("psu:A", "PSU_OUT_ON"), TestStep(step_type="confirm", params={"text": "Weiter?"})]
                 + abort_steps(), act=lambda: runner.confirm(False),
                 until=lambda: runner.waiting() is not None)
    check("Bestaetigung abgebrochen: Block laeuft, dann gestoppt",
          [d for d in devices.done if d[1].endswith("_OFF")] == BLOCK_ORDER and events[-1] == ("stopped",),
          f"{events} {devices.done}")

    # Freigabe-Hoechstzeit
    runner.release_available = True
    events = run([psu("psu:A", "PSU_OUT_ON"), TestStep(step_type="await_release", duration=0.3)] + abort_steps())
    check("Freigabe-Hoechstzeit: Block laeuft, dann FAIL",
          [d for d in devices.done if d[1].endswith("_OFF")] == BLOCK_ORDER and events[-1][0] == "failed",
          f"{events} {devices.done}")

    # Fehler im Block: Rest wird versucht, Ergebnis nicht ok
    devices.fail = {"fg:1"}
    events = run(main + abort_steps(), act=runner.stop, until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
    devices.fail = set()
    done = [e for e in events if e[0] == "abort_done"]
    check("Fehler im Block: Rest trotzdem versucht", [d for d in devices.done if d[1].endswith("_OFF")]
          == BLOCK_ORDER and len(done) == 4 and done[0][2] is False, str(done))
    check("Fehler im Block: block_ok False (Rueckfall in RunSession)", infos and not infos[0]["block_ok"])

    # Hoechstdauer
    old = testcase_runner.ABORT_BLOCK_MAX_S
    testcase_runner.ABORT_BLOCK_MAX_S = 0.3
    try:
        long_block = [TestStep(step_type="on_abort"), TestStep(step_type="wait", duration=2.0),
                      psu("psu:A", "PSU_OUT_OFF"), TestStep(step_type="end")]
        t0 = time.monotonic()
        events = run(main + long_block, act=runner.stop, until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
        took = time.monotonic() - t0
    finally:
        testcase_runner.ABORT_BLOCK_MAX_S = old
    check("Hoechstdauer: Block abgebrochen, Rest nicht ausgefuehrt", infos and infos[0]["timed_out"]
          and ("psu:A", "PSU_OUT_OFF") not in devices.done and took < 1.8, f"{took:.2f} {infos}")

    # Abbruch waehrend eine Aktion noch unterwegs ist: erst deren Quittung
    devices.hold = {"psu:H"}
    events, stop = record(runner)
    devices.done.clear()
    runner.start([psu("psu:H", "PSU_VOLT", value=5.0)] + abort_steps())
    spin(app, 0.2)
    runner.stop()
    spin(app, 0.3)
    early = [d for d in devices.done if d[1].endswith("_OFF")]
    devices.hold = set()
    devices.release()
    spin(app, 3.0, lambda: not runner.is_running())
    stop()
    check("unquittierte Aktion: Block wartet auf deren Quittung", early == []
          and [d for d in devices.done if d[1].endswith("_OFF")] == BLOCK_ORDER, f"{early} {devices.done}")

    # Sicherheitsabschaltung waehrend des Blocks: Block abgebrochen, mode watchdog
    slow = [TestStep(step_type="on_abort"), TestStep(step_type="wait", duration=1.0),
            psu("psu:A", "PSU_OUT_OFF"), TestStep(step_type="end")]
    events = run(main + slow, act=lambda: (runner.stop(), spin(app, 0.2),
                                           runner.abort("Grenzwert", mode="watchdog", trigger=("", "psu:B"))),
                 until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
    check("Watchdog waehrend des Blocks: Rest nicht ausgefuehrt, mode watchdog mit Ausloeser",
          infos and infos[0]["mode"] == "watchdog" and infos[0]["trigger"] == ("", "psu:B")
          and ("psu:A", "PSU_OUT_OFF") not in devices.done, f"{infos} {devices.done}")

    # Watchdog/ALLE AUS ohne Block
    events = run(main + abort_steps(), act=lambda: runner.abort("Grenzwert", mode="watchdog",
                                                                 trigger=("", "psu:B")),
                 until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
    check("Watchdog: Abbruch-Block laeuft NICHT", not [d for d in devices.done if d[1].endswith("_OFF")]
          and infos and infos[0]["mode"] == "watchdog" and not infos[0]["has_block"], f"{infos} {devices.done}")
    events = run(main + abort_steps(), act=lambda: runner.abort("ALLE AUS", mode="all_off"),
                 until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
    check("ALLE AUS: Abbruch-Block laeuft NICHT", not [d for d in devices.done if d[1].endswith("_OFF")]
          and infos and infos[0]["mode"] == "all_off", f"{infos} {devices.done}")

    # Abbruch von aussen (Verbindungsverlust): Block ja, kein Ausloeser
    events = run(main + abort_steps(), act=lambda: runner.abort("Verbund: Verbindung verloren"),
                 until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
    check("Verbindungsverlust: Block laeuft, kein Ausloeser",
          [d for d in devices.done if d[1].endswith("_OFF")] == BLOCK_ORDER and infos
          and infos[0]["trigger"] is None, f"{infos} {devices.done}")
    # zweiter Stopp waehrend des Blocks: wirkungslos
    events = run(main + slow, act=lambda: (runner.stop(), spin(app, 0.1), runner.stop()),
                 until=lambda: ("fg:1", "FG_OUT_ON") in devices.done)
    check("zweiter Stopp waehrend des Blocks: Block laeuft zu Ende", ("psu:A", "PSU_OUT_OFF") in devices.done
          and events.count(("stopped",)) == 1, f"{events}")


# -- session ---------------------------------------------------------------------------------------------

def make_fake_worker():
    from PySide6.QtCore import QObject, QTimer, Signal, Slot

    class FakeWorker(QObject):
        device_removed = Signal(str, str)
        load_measurement = Signal(str, float, float, float)
        psu_measurement = Signal(str, float, float, bool)
        load_input_state = Signal(str, bool)
        psu_output_state = Signal(str, bool)
        action_completed = Signal(bool, str, float)
        devices_off_done = Signal(int, list, list)
        can_frame_received = Signal(str, int, str, bool, float)
        can_signals_decoded = Signal(str, int, object)
        hil_digital_state = Signal(str, list, list)
        hil_analog_input = Signal(str, list)

        def __init__(self) -> None:
            super().__init__()
            self.actions: list[tuple[str, str]] = []
            self.off_calls: list[list[str]] = []
            self.fail: set[str] = set()
            self.online: set[str] = set()

        @Slot(str, str, str, float, int)
        def execute_action(self, device_id, kind, action, value, channel):
            self.actions.append((device_id, action))
            ok = device_id not in self.fail
            QTimer.singleShot(0, lambda: self.action_completed.emit(ok, "" if ok else "antwortet nicht", 0.0))

        @Slot(str, int, str, bool)
        def execute_can_send(self, *_a):
            QTimer.singleShot(0, lambda: self.action_completed.emit(True, "", 0.0))

        @Slot(str, str, str, str, str)
        def execute_restbus(self, *_a):
            QTimer.singleShot(0, lambda: self.action_completed.emit(True, "", 0.0))

        @Slot(str, str, int, bool)
        def set_hil_focus(self, *_a):
            pass

        @Slot(bool)
        def set_test_running(self, _running):
            pass

        @Slot()
        def close_picoscope_sessions(self):
            pass

        @Slot(int, list, str)
        def devices_off_tagged(self, tag, device_ids, _reason):
            self.off_calls.append(list(device_ids))
            off = [d for d in device_ids if d in self.online]
            failed = [d for d in device_ids if d not in self.online]
            QTimer.singleShot(10, lambda: self.devices_off_done.emit(tag, off, failed))

    return FakeWorker()


def make_session():
    from run_record import RunRecorder
    from run_session import DeviceDirectory, Interlock, RunSession
    from safety import SafetyMonitor

    worker = make_fake_worker()
    directory = DeviceDirectory()
    for kind, device_id in (("psu", "psu:A"), ("psu", "psu:B"), ("fg", "fg:1"), ("psu", "psu:12")):
        directory.set_online(kind, device_id, True)
        worker.online.add(device_id)
    safety = SafetyMonitor({"psu:B": {"max_current": {"enabled": True, "value": 2.0}}})
    all_offs: list[str] = []
    safety.all_off_requested.connect(all_offs.append)
    recorder = RunRecorder()
    for kind, device_id, label in (("psu", "psu:A", "Netzteil 12 V"), ("psu", "psu:B", "Netzteil 24 V Endstufe"),
                                   ("fg", "fg:1", "Generator"), ("psu", "@pi/psu:0001", "Pi · Netzteil")):
        recorder.on_device_known(kind, device_id, label)
    session = RunSession(worker, safety, recorder, directory, Interlock([], directory.is_online))
    return session, worker, safety, recorder, all_offs


def monitor_step():
    from testcase_model import TestStep
    return TestStep(step_type="monitor", cond_device_kind="psu", cond_device_id="psu:B", cond_field="current",
                    params={"monitor": {"mode": "band", "min": 0.0, "max": 2.0, "abort": True}})


def section_session(app) -> None:
    print("session -- RunSession: welche Geraete beim Abbruch abgeschaltet werden")
    import run_report
    import run_results
    from run_registry import RunRegistry
    from testcase_model import TestStep

    session, worker, safety, recorder, all_offs = make_session()
    runner = session.runner
    base = [psu("psu:A", "PSU_VOLT", value=12.0), psu("psu:A", "PSU_OUT_ON"), monitor_step(),
            fg("fg:1", "FG_OUT_ON"), TestStep(step_type="wait", duration=5.0), TestStep(step_type="end")]

    def start(steps):
        worker.actions.clear()
        worker.off_calls.clear()
        all_offs.clear()
        session.start(steps, "abbruch")
        spin(app, 2.0, lambda: ("fg:1", "FG_OUT_ON") in worker.actions)

    def finish():
        spin(app, 5.0, lambda: not runner.is_running())
        return recorder.record().shutdown or {}

    # Stopp ohne Block: Standard-Menge
    start(base)
    check("Ablauf schaltet psu:A und fg:1, psu:B nur gelesen", session._switched == ["psu:A", "fg:1"],
          str(session._switched))
    runner.stop()
    check("waehrend der Abschaltung laeuft der Lauf noch", runner.is_running())
    shutdown = finish()
    check("Stopp ohne Block: nur geschaltete Geraete aus, nur gelesenes Netzteil bleibt an",
          worker.off_calls == [["psu:A", "fg:1"]] and shutdown.get("mode") == "standard"
          and shutdown.get("off") == ["psu:A", "fg:1"] and "psu:B" not in str(worker.off_calls),
          f"{worker.off_calls} {shutdown}")
    check("Stopp: kein ALLE AUS", not all_offs, str(all_offs))
    check("Stopp: Laufende 'stopped' nach der Abschaltung", recorder.record().outcome == "stopped")

    # Ueberwachen-Grenze verletzt: Quelle (psu:B) ist Ausloeser und geht mit aus
    start(base)
    runner.on_psu_measurement("psu:B", 24.0, 5.0, False)
    shutdown = finish()
    check("Ueberwachen verletzt: Ausloeser psu:B mit aus", shutdown.get("trigger") == "psu:B"
          and worker.off_calls == [["psu:A", "fg:1", "psu:B"]] and shutdown.get("mode") == "standard",
          f"{worker.off_calls} {shutdown}")
    check("Ueberwachen verletzt: Ergebnis error", recorder.record().outcome == "error")

    # Abbruch-Block fehlerfrei: keine weitere Abschaltung, nicht genannte Geraete bleiben
    block = [TestStep(step_type="on_abort"), fg("fg:1", "FG_OUT_OFF"), TestStep(step_type="wait", duration=0.1),
             psu("psu:12", "PSU_OUT_OFF"), TestStep(step_type="end")]
    start(base + block)
    runner.stop()
    shutdown = finish()
    offs = [a for a in worker.actions if a[1].endswith("_OFF")]
    check("Block fehlerfrei: Schritte in Reihenfolge, psu:A bleibt an, keine Standard-Abschaltung",
          offs == [("fg:1", "FG_OUT_OFF"), ("psu:12", "PSU_OUT_OFF")] and worker.off_calls == []
          and shutdown.get("mode") == "block" and not shutdown.get("fallback"), f"{offs} {worker.off_calls}")
    check("Block: abgeschaltet = Geraete des Blocks", shutdown.get("off") == ["fg:1", "psu:12"]
          and [r["ok"] for r in shutdown.get("block", [])] == [True, True, True], str(shutdown))
    check("Block: geschaltete Geraete enthalten auch die des Blocks", session._switched == ["psu:A", "fg:1", "psu:12"],
          str(session._switched))

    # Fehler im Block: Rest + Rueckfall auf Standard
    worker.fail = {"fg:1"}
    start(base + block)
    worker.fail = {"fg:1"}
    runner.stop()
    shutdown = finish()
    worker.fail = set()
    check("Fehler im Block: Rest versucht, dann Standard-Abschaltung",
          ("psu:12", "PSU_OUT_OFF") in worker.actions and worker.off_calls == [["psu:A", "fg:1", "psu:12"]]
          and shutdown.get("fallback") and shutdown.get("mode") == "block", f"{worker.off_calls} {shutdown}")

    # Watchdog im Lauf (Strom an psu:B): kein Block, geschaltete + Ausloeser, kein ALLE AUS
    start(base + block)
    safety.on_psu_measurement("psu:B", 24.0, 9.0, False)
    shutdown = finish()
    offs = [a for a in worker.actions if a[1].endswith("_OFF")]
    check("Watchdog im Lauf: Abbruch-Block laeuft nicht", offs == [], str(offs))
    check("Watchdog im Lauf: geschaltete Geraete + Ausloeser aus", worker.off_calls == [["psu:A", "fg:1", "psu:12",
                                                                                         "psu:B"]]
          and shutdown.get("mode") == "watchdog" and shutdown.get("trigger") == "psu:B", f"{worker.off_calls}")
    check("Watchdog im Lauf: kein ALLE AUS", not all_offs and session.trip_handled, str(all_offs))
    session.trip_handled = False
    safety.acknowledge()

    # Watchdog "getrennt": veraltetes/getrenntes Geraet psu:A (geschaltet) -> Ausloeser psu:A, nicht erreichbar
    start(base)
    worker.online.discard("psu:A")
    safety.on_device_removed("psu", "psu:A")
    shutdown = finish()
    worker.online.add("psu:A")
    check("Watchdog getrennt: Ausloeser psu:A nicht erreichbar, fg:1 aus", shutdown.get("trigger") == "psu:A"
          and shutdown.get("unreachable") == ["psu:A"] and shutdown.get("off") == ["fg:1"], str(shutdown))
    session.trip_handled = False
    safety.acknowledge()

    # Watchdog ohne Lauf: ALLE AUS wie bisher
    all_offs.clear()
    safety.on_psu_measurement("psu:B", 24.0, 9.0, False)
    check("Watchdog ohne Lauf: ALLE AUS wie bisher", len(all_offs) == 1 and not session.trip_handled, str(all_offs))
    safety.acknowledge()

    # ALLE AUS waehrend des Laufs (NOTAUS): kein Block, nur vermerkt
    start(base + block)
    runner.abort("ALLE AUS – Ablauf abgebrochen", mode="all_off")
    shutdown = finish()
    check("ALLE AUS: kein Block, keine Einzel-Abschaltung, mode all_off",
          not [a for a in worker.actions if a[1].endswith("_OFF")] and worker.off_calls == []
          and shutdown.get("mode") == "all_off", f"{worker.actions} {shutdown}")

    # Partnergeraet im Ablauf: ueber partner_devices_off
    calls: list = []

    def partner_off(ids, reason, done):
        calls.append(list(ids))
        from PySide6.QtCore import QTimer
        QTimer.singleShot(20, lambda: done([], list(ids)))   # Partner nicht erreichbar
        return True
    session.partner_devices_off = partner_off
    session.partner_router = lambda device_id, *_a: device_id.startswith("@") and (
        __import__("PySide6.QtCore", fromlist=["QTimer"]).QTimer.singleShot(
            0, lambda: runner.on_action_completed(True, "", 0.0)) or True)
    start([psu("@pi/psu:0001", "PSU_OUT_ON")] + base)
    runner.stop()
    shutdown = finish()
    check("Partnergeraet: ueber partner_devices_off, nicht erreichbar vermerkt", calls == [["@pi/psu:0001"]]
          and shutdown.get("unreachable") == ["@pi/psu:0001"] and "psu:A" in shutdown.get("off", []),
          f"{calls} {shutdown}")
    check("Namen im Ergebnis", shutdown.get("labels", {}).get("@pi/psu:0001") == "Pi · Netzteil", str(shutdown))
    record = recorder.record()

    # Bericht, run-result.json, Laufregister (get_run)
    result = run_results.result_dict(record)
    check("run-result.json: shutdown mit off/unreachable", (result.get("shutdown") or {}).get("unreachable")
          == ["@pi/psu:0001"] and result["shutdown"]["mode"] == "standard", str(result.get("shutdown")))
    registry = RunRegistry()
    run_id = registry.begin("abbruch", "gui", 3)
    registry.finish(run_id, "stopped", 2, {}, None, run_results.shutdown_dict(record))
    entry = registry.get(run_id)
    check("get_run: shutdown mit Art, abgeschaltet, nicht erreichbar", entry["shutdown"]["mode"] == "standard"
          and entry["shutdown"]["off"] and entry["shutdown"]["unreachable"] == ["@pi/psu:0001"], str(entry))
    html = run_report.build_html(record, [])
    check("Bericht: Abschnitt mit Art und Geraeten", "Abschaltung beim Abbruch" in html
          and "Standard-Abschaltung" in html and "Pi · Netzteil" in html and "Netzteil 12 V" in html, html[-600:])
    session.partner_devices_off = None
    session.partner_router = None

    start(base + block)
    runner.stop()
    finish()
    html = run_report.build_html(recorder.record(), [])
    check("Bericht: Schritte des Abbruch-Blocks mit Ergebnis", "Abbruch-Block" in html and "Generator" in html
          and "Ausgang AUS" in html, html[-800:])
    registry.finish(run_id, "passed", 0, {}, None, None)
    check("normal beendet: shutdown None", registry.get(run_id)["shutdown"] is None)


# -- partner ---------------------------------------------------------------------------------------------

def section_partner(app) -> None:
    print("partner -- POST /devices-off und PartnerLink.devices_off")
    import share_api
    from partner_client import PartnerError
    from partner_link import PartnerLink
    from share_api import RemoteResult

    class Executor:
        def __init__(self, result):
            self.result = result
            self.calls = []

        def submit_devices_off(self, devices, reason, client):
            self.calls.append((devices, reason, client))
            return self.result

    route = share_api._post_route("/api/v1/devices-off")
    check("Route /devices-off", route == ("devices_off", ""), str(route))
    ex = Executor(RemoteResult("ok"))
    resp = share_api._devices_off(ex, json.dumps({"devices": ["psu:1", "psu:1", "fg:2"], "reason": "x"}).encode(),
                                  "pc")
    body = json.loads(resp.body)
    check("devices-off ok, doppelte Kennung nur einmal", resp.status == 200 and body["ok"] is True
          and ex.calls == [(["psu:1", "fg:2"], "x", "pc")], f"{resp.status} {body} {ex.calls}")
    ex = Executor(RemoteResult("error", "devices_off_incomplete", "fg:2"))
    resp = share_api._devices_off(ex, json.dumps({"devices": ["psu:1", "fg:2"]}).encode(), "pc")
    body = json.loads(resp.body)
    check("devices-off unvollstaendig -> 502 mit failures", resp.status == 502 and body["failures"] == ["fg:2"],
          f"{resp.status} {body}")
    for bad in (b"{}", b'{"devices": []}', b'{"devices": [1]}', b"kein json"):
        resp = share_api._devices_off(Executor(RemoteResult("ok")), bad, "pc")
        check(f"devices-off ungueltig abgelehnt ({bad[:14]!r})", resp.status == 400, str(resp.status))

    class Client:
        base_url = "http://pi:8765"

        def __init__(self, mode):
            self.mode = mode
            self.all_off_called = False

        def devices_off(self, ids, reason):
            if self.mode == "old":
                raise PartnerError("not_found", "", 404, {"error": "not_found"})
            if self.mode == "fail":
                raise PartnerError("devices_off_incomplete", "", 502, {"failures": ids[:1]})
            return {"ok": True}

        def all_off(self):
            self.all_off_called = True
            return {"ok": True}

    link = PartnerLink("pc")
    for mode, expect_off, expect_failed, all_off in (("new", ["psu:1", "fg:2"], [], False),
                                                     ("fail", ["fg:2"], ["psu:1"], False),
                                                     ("old", ["psu:1", "fg:2"], [], True)):
        client = Client(mode)
        link._client = client
        result: list = []
        ok = link.devices_off(["psu:1", "fg:2"], "Test", lambda off, failed: result.append((off, failed)))
        spin(app, 3.0, lambda: bool(result))
        check(f"PartnerLink.devices_off ({mode})", ok and result == [(expect_off, expect_failed)]
              and client.all_off_called == all_off, f"{result} {client.all_off_called}")
    link._client = None
    check("Verbund aus -> False", link.devices_off(["psu:1"], "x", lambda *_: None) is False)


# -- editor ----------------------------------------------------------------------------------------------

def section_editor(app) -> None:
    print("editor -- Abbruch-Block im Editor, Anzeige 'Beim Abbruch aus'")
    import testcase_model as tm
    from testcase_tab import COL_DEVICE, TestcaseTab

    tab = TestcaseTab()
    path = DATA / "editor_ohne.json"
    tm.save_steps([psu("psu:A", "PSU_OUT_ON"), monitor_step(), tm.TestStep(step_type="wait", duration=1.0),
                   tm.TestStep(step_type="end"), fg("fg:1", "FG_OUT_ON")], path)
    check("Laden", tab._load_path(path))
    text = tab.abort_chip_text()
    check("ohne Block: 'Beim Abbruch aus' mit geschalteten Geraeten, ohne nur gelesenes",
          text.startswith("Beim Abbruch aus:") and "psu:A" in text and "fg:1" in text and "psu:B" not in text, text)
    check("Tooltip nennt Regel und ALLE AUS", "Nur gelesene Geräte" in tab._abort_chip.toolTip()
          and "ALLE AUS" in tab._abort_chip.toolTip(), tab._abort_chip.toolTip())
    rows = tab._table.rowCount()
    tab._action_add_abort.trigger()
    steps = tab.steps()
    check("Menue: Abbruch-Block am Ende mit Netzteil AUS", [s.step_type for s in steps[rows:]]
          == ["on_abort", "action", "end"] and steps[rows + 1].action == "PSU_OUT_OFF", str(steps[rows:]))
    check("Zeile 'Abbruch-Block' und 'Ende (Abbruch-Block)'",
          tab._table.cellWidget(rows, COL_DEVICE).text() == "Abbruch-Block"
          and tab._table.cellWidget(rows + 2, COL_DEVICE).text() == "Ende (Abbruch-Block)",
          tab._table.cellWidget(rows + 2, COL_DEVICE).text())
    tab._action_add_abort.trigger()
    check("zweites Einfuegen legt keinen zweiten Block an",
          [s.step_type for s in tab.steps()].count("on_abort") == 1)
    text = tab.abort_chip_text()
    check("mit Block: Anzeige 'Abbruch-Block: <Geraete des Blocks>'", text.startswith("Abbruch-Block:"), text)
    check("Ausfuehren-Ansicht", tab._describe_step(tm.TestStep(step_type="on_abort")) == "Abbruch-Block")
    check("Start erlaubt (Struktur gueltig)", tab._structure_ok)
    out = DATA / "editor_mit.json"
    tm.save_steps(tab.steps(), out)
    check("gespeichert als Format 7", json.loads(out.read_text(encoding="utf-8"))["version"] == 7)
    tab2 = TestcaseTab()
    check("Laden mit Block", tab2._load_path(out) and not tab2.is_modified())
    bad = DATA / "editor_bad.json"
    tm.save_steps([psu("psu:A", "PSU_OUT_ON"), tm.TestStep(step_type="on_abort"),
                   tm.TestStep(step_type="confirm"), tm.TestStep(step_type="end")], bad)
    tab3 = TestcaseTab()
    tab3._load_path(bad)
    check("unzulaessiger Schritt im Block: Start gesperrt, Grund im Tooltip", not tab3._structure_ok
          and "Geräteaktionen und Warten" in tab3._run_button.toolTip(), tab3._run_button.toolTip())
    from i18n import Translator
    Translator.instance().set_language("en")
    app.processEvents()
    english = (tab2._action_add_abort.text(), tab2.abort_chip_text(),
               tab2._table.cellWidget(rows, COL_DEVICE).text())
    Translator.instance().set_language("de")
    app.processEvents()
    check("englische Texte", english[0] == "Abort block … End" and english[1].startswith("Abort block:")
          and english[2] == "Abort block", str(english))
    for t in (tab, tab2, tab3):
        t.deleteLater()


# -- window ----------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method: str, port: int, path: str, body=None, timeout: float = 25.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, {"_raw": raw.decode("utf-8", "replace")}


def ask(app, *args, **kwargs):
    box: dict = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", http(*args, **kwargs)), daemon=True)
    thread.start()
    spin(app, 30.0, lambda: "r" in box)
    return box.get("r", (0, {}))


def section_window(app) -> None:
    print("window -- Hauptfenster (Simulation), REST")
    import no_hardware
    no_hardware.block()
    import data_store
    import device_worker as dw
    import testcase_model as tm

    port = free_port()
    (DATA / "settings.json").write_text(json.dumps({
        "simulation_mode": True, "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port,
        "share_token": TOKEN, "share_local_bypass": True}), encoding="utf-8")
    from main_window import MainWindow
    from settings import Settings
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    spin(app, 8.0, lambda: dw.SIM_PSU_ID in window._online_devices.get("psu", set())
         and dw.SIM_FG_ID in window._online_devices.get("fg", set()))
    reasons: list[str] = []
    window._request_all_off.connect(reasons.append)
    folder = data_store.target_dir("testcases")
    TS = tm.TestStep
    p = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    f = dict(device_kind="fg", device_id=dw.SIM_FG_ID, hil_channel=1)
    tm.save_steps([TS(**p, action="PSU_VOLT", value=3.0), TS(**p, action="PSU_OUT_ON"), TS(**f, action="FG_OUT_ON"),
                   TS(step_type="wait", duration=30.0),
                   TS(step_type="on_abort"), TS(**f, action="FG_OUT_OFF"), TS(step_type="wait", duration=0.2),
                   TS(**p, action="PSU_OUT_OFF"), TS(step_type="end")], folder / "mit_block.json")

    def run_entry(run_id):
        return window._live_state.runs.get(run_id, 0) or {}

    status, body = ask(app, "POST", port, "/runs", {"testcase": "mit_block.json"})
    run_id = body.get("run", "")
    check("Start per HTTP", status == 202 and run_id, f"{status} {body}")
    spin(app, 6.0, lambda: run_entry(run_id).get("step") == 4)
    check("Anzeige im Editor: Abbruch-Block", window.testcase_tab.abort_chip_text().startswith("Abbruch-Block:"),
          window.testcase_tab.abort_chip_text())
    count = len(reasons)
    status, body = ask(app, "POST", port, f"/runs/{run_id}/stop", {})
    check("Stopp per HTTP", status == 200, f"{status} {body}")
    spin(app, 10.0, lambda: run_entry(run_id).get("running") is False)
    status, entry = ask(app, "GET", port, f"/runs/{run_id}?events=0")
    shutdown = entry.get("shutdown") or {}
    check("GET /runs/{id}: shutdown mode block, Schritte ok", shutdown.get("mode") == "block"
          and [r["ok"] for r in shutdown.get("block") or []] == [True, True, True]
          and set(shutdown.get("off") or []) == {dw.SIM_PSU_ID, dw.SIM_FG_ID}, str(shutdown))
    check("Stopp mit Block: kein ALLE AUS", not reasons[count:], str(reasons[count:]))
    status, result = ask(app, "GET", port, f"/runs/{run_id}/result")
    check("run-result.json ueber HTTP: shutdown", (result.get("shutdown") or {}).get("mode") == "block",
          str(result.get("shutdown")))
    spin(app, 1.0)

    # ALLE AUS (Knopf) waehrend des Laufs: kein Block, alles aus
    status, body = ask(app, "POST", port, "/runs", {"testcase": "mit_block.json"})
    run_id = body.get("run", "")
    spin(app, 6.0, lambda: run_entry(run_id).get("step") == 4)
    count = len(reasons)
    window._all_off_button.click()
    spin(app, 10.0, lambda: run_entry(run_id).get("running") is False)
    shutdown = run_entry(run_id).get("shutdown") or {}
    check("ALLE AUS-Knopf: ALLE AUS, Block nicht gelaufen", "manual all-off" in reasons[count:]
          and shutdown.get("mode") == "all_off" and not shutdown.get("block"), f"{reasons[count:]} {shutdown}")
    spin(app, 1.0)

    # POST /devices-off: nur dieses Geraet
    count = len(reasons)
    status, body = ask(app, "POST", port, "/devices-off", {"devices": [dw.SIM_FG_ID], "reason": "Test"})
    check("POST /devices-off -> 200, kein ALLE AUS", status == 200 and body.get("ok") is True and not reasons[count:],
          f"{status} {body}")
    status, body = ask(app, "POST", port, "/devices-off", {"devices": ["psu:GIBTESNICHT"]})
    check("POST /devices-off unbekanntes Geraet -> 502 mit failures", status == 502
          and body.get("failures") == ["psu:GIBTESNICHT"], f"{status} {body}")
    window._shutdown_for_tests = True
    window.close()
    spin(app, 2.0)


SECTIONS = {"format": section_format, "runner": section_runner, "session": section_session,
            "partner": section_partner, "editor": section_editor, "window": section_window}


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    wanted = [a for a in sys.argv[1:] if a in SECTIONS] or list(SECTIONS)
    for name in wanted:
        func = SECTIONS[name]
        if func.__code__.co_argcount:
            func(app)
        else:
            func()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER: " + "; ".join(FAILURES))
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

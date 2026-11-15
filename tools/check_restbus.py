"""Prueft FT-6 Restbus aus DBC (PI-56 c, 0.67.0): E2E-Profile (Pruefwerte, Zaehler, Data-ID-Varianten), RestbusNode
(Startwerte, Grenzen, Zyklen, Ein/Aus, E2E), Worker-Anbindung am simulierten Bus, Ablaufschritte ueber
`labcontrol run` und die Restbus-Ansicht.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_restbus.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-restbus-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []

# Eigene Test-DBC: Intel- und Motorola-Signale, Wertetabelle, Startwert, E2E-Signale (Zaehler 4 Bit, CRC 8 Bit)
DBC = '''VERSION ""

NS_ :

BS_:

BU_: PC ECU

BO_ 528 Cmd: 8 PC
 SG_ Setpoint : 7|16@0- (0.5,1000) [-4000|6000] "mV" ECU
 SG_ Mode : 28|2@1+ (1,0) [0|3] "" ECU
 SG_ Crc : 32|8@1+ (1,0) [0|255] "" ECU
 SG_ Counter : 40|4@1+ (1,0) [0|15] "" ECU
 SG_ IdNibble : 44|4@1+ (1,0) [0|15] "" ECU

BO_ 529 P2: 8 PC
 SG_ Crc2 : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ Counter2 : 8|4@1+ (1,0) [0|15] "" ECU
 SG_ Value : 16|8@1+ (1,0) [0|200] "" ECU

BO_ 530 Plain: 2 PC
 SG_ Level : 0|8@1+ (0.5,0) [0|100] "%" ECU
 SG_ Alive : 8|4@1+ (1,0) [0|15] "" ECU

BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;
BA_DEF_ SG_  "GenSigStartValue" FLOAT -2147483648 4294967295;
BA_DEF_DEF_  "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 528 50;
BA_ "GenSigStartValue" SG_ 528 Setpoint 3000;
VAL_ 528 Mode 0 "Off" 1 "Slow" 2 "Fast" ;
'''


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def _db():
    import cantools
    path = DATA / "test.dbc"
    path.write_text(DBC, encoding="utf-8")
    return cantools.database.load_file(str(path)), path


def section_e2e() -> None:
    print("e2e -- Profile")
    import can_bus.e2e as e2e

    check("CRC8 SAE J1850 Pruefwert", e2e.crc8_sae_j1850(b"123456789") == 0x4B)
    check("CRC8H2F Pruefwert", e2e.crc8_h2f(b"123456789") == 0xDF)
    db, _path = _db()
    msg = db.get_message_by_name("Cmd")
    base = {"Setpoint": 2500.0, "Mode": 1, "IdNibble": 0}
    prot = e2e.E2EProtector(msg, e2e.E2EConfig.from_dict({"profile": "P01", "data_id": 0x0210, "crc": "Crc",
                                                          "counter": "Counter"}))
    frames = [prot.protect(base) for _ in range(16)]
    counters = [f[5] & 0x0F for f in frames]
    check("P01 Zaehler 0..14, dann 0", counters == list(range(15)) + [0], str(counters))
    payload = [b for i, b in enumerate(frames[0]) if i != 4]
    expected = e2e.crc8([0x10, 0x02] + payload, e2e._TABLE_1D, 0x00, 0x00)
    check("P01 CRC ueber Data ID (Low, High) und Daten ohne CRC-Byte", frames[0][4] == expected)
    check("Werte bleiben erhalten", msg.decode(frames[3], decode_choices=False)["Setpoint"] == 2500.0
          and msg.decode(frames[3], decode_choices=False)["Mode"] == 1)
    alt = e2e.E2EProtector(msg, e2e.E2EConfig.from_dict({"profile": "P01", "data_id": 0x0210, "data_id_mode": "ALT",
                                                         "crc": "Crc", "counter": "Counter"}))
    f0, f1 = alt.protect(base), alt.protect(base)
    p1 = [b for i, b in enumerate(f1) if i != 4]
    check("P01 ALT: ungerader Zaehler nimmt das High-Byte",
          f1[4] == e2e.crc8([0x02] + p1, e2e._TABLE_1D, 0x00, 0x00) and f0[4] != f1[4])
    nib = e2e.E2EProtector(msg, e2e.E2EConfig.from_dict({"profile": "P11", "data_id": 0x0A10, "data_id_mode": "NIBBLE",
                                                         "crc": "Crc", "counter": "Counter",
                                                         "data_id_nibble": "IdNibble"}))
    fn = nib.protect(base)
    check("P11 NIBBLE: High-Nibble der Data ID im Signal", msg.decode(fn)["IdNibble"] == 0xA)
    check("P11 NIBBLE: CRC mit Low-Byte und 0x00", fn[4] == e2e.crc8([0x10, 0x00] + [b for i, b in enumerate(fn) if i != 4],
                                                                    e2e._TABLE_1D, 0x00, 0x00))
    p2msg = db.get_message_by_name("P2")
    ids = list(range(0x30, 0x40))
    p2 = e2e.E2EProtector(p2msg, e2e.E2EConfig.from_dict({"profile": "P02", "data_id_list": ids, "crc": "Crc2",
                                                          "counter": "Counter2"}))
    g = [p2.protect({"Value": 7}) for _ in range(17)]
    check("P02 Zaehler 1..15, 0, 1", [x[1] & 0x0F for x in g] == list(range(1, 16)) + [0, 1])
    check("P02 CRC8H2F mit Data ID aus der Liste", g[0][0] == e2e.crc8_h2f(list(g[0][1:]) + [ids[1]]))
    for bad in ({"profile": "P03", "crc": "Crc", "counter": "Counter"}, {"profile": "P01", "counter": "Counter"},
                {"profile": "P02", "crc": "Crc2", "counter": "Counter2", "data_id_list": [1, 2]},
                {"profile": "P11", "data_id_mode": "ALT", "crc": "Crc", "counter": "Counter"}):
        try:
            e2e.E2EConfig.from_dict(bad)
            check(f"abgelehnt {bad}", False)
        except e2e.E2EError:
            check(f"abgelehnt {bad}", True)
    try:
        e2e.E2EProtector(db.get_message_by_name("Plain"), e2e.E2EConfig.from_dict(
            {"profile": "P01", "crc": "Level", "counter": "Alive"}))
        check("CRC-Signal muss ein ganzes Byte sein (Level ist es, aber 0,5-Skalierung egal)", True)
    except e2e.E2EError as exc:
        check("CRC-Signal muss ein ganzes Byte sein", False, str(exc))
    cnt = e2e.E2EProtector(db.get_message_by_name("Plain"), e2e.E2EConfig.from_dict(
        {"profile": "COUNTER", "counter": "Alive"}))
    check("COUNTER zaehlt 0..15", [cnt.protect({"Level": 10})[1] & 0x0F for _ in range(17)] == list(range(16)) + [0])


def section_node() -> None:
    print("node -- RestbusNode")
    from can_bus.restbus import RestbusError, RestbusNode, default_cycle_ms, messages_overview, import_e2e_from_rte

    db, _path = _db()
    check("Zyklus aus der DBC", default_cycle_ms(db.get_message_by_name("Cmd")) == 50)
    check("ohne Zyklus 100 ms", default_cycle_ms(db.get_message_by_name("Plain")) == 100)
    overview = messages_overview(db)
    check("Uebersicht", [m["name"] for m in overview] == ["Cmd", "P2", "Plain"]
          and overview[0]["signals"][0]["initial"] == 2500.0, str(overview[0]["signals"][0]))
    node = RestbusNode(db)
    problems = node.configure({"messages": [
        {"name": "Cmd", "enabled": True, "values": {"Mode": "Fast"},
         "e2e": {"profile": "P01", "data_id": 0x210, "crc": "Crc", "counter": "Counter"}},
        {"name": "Plain", "cycle_ms": 200, "values": {"Level": 150}},
        {"name": "Fehlt"}]})
    check("Probleme gemeldet (Grenze, fehlende Botschaft)", len(problems) == 2 and "Fehlt" in problems[1], str(problems))
    check("Wertetabelle als Name", node.state()["messages"]["Cmd"]["values"]["Mode"] == 2.0)
    check("vor dem Start nichts gesendet", node.due_frames(100.0) == [])
    node.set_running(True)
    t = 100.0
    sent: list = []
    while t < 101.0 + 1e-9:
        sent += [(round(t, 3), f.arbitration_id, f.data) for f in node.due_frames(t)]
        t += 0.01
    cmd = [s for s in sent if s[1] == 528]
    plain = [s for s in sent if s[1] == 530]
    check("Cmd alle 50 ms (21 in 1 s)", len(cmd) == 21, str(len(cmd)))
    check("Plain alle 200 ms (6 in 1 s)", len(plain) == 6, str(len(plain)))
    check("E2E-Zaehler laeuft", [d[5] & 0x0F for _t, _i, d in cmd[:3]] == [0, 1, 2])
    value = node.set_signal("Cmd", "Setpoint", "1234,5")
    check("Signal setzen (Komma)", value == 1234.5)
    frame = node.due_frames(t + 0.06)
    check("neuer Wert im naechsten Frame", frame and db.get_message_by_name("Cmd").decode(frame[0].data)["Setpoint"]
          == 1234.5)
    for args in (("Cmd", "Setpoint", 9000), ("Cmd", "Counter", 3), ("Cmd", "Gibts", 1), ("Nix", "A", 1),
                 ("Cmd", "Mode", "Turbo")):
        try:
            node.set_signal(*args)
            check(f"abgelehnt {args}", False)
        except RestbusError:
            check(f"abgelehnt {args}", True)
    node.set_enabled("Cmd", False)
    check("Botschaft aus", all(f.arbitration_id != 528 for f in node.due_frames(t + 0.5)))
    counter = node.state()["messages"]["Cmd"]["counter"]
    node.configure({"messages": [{"name": "Cmd", "e2e": {"profile": "P01", "data_id": 0x210, "crc": "Crc",
                                                          "counter": "Counter"}}]})
    check("Zaehler bleibt bei gleicher E2E-Angabe", node.state()["messages"]["Cmd"]["counter"] == counter)
    node.set_running(False)
    check("gestoppt: nichts mehr", node.due_frames(t + 2) == [])
    imported = import_e2e_from_rte({"can": {"qualifiers": {
        "PC_Command": {"debounce_ms": 250, "e2e": {"profile": "P01", "data_id": 528, "data_id_mode": "BOTH",
                                                  "crc": "PcCrc", "counter": "PcCounter"}},
        "Other": {"on_error": "hold"}}}})
    check("E2E aus rte.yaml", imported == {"PC_Command": {"profile": "P01", "data_id": 528, "data_id_mode": "BOTH",
                                                          "crc": "PcCrc", "counter": "PcCounter"}}, str(imported))


def spin(app, seconds: float, until=None) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


def section_integration() -> None:
    print("worker -- simulierter Bus")
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication
    from device_worker import SIM_CAN_ID, DeviceWorker

    app = QApplication.instance() or QApplication([])
    db_obj, path = _db()
    worker = DeviceWorker(simulation_mode=True, can_configs=[])
    worker._add_mock_can()
    bus = worker._can_buses[SIM_CAN_ID]
    states: list[dict] = []
    done: list[tuple] = []
    worker.restbus_state.connect(lambda can_id, st: states.append(st))
    worker.action_completed.connect(lambda ok, msg, _v: done.append((ok, msg)))
    worker.set_restbus_configs({SIM_CAN_ID: {"dbc": str(path), "messages": [
        {"name": "Plain", "cycle_ms": 50, "enabled": True, "values": {"Level": 20}},
        {"name": "Cmd", "enabled": True, "e2e": {"profile": "P01", "data_id": 528, "crc": "Crc",
                                                "counter": "Counter"}}]}})
    check("Restbus angehaengt, steht", states and states[-1]["available"] and not states[-1]["running"],
          str(states[-1:]))
    before = len(bus.sent_frames)
    worker.restbus_command(SIM_CAN_ID, "start", "", "", "")
    spin(app, 0.6)
    plain = [f for f in bus.sent_frames[before:] if f.arbitration_id == 530]
    cmd = [f for f in bus.sent_frames[before:] if f.arbitration_id == 528]
    check("Plain ~alle 50 ms gesendet", 8 <= len(plain) <= 14, str(len(plain)))
    check("Cmd mit E2E gesendet", len(cmd) >= 8 and [f.data[5] & 0x0F for f in cmd[:3]] == [0, 1, 2],
          str([f.data.hex() for f in cmd[:3]]))
    check("Startwert in den Daten", bool(plain) and plain[0].data[0] == 40)
    worker.execute_restbus(SIM_CAN_ID, "RESTBUS_SET", "Plain", "Level", "42,5")
    check("Schritt Signal setzen ok", done[-1] == (True, ""), str(done[-1:]))
    mark = len(bus.sent_frames)
    spin(app, 0.2)
    check("neuer Wert gesendet", any(f.arbitration_id == 530 and f.data[0] == 85 for f in bus.sent_frames[mark:]))
    worker.execute_restbus(SIM_CAN_ID, "RESTBUS_SET", "Plain", "Level", "500")
    check("Wert ausserhalb -> Schritt scheitert mit Grund", done[-1][0] is False and "Maximum" in done[-1][1],
          str(done[-1:]))
    worker.restbus_command(SIM_CAN_ID, "set", "Plain", "Gibts", "1")
    check("Bedienfehler im Zustand", any("Gibts" in e for e in states[-1]["errors"]), str(states[-1]["errors"]))
    worker.all_outputs_off("check")
    mark = len(bus.sent_frames)
    spin(app, 0.3)
    check("ALLE AUS stoppt den Restbus", not states[-1]["running"]
          and not [f for f in bus.sent_frames[mark:] if f.arbitration_id in (528, 530)])
    worker.execute_restbus("can:vector:1:0", "RESTBUS_START", "", "", "")
    check("Interface ohne Restbus -> Fehler", done[-1][0] is False and "Restbus" in done[-1][1])
    worker.set_restbus_configs({})
    check("ohne Botschaften abgehaengt", states[-1]["available"] is False)

    print("cli -- Ablauf mit Restbus-Schritten (Simulation)")
    import testcase_model as tm
    data = DATA / "cli"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True, "restbus": {SIM_CAN_ID: {
        "dbc": str(path), "messages": [{"name": "Plain", "cycle_ms": 50, "enabled": True}]}}}), encoding="utf-8")

    def run(name, steps, *extra):
        file = data / f"{name}.json"
        tm.save_steps(steps, file)
        env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "run",
                               str(file), "--simulation", "--wait-devices", "8", *extra], env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        return proc.returncode, proc.stdout + proc.stderr

    start = tm.TestStep(device_kind="can", device_id=SIM_CAN_ID, action="RESTBUS_START", duration=0.3)
    set_ok = tm.TestStep(device_kind="can", device_id=SIM_CAN_ID, action="RESTBUS_SET", duration=0.3,
                         params={"message": "Plain", "signal": "Level", "value": 10, "refs": {"value": "level"}})
    stop = tm.TestStep(device_kind="can", device_id=SIM_CAN_ID, action="RESTBUS_STOP")
    code, out = run("ok", [start, set_ok, stop], "--var", "level=33")
    check("Ablauf Start/Setzen (Wert aus Variable)/Stopp: Exit 0", code == 0, out[-500:])
    bad = tm.TestStep(device_kind="can", device_id=SIM_CAN_ID, action="RESTBUS_SET",
                      params={"message": "Plain", "signal": "Level", "value": 999})
    code, out = run("bad", [start, bad, stop])
    check("Wert ausserhalb -> Abbruch Exit 2", code == 2 and "Maximum" in out, out[-400:])

    print("ui -- Editor, Ansicht, Kachel")
    from control_tab import CanControlGroup
    from restbus_dialog import RestbusDialog, RestbusStepDialog
    from testcase_tab import COL_VALUE, TestcaseTab

    tab = TestcaseTab()
    tab.set_restbus_db_provider(lambda _can: db_obj)
    file = data / "editor.json"
    tm.save_steps([set_ok], file)
    tab._load_path(file)
    step = tab.steps()[0]
    check("Editor behaelt Botschaft/Signal/Wert", step.params.get("message") == "Plain"
          and step.params.get("value") == 10 and step.params.get("refs") == {"value": "level"}, str(step.params))
    page = tab._table.cellWidget(0, COL_VALUE)
    check("Editor zeigt Zusammenfassung", page.currentIndex() == 6, str(page.currentIndex()))
    dialog = RestbusStepDialog({"message": "Cmd"}, db_obj)
    check("Schrittdialog: Signale der Botschaft", dialog._signal.count() == 5)
    dialog._signal.setCurrentText("Mode")
    dialog._value.setText("Fast")
    dialog._accept()
    check("Schrittdialog: Name aus Wertetabelle", dialog.params() == {"message": "Cmd", "signal": "Mode",
                                                                      "value": "Fast"}, str(dialog.params()))
    view = RestbusDialog(SIM_CAN_ID, "Sim", {"dbc": str(path), "messages": [{"name": "Plain", "enabled": True}]},
                         "", ("", ""))
    check("Ansicht listet DBC-Botschaften", view._messages.rowCount() == 3)
    saved: list = []
    view.config_saved.connect(lambda cid, cfg: saved.append(cfg))
    view._messages.setCurrentCell(0, 1)
    view._show_message(0)
    view._profile.setCurrentIndex(view._profile.findData("P01"))
    view._data_id.setText("0x210")
    view._crc.setCurrentIndex(view._crc.findData("Crc"))
    view._counter.setCurrentIndex(view._counter.findData("Counter"))
    view._store_e2e()
    view._save()
    cmd_cfg = next((m for m in saved[-1]["messages"] if m["name"] == "Cmd"), {}) if saved else {}
    check("Ansicht speichert E2E", cmd_cfg.get("e2e", {}).get("data_id") == 0x210
          and cmd_cfg["e2e"].get("crc") == "Crc", str(cmd_cfg))
    sent: list = []
    view.signal_set.connect(lambda *a: sent.append(a))
    view._messages.setCurrentCell(2, 1)
    view._show_message(2)
    view._signals.item(0, 1).setText("12")
    check("Wert aendern geht sofort an den Restbus", bool(sent) and sent[-1][1:] == ("Plain", "Level", "12"),
          str(sent))
    view._import_e2e()
    check("Import ohne neXtBSW meldet sich", "neXtBSW" in view._status.text())
    tile = CanControlGroup(SIM_CAN_ID, "Sim")
    tile.set_restbus_state({"available": False})
    check("Kachel: ohne Restbus gesperrt", not tile._restbus_button.isEnabled())
    tile.set_restbus_state({"available": True, "running": True, "messages": {"Plain": {"enabled": True}}})
    check("Kachel: laeuft", tile._restbus_button.isChecked() and "stoppen" in tile._restbus_button.toolTip())
    requests: list = []
    tile.restbus_run_requested.connect(lambda cid, on: requests.append(on))
    tile._restbus_button.click()
    check("Kachel: Klick fordert Stopp an", requests == [False])


def main() -> int:
    section_e2e()
    section_node()
    section_integration()
    print(f"\n{'ALLES OK' if not FAILURES else f'{len(FAILURES)} FEHLER: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # Ergebnis steht fest; das Aufraeumen von Worker-/Prozess-Objekten am Interpreter-Ende stuerzte unter WSL
    # gelegentlich ab (Bus error nach "ALLES OK")
    os._exit(code)

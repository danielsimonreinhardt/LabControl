"""Prueft PI-77 a (1.0.1): "Warten seit Aktion" zaehlt jede Schaltung ueber LabControl, auch im Verbund.

- unit     -- switch_log allein: verbuchen, "automatisch", Alter statt Zeitpunkt, Kachel vom Partner umrechnen
- worker   -- DeviceWorker (Simulation): Kachel, Preset, Ablauf, MCP/REST, Verbund, ALLE AUS werden verbucht,
              Lesen und fehlgeschlagene Aktionen nicht (unbekanntes Geraet, Netzteil in Handbedienung)
- runner   -- TestRunner mit Auskunft: Kachel-Schaltung vor dem Lauf zaehlt, unbekannt -> volle Zeit, Schaltung
              waehrend des Wartens verlaengert, Stand des Besitzers zu alt -> auf neueren warten bzw. volle Zeit,
              keine Verbindung -> volle Zeit mit Grund, eigene juengere Ausfuehrung gewinnt
- verbund  -- ZWEI Hauptfenster (B = Besitzer "pi-sim", A = Leitstand): Kachel und MCP/REST an B, Partner-Befehl von
              A wird bei B verbucht ("Verbund"), /values meldet "last_switch"; A wartet im Ablauf nur den Rest und
              vermerkt die Quelle; eigene Kachel an A zaehlt; unbekannt -> volle Zeit; B getrennt -> volle Zeit

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_last_switch.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-last-switch-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN_B = "token-B"
HOST_B = "pi-sim"


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


# -- unit ------------------------------------------------------------------------------------------------

def section_unit() -> None:
    print("unit -- switch_log allein")
    import switch_log as sl

    log = sl.SwitchLog()
    check("leer: unbekannt", log.last("psu", "psu:A", "PSU_OUT_ON") is None
          and log.lookup("psu", "psu:A", "PSU_OUT_ON").at is None)
    log.note("psu", "psu:A", "PSU_OUT_ON", sl.SOURCE_TILE, at=100.0)
    log.note("psu", "psu:B", "PSU_OUT_ON", sl.SOURCE_REMOTE, at=105.0)
    log.note("load", "load:A", "OUT_ON", sl.SOURCE_RUN, at=110.0)
    check("je Geraet", log.last("psu", "psu:A", "PSU_OUT_ON") == (100.0, "Kachel"))
    check("'automatisch': juengste Schaltung der Art", log.last("psu", "", "PSU_OUT_ON") == (105.0, "MCP/REST"))
    check("'automatisch' nur gleiche Art", log.last("psu", "", "OUT_ON") is None)
    log.note("psu", "psu:A", "PSU_OUT_ON", sl.SOURCE_VERBUND)
    info = log.lookup("psu", "psu:A", "PSU_OUT_ON")
    check("neue Schaltung ersetzt die alte, Stand = jetzt", info.source == "Verbund"
          and abs(info.at - time.monotonic()) < 0.5 and info.fresh_since >= info.at)
    ages = log.ages()
    check("Freigabe: Alter in s statt Zeitpunkt", 0 <= ages["psu:A"]["PSU_OUT_ON"]["age_s"] < 0.5
          and ages["psu:A"]["PSU_OUT_ON"]["source"] == "Verbund" and set(ages) == {"psu:A", "psu:B", "load:A"})
    tile = {"id": "psu:1", "last_switch": {"PSU_OUT_ON": {"age_s": 42.0, "source": "Kachel"}}}
    got = sl.from_tile(tile, "PSU_OUT_ON", received=1000.0, fresh_since=999.8)
    check("Partner: Alter von der Ankunft zurueckgerechnet", got == (958.0, "Kachel", 999.8), str(got))
    check("Partner: Aktion nie geschaltet", sl.from_tile(tile, "PSU_OUT_OFF", 1000.0, 999.8) == (None, "", 999.8))
    check("Partner aeltere Version (Feld fehlt)",
          sl.from_tile({"id": "psu:1"}, "PSU_OUT_ON", 1000.0, 999.8).source == sl.OWNER_WITHOUT_TIMES)
    check("Partner: unbrauchbares Alter -> unbekannt", sl.from_tile(
        {"last_switch": {"PSU_OUT_ON": {"age_s": "x"}}}, "PSU_OUT_ON", 1000.0, 999.8).at is None)


# -- worker ----------------------------------------------------------------------------------------------

def section_worker(app) -> None:
    print("worker -- DeviceWorker (Simulation) verbucht")
    import device_worker as dw

    worker = dw.DeviceWorker(simulation_mode=True)
    worker.start()
    spin(app, 1.0, lambda: dw.SIM_PSU_ID in worker._psus and dw.SIM_LOAD_ID in worker._loads)
    log = worker.switch_log

    def src(kind, device_id, action):
        hit = log.last(kind, device_id, action)
        return hit[1] if hit else None

    worker.set_psu_output(dw.SIM_PSU_ID, True)
    check("Kachel: Netzteil EIN", src("psu", dw.SIM_PSU_ID, "PSU_OUT_ON") == "Kachel")
    worker.set_operation_source("Preset")
    worker.set_load_current(dw.SIM_LOAD_ID, 0.5)
    worker.set_operation_source("Kachel")
    check("Preset: Last-Sollwert", src("load", dw.SIM_LOAD_ID, "CURR") == "Preset")
    worker.set_fg_waveform(dw.SIM_FG_ID, 1, dw.FG_WAVE_ACTIONS["FG_WAVE_SQUARE"])
    check("Kachel: Wellenform als Ablauf-Aktion", src("fg", dw.SIM_FG_ID, "FG_WAVE_SQUARE") == "Kachel")
    worker.execute_action(dw.SIM_PSU_ID, "psu", "PSU_OUT_OFF", 0.0, 0)
    check("Ablauf: Netzteil AUS", src("psu", dw.SIM_PSU_ID, "PSU_OUT_OFF") == "Ablauf")
    worker.execute_action(dw.SIM_HIL_ID, "hil", "HIL_AIN_READ", 0.0, 1)
    check("Lesen zaehlt nicht", src("hil", dw.SIM_HIL_ID, "HIL_AIN_READ") is None)
    worker.execute_remote_action(1, dw.SIM_LOAD_ID, "load", "OUT_ON", 0.0, 0, "MCP/REST")
    check("MCP/REST: Last EIN", src("load", dw.SIM_LOAD_ID, "OUT_ON") == "MCP/REST")
    worker.execute_remote_action(2, dw.SIM_LOAD_ID, "load", "OUT_OFF", 0.0, 0, "Verbund")
    check("Verbund-Partner: Last AUS", src("load", dw.SIM_LOAD_ID, "OUT_OFF") == "Verbund")
    worker.execute_remote_action(3, dw.SIM_HIL_ID, "hil", "HIL_RELAY_ON", 0.0, 2)
    check("Fernsteuerung ohne Angabe: MCP/REST", src("hil", dw.SIM_HIL_ID, "HIL_RELAY_ON") == "MCP/REST")

    before = log.ages()
    worker.set_psu_output("psu:GIBTESNICHT", True)
    worker.execute_action("psu:GIBTESNICHT", "psu", "PSU_OUT_ON", 0.0, 0)
    worker.execute_remote_action(4, "load:GIBTESNICHT", "load", "OUT_ON", 0.0, 0, "MCP/REST")
    check("fehlgeschlagen (unbekanntes Geraet) zaehlt nicht", set(log.ages()) == set(before)
          and "psu:GIBTESNICHT" not in log.ages())
    on_before = log.last("psu", dw.SIM_PSU_ID, "PSU_OUT_ON")
    spin(app, 0.05)   # monotone Uhr unter Windows: ~16 ms Aufloesung
    worker.set_psu_manual(dw.SIM_PSU_ID, True)
    on_after = log.last("psu", dw.SIM_PSU_ID, "PSU_OUT_ON")
    check("Freigabe zur Handbedienung schaltet EIN (Kachel)", on_after is not None and on_after != on_before
          and on_after[1] == "Kachel")
    off_before = log.last("psu", dw.SIM_PSU_ID, "PSU_OUT_OFF")
    results: list = []
    worker.remote_action_completed.connect(lambda *args: results.append(args))
    worker.execute_remote_action(5, dw.SIM_PSU_ID, "psu", "PSU_OUT_OFF", 0.0, 0, "MCP/REST")
    spin(app, 0.1)
    check("abgelehnt (Handbedienung) zaehlt nicht", results and results[-1][1] is False
          and log.last("psu", dw.SIM_PSU_ID, "PSU_OUT_OFF") == off_before, str(results[-1:]))
    worker.set_psu_manual(dw.SIM_PSU_ID, False)
    spin(app, 0.2)
    worker.all_outputs_off("check")
    check("ALLE AUS: Netzteil/Last AUS verbucht", src("psu", dw.SIM_PSU_ID, "PSU_OUT_OFF") == "Alle aus"
          and src("load", dw.SIM_LOAD_ID, "OUT_OFF") == "Alle aus")
    worker.stop_timers()


# -- runner ----------------------------------------------------------------------------------------------

def run_steps(app, runner, steps, timeout=10.0):
    events: list = []
    connections = [
        (runner.step_started, lambda i, _s: events.append(("started", i, time.monotonic()))),
        (runner.run_finished, lambda: events.append(("finished", time.monotonic()))),
        (runner.step_failed, lambda i, m: events.append(("failed", i, m))),
        (runner.step_note, lambda i, m: events.append(("note", i, m))),
    ]
    for signal, slot in connections:
        signal.connect(slot)
    runner.start(steps)
    spin(app, timeout, lambda: not runner.is_running())
    for signal, slot in connections:
        signal.disconnect(slot)
    return events


def gap_of(events, index):
    start = next((e[2] for e in events if e[0] == "started" and e[1] == index), None)
    end = next((e[1] for e in events if e[0] == "finished"), None)
    return (end - start) if start is not None and end is not None else -1.0


def notes_of(events):
    return [e[2] for e in events if e[0] == "note"]


def section_runner(app) -> None:
    print("runner -- TestRunner mit Auskunft ueber die letzte Schaltung")
    from PySide6.QtCore import QTimer
    import switch_log as sl
    import testcase_runner
    from testcase_model import TestStep
    from testcase_runner import TestRunner

    runner = TestRunner()
    runner.execute_action.connect(lambda *_a: QTimer.singleShot(0, lambda: runner.on_action_completed(True, "", 0.0)))
    log = sl.SwitchLog()
    runner.switch_lookup = log.lookup
    psu = dict(device_kind="psu", device_id="psu:A")

    log.note("psu", "psu:A", "PSU_OUT_ON", sl.SOURCE_TILE, at=time.monotonic() - 0.4)
    events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.9)])
    gap = gap_of(events, 0)
    check("Kachel-Schaltung vor dem Lauf zaehlt (nur der Rest)", 0.4 <= gap < 0.7, f"{gap:.3f}")
    check("Vermerk mit Quelle Kachel", any("zuletzt vor" in n and "(Kachel)" in n for n in notes_of(events)),
          str(notes_of(events)))

    events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_VOLT", duration=0.5)])
    check("unbekannt -> volle Zeit", gap_of(events, 0) >= 0.5, f"{gap_of(events, 0):.3f}")
    check("Vermerk 'noch nicht ausgefuehrt'", any("noch nicht" in n for n in notes_of(events)), str(notes_of(events)))

    # Qt-Timer duerfen bis ~5 % zu frueh feuern: ab dem tatsaechlichen Verbuchen rechnen, nicht ab 1,2 s
    noted: list[float] = []

    def note_remote():
        noted.append(time.monotonic())
        log.note("psu", "psu:A", "PSU_OUT_OFF", sl.SOURCE_REMOTE)

    QTimer.singleShot(1200, note_remote)
    t0 = time.monotonic()
    events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_OUT_OFF", duration=1.5)])
    end = time.monotonic()
    took = end - t0
    after = end - noted[0] if noted else 0.0
    check("Schaltung waehrend des Wartens verlaengert (MCP/REST)", bool(noted) and noted[0] - t0 > 0.9
          and after >= 1.4, f"gesamt {took:.3f} s, nach der Schaltung {after:.3f} s")
    check("Vermerk 'erneut ausgefuehrt'", any("erneut" in n and "MCP/REST" in n for n in notes_of(events)),
          str(notes_of(events)))

    # Partner: Stand von VOR dem Schrittbeginn -> auf einen neueren warten
    # Stand von 0,5 s VOR dem Schrittbeginn, bis "fresh" gesetzt wird (wie der naechste Abgleich)
    state = {"fresh": False, "taken": time.monotonic() - 0.5}

    def stale_then_fresh(_kind, _device_id, _action):
        now = time.monotonic()
        return sl.LastSwitch(now - 20.0, "Kachel am Pi", now if state["fresh"] else state["taken"])

    runner.switch_lookup = stale_then_fresh
    QTimer.singleShot(500, lambda: state.update(fresh=True))
    events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=1.0)])
    gap = gap_of(events, 0)
    check("alter Stand, Frist laut ihm um: wartet auf neueren", 0.45 <= gap < 0.95, f"{gap:.3f}")
    old_timeout = testcase_runner.SINCE_FRESH_TIMEOUT_S
    testcase_runner.SINCE_FRESH_TIMEOUT_S = 0.6
    try:
        state.update(fresh=False, taken=time.monotonic() - 0.5)
        events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=1.0)])
    finally:
        testcase_runner.SINCE_FRESH_TIMEOUT_S = old_timeout
    gap = gap_of(events, 0)
    check("nie ein neuerer Stand -> volle Zeit ab Schrittbeginn", gap >= 1.0, f"{gap:.3f}")
    check("Vermerk 'kein aktueller Stand'", any("kein aktueller Stand" in n for n in notes_of(events)),
          str(notes_of(events)))

    runner.switch_lookup = lambda *_a: sl.LastSwitch(None, "keine Verbindung zu Pi", time.monotonic())
    events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.5)])
    check("keine Verbindung -> volle Zeit", gap_of(events, 0) >= 0.5, f"{gap_of(events, 0):.3f}")
    check("Vermerk nennt den Grund", any("unbekannt (keine Verbindung zu Pi)" in n for n in notes_of(events)),
          str(notes_of(events)))

    runner.switch_lookup = lambda *_a: sl.LastSwitch(time.monotonic() - 30.0, "Kachel", time.monotonic())
    events = run_steps(app, runner, [TestStep(**psu, action="PSU_OUT_ON"),
                                     TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.5)])
    check("eigene juengere Ausfuehrung im Lauf gewinnt", gap_of(events, 1) >= 0.45
          and any("(Ablauf)" in n for n in notes_of(events)), f"{gap_of(events, 1):.3f} {notes_of(events)}")

    def broken(*_a):
        raise RuntimeError("kaputt")

    runner.switch_lookup = broken
    events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.3)])
    check("Fehler beim Nachsehen: volle Zeit statt Abbruch", gap_of(events, 0) >= 0.3
          and not any(e[0] == "failed" for e in events))


# -- verbund ---------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_window(settings_data: dict):
    from main_window import MainWindow
    from settings import Settings
    settings = Settings()
    settings._data = {"simulation_mode": True, **settings_data}
    window = MainWindow(settings)
    window.resize(1280, 800)
    window.show()
    return window


def http(method: str, port: int, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {TOKEN_B}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def ask(app, *args):
    """HTTP im eigenen Thread, waehrend die Ereignisschleife weiterlaeuft (B fuehrt im GUI-Thread aus)."""
    box: dict = {}
    threading.Thread(target=lambda: box.setdefault("r", http(*args)), daemon=True).start()
    spin(app, 20.0, lambda: "r" in box)
    return box.get("r", (0, {}))


def section_verbund(app) -> None:
    print("verbund -- zwei Hauptfenster")
    import device_worker as dw
    import verbund_ids
    from testcase_model import TestStep, save_steps

    port = free_port()
    window_b = make_window({
        "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port, "share_token": TOKEN_B,
        "share_devices": {dw.SIM_PSU_ID: {"read": True, "control": True},
                          dw.SIM_LOAD_ID: {"read": True, "control": True}},
    })
    server_b = window_b._share_server
    server_b._app_info = {**server_b._app_info, "host": HOST_B}
    if server_b._server is not None:
        server_b._server.share_app_info = server_b._app_info
    window_b._live_state.set_remote_control(True, 3600.0)
    window_a = make_window({"partner_enabled": True, "partner_address": f"127.0.0.1:{port}",
                            "partner_token": TOKEN_B, "partner_name": "Pi"})
    partner_psu = verbund_ids.qualify(HOST_B, dw.SIM_PSU_ID)
    partner_load = verbund_ids.qualify(HOST_B, dw.SIM_LOAD_ID)
    ready = spin(app, 15.0, lambda: window_a._partner.is_connected()
                 and dw.SIM_PSU_ID in window_b._worker._psus and dw.SIM_LOAD_ID in window_b._worker._loads
                 and dw.SIM_PSU_ID in window_a._worker._psus)
    check("A mit B verbunden", ready)
    log_b, log_a = window_b._worker.switch_log, window_a._worker.switch_log

    window_b.control_tab._sections[dw.SIM_PSU_ID]._output_on_button.click()
    spin(app, 3.0, lambda: log_b.last("psu", dw.SIM_PSU_ID, "PSU_OUT_ON") is not None)
    tile_b = log_b.last("psu", dw.SIM_PSU_ID, "PSU_OUT_ON")
    check("B: Kachel EIN verbucht", tile_b is not None and tile_b[1] == "Kachel", str(tile_b))

    status, body = ask(app, "POST", port, f"/tiles/{dw.SIM_PSU_ID}/actions", {"action": "PSU_OUT_OFF"})
    mcp_b = log_b.last("psu", dw.SIM_PSU_ID, "PSU_OUT_OFF")
    check("B: MCP/REST-Befehl verbucht", status == 200 and mcp_b is not None and mcp_b[1] == "MCP/REST",
          f"{status} {body} {mcp_b}")

    window_b._live_state.set_partner_ip("127.0.0.1")   # A ist der gekoppelte Partner (Verbund-Kopf + Adresse)
    check("Last-Spannung bei B noch nie geschaltet", log_b.last("load", dw.SIM_LOAD_ID, "VOLT") is None)
    window_a._partner.send_action(dw.SIM_LOAD_ID, "OUT_ON", label="Pruefung")
    spin(app, 5.0, lambda: log_b.last("load", dw.SIM_LOAD_ID, "OUT_ON") is not None)
    partner_b = log_b.last("load", dw.SIM_LOAD_ID, "OUT_ON")
    check("Partner-Befehl von A wird bei B verbucht (Verbund)", partner_b is not None and partner_b[1] == "Verbund",
          str(partner_b))

    status, values = ask(app, "GET", port, f"/values?tiles={dw.SIM_PSU_ID},{dw.SIM_LOAD_ID}")
    tiles = {t.get("id"): t for t in values.get("tiles", [])}
    psu_switch = tiles.get(dw.SIM_PSU_ID, {}).get("last_switch", {})
    check("/values: last_switch mit Alter und Quelle", status == 200
          and psu_switch.get("PSU_OUT_ON", {}).get("source") == "Kachel"
          and psu_switch.get("PSU_OUT_OFF", {}).get("source") == "MCP/REST"
          and isinstance(psu_switch.get("PSU_OUT_OFF", {}).get("age_s"), float)
          and tiles.get(dw.SIM_LOAD_ID, {}).get("last_switch", {}).get("OUT_ON", {}).get("source") == "Verbund",
          str(psu_switch))

    def a_knows() -> bool:
        snap = window_a._partner.snapshot()
        tile = next((t for t in (snap.tiles if snap else []) if t.get("id") == dw.SIM_LOAD_ID), {})
        return "OUT_ON" in tile.get("last_switch", {})

    check("A kennt die Schaltung ueber den Abgleich", spin(app, 5.0, a_knows))

    window_a.control_tab._sections[dw.SIM_PSU_ID]._output_on_button.click()
    spin(app, 3.0, lambda: log_a.last("psu", dw.SIM_PSU_ID, "PSU_OUT_ON") is not None)
    tile_a = log_a.last("psu", dw.SIM_PSU_ID, "PSU_OUT_ON")
    check("A: eigene Kachel EIN verbucht", tile_a is not None and tile_a[1] == "Kachel", str(tile_a))

    # Jede Schaltung soll mindestens 1,5 s her sein, sonst ist "nur der Rest" nicht vom vollen
    # Warten zu unterscheiden (langsamer CI-Runner: Lauf startete 0,7 s nach der Schaltung)
    spin(app, 3.0, lambda: time.monotonic() - tile_a[0] >= 1.5)
    # Mindestzeiten so, dass jeweils noch ~1,5-2,5 s fehlen: gewartet wird nur der Rest
    now = time.monotonic()
    d_load = round(now - partner_b[0] + 2.5, 1)
    d_on = round(now - tile_b[0] + 3.5, 1)
    d_off = round(now - mcp_b[0] + 4.5, 1)
    d_own = round(now - tile_a[0] + 5.5, 1)
    own_psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    steps = [TestStep(**own_psu, action="PSU_VOLT", value=3.0),
             TestStep(step_type="wait_since", device_kind="load", device_id=partner_load, action="OUT_ON",
                      duration=d_load),
             TestStep(step_type="wait_since", device_kind="psu", device_id=partner_psu, action="PSU_OUT_ON",
                      duration=d_on),
             TestStep(step_type="wait_since", device_kind="psu", device_id=partner_psu, action="PSU_OUT_OFF",
                      duration=d_off),
             TestStep(step_type="wait_since", **own_psu, action="PSU_OUT_ON", duration=d_own),
             TestStep(step_type="wait_since", device_kind="load", device_id=partner_load, action="VOLT",
                      duration=1.0)]
    path = DATA / "testcases" / "warten_seit.json"
    path.parent.mkdir(exist_ok=True)
    save_steps(steps, path)
    tab_a = window_a.testcase_tab
    check("Laden", tab_a._load_path(path))
    starts: dict[int, float] = {}
    finished: list[float] = []
    failed: list[str] = []
    runner = window_a._test_runner
    on_start = lambda i, _s: starts.setdefault(i, time.monotonic())  # noqa: E731
    on_finish = lambda: finished.append(time.monotonic())  # noqa: E731
    on_fail = lambda _i, m: failed.append(m)  # noqa: E731
    runner.step_started.connect(on_start)
    runner.run_finished.connect(on_finish)
    runner.step_failed.connect(on_fail)
    window_a._on_run_requested()
    spin(app, 40.0, lambda: bool(finished) or bool(failed))
    check("Lauf an A beendet ohne Fehler", finished and not failed, str(failed) or tab_a._status_label.text())
    ends = {i: starts.get(i + 1, finished[0] if finished else 0.0) for i in range(1, 6)}
    record = window_a._run_recorder.record()
    notes = {e.index: e.message for e in record.events if e.kind == "note"} if record else {}

    def judged(index, at, duration, label, source):
        start, end = starts.get(index, 0.0), ends.get(index, 0.0)
        waited = end - start
        enough = end - at >= duration - 0.01
        partial = waited < duration - 0.8
        check(f"{label}: mindestens {duration:g} s seit der Schaltung, nur der Rest gewartet",
              enough and partial, f"seit Schaltung {end - at:.2f} s, gewartet {waited:.2f} s")
        check(f"{label}: Vermerk nennt '{source}'", source in notes.get(index, ""), notes.get(index, ""))

    if finished:
        judged(1, partner_b[0], d_load, "Partner-Befehl (Last EIN)", "Verbund am Pi")
        judged(2, tile_b[0], d_on, "Kachel am Besitzer (Netzteil EIN)", "Kachel am Pi")
        judged(3, mcp_b[0], d_off, "MCP/REST am Besitzer (Netzteil AUS)", "MCP/REST am Pi")
        judged(4, tile_a[0], d_own, "eigene Kachel (Netzteil EIN)", "(Kachel)")
        waited = ends[5] - starts.get(5, 0.0)
        check("Partner: nie geschaltet -> volle Zeit", waited >= 1.0, f"{waited:.2f}")
        check("Partner: Vermerk 'seit dessen Start nicht geschaltet'",
              "seit dessen Start nicht geschaltet" in notes.get(5, ""), notes.get(5, ""))
    spin(app, 3.0)

    server_b.stop()
    check("B getrennt", spin(app, 10.0, lambda: not window_a._partner.is_connected()))
    save_steps([TestStep(**own_psu, action="PSU_VOLT", value=2.0),
                TestStep(step_type="wait_since", device_kind="psu", device_id=partner_psu, action="PSU_OUT_ON",
                         duration=0.8)], path)
    tab_a._load_path(path)
    starts.clear()
    finished.clear()
    failed.clear()
    window_a._on_run_requested()
    spin(app, 10.0, lambda: bool(finished) or bool(failed))
    record = window_a._run_recorder.record()
    note = next((e.message for e in record.events if e.kind == "note" and e.index == 1), "") if record else ""
    waited = (finished[0] - starts.get(1, 0.0)) if finished else -1.0
    check("Verbindung weg: volle Zeit", finished and not failed and waited >= 0.8,
          f"{waited:.2f} {failed} {tab_a._status_label.text()}")
    check("Verbindung weg: Vermerk nennt den Grund", "keine Verbindung zu Pi" in note, note)
    runner.step_started.disconnect(on_start)
    runner.run_finished.disconnect(on_finish)
    runner.step_failed.disconnect(on_fail)
    for window in (window_a, window_b):
        window.close()
    app.processEvents()


def main() -> int:
    import no_hardware
    no_hardware.block()   # echte Geraete nicht anfassen
    section_unit()
    app = QApplication.instance() or QApplication([])
    section_worker(app)
    section_runner(app)
    section_verbund(app)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok. (nur Simulation, keine Hardware geprueft)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

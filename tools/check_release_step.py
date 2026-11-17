"""Prueft PI-74 a (1.0.1): Schritt "Warten auf Freigabe" (Freigabe durch ein Programm ueber REST/MCP) und den
Bugfix "error" im Laufregister nach einem Schrittfehler bei Laeufen im Fenster.

- format  -- speichern/laden: Format 6 (Bausteine 5) nur mit dem neuen Schritt, Kennung/Hoechstzeit zurueck,
             aeltere Versionen (Format 5) lehnen ab; Dateien ohne ihn bleiben beim alten Format
- runner  -- TestRunner allein: wartet, Freigabe (mit/ohne Kennung, Schrittnummer), falsche Kennung/Schritt,
             Hoechstzeit -> FAIL mit Meldung, ohne Server -> Abbruch, Bestaetigung NICHT ueber release(),
             Freigabe waehrend Pause, doppelte Freigabe
- registry -- RunRegistry.finish traegt die Fehlermeldung nach (Bugfix), ueberschreibt keine vorhandene
- editor  -- Einfuegen-Menue, Kennung/Hoechstzeit bearbeiten, Speichern/Laden, Ausfuehren-Ansicht, Baustein-Vorschau
- window  -- echtes Hauptfenster mit Freigabe: "waiting" kind=release in /runs/{id} und /status, Freigabe per
             POST /runs/{id}/continue (falsche Kennung/Schritt, unbekannter Lauf, ohne Steuerrecht abgelehnt),
             Bericht mit Aufrufer/Uhrzeit, Hoechstzeit -> FAIL und "error" in GET /runs/{id} (auch Start im
             Fenster), Bestaetigung weiterhin NICHT ueber REST, ohne Server nicht gestartet
- mcp     -- echter MCP-Server (labcontrol_mcp/.venv) gegen das Hauptfenster: continue_run (falsche Kennung,
             richtige), get_run mit waiting, continue_run auf eine Bestaetigung -> confirm_local_only
- cli     -- `labcontrol run` ohne Fenster: Warten auf Freigabe -> Exit 3 mit Meldung

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_release_step.py [abschnitt ...]
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
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
DATA = Path(tempfile.mkdtemp(prefix="lc-release-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN = "release-check-token"


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


# -- format ----------------------------------------------------------------------------------------------

def section_format() -> None:
    print("format -- speichern/laden")
    import testcase_model as tm

    folder = Path(tempfile.mkdtemp(prefix="lc-release-format-"))
    steps = [
        tm.TestStep(device_kind="psu", device_id="psu:A", action="PSU_OUT_ON"),
        tm.TestStep(step_type="await_release", params={"key": "flash_ok"}, duration=45.0),
        tm.TestStep(step_type="await_release", duration=5.0, enabled=False),
    ]
    path = folder / "neu.json"
    tm.save_steps(steps, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    check("mit Warten auf Freigabe: Format 6", data["version"] == 6, str(data["version"]))
    loaded, _meta = tm.load_testcase(path)
    check("Kennung/Hoechstzeit zurueck", (loaded[1].step_type, tm.release_key(loaded[1]), loaded[1].duration)
          == ("await_release", "flash_ok", 45.0), str(loaded[1]))
    check("ohne Kennung und deaktiviert zurueck", tm.release_key(loaded[2]) == "" and not loaded[2].enabled
          and loaded[2].duration == 5.0)
    tm.save_steps(loaded, folder / "neu2.json")
    check("Laden+Speichern verlustfrei", json.loads((folder / "neu2.json").read_text(encoding="utf-8")) == data)
    tm.save_steps([tm.TestStep(step_type="confirm", params={"text": "x"})], folder / "v4.json")
    check("Bestaetigung allein weiter Format 4", json.loads((folder / "v4.json").read_text(encoding="utf-8"))
          ["version"] == 4)
    tm.save_steps([tm.TestStep(step_type="wait", duration=1.0)], folder / "v2.json")
    check("ohne neue Schritte weiter Format 2", json.loads((folder / "v2.json").read_text(encoding="utf-8"))
          ["version"] == 2)
    old_max = tm.FILE_FORMAT_VERSION
    try:
        tm.FILE_FORMAT_VERSION = 5
        try:
            tm.load_testcase(path)
            refused = False
        except ValueError as exc:
            refused = "neueren Programmversion" in str(exc)
    finally:
        tm.FILE_FORMAT_VERSION = old_max
    check("Version mit Format 5 lehnt die Datei ab (statt still zu ueberspringen)", refused)
    block = folder / "baustein.json"
    tm.save_block(steps[1:2], "Freigabe", block)
    block_data = json.loads(block.read_text(encoding="utf-8"))
    name, block_steps = tm.load_block(block)
    check("Baustein: Format 5, Schritt zurueck", block_data["version"] == 5 and name == "Freigabe"
          and tm.release_key(block_steps[0]) == "flash_ok", str(block_data["version"]))
    tm.save_block([tm.TestStep(step_type="confirm", params={"text": "x"})], "b", folder / "b4.json")
    check("Baustein mit Bestaetigung weiter Format 3",
          json.loads((folder / "b4.json").read_text(encoding="utf-8"))["version"] == 3)
    check("Kurztext", tm.release_summary(steps[1]) == "„flash_ok“ (≤ 45 s)"
          and tm.release_summary(steps[2]) == "(≤ 5 s)", tm.release_summary(steps[1]))
    check("Bezeichnung 'Warten auf Freigabe'", tm.CONTROL_STEP_LABELS["await_release"] == "Warten auf Freigabe")


# -- runner ----------------------------------------------------------------------------------------------

class FakeDevices:
    def __init__(self, runner) -> None:
        from PySide6.QtCore import QTimer
        self.done: list[tuple[str, str]] = []

        def execute(device_id, kind, action, value, channel):
            def ack():
                self.done.append((device_id, action))
                runner.on_action_completed(True, "", 0.0)
            QTimer.singleShot(0, ack)
        runner.execute_action.connect(execute)


def record(runner):
    events: list = []
    connections = [
        (runner.step_started, lambda i, _s: events.append(("started", i))),
        (runner.run_finished, lambda: events.append(("finished",))),
        (runner.run_stopped, lambda: events.append(("stopped",))),
        (runner.step_failed, lambda i, m: events.append(("failed", i, m))),
        (runner.step_note, lambda i, m: events.append(("note", i, m))),
        (runner.waiting_changed, lambda info: events.append(("waiting", info))),
        (runner.released, lambda i, who: events.append(("released", i, who))),
    ]
    for signal, slot in connections:
        signal.connect(slot)

    def stop():
        for signal, slot in connections:
            signal.disconnect(slot)
    return events, stop


def section_runner(app) -> None:
    print("runner -- TestRunner allein")
    from testcase_model import TestStep
    from testcase_runner import TestRunner

    runner = TestRunner()
    devices = FakeDevices(runner)
    psu = dict(device_kind="psu", device_id="psu:A")
    steps = [TestStep(**psu, action="PSU_VOLT", value=3.0),
             TestStep(step_type="await_release", params={"key": "flash_ok"}, duration=10.0),
             TestStep(**psu, action="PSU_OUT_ON")]

    runner.release_available = lambda: True
    events, stop = record(runner)
    runner.start(steps)
    spin(app, 2.0, lambda: (runner.waiting() or {}).get("kind") == "release")
    waiting = runner.waiting() or {}
    check("wartet auf Freigabe mit Kennung und Hoechstzeit", waiting.get("kind") == "release"
          and waiting.get("key") == "flash_ok" and waiting.get("step") == 2 and waiting.get("timeout_s") == 10.0
          and "wartet auf Freigabe" in waiting.get("message", ""), str(waiting))
    check("vor der Freigabe nichts eingeschaltet", ("psu:A", "PSU_OUT_ON") not in devices.done, str(devices.done))
    check("falsche Kennung abgelehnt", runner.release("prog", "andere") == "release_mismatch")
    check("falscher Schritt abgelehnt", runner.release("prog", None, 3) == "release_mismatch")
    check("... wartet weiter", (runner.waiting() or {}).get("kind") == "release" and runner.is_running())
    check("Freigabe mit Kennung und Schritt", runner.release("127.0.0.1 Programm:ci", "flash_ok", 2) == "")
    check("doppelte Freigabe -> not_waiting_release", runner.release("prog") in ("not_waiting_release",
                                                                                "not_running"))
    spin(app, 2.0, lambda: not runner.is_running())
    stop()
    check("nach der Freigabe weiter und fertig", ("finished",) in events and ("psu:A", "PSU_OUT_ON") in devices.done,
          str(events))
    notes = [e[2] for e in events if e[0] == "note"]
    check("Vermerk: von wem und wann", any(n.startswith("freigegeben von 127.0.0.1 Programm:ci um ") for n in notes),
          str(notes))
    check("Signal released mit Aufrufer", ("released", 1, "127.0.0.1 Programm:ci") in events)
    waits = [e[1] for e in events if e[0] == "waiting"]
    check("Wartezustand wieder geloescht", waits and waits[-1] is None, str(waits))
    check("nach dem Ende -> not_running", runner.release("prog") == "not_running")

    # ohne Kennung im Aufruf: gibt den wartenden Schritt frei
    devices.done.clear()
    events, stop = record(runner)
    runner.start([TestStep(step_type="await_release", duration=10.0), TestStep(**psu, action="PSU_OUT_OFF")])
    spin(app, 1.0, lambda: runner.waiting() is not None)
    check("Freigabe ohne Kennung", runner.release("x") == "")
    spin(app, 2.0, lambda: not runner.is_running())
    stop()
    check("... und fertig", ("finished",) in events and devices.done == [("psu:A", "PSU_OUT_OFF")])

    # Hoechstzeit
    devices.done.clear()
    events, stop = record(runner)
    t0 = time.monotonic()
    runner.start([TestStep(step_type="await_release", params={"key": "k"}, duration=0.5),
                  TestStep(**psu, action="PSU_OUT_ON")])
    spin(app, 3.0, lambda: not runner.is_running())
    took = time.monotonic() - t0
    stop()
    failed = [e for e in events if e[0] == "failed"]
    check("Hoechstzeit -> FAIL mit Meldung", failed and failed[0][1] == 0 and "Höchstzeit" in failed[0][2]
          and "„k“" in failed[0][2] and took >= 0.5, f"{failed} {took:.2f}")
    check("... nichts geschaltet, Wartezustand geloescht", not devices.done and runner.waiting() is None)

    # Pause: Hoechstzeit laeuft nicht weiter, Freigabe waehrend der Pause wirkt nach "Weiter"
    devices.done.clear()
    events, stop = record(runner)
    runner.start([TestStep(step_type="await_release", duration=0.6), TestStep(**psu, action="PSU_OUT_ON")])
    spin(app, 0.2)
    runner.pause()
    paused = runner.is_paused()
    spin(app, 0.8)
    check("Pause haelt die Hoechstzeit an", paused and runner.is_running() and not any(e[0] == "failed" for e in events))
    check("Freigabe waehrend der Pause", runner.release("x") == "" and runner.waiting() is None)
    spin(app, 0.3)
    check("... schaltet erst nach Weiter", not devices.done)
    runner.resume()
    spin(app, 2.0, lambda: not runner.is_running())
    stop()
    check("... dann weiter und fertig", ("finished",) in events and devices.done == [("psu:A", "PSU_OUT_ON")],
          str(events))

    # Bestaetigung geht NICHT ueber release()
    runner.confirm_available = True
    runner.start([TestStep(step_type="confirm", params={"text": "Ein?"}), TestStep(**psu, action="PSU_OUT_ON")])
    spin(app, 0.2)
    code = runner.release("prog")
    check("Bestaetigung nicht ueber Freigabe -> confirm_local_only", code == "confirm_local_only"
          and (runner.waiting() or {}).get("kind") == "confirm", code)
    runner.stop()
    spin(app, 0.2)

    # ohne Server
    runner.release_available = False
    devices.done.clear()
    events, stop = record(runner)
    runner.start([TestStep(step_type="await_release", duration=5.0), TestStep(**psu, action="PSU_OUT_ON")])
    spin(app, 1.0, lambda: not runner.is_running())
    stop()
    failed = [e for e in events if e[0] == "failed"]
    check("ohne Server: Abbruch mit Meldung, nichts geschaltet", failed and "Netzwerkfreigabe" in failed[0][2]
          and not devices.done, str(failed))


# -- registry --------------------------------------------------------------------------------------------

def section_registry() -> None:
    print("registry -- Fehlermeldung im Laufregister")
    from run_registry import RunRegistry

    registry = RunRegistry()
    run_id = registry.begin("a.json", "gui", 3)
    registry.finish(run_id, "error", 2, {}, (1, "Gerät antwortet nicht"))
    entry = registry.get(run_id, 0)
    check("finish traegt die Meldung nach", entry["error"] == "Schritt 2: Gerät antwortet nicht"
          and [e["kind"] for e in entry["events"]] == ["failed", "end"], str(entry))
    run_id = registry.begin("b.json", "gui", 3)
    registry.step_failed(run_id, 0, "zuerst")
    registry.finish(run_id, "error", 2, {}, (0, "zuerst"))
    entry = registry.get(run_id, 0)
    check("vorhandene Meldung bleibt, kein doppeltes Ereignis", entry["error"] == "Schritt 1: zuerst"
          and [e["kind"] for e in entry["events"]].count("failed") == 1, str(entry))
    run_id = registry.begin("c.json", "gui", 1)
    registry.finish(run_id, "passed", 0, {}, None)
    check("ohne Fehler bleibt error leer", registry.get(run_id)["error"] is None)
    registry.released(run_id, 1, "127.0.0.1 (ci)")
    check("Meldung 'released'", registry.get(run_id, 0)["events"][-1] == {
        "t": registry.get(run_id, 0)["events"][-1]["t"], "kind": "released", "step": 2, "by": "127.0.0.1 (ci)"})


# -- editor ----------------------------------------------------------------------------------------------

def section_editor(app) -> None:
    print("editor -- Einfuegen, Bearbeiten, Speichern/Laden")
    import testcase_model as tm
    from block_dialog import _step_line
    from testcase_tab import COL_ACTION, COL_DEVICE, COL_DURATION, TestcaseTab

    tab = TestcaseTab()
    tab._action_add_release.trigger()
    row = tab._table.rowCount() - 1
    step = tab.steps()[row]
    check("Menue 'Warten auf Freigabe': Vorgabe 60 s, ohne Kennung",
          (step.step_type, step.duration, tm.release_key(step)) == ("await_release", 60.0, ""), str(step))
    check("Zeile zeigt 'Warten auf Freigabe'", tab._table.cellWidget(row, COL_DEVICE).text() == "Warten auf Freigabe")
    check("ohne Kennung kein leerer Parameter", "key" not in step.params, str(step.params))
    tab._table.cellWidget(row, COL_ACTION).setText(" flash_ok ")
    tab._table.cellWidget(row, COL_DURATION).setValue(90.0)
    step = tab.steps()[row]
    check("Bearbeiten: Kennung/Hoechstzeit", (tm.release_key(step), step.duration) == ("flash_ok", 90.0), str(step))
    check("Ausfuehren-Ansicht", tab._describe_step(step) == "Warten auf Freigabe „flash_ok“ (≤ 90 s)",
          tab._describe_step(step))
    check("Baustein-Vorschau", _step_line(step) == "Warten auf Freigabe „flash_ok“ (≤ 90 s)", _step_line(step))
    path = DATA / "editor_release.json"
    tm.save_steps(tab.steps(), path)
    tab2 = TestcaseTab()
    check("Laden", tab2._load_path(path))
    again = tab2.steps()
    check("Laden: gleiche Schritte", [(s.step_type, s.duration, tm.release_key(s)) for s in again]
          == [(s.step_type, s.duration, tm.release_key(s)) for s in tab.steps()])
    check("nach dem Laden unveraendert", not tab2.is_modified())
    from i18n import Translator
    Translator.instance().set_language("en")
    app.processEvents()
    english = tab2._table.cellWidget(row, COL_DEVICE).text()
    menu = tab2._action_add_release.text()
    Translator.instance().set_language("de")
    app.processEvents()
    check("englischer Zeilen- und Menuetext", english == "Wait for release" and menu == "Wait for release",
          f"{english} / {menu}")
    check("nach Sprachwechsel Kennung erhalten", tm.release_key(tab2.steps()[row]) == "flash_ok")
    tab.deleteLater()
    tab2.deleteLater()


# -- window ----------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(method: str, port: int, path: str, body=None, timeout: float = 20.0):
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
    spin(app, 25.0, lambda: "r" in box)
    return box.get("r", (0, {}))


_WINDOW: dict = {}


def open_window(app):
    if "window" in _WINDOW:
        return _WINDOW["window"], _WINDOW["port"]
    import no_hardware
    no_hardware.block()
    import device_worker as dw

    port = free_port()
    (DATA / "settings.json").write_text(json.dumps({
        "simulation_mode": True, "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port,
        "share_token": TOKEN, "share_local_bypass": True}), encoding="utf-8")
    from main_window import MainWindow
    from settings import Settings
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    spin(app, 5.0, lambda: dw.SIM_PSU_ID in window._online_devices.get("psu", set()))
    _WINDOW.update(window=window, port=port)
    return window, port


def save_cases() -> None:
    import data_store
    import device_worker as dw
    import testcase_model as tm

    folder = data_store.target_dir("testcases")
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=3.0),
                   tm.TestStep(step_type="await_release", params={"key": "flash_ok"}, duration=30.0),
                   tm.TestStep(**psu, action="PSU_OUT_ON"),
                   tm.TestStep(**psu, action="PSU_OUT_OFF")], folder / "freigabe.json")
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=3.0),
                   tm.TestStep(step_type="await_release", params={"key": "spaet"}, duration=1.0),
                   tm.TestStep(**psu, action="PSU_OUT_ON")], folder / "freigabe_timeout.json")
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=3.0),
                   tm.TestStep(step_type="confirm", params={"text": "Evalboard einschalten?"}),
                   tm.TestStep(**psu, action="PSU_OUT_ON")], folder / "bestaetigen.json")
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=3.0),
                   tm.TestStep(device_kind="psu", device_id="psu:GIBTESNICHT", action="PSU_OUT_ON")],
                  folder / "fehler.json")


def section_window(app) -> None:
    print("window -- Hauptfenster, REST")
    import data_store

    window, port = open_window(app)
    save_cases()
    reasons: list[str] = []
    window._request_all_off.connect(reasons.append)

    def run_entry(run_id):
        return window._live_state.runs.get(run_id, 0) or {}

    def waiting_kind(run_id):
        return (run_entry(run_id).get("waiting") or {}).get("kind")

    status, body = ask(app, "POST", port, "/runs", {"testcase": "freigabe.json"})
    run_id = body.get("run", "")
    check("Start per HTTP", status == 202 and run_id, f"{status} {body}")
    spin(app, 5.0, lambda: waiting_kind(run_id) == "release")
    status, entry = ask(app, "GET", port, f"/runs/{run_id}?events=0")
    waiting = entry.get("waiting") or {}
    check("GET /runs/{id}: wartet auf Freigabe (kind=release, Kennung, Rest)", waiting.get("kind") == "release"
          and waiting.get("key") == "flash_ok" and waiting.get("step") == 2 and waiting.get("timeout_s") == 30.0
          and 0 < waiting.get("remaining_s", 0) <= 30.0, str(entry)[:400])
    check("Meldung 'waiting' wait=release", any(e.get("kind") == "waiting" and e.get("wait") == "release"
                                                for e in entry.get("events", [])), str(entry.get("events")))
    status, info = ask(app, "GET", port, "/status")
    check("GET /status: run_waiting release", (info.get("run_waiting") or {}).get("kind") == "release",
          str(info.get("run_waiting")))
    status, body = ask(app, "POST", port, f"/runs/{run_id}/continue", {"key": "anders"})
    check("falsche Kennung -> 409 release_mismatch mit waiting", status == 409
          and body.get("error") == "release_mismatch" and (body.get("waiting") or {}).get("key") == "flash_ok",
          f"{status} {body}")
    status, body = ask(app, "POST", port, f"/runs/{run_id}/continue", {"step": 3})
    check("falscher Schritt -> 409 release_mismatch", status == 409 and body.get("error") == "release_mismatch",
          f"{status} {body}")
    status, body = ask(app, "POST", port, f"/runs/{run_id}/continue", {"step": "2"})
    check("ungueltiger Schritt -> 400", status == 400 and body.get("error") == "invalid_step", f"{status} {body}")
    status, body = ask(app, "POST", port, "/runs/19990101-000000-99/continue", {})
    check("unbekannter Lauf -> 404 unknown_run", status == 404 and body.get("error") == "unknown_run",
          f"{status} {body}")
    window._settings.set_share_local_bypass(False)
    spin(app, 0.3)
    status, body = ask(app, "POST", port, f"/runs/{run_id}/continue", {"key": "flash_ok"})
    check("ohne Steuerrecht -> 403 remote_control_inactive", status == 403
          and body.get("error") == "remote_control_inactive", f"{status} {body}")
    window._settings.set_share_local_bypass(True)
    spin(app, 0.3, lambda: window._share_server.is_running())
    check("... wartet weiter, nichts eingeschaltet", waiting_kind(run_id) == "release"
          and run_entry(run_id).get("step") == 2)
    status, body = ask(app, "POST", port, f"/runs/{run_id}/continue", {"key": "flash_ok", "step": 2, "by": "ci-skript"})
    check("Freigabe per HTTP -> 200 mit Schritt", status == 200 and body.get("ok") is True and body.get("step") == 2,
          f"{status} {body}")
    spin(app, 15.0, lambda: run_entry(run_id).get("running") is False)
    entry = run_entry(run_id)
    check("Lauf bestanden, nichts wartet mehr", entry.get("status") == "passed" and entry.get("waiting") is None,
          str(entry)[:300])
    released = [e for e in entry.get("events", []) if e.get("kind") == "released"]
    check("Meldung 'released' mit Aufrufer", released and released[0]["step"] == 2
          and "127.0.0.1" in released[0]["by"] and "(ci-skript)" in released[0]["by"], str(released))
    notes = [e.message for e in window._run_recorder.record().events if e.kind == "note"]
    check("Laufaufzeichnung: freigegeben von ... um ...", any(n.startswith("freigegeben von 127.0.0.1")
                                                             and "(ci-skript) um " in n for n in notes), str(notes))
    status, report = ask(app, "GET", port, f"/runs/{run_id}/report")
    html = report.get("_raw", "")
    check("Bericht: Schritt und Freigabe vermerkt", "Warten auf Freigabe „flash_ok“ (≤ 30 s)" in html
          and "freigegeben von 127.0.0.1" in html, html[-400:])
    status, body = ask(app, "POST", port, f"/runs/{run_id}/continue", {})
    check("nach dem Ende -> 409 not_running", status == 409 and body.get("error") == "not_running", f"{status} {body}")
    spin(app, 3.0)

    # Hoechstzeit ohne Freigabe -> FAIL, Meldung in GET /runs/{id} (Bugfix "error" leer)
    count = len(reasons)
    status, body = ask(app, "POST", port, "/runs", {"testcase": "freigabe_timeout.json"})
    run_id = body.get("run", "")
    spin(app, 10.0, lambda: run_entry(run_id).get("running") is False)
    status, entry = ask(app, "GET", port, f"/runs/{run_id}?events=0")
    check("Hoechstzeit -> Fehler, Exit 2", entry.get("status") == "error" and entry.get("exit_code") == 2,
          str(entry)[:300])
    check("GET /runs/{id}: error mit Meldung", "Schritt 2" in (entry.get("error") or "")
          and "Höchstzeit" in (entry.get("error") or ""), str(entry.get("error")))
    check("... Ereignis 'failed' vor 'end'", [e["kind"] for e in entry.get("events", [])
                                              if e["kind"] in ("failed", "end")] == ["failed", "end"],
          str(entry.get("events")))
    # seit PI-79 a: Standard-Abschaltung der Ablauf-Geraete statt ALLE AUS
    shutdown = entry.get("shutdown") or {}
    check("... Netzteil des Ablaufs abgeschaltet, kein ALLE AUS", shutdown.get("mode") == "standard"
          and shutdown.get("off") and not reasons[count:], f"{shutdown} {reasons[count:]}")
    spin(app, 3.0)

    # Bugfix: Schrittfehler bei einem Start im Fenster (nicht ueber REST)
    error = window.testcase_tab.load_for_run(data_store.target_dir("testcases") / "fehler.json")
    check("Ablauf im Editor geladen", not error, error)
    window._on_run_requested()
    spin(app, 10.0, lambda: not window._test_runner.is_running())
    spin(app, 0.5)
    entry = window._live_state.runs.list()[0]
    check("Start im Fenster: Lauf im Register mit Quelle gui", entry.get("source") == "gui"
          and entry.get("testcase", "").startswith("fehler"), str(entry)[:200])
    check("Start im Fenster: error mit Meldung des Schritts", entry.get("status") == "error"
          and "Schritt 2" in (entry.get("error") or ""), str(entry)[:300])
    status, body = ask(app, "GET", port, f"/runs/{entry.get('id')}")
    check("... auch ueber HTTP", status == 200 and body.get("error") == entry.get("error"), f"{status} {body}")
    spin(app, 3.0)

    # Bestaetigung bleibt nur am Bildschirm
    status, body = ask(app, "POST", port, "/runs", {"testcase": "bestaetigen.json"})
    run_id = body.get("run", "")
    spin(app, 5.0, lambda: window.testcase_tab.confirm_dialog() is not None)
    dialog = window.testcase_tab.confirm_dialog()
    status, body = ask(app, "POST", port, f"/runs/{run_id}/continue", {})
    check("Bestaetigung nicht ueber continue -> 409 confirm_local_only", status == 409
          and body.get("error") == "confirm_local_only" and (body.get("waiting") or {}).get("kind") == "confirm",
          f"{status} {body}")
    status, body = ask(app, "POST", port, f"/runs/{run_id}/confirm", {})
    check("kein /confirm ueber das Netz", status in (404, 405), f"{status} {body}")
    spin(app, 0.3)
    check("Rueckfrage weiter offen, Lauf wartet", window.testcase_tab.confirm_dialog() is dialog
          and waiting_kind(run_id) == "confirm" and run_entry(run_id).get("step") == 2)
    ask(app, "POST", port, f"/runs/{run_id}/stop", {})
    spin(app, 5.0, lambda: run_entry(run_id).get("running") is False)
    spin(app, 3.0)

    # ohne laufenden Server startet ein Ablauf mit "Warten auf Freigabe" nicht
    import testcase_model as tm
    steps = tm.load_testcase(data_store.target_dir("testcases") / "freigabe.json")[0]
    saved = window._test_runner.release_available
    window._test_runner.release_available = lambda: False
    refusal = window._run_session.refusal(steps)
    window._test_runner.release_available = saved
    check("ohne Server: abgelehnt vor dem Start", "Schritt 2" in refusal and "Netzwerkfreigabe" in refusal, refusal)
    check("mit Server: startbar", window._run_session.refusal(steps) == "", window._run_session.refusal(steps))


# -- mcp -------------------------------------------------------------------------------------------------

def mcp_python() -> Path | None:
    for path in (ROOT / "labcontrol_mcp" / ".venv" / "Scripts" / "python.exe",
                 ROOT / "labcontrol_mcp" / ".venv" / "bin" / "python"):
        if path.is_file():
            return path
    return None


def run_probe(app, port: int, steps: list[dict]) -> dict | None:
    from check_network_share import MCP_PROBE
    box: dict = {}

    def run():
        env = {**os.environ, "LABCONTROL_URL": f"http://127.0.0.1:{port}", "LABCONTROL_TOKEN": TOKEN}
        box["out"] = subprocess.run([str(mcp_python()), "-c", MCP_PROBE, str(ROOT / "labcontrol_mcp" / "server.py"),
                                     json.dumps(steps)], capture_output=True, text=True, env=env, timeout=180)
    threading.Thread(target=run, daemon=True).start()
    spin(app, 180.0, lambda: "out" in box)
    done = box.get("out")
    line = next((l for l in (done.stdout if done else "").splitlines() if l.startswith("RESULT ")), None)
    if line is None:
        check("MCP-Probe lief", False, ((done.stdout if done else "")[-500:] + (done.stderr if done else "")[-800:]))
        return None
    return json.loads(line[len("RESULT "):])


def section_mcp(app) -> None:
    print("mcp -- MCP-Server gegen das Hauptfenster")
    if mcp_python() is None:
        print("  (labcontrol_mcp/.venv fehlt -- Abschnitt uebersprungen)")
        FAILURES.append("mcp: .venv fehlt")
        return
    window, port = open_window(app)
    save_cases()

    def run_entry(run_id):
        return window._live_state.runs.get(run_id, 0) or {}

    status, body = ask(app, "POST", port, "/runs", {"testcase": "freigabe.json"})
    run_id = body.get("run", "")
    spin(app, 5.0, lambda: (run_entry(run_id).get("waiting") or {}).get("kind") == "release")
    out = run_probe(app, port, [
        {"name": "get", "tool": "get_run", "args": {"run_id": run_id}},
        {"name": "wrong", "tool": "continue_run", "args": {"run_id": run_id, "key": "anders"}},
        {"name": "ok", "tool": "continue_run", "args": {"run_id": run_id, "key": "flash_ok"}},
    ])
    if out is None:
        return
    check("MCP: continue_run angeboten", "continue_run" in out["tools"], str(out["tools"]))
    check("MCP: Hinweise nennen continue_run und kind='release'", "continue_run" in out["instructions"]
          and "kind='release'" in out["instructions"])
    data = lambda name: out[name]["data"] or {}  # noqa: E731
    check("MCP get_run: waiting kind=release mit Kennung", (data("get").get("waiting") or {}).get("kind") == "release"
          and (data("get").get("waiting") or {}).get("key") == "flash_ok", str(out["get"])[:300])
    check("MCP continue_run mit falscher Kennung -> release_mismatch", out["wrong"]["error"]
          and "release_mismatch" in out["wrong"]["text"], str(out["wrong"])[:300])
    check("MCP continue_run -> ok", not out["ok"]["error"] and data("ok").get("ok") is True
          and data("ok").get("step") == 2, str(out["ok"])[:300])
    spin(app, 15.0, lambda: run_entry(run_id).get("running") is False)
    entry = run_entry(run_id)
    released = [e for e in entry.get("events", []) if e.get("kind") == "released"]
    check("MCP: Lauf bestanden, Freigabe als 'MCP' vermerkt", entry.get("status") == "passed" and released
          and "(MCP)" in released[0]["by"], str(entry)[:300])
    spin(app, 3.0)

    status, body = ask(app, "POST", port, "/runs", {"testcase": "bestaetigen.json"})
    run_id = body.get("run", "")
    spin(app, 5.0, lambda: window.testcase_tab.confirm_dialog() is not None)
    out = run_probe(app, port, [{"name": "confirm", "tool": "continue_run", "args": {"run_id": run_id}}])
    if out is not None:
        check("MCP continue_run auf Bestaetigung -> confirm_local_only", out["confirm"]["error"]
              and "confirm_local_only" in out["confirm"]["text"], str(out["confirm"])[:300])
    check("... Lauf wartet weiter auf Bestaetigung im Fenster", window.testcase_tab.confirm_dialog() is not None
          and (run_entry(run_id).get("waiting") or {}).get("kind") == "confirm")
    ask(app, "POST", port, f"/runs/{run_id}/stop", {})
    spin(app, 5.0, lambda: run_entry(run_id).get("running") is False)
    spin(app, 2.0)


# -- cli -------------------------------------------------------------------------------------------------

def child_main() -> int:
    import no_hardware
    no_hardware.block()
    import headless
    return headless.main(["labcontrol", *sys.argv[2:]])


def run_cli(*args: str, timeout: float = 120.0) -> tuple[int, str]:
    data = Path(tempfile.mkdtemp(prefix="lc-release-cli-"))
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    env.pop("LABCONTROL_NETWORK_DIR", None)
    proc = subprocess.run([sys.executable, __file__, "--child", *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def section_cli() -> None:
    print("cli -- labcontrol run ohne Fenster")
    import device_worker as dw
    import testcase_model as tm

    folder = Path(tempfile.mkdtemp(prefix="lc-release-cases-"))
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    case = folder / "freigabe.json"
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=3.0),
                   tm.TestStep(step_type="await_release", params={"key": "k"}, duration=5.0),
                   tm.TestStep(**psu, action="PSU_OUT_ON")], case)
    code, text = run_cli("run", str(case), "--simulation")
    check("Warten auf Freigabe ohne Fenster -> Exit 3 mit Meldung", code == 3 and "Freigabe" in text
          and "Schritt 2" in text, f"{code}: {text[-300:]}")
    check("... und nicht gestartet", "Start:" not in text, text[-300:])


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main()
    wanted = set(sys.argv[1:]) or {"format", "runner", "registry", "editor", "window", "mcp", "cli"}
    if "format" in wanted:
        section_format()
    if "registry" in wanted:
        section_registry()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    if "runner" in wanted:
        section_runner(app)
    if "editor" in wanted:
        section_editor(app)
    if "window" in wanted:
        section_window(app)
    if "mcp" in wanted:
        section_mcp(app)
    if "window" in _WINDOW or "window" in wanted or "mcp" in wanted:
        window = _WINDOW.get("window")
        if window is not None:
            window.close()
            app.processEvents()
    if "cli" in wanted:
        section_cli()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

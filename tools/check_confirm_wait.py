"""Prueft PI-71 a (1.0.1): Schrittarten "Bestätigung" und "Warten seit Aktion".

- format  -- speichern/laden: Format 4 nur mit den neuen Schritten (aeltere Versionen lehnen ab statt die Bestaetigung
             still auszulassen), Text/Geraet/Aktion/Mindestzeit zurueck, auch Partnerkennung "@host/id"; Bausteine;
             Dateien ohne die neuen Schritte bleiben Format 2/3
- runner  -- TestRunner allein: Warten seit ohne vorherige Aktion (volle Zeit), mit Aktion (nur der Rest), Frist schon
             um, Partner- und "automatisch"-Kennung, Bestaetigen, Abbrechen (= Stopp), ohne Fenster -> Abbruch
- editor  -- Einfuegen-Menue, Zeilen bearbeiten, steps()/Speichern/Laden, Ausfuehren-Ansicht, Baustein-Vorschau
- window  -- echtes Hauptfenster mit Freigabe: Lauf per HTTP, "waiting" in /runs/{id} und /status, kein Bestaetigen
             ueber das Netz, Rueckfrage im Fenster (auch Touch-Profil), Weiter, Abbrechen (Abschaltung der Ablauf-Geraete,
             seit PI-79 a), Stopp per HTTP
             schliesst die Rueckfrage, Bericht mit Vermerken
- cli     -- `labcontrol run` ohne Fenster: Bestaetigung -> Exit 3 mit Meldung, "Warten seit" laeuft

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_confirm_wait.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-confirm-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN = "confirm-check-token"


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

    folder = Path(tempfile.mkdtemp(prefix="lc-confirm-format-"))
    steps = [
        tm.TestStep(device_kind="psu", device_id="@pi/psu:0001", action="PSU_OUT_ON"),
        tm.TestStep(step_type="confirm", params={"text": "Board einschalten? J-Link steckt"}),
        tm.TestStep(step_type="wait_since", device_kind="psu", device_id="@pi/psu:0001", action="PSU_OUT_ON",
                    duration=65.0),
        tm.TestStep(step_type="wait_since", device_kind="jlink", device_id="", action="JLINK_RESET", duration=10.0,
                    enabled=False),
    ]
    path = folder / "neu.json"
    tm.save_steps(steps, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    check("mit Bestaetigung/Warten seit: Format 4", data["version"] == 4, str(data["version"]))
    loaded, _meta = tm.load_testcase(path)
    check("Bestaetigungstext zurueck", tm.confirm_text(loaded[1]) == "Board einschalten? J-Link steckt")
    check("Warten seit: Geraet/Aktion/Zeit zurueck (Partner)",
          (loaded[2].step_type, loaded[2].device_kind, loaded[2].device_id, loaded[2].action, loaded[2].duration)
          == ("wait_since", "psu", "@pi/psu:0001", "PSU_OUT_ON", 65.0))
    check("deaktiviert und 'automatisch' zurueck", not loaded[3].enabled and loaded[3].device_id == ""
          and loaded[3].action == "JLINK_RESET")
    tm.save_steps(loaded, folder / "neu2.json")
    check("Laden+Speichern verlustfrei", json.loads((folder / "neu2.json").read_text(encoding="utf-8")) == data)
    plain = folder / "alt.json"
    tm.save_steps([tm.TestStep(step_type="wait", duration=1.0)], plain)
    check("ohne neue Schritte weiter Format 2", json.loads(plain.read_text(encoding="utf-8"))["version"] == 2)
    tm.save_steps([tm.TestStep(device_kind="psu", action="PSU_VOLT", value=3.0, requirements=["SSR-1"])],
                  folder / "v3.json")
    check("v3-Inhalte ohne neue Schritte weiter Format 3",
          json.loads((folder / "v3.json").read_text(encoding="utf-8"))["version"] == 3)
    # Eine aeltere Programmversion kannte Format 3 als hoechstes: sie lehnt die Datei mit Meldung ab
    old_max = tm.FILE_FORMAT_VERSION
    try:
        tm.FILE_FORMAT_VERSION = 3
        try:
            tm.load_testcase(path)
            refused = False
        except ValueError as exc:
            refused = "neueren Programmversion" in str(exc)
    finally:
        tm.FILE_FORMAT_VERSION = old_max
    check("Version mit Format 3 lehnt die Datei ab (statt still zu ueberspringen)", refused)
    block = folder / "baustein.json"
    tm.save_block(steps[1:3], "Einschalten", block)
    block_data = json.loads(block.read_text(encoding="utf-8"))
    name, block_steps = tm.load_block(block)
    check("Baustein: Format 3, Schritte zurueck", block_data["version"] == 3 and name == "Einschalten"
          and [s.step_type for s in block_steps] == ["confirm", "wait_since"]
          and tm.confirm_text(block_steps[0]).startswith("Board"), str(block_data["version"]))
    tm.save_block([tm.TestStep(step_type="wait", duration=1.0)], "alt", folder / "b_alt.json")
    check("Baustein ohne neue Schritte weiter Format 1",
          json.loads((folder / "b_alt.json").read_text(encoding="utf-8"))["version"] == 1)
    check("Kurztext Warten seit", tm.wait_since_summary(steps[2], "Pi-Netzteil") ==
          "≥ 65 s seit Ausgang EIN (Pi-Netzteil)", tm.wait_since_summary(steps[2], "Pi-Netzteil"))
    check("Warten seit bietet Schalt-, keine Leseaktionen", "PSU_OUT_OFF" in tm.wait_since_actions("psu")
          and "PSU_ARB" not in tm.wait_since_actions("psu") and "BS_CELL_READ" not in tm.wait_since_actions("battsim"))


# -- runner ----------------------------------------------------------------------------------------------

class FakeDevices:
    """Quittiert jede Aktion sofort (wie der Worker) und merkt sich, wann."""

    def __init__(self, runner) -> None:
        from PySide6.QtCore import QTimer
        self.done: list[tuple[str, str, float]] = []

        def execute(device_id, kind, action, value, channel):
            def ack():
                self.done.append((device_id, action, time.monotonic()))
                runner.on_action_completed(True, "", 0.0)
            QTimer.singleShot(0, ack)
        runner.execute_action.connect(execute)


def run_steps(app, runner, steps, timeout=6.0, on_confirm=None):
    events: list = []
    connections = [
        (runner.step_started, lambda i, _s: events.append(("started", i, time.monotonic()))),
        (runner.run_finished, lambda: events.append(("finished", time.monotonic()))),
        (runner.run_stopped, lambda: events.append(("stopped", time.monotonic()))),
        (runner.step_failed, lambda i, m: events.append(("failed", i, m))),
        (runner.step_note, lambda i, m: events.append(("note", i, m))),
        (runner.confirm_requested, lambda i, t: events.append(("confirm", i, t))),
        (runner.waiting_changed, lambda info: events.append(("waiting", info))),
    ]
    for signal, slot in connections:
        signal.connect(slot)
    if on_confirm is not None:
        runner.confirm_requested.connect(on_confirm)
    runner.start(steps)
    spin(app, timeout, lambda: not runner.is_running())
    for signal, slot in connections:
        signal.disconnect(slot)
    if on_confirm is not None:
        runner.confirm_requested.disconnect(on_confirm)
    return events


def started_at(events, index):
    return next((e[2] for e in events if e[0] == "started" and e[1] == index), None)


def ended_at(events):
    return next((e[1] for e in events if e[0] in ("finished", "stopped")), None)


def section_runner(app) -> None:
    print("runner -- TestRunner allein")
    from PySide6.QtCore import QTimer
    from testcase_model import TestStep
    from testcase_runner import TestRunner

    runner = TestRunner()
    devices = FakeDevices(runner)
    psu = dict(device_kind="psu", device_id="psu:A")

    events = run_steps(app, runner, [TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.6)])
    took = ended_at(events) - started_at(events, 0)
    notes = [e[2] for e in events if e[0] == "note"]
    check("ohne vorherige Aktion: volle Zeit", ("finished",) == tuple(e[0] for e in events if e[0] == "finished")
          and took >= 0.6, f"{took:.3f}")
    check("Vermerk 'noch nicht ausgefuehrt'", notes and "noch nicht" in notes[0], str(notes))
    waits = [e[1] for e in events if e[0] == "waiting"]
    check("Wartezustand gesetzt und wieder geloescht", waits and waits[0]["kind"] == "wait_since"
          and waits[0]["remaining_s"] > 0 and waits[-1] is None, str(waits))

    devices.done.clear()
    events = run_steps(app, runner, [TestStep(**psu, action="PSU_OUT_ON"),
                                     TestStep(step_type="wait", duration=0.4),
                                     TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.9),
                                     TestStep(**psu, action="PSU_OUT_OFF")])
    switched = devices.done[0][2]
    off = devices.done[-1][2] if len(devices.done) > 1 else 0.0
    own = ended_at(events) and (started_at(events, 3) - started_at(events, 2))
    check("mit Aktion: mindestens 0,9 s seit Ausgang EIN", off - switched >= 0.9, f"{off - switched:.3f}")
    check("mit Aktion: nur der Rest gewartet (< 0,75 s)", own is not None and own < 0.75, f"{own}")

    devices.done.clear()
    events = run_steps(app, runner, [TestStep(**psu, action="PSU_OUT_ON"),
                                     TestStep(step_type="wait", duration=0.5),
                                     TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.2)])
    gap = ended_at(events) - started_at(events, 2)
    check("Frist schon um: sofort weiter", gap < 0.15, f"{gap:.3f}")

    partner = dict(device_kind="psu", device_id="@pi/psu:0001")
    events = run_steps(app, runner, [TestStep(**partner, action="PSU_OUT_OFF"),
                                     TestStep(step_type="wait", duration=0.3),
                                     TestStep(step_type="wait_since", **partner, action="PSU_OUT_OFF",
                                              duration=0.6)])
    gap = ended_at(events) - started_at(events, 2)
    check("Partnergeraet als Bezug (nur der Rest)", 0.2 < gap < 0.45, f"{gap:.3f}")
    events = run_steps(app, runner, [TestStep(**partner, action="PSU_OUT_OFF"),
                                     TestStep(step_type="wait", duration=0.3),
                                     TestStep(step_type="wait_since", device_kind="psu", device_id="@pi/psu:0002",
                                              action="PSU_OUT_OFF", duration=0.5)])
    gap = ended_at(events) - started_at(events, 2)
    check("anderes Partnergeraet zaehlt nicht (volle Zeit)", gap >= 0.5, f"{gap:.3f}")
    events = run_steps(app, runner, [TestStep(device_kind="psu", device_id="", action="PSU_OUT_ON"),
                                     TestStep(step_type="wait", duration=0.3),
                                     TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.6)])
    gap = ended_at(events) - started_at(events, 2)
    check("'automatisch' zaehlt fuer jedes Geraet der Art (vorsichtig)", 0.2 < gap < 0.45, f"{gap:.3f}")
    events = run_steps(app, runner, [TestStep(**psu, action="PSU_OUT_ON"),
                                     TestStep(step_type="wait", duration=0.3),
                                     TestStep(step_type="wait_since", **psu, action="PSU_OUT_OFF", duration=0.5)])
    gap = ended_at(events) - started_at(events, 2)
    check("andere Aktion zaehlt nicht (volle Zeit)", gap >= 0.5, f"{gap:.3f}")

    # Anhalten mitten in "Warten seit": die Frist bleibt eine Mindestzeit
    runner.start([TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=0.6)])
    t0 = time.monotonic()
    spin(app, 0.2)
    runner.pause()
    paused = runner.is_paused()
    spin(app, 0.3)
    runner.resume()
    spin(app, 2.0, lambda: not runner.is_running())
    check("Anhalten/Weiter in 'Warten seit'", paused and not runner.is_running() and time.monotonic() - t0 >= 0.6)

    # Bestaetigung
    runner.confirm_available = True
    events = run_steps(app, runner, [TestStep(**psu, action="PSU_VOLT", value=3.0),
                                     TestStep(step_type="confirm", params={"text": "Board an?"}),
                                     TestStep(**psu, action="PSU_OUT_ON")],
                       on_confirm=lambda _i, _t: QTimer.singleShot(300, lambda: runner.confirm(True)))
    confirms = [e for e in events if e[0] == "confirm"]
    check("Bestaetigung angefragt mit Text", confirms == [("confirm", 1, "Board an?")], str(confirms))
    waits = [e[1] for e in events if e[0] == "waiting"]
    check("Wartezustand 'confirm' mit Text", waits and waits[0]["kind"] == "confirm"
          and waits[0]["text"] == "Board an?" and "Board an?" in waits[0]["message"] and waits[-1] is None,
          str(waits))
    t_confirm = started_at(events, 1)
    t_next = started_at(events, 2)
    check("Weiter: Lauf geht erst nach der Antwort weiter", t_next is not None and t_next - t_confirm >= 0.25
          and any(e[0] == "finished" for e in events), str([e[0] for e in events]))
    check("Vermerk 'bestätigt'", ("note", 1, "bestätigt") in events)

    devices.done.clear()
    events = run_steps(app, runner, [TestStep(step_type="confirm", params={"text": "Flashen?"}),
                                     TestStep(**psu, action="PSU_OUT_ON")],
                       on_confirm=lambda _i, _t: QTimer.singleShot(50, lambda: runner.confirm(False)))
    check("Abbrechen: Lauf gestoppt, nichts geschaltet", any(e[0] == "stopped" for e in events)
          and not any(e[0] == "finished" for e in events) and not devices.done, str([e[0] for e in events]))
    check("Vermerk 'abgebrochen'", ("note", 0, "abgebrochen") in events)
    runner.start([TestStep(step_type="confirm", params={"text": "x"}), TestStep(**psu, action="PSU_OUT_ON")])
    spin(app, 0.1)
    runner.confirm(True)
    runner.confirm(True)   # doppelte Antwort darf nichts ueberspringen
    spin(app, 0.5, lambda: not runner.is_running())
    check("doppelte Antwort harmlos", not runner.is_running())

    runner.confirm_available = False
    devices.done.clear()
    events = run_steps(app, runner, [TestStep(step_type="confirm", params={"text": "Ein?"}),
                                     TestStep(**psu, action="PSU_OUT_ON")])
    failed = [e for e in events if e[0] == "failed"]
    check("ohne Fenster: Abbruch mit Meldung, nichts geschaltet", failed and "Fenster" in failed[0][2]
          and not devices.done, str(failed))


# -- editor ----------------------------------------------------------------------------------------------

def section_editor(app) -> None:
    print("editor -- Einfuegen, Bearbeiten, Speichern/Laden")
    import testcase_model as tm
    from block_dialog import _step_line
    from testcase_tab import COL_ACTION, COL_DEVICE, COL_DURATION, COL_VALUE, TestcaseTab

    tab = TestcaseTab()
    tab.on_device_known("psu", "psu:A", "NT 16V-30A")
    tab.on_device_known("psu", "@pi/psu:0001", "Pi · NT 60V")
    tab._action_add_confirm.trigger()
    row = tab._table.rowCount() - 1
    check("Menue 'Bestätigung' fuegt eine Zeile ein", tab.steps()[row].step_type == "confirm")
    check("Zeile zeigt 'Bestätigung'", tab._table.cellWidget(row, COL_DEVICE).text() == "Bestätigung")
    tab._table.cellWidget(row, COL_ACTION).setText("Evalboard einschalten?")
    tab._action_add_wait_since.trigger()
    row2 = tab._table.rowCount() - 1
    step = tab.steps()[row2]
    check("Menue 'Warten seit Aktion': Vorgabe Netzteil EIN, 10 s",
          (step.step_type, step.device_kind, step.action, step.duration) == ("wait_since", "psu", "PSU_OUT_ON", 10.0),
          str(step))
    device_combo = tab._table.cellWidget(row2, COL_ACTION)
    action_combo = tab._table.cellWidget(row2, COL_VALUE)
    keys = [device_combo.itemData(i) for i in range(device_combo.count())]
    partner_index = next((i for i, k in enumerate(keys) if "@pi/psu:0001" in str(k)), -1)
    check("Partnergeraet in der Auswahl", partner_index >= 0, str(keys))
    device_combo.setCurrentIndex(partner_index)
    action_combo.setCurrentIndex(action_combo.findData("PSU_OUT_OFF"))
    tab._table.cellWidget(row2, COL_DURATION).setValue(12.5)
    steps = tab.steps()
    check("Bearbeiten: Text", tm.confirm_text(steps[row]) == "Evalboard einschalten?")
    check("Bearbeiten: Geraet/Aktion/Zeit", (steps[row2].device_id, steps[row2].action, steps[row2].duration)
          == ("@pi/psu:0001", "PSU_OUT_OFF", 12.5), str(steps[row2]))
    check("Ausfuehren-Ansicht beschreibt beide", tab._describe_step(steps[row]) == "Bestätigung: Evalboard einschalten?"
          and tab._describe_step(steps[row2]) == "Warten ≥ 12.5 s seit Ausgang AUS (Pi · NT 60V)",
          f"{tab._describe_step(steps[row])} | {tab._describe_step(steps[row2])}")
    check("Baustein-Vorschau", _step_line(steps[row]) == "Bestätigung: Evalboard einschalten?"
          and _step_line(steps[row2]).startswith("Warten ≥ 12.5 s seit Ausgang AUS"), _step_line(steps[row2]))
    path = DATA / "editor.json"
    tm.save_steps(steps, path)
    tab2 = TestcaseTab()
    tab2.on_device_known("psu", "@pi/psu:0001", "Pi · NT 60V")
    check("Laden", tab2._load_path(path))
    again = tab2.steps()
    check("Laden: gleiche Schritte", [(s.step_type, s.device_id, s.action, s.duration, tm.confirm_text(s))
                                      for s in again]
          == [(s.step_type, s.device_id, s.action, s.duration, tm.confirm_text(s)) for s in steps])
    check("Laden: Aktion in der Auswahl", tab2._table.cellWidget(row2, COL_VALUE).currentData() == "PSU_OUT_OFF")
    check("nach dem Laden unveraendert", not tab2.is_modified())
    tab2._table.selectRow(row2)
    tab2._resync_selection()
    tab2._move_selected_row(-1)
    moved = tab2.steps()
    check("Verschieben behaelt die Felder", moved[row2 - 1].step_type == "wait_since"
          and moved[row2 - 1].device_id == "@pi/psu:0001" and moved[row2 - 1].action == "PSU_OUT_OFF")
    from i18n import Translator
    Translator.instance().set_language("en")
    app.processEvents()
    english = tab2._table.cellWidget(row, COL_DEVICE).text()
    Translator.instance().set_language("de")
    app.processEvents()
    check("englischer Zeilentext", english in ("Confirmation", "Wait since"), english)
    check("nach Sprachwechsel Aktion erhalten", tab2.steps()[row2 - 1].action == "PSU_OUT_OFF")
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


def closed(dialog) -> bool:
    """Rueckfrage zu (unsichtbar oder schon geloescht)."""
    import shiboken6
    return dialog is not None and (not shiboken6.isValid(dialog) or not dialog.isVisible())


def ask(app, *args, **kwargs):
    import threading
    box: dict = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", http(*args, **kwargs)), daemon=True)
    thread.start()
    spin(app, 25.0, lambda: "r" in box)
    return box.get("r", (0, {}))


def section_window(app) -> None:
    print("window -- Hauptfenster, REST, Rueckfrage")
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
    spin(app, 5.0, lambda: dw.SIM_PSU_ID in window._online_devices.get("psu", set()))
    reasons: list[str] = []
    window._request_all_off.connect(reasons.append)
    folder = data_store.target_dir("testcases")
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    tm.save_steps([
        tm.TestStep(**psu, action="PSU_VOLT", value=3.0),
        tm.TestStep(step_type="confirm", params={"text": "Evalboard einschalten?"}),
        tm.TestStep(**psu, action="PSU_OUT_ON"),
        tm.TestStep(step_type="wait_since", **psu, action="PSU_OUT_ON", duration=1.5),
        tm.TestStep(**psu, action="PSU_OUT_OFF"),
    ], folder / "bestaetigen.json")

    def run_entry(run_id):
        return window._live_state.runs.get(run_id) or {}

    status, body = ask(app, "POST", port, "/runs", {"testcase": "bestaetigen.json"})
    run_id = body.get("run", "")
    check("Start per HTTP", status == 202 and run_id, f"{status} {body}")
    spin(app, 5.0, lambda: window.testcase_tab.confirm_dialog() is not None)
    dialog = window.testcase_tab.confirm_dialog()
    check("Rueckfrage im Fenster offen, nicht modal", dialog is not None and dialog.isVisible()
          and not dialog.isModal() and "Evalboard einschalten?" in dialog.text_label.text())
    status, entry = ask(app, "GET", port, f"/runs/{run_id}?events=0")
    waiting = entry.get("waiting") or {}
    check("GET /runs/{id}: wartet auf Bestaetigung mit Text", waiting.get("kind") == "confirm"
          and waiting.get("text") == "Evalboard einschalten?" and waiting.get("step") == 2
          and "wartet auf Bestätigung" in waiting.get("message", ""), str(entry)[:300])
    check("Meldung 'waiting' in den events", any(e.get("kind") == "waiting" and e.get("wait") == "confirm"
                                                 for e in entry.get("events", [])), str(entry.get("events")))
    status, info = ask(app, "GET", port, "/status")
    check("GET /status: run_waiting", (info.get("run_waiting") or {}).get("kind") == "confirm", str(info)[:200])
    status, body = ask(app, "POST", port, f"/runs/{run_id}/confirm", {})
    spin(app, 0.5)
    check("kein Bestaetigen ueber das Netz", status in (404, 405) and window.testcase_tab.confirm_dialog() is dialog
          and (run_entry(run_id).get("waiting") or {}).get("kind") == "confirm", f"{status} {body}")
    check("noch nichts eingeschaltet", run_entry(run_id).get("step") == 2)
    dialog.continue_button.click()
    spin(app, 5.0, lambda: (run_entry(run_id).get("waiting") or {}).get("kind") == "wait_since")
    waiting = run_entry(run_id).get("waiting") or {}
    check("nach Weiter: wartet seit Ausgang EIN mit Rest", waiting.get("kind") == "wait_since"
          and 0 < waiting.get("remaining_s", 0) <= 1.5, str(waiting))
    check("Rueckfrage geschlossen", window.testcase_tab.confirm_dialog() is None and closed(dialog))
    spin(app, 15.0, lambda: run_entry(run_id).get("running") is False)
    entry = run_entry(run_id)
    check("Lauf bestanden, nichts wartet mehr", entry.get("status") == "passed" and entry.get("waiting") is None,
          str(entry)[:300])
    record = window._run_recorder.record()
    notes = [e.message for e in record.events if e.kind == "note"]
    check("Laufaufzeichnung: Vermerke", "bestätigt" in notes and any("wartet" in n for n in notes), str(notes))
    status, report = ask(app, "GET", port, f"/runs/{run_id}/report")
    html = report.get("_raw", "")
    check("Bericht: Bestaetigung und Warten seit", "Bestätigung – Evalboard einschalten?" in html
          and "Warten ≥ 1.5 s seit Ausgang EIN" in html and "bestätigt" in html, html[-400:])
    spin(app, 3.0)

    # Abbrechen in der Rueckfrage = Stopp (Abschaltung der Ablauf-Geraete, PI-79 a), im Touch-Profil
    import touch_ui
    touch_ui.sizer().set_active(True)
    count = len(reasons)
    status, body = ask(app, "POST", port, "/runs", {"testcase": "bestaetigen.json"})
    run_id = body.get("run", "")
    spin(app, 5.0, lambda: window.testcase_tab.confirm_dialog() is not None)
    dialog = window.testcase_tab.confirm_dialog()
    spin(app, 0.3)
    check("Touch-Profil: Knoepfe in Touch-Hoehe", dialog is not None
          and dialog.continue_button.height() == touch_ui.TOUCH_H, str(dialog and dialog.continue_button.height()))
    if dialog is not None:
        dialog.cancel_button.click()
    spin(app, 5.0, lambda: run_entry(run_id).get("running") is False)
    entry = run_entry(run_id)
    check("Abbrechen: gestoppt, Exit 2", entry.get("status") == "stopped" and entry.get("exit_code") == 2,
          str(entry)[:200])
    # seit PI-79 a: kein ALLE AUS mehr, sondern die Geraete, die der Ablauf schaltet (wie der Stopp)
    shutdown = entry.get("shutdown") or {}
    check("Abbrechen: wie Stopp -- Standard-Abschaltung des Ablauf-Netzteils, kein ALLE AUS",
          shutdown.get("mode") == "standard" and shutdown.get("cause") == "stopped"
          and "psu:SIM" in shutdown.get("off", []) and not reasons[count:], f"{shutdown} {reasons[count:]}")
    touch_ui.sizer().set_active(False)
    spin(app, 3.0)

    # Stopp ueber das Netz schliesst die Rueckfrage
    status, body = ask(app, "POST", port, "/runs", {"testcase": "bestaetigen.json"})
    run_id = body.get("run", "")
    spin(app, 5.0, lambda: window.testcase_tab.confirm_dialog() is not None)
    dialog = window.testcase_tab.confirm_dialog()
    status, body = ask(app, "POST", port, f"/runs/{run_id}/stop", {})
    spin(app, 5.0, lambda: run_entry(run_id).get("running") is False)
    check("Stopp per HTTP schliesst die Rueckfrage", status == 200 and window.testcase_tab.confirm_dialog() is None
          and closed(dialog) and run_entry(run_id).get("status") == "stopped")
    spin(app, 3.0)
    window.close()
    app.processEvents()


# -- cli -------------------------------------------------------------------------------------------------

def child_main() -> int:
    import no_hardware
    no_hardware.block()
    import headless
    return headless.main(["labcontrol", *sys.argv[2:]])


def run_cli(*args: str, timeout: float = 120.0) -> tuple[int, str]:
    data = Path(tempfile.mkdtemp(prefix="lc-confirm-cli-"))
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

    folder = Path(tempfile.mkdtemp(prefix="lc-confirm-cases-"))
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    confirm_case = folder / "bestaetigen.json"
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=3.0),
                   tm.TestStep(step_type="confirm", params={"text": "Ein?"}),
                   tm.TestStep(**psu, action="PSU_OUT_ON")], confirm_case)
    code, text = run_cli("run", str(confirm_case), "--simulation")
    check("Bestaetigung ohne Fenster -> Exit 3 mit Meldung", code == 3 and "Fenster" in text
          and "Schritt 2" in text, f"{code}: {text[-300:]}")
    check("... und nicht gestartet", "Start:" not in text, text[-300:])
    since_case = folder / "warten.json"
    tm.save_steps([tm.TestStep(**psu, action="PSU_OUT_OFF"),
                   tm.TestStep(step_type="wait_since", **psu, action="PSU_OUT_OFF", duration=1.0),
                   tm.TestStep(**psu, action="PSU_VOLT", value=2.0)], since_case)
    out = folder / "warten_result.json"
    code, text = run_cli("run", str(since_case), "--simulation", "--wait-devices", "20", "--json", str(out))
    check("Warten seit ohne Fenster -> Exit 0", code == 0, f"{code}: {text[-300:]}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main()
    section_format()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    section_runner(app)
    section_editor(app)
    section_window(app)
    section_cli()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

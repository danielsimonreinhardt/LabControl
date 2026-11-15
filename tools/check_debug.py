"""Prueft FT-12 Ablaeufe debuggen (PI-58 a, 0.69.0): Haltepunkte, Anhalten auch mitten in einer Wartezeit,
Einzelschritt, Weiter, Stopp waehrend der Pause, ab einer Zeile starten (nur oberste Ebene), Variablen/Messwerte
in der Pause, Editor-Knoepfe und: Haltepunkte gelten nur fuer Laeufe aus dem Fenster, nie fuer REST/CLI.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_debug.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-debug-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(app, seconds: float, until=None) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


def steps():
    from testcase_model import TestStep
    return [TestStep(step_type="set_var", var_name="a", value=1),
            TestStep(step_type="wait", duration=0.3),
            TestStep(step_type="inc_var", var_name="a", value=1),
            TestStep(step_type="wait", duration=0.3),
            TestStep(step_type="set_var", var_name="b", value=5)]


def section_runner(app) -> None:
    print("runner -- Haltepunkte, Anhalten, Einzelschritt, ab Zeile")
    from testcase_model import TestStep
    from testcase_runner import TestRunner

    runner = TestRunner()
    events: list = []
    runner.paused.connect(lambda i: events.append(("paused", i)))
    runner.resumed.connect(lambda: events.append(("resumed",)))
    runner.run_finished.connect(lambda: events.append(("finished",)))
    runner.run_stopped.connect(lambda: events.append(("stopped",)))
    runner.step_failed.connect(lambda i, m: events.append(("failed", i, m)))

    runner.set_breakpoints({2})
    runner.start(steps())
    spin(app, 2, lambda: events)
    check("Haltepunkt: angehalten vor Zeile 3", events == [("paused", 2)], str(events))
    check("Variablen in der Pause", runner.debug_snapshot()["vars"] == {"a": 1.0})
    spin(app, 0.5)
    check("bleibt angehalten", runner.is_paused() and runner.is_running() and len(events) == 1)
    runner.resume()
    spin(app, 2, lambda: ("finished",) in events)
    check("Weiter: Lauf endet, Haltepunkt nicht erneut", events[1:] == [("resumed",), ("finished",)], str(events))

    events.clear()
    runner.set_breakpoints(set())
    runner.start(steps())
    spin(app, 0.1)
    runner.pause()
    check("Anhalten mitten in der Wartezeit: sofort", events == [("paused", 1)], str(events))
    t0 = time.monotonic()
    spin(app, 0.6)
    check("Wartezeit steht", runner.is_paused() and ("finished",) not in events)
    runner.resume()
    spin(app, 2, lambda: ("finished",) in events)
    took = time.monotonic() - t0
    check("Rest der Wartezeit und Lauf zu Ende", ("finished",) in events and took > 0.9, f"{took:.2f}")

    events.clear()
    runner.set_breakpoints({0})
    runner.start(steps())
    spin(app, 1, lambda: events)
    for expect in (1, 2, 3):
        runner.resume(single_step=True)
        spin(app, 2, lambda e=expect: ("paused", e) in events)
    check("Einzelschritt: Zeile fuer Zeile", [e for e in events if e[0] == "paused"]
          == [("paused", 0), ("paused", 1), ("paused", 2), ("paused", 3)], str(events))
    check("Variable nach Einzelschritten", runner.debug_snapshot()["vars"] == {"a": 2.0})
    runner.stop()
    check("Stopp in der Pause", ("stopped",) in events and not runner.is_paused() and not runner.is_running())

    events.clear()
    runner.set_breakpoints(set())
    runner.start(steps(), start_index=4)
    spin(app, 1, lambda: ("finished",) in events)
    check("ab Zeile 5: nur dieser Schritt", ("finished",) in events and runner.debug_snapshot()["vars"] == {"b": 5.0},
          str(runner.debug_snapshot()["vars"]))
    events.clear()
    runner.start(steps(), start_index=2)
    spin(app, 1, lambda: events)
    check("ab Zeile 3 ohne gesetzte Variable -> Fehler mit Grund", events and events[0][0] == "failed"
          and "a" in events[0][2], str(events))
    looped = [TestStep(step_type="loop", loop_count=2), TestStep(step_type="wait", duration=0.1),
              TestStep(step_type="end"), TestStep(step_type="wait", duration=0.1)]
    check("ab hier im Block abgelehnt", "Block" in TestRunner.start_refusal(looped, 1))
    check("ab hier auf Ende abgelehnt", TestRunner.start_refusal(looped, 2) != "")
    check("ab hier auf oberster Ebene erlaubt", TestRunner.start_refusal(looped, 0) == ""
          and TestRunner.start_refusal(looped, 3) == "")
    events.clear()
    runner.set_breakpoints({1})
    runner.start(looped)
    spin(app, 1, lambda: events)
    runner.resume()
    spin(app, 1, lambda: len([e for e in events if e[0] == "paused"]) >= 2)
    check("Haltepunkt in einer Schleife haelt je Durchlauf", [e for e in events if e[0] == "paused"]
          == [("paused", 1), ("paused", 1)], str(events))
    runner.stop()


def section_ui(app) -> None:
    print("ui -- Editor und Fenster")
    import no_hardware
    no_hardware.block()
    import testcase_model as tm
    from main_window import MainWindow
    from settings import Settings
    from testcase_tab import TestcaseTab

    tab = TestcaseTab()
    path = DATA / "dbg.json"
    tm.save_steps(steps(), path)
    tab._load_path(path)
    tab._table.selectRow(2)
    app.processEvents()
    tab._toggle_breakpoint()
    check("Haltepunkt an markierter Zeile", tab.breakpoints() == {2})
    tab._toggle_breakpoint()
    check("... und wieder aus", tab.breakpoints() == set())
    tab.set_breakpoint(1, True)
    tab.set_running(True)
    check("waehrend des Laufs: Anhalten/Einzelschritt an, Haltepunkt/ab hier aus",
          tab._pause_button.isEnabled() and tab._step_button.isEnabled() and not tab._break_button.isEnabled()
          and not tab._run_from_button.isEnabled())
    requests: list = []
    tab.pause_requested.connect(lambda: requests.append("pause"))
    tab.resume_requested.connect(lambda single: requests.append(("resume", single)))
    tab._on_pause_clicked()
    tab.on_paused(1, {"vars": {"a": 1.0}, "values": {"psu:SIM U": (12.0, "V", 0.4)}})
    check("Pause: Variablen und Messwerte sichtbar", tab._debug_label.isVisibleTo(tab)
          and "a = 1" in tab._debug_label.text() and "psu:SIM U = 12 V" in tab._debug_label.text(),
          tab._debug_label.text())
    tab._on_pause_clicked()
    tab._step_button.click()
    check("Knoepfe: Anhalten, Weiter, Einzelschritt", requests == ["pause", ("resume", False), ("resume", True)],
          str(requests))
    tab.on_resumed()
    check("Weiter: Anzeige weg", not tab._debug_label.isVisibleTo(tab))
    tab.set_running(False)

    settings = Settings()
    window = MainWindow(settings)
    with_action = [tm.TestStep(device_kind="psu", device_id="psu:SIM", action="PSU_VOLT", value=5.0, duration=0.1)]         + steps()
    path = DATA / "dbg_action.json"
    tm.save_steps(with_action, path)
    window.testcase_tab._load_path(path)
    window.testcase_tab.set_breakpoint(2, True)
    runner = window._test_runner
    paused: list = []
    started: list = []
    runner.paused.connect(paused.append)
    runner.step_started.connect(lambda i, _s: started.append(i))
    spin(app, 6, lambda: window._directory.is_online("psu:SIM"))
    window._run_source = "rest-check"
    window._on_run_requested()
    spin(app, 3, lambda: started and not runner.is_running())
    check("Lauf ueber REST/CLI ignoriert Haltepunkte", started and not paused and not runner.is_running(),
          f"{started} {paused} {window._last_refusal}")
    started.clear()
    window._run_source = "gui"
    window._on_run_requested()
    spin(app, 3, lambda: paused)
    check("Lauf aus dem Fenster haelt am Haltepunkt", paused == [2] and window.testcase_tab._debug_label.isVisibleTo(
        window.testcase_tab), f"{paused} {started}")
    runner.resume()
    spin(app, 2, lambda: not runner.is_running())
    check("... und laeuft nach Weiter zu Ende", not runner.is_running())
    window.testcase_tab.set_breakpoint(1, False)
    window._on_run_from(5)
    spin(app, 1.5, lambda: not runner.is_running())
    check("Ab Zeile 5 aus dem Fenster", not runner.is_running() and window._run_start_index == 0)
    refused: list = []
    original = window._refuse_run
    window._refuse_run = lambda message: refused.append(message)
    looped = [tm.TestStep(step_type="loop", loop_count=2), tm.TestStep(step_type="wait", duration=0.1),
              tm.TestStep(step_type="end")]
    tm.save_steps(looped, DATA / "loop.json")
    window.testcase_tab._load_path(DATA / "loop.json")
    window._on_run_from(1)
    check("Ab hier im Block wird abgelehnt", refused and "Block" in refused[0], str(refused))
    window._refuse_run = original
    window._safe_stop("check")
    window.close()


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    section_runner(app)
    section_ui(app)
    print(f"\n{'ALLES OK' if not FAILURES else f'{len(FAILURES)} FEHLER: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)

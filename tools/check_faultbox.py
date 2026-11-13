"""Prueft FT-9 Fehlerbox (0.61.0): Schaltfolgen, faultbox.json, Dienst im Hauptfenster (Simulation mit microHIL):
Kachel, Setzen/Aufheben, kein Wechsel an der Kachel (FP-26 c), automatischer Wechsel im Ablauf, Sperre der Kanaele
in Kachel, Netzwerk und Ablaufschritten (FP-27 a), Aufheben am Laufende und durch ALLE AUS, Einstellungen.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_faultbox.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-faultbox-check-"))
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


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return bool(until()) if until is not None else False


def section_model() -> None:
    print("model -- Schaltfolgen und Datei")
    import faultbox as fb
    check("Kurzschrift", fb.parse_sequence("R1=1, o3=1; A1=1500") == [("R", 1, 1), ("O", 3, 1), ("A", 1, 1500)])
    for bad in ("R5=1", "R1=2", "X1=1", "A1=99999", "R1=1, R1=0", ""):
        try:
            fb.parse_sequence(bad)
            ok = False
        except fb.FaultError:
            ok = True
        check(f"abgelehnt: '{bad}'", ok)
    fault = fb.Fault("F", "R1=1, A1=1500, O3=1")
    check("Setzen in Reihenfolge", fault.set_actions() == [("HIL_RELAY_ON", 1, 0.0), ("HIL_AOUT", 1, 1500.0),
                                                           ("HIL_OUT_ON", 3, 0.0)])
    check("Aufheben umgekehrt in Ruhe", fault.clear_actions() == [("HIL_OUT_OFF", 3, 0.0), ("HIL_AOUT", 1, 0.0),
                                                                  ("HIL_RELAY_OFF", 1, 0.0)])
    check("PWM belegt OUT", fb.channel_of_action("HIL_PWM", 3) == ("O", 3))
    path = DATA / "model" / "faultbox.json"
    fb.save(path, [fault, fb.Fault("G", "R2=1", settle_ms=50, description="Masse offen")])
    loaded = fb.load(path)
    check("Datei hin und zurueck", [f.name for f in loaded] == ["F", "G"] and loaded[1].settle_ms == 50
          and loaded[1].description == "Masse offen")
    try:
        fb.save(path, [fault, fb.Fault("F", "R2=1")])
        ok = False
    except fb.FaultError:
        ok = True
    check("doppelter Name abgelehnt", ok)


def section_window() -> None:
    print("window -- Hauptfenster (Simulation mit microHIL)")
    import no_hardware
    no_hardware.block()
    import device_worker as dw
    import faultbox as fb
    import testcase_model as tm
    from PySide6.QtWidgets import QApplication
    from main_window import FAULT_DEVICE_ID, MainWindow
    from settings import Settings

    fb.save(DATA / "faultbox.json", [fb.Fault("KL30 offen", "R1=1, O3=1", settle_ms=50),
                                      fb.Fault("Masse offen", "R2=1, A1=1500", settle_ms=50)])
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    service = window._faultbox
    spin(app, 6.0, lambda: FAULT_DEVICE_ID in window._online_devices.get("fault", set())
         and dw.SIM_HIL_ID in window.control_tab._sections)
    tile = window.control_tab._sections.get(FAULT_DEVICE_ID)
    hil_tile = window.control_tab._sections.get(dw.SIM_HIL_ID)
    check("Kachel mit zwei Tasten, online", tile is not None and len(tile._buttons) == 2
          and FAULT_DEVICE_ID in window._online_devices.get("fault", set()))
    check("Uebersicht und Leiste", FAULT_DEVICE_ID in window.dashboard._panels and FAULT_DEVICE_ID in window._status_labels)

    tile._buttons["KL30 offen"].click()
    spin(app, 5.0, lambda: service.active() == "KL30 offen")
    check("Setzen ueber die Kachel", service.active() == "KL30 offen" and tile._buttons["KL30 offen"].isChecked())
    spin(app, 3.0, lambda: hil_tile._relay_buttons[0].isChecked())
    check("Relais 1 am microHIL eingeschaltet", hil_tile._relay_buttons[0].isChecked())
    check("Kanaele gesperrt (REL1, OUT3, PWM3)", not hil_tile._relay_buttons[0].isEnabled()
          and not hil_tile._out_buttons[2].isEnabled() and not hil_tile._pwm_rows[2].isEnabled())
    check("andere Kanaele frei", hil_tile._relay_buttons[1].isEnabled() and hil_tile._out_buttons[0].isEnabled())
    tile._buttons["Masse offen"].click()
    spin(app, 1.0)
    check("Wechsel an der Kachel abgelehnt (FP-26 c)", service.active() == "KL30 offen"
          and "aufheben" in service.state()["last"], service.state()["last"])

    answers = []
    window._share_bridge.complete = lambda rid, ok, code, msg, val: answers.append((ok, code, msg))
    window._on_share_action(7, dw.SIM_HIL_ID, "hil", "HIL_RELAY_OFF", 0.0, 1, True)
    check("Netzwerk-Aktion auf gesperrtem Kanal abgelehnt", answers and answers[-1][0] is False
          and "KL30 offen" in answers[-1][2], str(answers))

    def run(steps):
        path = DATA / f"lauf{len(answers)}{time.monotonic_ns()}.json"
        tm.save_steps(steps, path)
        window.testcase_tab._load_path(path)
        window._on_run_requested()
        spin(app, 15.0, lambda: not window._test_runner.is_running())
        spin(app, 2.0, lambda: not service.busy())
        return window._run_recorder.record()

    fault = lambda action, name="": tm.TestStep(device_kind="fault", action=action,
                                                params={"fault": name} if name else {})
    hil = lambda action, ch: tm.TestStep(device_kind="hil", device_id=dw.SIM_HIL_ID, action=action, hil_channel=ch)
    record = run([fault("FAULT_SET", "Masse offen"), tm.TestStep(step_type="wait", duration=0.2),
                  hil("HIL_RELAY_ON", 2)])
    check("Ablauf wechselt automatisch (FP-26 c), Schritt auf gesperrtem Kanal scheitert",
          record.outcome == "error" and "Masse offen" in record.error_message, record.error_message)
    window._on_safety_acknowledge()
    spin(app, 3.0, lambda: service.active() is None)
    check("nach dem Lauf kein Fehler mehr aktiv (Laufende/ALLE AUS)", service.active() is None and not service.state()["locked"])
    record = run([fault("FAULT_SET", "KL30 offen"), tm.TestStep(step_type="wait", duration=0.2),
                  fault("FAULT_CLEAR", "KL30 offen"), hil("HIL_RELAY_ON", 1), hil("HIL_RELAY_OFF", 1)])
    check("Setzen, Aufheben, danach Kanal frei", record.outcome == "passed", record.error_message)
    record = run([fault("FAULT_SET", "KL30 offen"), tm.TestStep(step_type="wait", duration=0.2)])
    spin(app, 3.0, lambda: service.active() is None)
    check("vom Ablauf gesetzter Fehler wird am Laufende aufgehoben", record.outcome == "passed"
          and service.active() is None)
    check("unbekannter Fehler -> Lauf startet nicht",
          "Unbekannter Fehler" in window._run_session.refusal([fault("FAULT_SET", "gibt es nicht")]))

    tile._buttons["KL30 offen"].click()
    spin(app, 5.0, lambda: service.active() == "KL30 offen")
    window._safe_stop("check")
    spin(app, 5.0, lambda: service.active() is None)
    check("ALLE AUS hebt den Fehler auf und gibt die Kanaele frei", service.active() is None
          and hil_tile._relay_buttons[0].isEnabled())

    editor = window.settings_tab.faultbox_editor
    check("Einstellungen zeigen die Fehler", editor.table.rowCount() == 2)
    editor._add_row()
    editor.table.item(2, 1).setText("R9=1")
    check("ungueltige Schaltfolge nicht gespeichert", not editor.apply() and "R9=1" in editor._status.text())
    editor.table.item(2, 1).setText("O5=1")
    check("gueltige gespeichert", editor.apply() and len(service.faults()) == 3 and len(tile._buttons) == 3)
    window.close()
    app.processEvents()


def section_cli() -> None:
    print("cli -- Fehlerbox ueber labcontrol run")
    import subprocess
    import device_worker as dw
    import faultbox as fb
    import testcase_model as tm
    data = DATA / "cli"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    fb.save(data / "faultbox.json", [fb.Fault("KL30 offen", "R1=1")])

    def run(name, steps):
        path = data / f"{name}.json"
        tm.save_steps(steps, path)
        env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "run",
                               str(path), "--simulation"], env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)
        return proc.returncode, proc.stdout + proc.stderr

    fault = tm.TestStep(device_kind="fault", action="FAULT_SET", params={"fault": "KL30 offen"})
    blocked = tm.TestStep(device_kind="hil", device_id=dw.SIM_HIL_ID, action="HIL_RELAY_OFF", hil_channel=1)
    code, text = run("gut", [fault, tm.TestStep(step_type="wait", duration=0.2),
                             tm.TestStep(device_kind="fault", action="FAULT_CLEAR_ALL")])
    check("Setzen und Aufheben -> Exit 0", code == 0, f"{code}: {text[-300:]}")
    code, text = run("gesperrt", [fault, blocked])
    check("gesperrter Kanal -> Exit 2", code == 2 and "KL30 offen" in text, f"{code}: {text[-300:]}")


def main() -> int:
    section_model()
    section_window()
    section_cli()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

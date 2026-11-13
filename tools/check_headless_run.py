"""Prueft 0.58.0 "Fundament": Ablaufformat v3, Verriegelung Netzteil/Last (FP-7 b) und `labcontrol run`
ohne Fenster mit Exit-Codes (FP-3 a), JUnit, run-result.json und Instanz-Sperre.

- format    -- v3 speichern/laden: params, requirements, unbekannte Felder, meta; ohne neue Inhalte v2
- editor    -- TestcaseTab verliert params/requirements/meta beim Laden -> steps()/Speichern nicht
- interlock -- run_session.Interlock allein
- cli       -- Kindprozesse `run` im Simulationsmodus: bestanden 0, Pruefung verletzt 1, Verriegelung 2,
               Quelle/Senke 0, Geraet fehlt 3, zweite Instanz 3, falsches --var 3, (Linux) SIGTERM 130

Offscreen, Simulation, keine Hardware (no_hardware.block() auch in jedem Kindprozess).
Aufruf:  python tools/check_headless_run.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def child_main() -> int:
    """Kindprozess: echte Geraete sperren, dann die Kommandozeile wie main.py."""
    import no_hardware
    no_hardware.block()
    import headless
    return headless.main(["labcontrol", *sys.argv[2:]])


def new_data_dir(groups: list | None = None) -> Path:
    data = Path(tempfile.mkdtemp(prefix="lc-headless-check-"))
    settings = {"simulation_mode": True}
    if groups:
        settings["interlock_groups"] = groups
    (data / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return data


def run_cli(data: Path, *args: str, timeout: float = 90.0) -> tuple[int, str]:
    env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    env.pop("LABCONTROL_NETWORK_DIR", None)
    proc = subprocess.run([sys.executable, __file__, "--child", *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def section_format() -> None:
    print("format -- Ablaufformat v3")
    import testcase_model as tm

    folder = Path(tempfile.mkdtemp(prefix="lc-format-"))
    plain = [tm.TestStep(step_type="wait", duration=0.1)]
    tm.save_steps(plain, folder / "v2.json")
    data = json.loads((folder / "v2.json").read_text(encoding="utf-8"))
    check("ohne neue Inhalte weiter Format 2", data["version"] == 2 and "params" not in data["steps"][0]
          and "meta" not in data, str(data)[:120])
    rich = [tm.TestStep(device_kind="psu", action="PSU_VOLT", value=5.0, params={"profile": "x"},
                        requirements=["SSR-1"], extra={"zukunft": 7})]
    meta = {"description": "Test", "requirements": ["SYS-2"], "source_sink": True, "fremd": "bleibt"}
    tm.save_steps(rich, folder / "v3.json", meta=meta)
    data = json.loads((folder / "v3.json").read_text(encoding="utf-8"))
    check("mit params/requirements Format 3", data["version"] == 3)
    check("unbekanntes Feld flach in der Datei", data["steps"][0].get("zukunft") == 7 and "extra" not in data["steps"][0])
    steps, loaded_meta = tm.load_testcase(folder / "v3.json")
    step = steps[0]
    check("params/requirements zurueck", step.params == {"profile": "x"} and step.requirements == ["SSR-1"])
    check("unbekanntes Feld in extra", step.extra == {"zukunft": 7})
    check("meta zurueck (auch fremder Schluessel)", loaded_meta == meta, str(loaded_meta))
    tm.save_steps(steps, folder / "v3b.json", meta=loaded_meta)
    check("Laden+Speichern verlustfrei", json.loads((folder / "v3b.json").read_text(encoding="utf-8")) == data)
    (folder / "v9.json").write_text(json.dumps({"version": 9, "steps": []}), encoding="utf-8")
    try:
        tm.load_testcase(folder / "v9.json")
        rejected = False
    except ValueError:
        rejected = True
    check("neueres Format abgelehnt", rejected)
    old = folder / "alt.json"
    old.write_text(json.dumps([{"device": "Netzteil", "action": "PSU_VOLT", "value": 3.0}]), encoding="utf-8")
    check("v1 (nacktes Array) laedt weiter", tm.load_steps(old)[0].device_kind == "psu")


def section_editor() -> None:
    print("editor -- Testablauf-Editor behaelt v3-Felder")
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication
    import testcase_model as tm
    from testcase_tab import TestcaseTab

    QApplication.instance() or QApplication([])
    folder = Path(tempfile.mkdtemp(prefix="lc-editor-"))
    path = folder / "a.json"
    tm.save_steps([tm.TestStep(device_kind="psu", action="PSU_VOLT", value=5.0, requirements=["SSR-7"],
                               params={"k": 1}),
                   tm.TestStep(step_type="wait", duration=0.2, extra={"neu": True})],
                  path, meta={"source_sink": True, "requirements": ["SYS-1"]})
    tab = TestcaseTab()
    check("Laden", tab._load_path(path))
    steps = tab.steps()
    check("requirements/params im Editor erhalten", steps[0].requirements == ["SSR-7"] and steps[0].params == {"k": 1},
          f"{steps[0].requirements} {steps[0].params}")
    check("unbekanntes Feld im Editor erhalten", steps[1].extra == {"neu": True})
    check("meta im Editor", tab.meta() == {"source_sink": True, "requirements": ["SYS-1"]}, str(tab.meta()))
    from testcase_properties import split_requirements
    check("Anforderungen trennen", split_requirements("SSR-1, SSR-2;SYS-3  SSR-1") == ["SSR-1", "SSR-2", "SYS-3"])
    tab.deleteLater()


def section_interlock() -> None:
    print("interlock -- Verriegelung Netzteil/Last")
    from run_session import Interlock

    online = {"psu:A", "load:B"}
    lock = Interlock([["psu:A", "load:B"]], lambda d: d in online)
    check("unbekannter Zustand (online) -> abgelehnt", "unbekannt" in lock.check("load:B", "load", "OUT_ON"))
    lock.on_output_state("psu:A", True)
    check("Netzteil an -> Last EIN abgelehnt", "eingeschaltet" in lock.check("load:B", "load", "OUT_ON"))
    check("Sollwert setzen bleibt erlaubt", lock.check("load:B", "load", "CURR") == "")
    lock.on_output_state("load:B", False)
    check("Netzteil EIN bei Last aus erlaubt", lock.check("psu:A", "psu", "PSU_OUT_ON") == "")
    lock.note_action("psu:A", "psu", "PSU_OUT_OFF")
    check("nach PSU_OUT_OFF sofort frei", lock.check("load:B", "load", "OUT_ON") == "")
    online.discard("psu:A")
    lock.on_device_removed("psu", "psu:A")
    check("Gegenstueck getrennt -> erlaubt", lock.check("load:B", "load", "OUT_ON") == "")
    check("fremdes Geraet nicht betroffen", lock.check("load:C", "load", "OUT_ON") == "")


def write_case(folder: Path, name: str, steps: list, meta: dict | None = None) -> Path:
    import testcase_model as tm
    path = folder / f"{name}.json"
    tm.save_steps(steps, path, meta=meta)
    return path


def section_cli() -> None:
    print("cli -- labcontrol run ohne Fenster")
    import device_worker as dw
    import testcase_model as tm
    from run_session import EXIT_ABORTED, EXIT_FAILED, EXIT_PASSED, EXIT_SETUP

    folder = Path(tempfile.mkdtemp(prefix="lc-cases-"))
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    load = dict(device_kind="load", device_id=dw.SIM_LOAD_ID)
    passing = write_case(folder, "gut", [
        tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=0.5, check_enabled=True, check_field="voltage",
                    check_min=0.0, check_max=100.0, requirements=["SSR-1"]),
        tm.TestStep(**psu, action="PSU_OUT_OFF"),
    ], meta={"requirements": ["SYS-1"]})
    failing = write_case(folder, "schlecht", [
        tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=0.5, check_enabled=True, check_field="voltage",
                    check_min=-2.0, check_max=-1.0),
    ])
    interlocked = [
        tm.TestStep(**psu, action="PSU_VOLT", value=5.0),
        tm.TestStep(**psu, action="PSU_OUT_ON", duration=0.3),
        tm.TestStep(**load, action="OUT_ON", duration=0.3),
        tm.TestStep(**load, action="OUT_OFF"),
        tm.TestStep(**psu, action="PSU_OUT_OFF"),
    ]
    locked = write_case(folder, "verriegelt", interlocked)
    released = write_case(folder, "quelle_senke", interlocked, meta={"source_sink": True})
    missing = write_case(folder, "fehlt", [tm.TestStep(device_kind="psu", device_id="psu:GIBTSNICHT",
                                                        action="PSU_VOLT", value=3.0)])
    groups = [[dw.SIM_PSU_ID, dw.SIM_LOAD_ID]]

    data = new_data_dir(groups)
    out = folder / "out"
    code, text = run_cli(data, "run", str(passing), "--simulation", "--wait-devices", "20",
                         "--junit", str(out / "gut.xml"), "--json", str(out / "gut.json"),
                         "--report", str(out / "gut.html"))
    check("bestanden -> Exit 0", code == EXIT_PASSED, f"{code}: {text[-600:]}")
    try:
        result = json.loads((out / "gut.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {}
    check("run-result.json: passed, Anforderung am Schritt", result.get("outcome") == "passed"
          and result.get("checks", [{}])[0].get("requirements") == ["SSR-1"]
          and result.get("meta", {}).get("requirements") == ["SYS-1"], str(result)[:300])
    try:
        suite = ET.parse(out / "gut.xml").getroot().find("testsuite")
        junit_ok = (suite is not None and suite.get("failures") == "0" and suite.get("errors") == "0"
                    and len(suite.findall("testcase")) == 1
                    and suite.find("testcase/properties/property").get("value") == "SSR-1")
    except (OSError, ET.ParseError, AttributeError):
        junit_ok = False
    check("JUnit: 1 Testfall, Anforderung als property", junit_ok)
    html_text = (out / "gut.html").read_text(encoding="utf-8") if (out / "gut.html").exists() else ""
    check("HTML-Bericht mit Verriegelung", "Verriegelung" in html_text and "SYS-1" in html_text)

    code, text = run_cli(data, "run", str(failing), "--simulation", "--junit", str(out / "schlecht.xml"))
    check("Pruefung verletzt -> Exit 1", code == EXIT_FAILED, f"{code}: {text[-400:]}")
    try:
        failures = ET.parse(out / "schlecht.xml").getroot().find("testsuite").get("failures")
    except (OSError, ET.ParseError, AttributeError):
        failures = None
    check("JUnit: failure", failures == "1")

    abort_case = write_case(folder, "abbruch", [
        tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=0.5, check_enabled=True, check_field="voltage",
                    check_min=-2.0, check_max=-1.0, check_abort=True),
        tm.TestStep(**psu, action="PSU_VOLT", value=6.0),
    ])
    code, text = run_cli(data, "run", str(abort_case), "--simulation")
    check("Pruefung verletzt mit Abbruch -> Exit 1", code == EXIT_FAILED, f"{code}: {text[-400:]}")

    code, text = run_cli(data, "run", str(locked), "--simulation", "--json", str(out / "verriegelt.json"))
    check("Verriegelung -> Exit 2", code == EXIT_ABORTED, f"{code}: {text[-400:]}")
    check("Meldung nennt Verriegelung", "Verriegelung" in text, text[-400:])
    code, text = run_cli(data, "run", str(released), "--simulation")
    check("Quelle/Senke freigegeben -> Exit 0", code == EXIT_PASSED, f"{code}: {text[-400:]}")

    code, text = run_cli(data, "run", str(missing), "--simulation", "--wait-devices", "2")
    check("Geraet fehlt -> Exit 3", code == EXIT_SETUP and "nicht verbunden" in text, f"{code}: {text[-300:]}")
    code, text = run_cli(data, "run", str(passing), "--simulation", "--var", "kaputt")
    check("falsches --var -> Exit 3", code == EXIT_SETUP, f"{code}: {text[-200:]}")
    code, text = run_cli(data, "run", str(folder / "gibtsnicht.json"), "--simulation")
    check("Datei fehlt -> Exit 3", code == EXIT_SETUP)

    from instance_lock import InstanceLock
    lock = InstanceLock(data)
    check("Sperre im Pruefprozess geholt", lock.acquire())
    code, text = run_cli(data, "run", str(passing), "--simulation")
    check("zweite Instanz -> Exit 3", code == EXIT_SETUP and "läuft bereits" in text, f"{code}: {text[-300:]}")
    lock.release()

    if sys.platform != "win32":
        import signal
        slow = write_case(folder, "lang", [tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=30.0)])
        env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen([sys.executable, __file__, "--child", "run", str(slow), "--simulation"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 30
        lines = []
        for line in proc.stdout:
            lines.append(line)
            if line.startswith("Start:") or time.monotonic() > deadline:
                break
        time.sleep(1.0)
        proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = None
        check("SIGTERM -> Exit 130", code == 130, f"{code}: {''.join(lines)[-300:]}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main()
    section_format()
    section_editor()
    section_interlock()
    section_cli()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

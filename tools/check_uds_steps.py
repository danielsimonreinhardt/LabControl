"""Prueft FT-1 Ablaufschritte (0.60.1): uds_steps (Befehl, Urteil, Text), ganze Ablaeufe mit UDS-Schritten gegen
das nachgebildete Steuergeraet ueber `labcontrol run` (Simulation), Editor und Parameter-Dialog.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_uds_steps.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-udssteps-check-"))
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


def section_unit() -> None:
    print("unit -- uds_steps")
    import uds_steps as us
    from uds_client import UdsResult
    check("Erwartung lesen", us.parse_expect(None) is None and us.parse_expect("nrc:0x33") == 0x33)
    check("Befehl Session", us.command_for("UDS_SESSION", {"session": "Extended", "expect": "positive"})
          == ("session", {"session": "Extended"}))
    check("Befehl DTC lesen mit Maske als Text", us.command_for("UDS_READ_DTC", {"mask": "0x09"}) == ("read_dtcs", {"mask": 9}))
    ok = UdsResult(True, values={"numeric": {"supply": 13800.0}})
    check("positiv erwartet, positiv -> ok mit Wert", us.evaluate("UDS_READ_DID", {"element": "supply"}, ok).value == 13800.0)
    check("positiv erwartet, NRC -> verletzt", us.evaluate("UDS_SESSION", {}, UdsResult(False, nrc=0x22)).status == "check_failed")
    check("NRC erwartet, gleiche NRC -> ok", us.evaluate("UDS_WRITE_DID", {"expect": "nrc:0x33"}, UdsResult(False, nrc=0x33)).status == "ok")
    check("NRC erwartet, positiv -> verletzt", us.evaluate("UDS_WRITE_DID", {"expect": "nrc:0x33"}, UdsResult(True)).status == "check_failed")
    check("keine Antwort -> Fehler", us.evaluate("UDS_SESSION", {}, UdsResult(False, message="Zeit")).status == "error")
    dtcs = UdsResult(True, values={"dtcs": [(0x904001, 9)]})
    check("DTC erwartet und da -> ok", us.evaluate("UDS_READ_DTC", {"present": ["0x904001"]}, dtcs).status == "ok")
    check("DTC verboten und da -> verletzt", us.evaluate("UDS_READ_DTC", {"absent": ["0x904001"]}, dtcs).status == "check_failed")
    check("leer verlangt, aber DTC -> verletzt", us.evaluate("UDS_READ_DTC", {"empty": True}, dtcs).status == "check_failed")
    check("Text", us.summary("UDS_WRITE_DID", {"did": "RamTestValue", "values": {"ramTestValue": 5},
                                               "expect": "nrc:0x33"}) == "RamTestValue ← ramTestValue=5 → NRC 0x33")


def run_case(name: str, steps, settings: dict | None = None, *extra: str) -> tuple[int, str, dict]:
    import testcase_model as tm
    data = DATA / name
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True, **(settings or {})}), encoding="utf-8")
    path = data / f"{name}.json"
    tm.save_steps(steps, path)
    out = data / "r.json"
    env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "run", str(path),
                           "--simulation", "--json", str(out), "--report", str(data / "r.html"), *extra],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return proc.returncode, proc.stdout + proc.stderr, result


def section_runs() -> None:
    print("runs -- Ablaeufe gegen das nachgebildete Steuergeraet")
    import testcase_model as tm

    def uds(action: str, check: dict | None = None, **params):
        step = tm.TestStep(device_kind="uds", action=action, params=params)
        for key, value in (check or {}).items():
            setattr(step, key, value)
        return step

    full = [
        uds("UDS_WRITE_DID", did="RamTestValue", values={"ramTestValue": 1}, expect="nrc:0x7F"),
        uds("UDS_SESSION", session="Extended"),
        uds("UDS_WRITE_DID", did="RamTestValue", values={"ramTestValue": 1}, expect="nrc:0x33"),
        uds("UDS_SECURITY"),
        uds("UDS_WRITE_DID", did="RamTestValue", values={"ramTestValue": 42}),
        uds("UDS_READ_DID", {"check_enabled": True, "check_field": "uds_value", "check_min": 41, "check_max": 43,
                             "store_var": "ram", "requirements": ["UDS-1"]}, did="RamTestValue", element="ramTestValue"),
        tm.TestStep(step_type="if", cond_source="variable", cond_var="ram", cond_op="==", cond_value=42),
        uds("UDS_READ_DTC", present=["0x904001"]),
        tm.TestStep(step_type="end"),
        uds("UDS_CLEAR_DTC"),
        uds("UDS_READ_DTC", empty=True),
        uds("UDS_ROUTINE", routine="LedSelfTest", step="start"),
        uds("UDS_RESET", type=1),
        tm.TestStep(step_type="wait", duration=0.3),
        uds("UDS_READ_DID", {"check_enabled": True, "check_field": "uds_value", "check_min": 2, "check_max": 2},
            did="EcuStatistics", element="starts"),
        uds("UDS_RAW", request="22 F1 86"),
    ]
    code, text, result = run_case("voll", full)
    check("ganzer UDS-Ablauf -> Exit 0", code == 0, text[-600:])
    reqs = {r["id"]: r["verdict"] for r in result.get("requirements", [])}
    check("Anforderung an der DID-Pruefung bestanden", reqs.get("UDS-1") == "passed", str(reqs))
    report = (DATA / "voll" / "r.html").read_text(encoding="utf-8") if (DATA / "voll" / "r.html").exists() else ""
    check("Bericht nennt UDS-Schritte mit Parametern", "Prüfling (UDS)" in report and "RamTestValue" in report)

    code, text, result = run_case("nrc", [uds("UDS_WRITE_DID", did="RamTestValue", values={"ramTestValue": 1})])
    check("positiv erwartet, NRC erhalten -> Exit 1", code == 1 and "NRC 0x7F" in text, f"{code}: {text[-300:]}")
    code, text, _ = run_case("dtc", [uds("UDS_READ_DTC", empty=True)])
    check("DTC-Erwartung verletzt -> Exit 1", code == 1 and "Fehlerspeicher" in text, f"{code}: {text[-300:]}")
    code, text, _ = run_case("range", [uds("UDS_READ_DID", {"check_enabled": True, "check_field": "uds_value",
                                                            "check_min": 0, "check_max": 100}, did=0x0120,
                                           element="supply")])
    check("Bereich verletzt (13800 mV) -> Exit 1", code == 1, f"{code}: {text[-300:]}")
    code, text, _ = run_case("ohne", [uds("UDS_SESSION", session="Extended")],
                             {"dut": {"odx": str(DATA / "gibtsnicht.pdx")}})
    check("Pruefling nicht lesbar -> Exit 3", code == 3 and "Prüfling" in text, f"{code}: {text[-300:]}")


def section_editor() -> None:
    print("editor -- Zeile und Dialog")
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication
    import testcase_model as tm
    from testcase_tab import COL_VALUE, TestcaseTab
    from uds_sim import sim_odx
    from uds_step_dialog import UdsStepDialog
    QApplication.instance() or QApplication([])
    odx = sim_odx()
    tab = TestcaseTab()
    tab.set_uds_odx_provider(lambda: odx)
    path = DATA / "editor.json"
    tm.save_steps([tm.TestStep(device_kind="uds", action="UDS_WRITE_DID",
                               params={"did": "RamTestValue", "values": {"ramTestValue": 3}})], path)
    tab._load_path(path)
    step = tab.steps()[0]
    check("Zeile behaelt UDS-Parameter", step.device_kind == "uds" and step.params == {"did": "RamTestValue",
                                                                                         "values": {"ramTestValue": 3}},
          str(step.params))
    page = tab._table.cellWidget(0, COL_VALUE)
    check("Zeile zeigt die UDS-Seite mit Text", page.currentIndex() == 6
          and "RamTestValue" in page.widget(6).findChild(type(page.widget(6).layout().itemAt(0).widget())).text())
    dialog = UdsStepDialog("UDS_WRITE_DID", step.params, odx)
    dialog._value_edits["ramTestValue"].setText("9")
    dialog._expect.setCurrentIndex(1)
    dialog._nrc.setText("0x33")
    dialog._accept()
    check("Dialog: Wert und Erwartung", dialog.params() == {"did": "RamTestValue", "values": {"ramTestValue": 9},
                                                            "expect": "nrc:0x33"}, str(dialog.params()))
    dialog = UdsStepDialog("UDS_READ_DTC", {}, odx)
    dialog._fields["present"].setText("0x904001; 0xC10016")
    dialog._accept()
    check("Dialog: DTC-Liste", dialog.params().get("present") == ["0x904001", "0xC10016"], str(dialog.params()))
    dialog = UdsStepDialog("UDS_RAW", {}, odx)
    dialog._fields["request"].setText("22 ZZ")
    dialog._accept()
    check("Dialog: ungueltiges Hex wird abgelehnt", dialog.result() == 0 and dialog._error.text() != "")
    page.widget(6)._params = {"did": "EcuConditions", "element": "supply"}
    tab._table.cellWidget(0, 2).setCurrentIndex(tab._table.cellWidget(0, 2).findData("UDS_READ_DID"))
    check("geaenderte Parameter landen im Schritt", tab.steps()[0].params == {"did": "EcuConditions", "element": "supply"})
    tab.deleteLater()


def main() -> int:
    section_unit()
    section_runs()
    section_editor()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

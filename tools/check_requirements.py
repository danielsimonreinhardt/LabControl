"""Prueft FT-3 Anforderungen (0.59.3): Register (StrictDoc/CSV/Markdown), Urteil je Anforderung (FP-23 a),
Abdeckung (labcontrol coverage), Editor (Anforderungen im Pruefungsdialog), Bericht und run-result.json.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_requirements.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-req-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
SDOC = """[DOCUMENT]
TITLE: Test

[GRAMMAR]
IMPORT_FROM_FILE: @x

[TEXT]
STATEMENT: >>>
UID: KEIN-1 (Text, keine Anforderung)
<<<

[[SECTION]]
TITLE: Abschnitt

[SSR]
UID: SSR-001
TITLE: Reaction decided in one place
STATUS: implemented
NOTE: >>>
TITLE: nicht der Titel
<<<

[CLAUSE]
UID: SSR-001(a)
STATEMENT: >>>
Every mechanism shall report a fault.
second line
<<<

[[/SECTION]]
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def section_register() -> None:
    print("register -- StrictDoc, CSV, Markdown")
    import requirements as rq
    reg = rq.parse_sdoc(SDOC, "t.sdoc")
    check("sdoc: SSR und CLAUSE, kein TEXT", sorted(reg) == ["SSR-001", "SSR-001(a)"], str(sorted(reg)))
    check("sdoc: Titel aus TITLE, nicht aus NOTE", reg.get("SSR-001", {}).get("title") == "Reaction decided in one place")
    check("sdoc: Klausel-Titel aus STATEMENT", reg.get("SSR-001(a)", {}).get("title") == "Every mechanism shall report a fault.")
    csv_reg = rq.parse_table("ID;Titel;Status\nBS-1;Zellspannung;offen\nBS-2;Stapel;ok\n", "a.csv")
    check("CSV mit Kopfzeile", csv_reg.get("BS-2", {}).get("title") == "Stapel" and len(csv_reg) == 2, str(csv_reg))
    md_reg = rq.parse_table("| ID | Titel |\n|---|---|\n| LB-10 | Bremse |\n| Text | ohne Nummer |\n", "a.md",
                            markdown=True)
    check("Markdown-Tabelle", list(md_reg) == ["LB-10"], str(md_reg))
    (DATA / "reg.sdoc").write_text(SDOC, encoding="utf-8")
    (DATA / "reg.csv").write_text("id,title\nBS-1,Zellspannung\n", encoding="utf-8")
    both = rq.load_register([DATA / "reg.sdoc", DATA / "reg.csv", DATA / "fehlt.md"])
    check("mehrere Dateien, fehlende uebersprungen", sorted(both) == ["BS-1", "SSR-001", "SSR-001(a)"])
    real = Path("C:/WORK/vscode_workspace/S32K14x basic software/doc/requirements/safety/ssr.sdoc")
    if real.exists():
        count = len(rq.load_register([real]))
        check("echtes ssr.sdoc (neXtBSW) liest > 200 Eintraege", count > 200, str(count))


def make_record(outcome: str = "passed"):
    import testcase_model as tm
    from run_record import RunRecord, StepEvent
    steps = [
        tm.TestStep(check_enabled=True, requirements=["A-1"]),
        tm.TestStep(check_enabled=True, requirements=["A-2", "A-1"]),
        tm.TestStep(check_enabled=False, requirements=["A-3"]),     # ohne Pruefung -> kein Nachweis
        tm.TestStep(check_enabled=True, requirements=["A-4"]),      # nie erreicht
    ]
    events = [StepEvent(0, "result", 0, passed=True, measured=1.0),
              StepEvent(0, "result", 1, passed=False, measured=9.0),
              StepEvent(0, "result", 0, passed=True, measured=1.1)]
    return RunRecord(testcase_name="t", steps=steps, events=events, outcome=outcome,
                     meta={"requirements": ["SYS-1"]}, requirement_register={"A-1": {"title": "Eins"}})


def section_verdicts() -> None:
    print("verdicts -- Urteil je Anforderung (FP-23 a)")
    import requirements as rq
    rows = {r["id"]: r for r in rq.verdicts(make_record("failed_checks"), {"A-1": {"title": "Eins"}})}
    check("A-1: eine Pruefung verletzt -> nicht bestanden", rows["A-1"]["verdict"] == rq.FAILED
          and rows["A-1"]["executions"] == 3 and rows["A-1"]["title"] == "Eins", str(rows["A-1"]))
    check("A-2: verletzt -> nicht bestanden", rows["A-2"]["verdict"] == rq.FAILED)
    check("A-3: Schritt ohne Pruefung -> nicht geprueft", rows["A-3"]["verdict"] == rq.NOT_TESTED)
    check("A-4: Pruefung nie gelaufen -> nicht geprueft", rows["A-4"]["verdict"] == rq.NOT_TESTED)
    check("SYS-1 (ganzer Ablauf) mit Verletzung -> nicht bestanden", rows["SYS-1"]["verdict"] == rq.FAILED)
    import testcase_model as tm
    from run_record import RunRecord, StepEvent
    ok = RunRecord(steps=[tm.TestStep(check_enabled=True, requirements=["B-1"])],
                   events=[StepEvent(0, "result", 0, passed=True)], outcome="passed", meta={"requirements": ["SYS-2"]})
    rows = {r["id"]: r for r in rq.verdicts(ok)}
    check("alle bestanden -> bestanden", rows["B-1"]["verdict"] == rq.PASSED and rows["SYS-2"]["verdict"] == rq.PASSED)
    ok.outcome = "stopped"
    rows = {r["id"]: r for r in rq.verdicts(ok)}
    check("abgebrochen: Schritt-Anforderung bleibt bestanden, Ablauf-Anforderung nicht geprueft",
          rows["B-1"]["verdict"] == rq.PASSED and rows["SYS-2"]["verdict"] == rq.NOT_TESTED, str(rows))


def section_coverage() -> None:
    print("coverage -- Abdeckung ueber Laeufe")
    import requirements as rq
    results = [
        {"started": "2026-11-12T10:00:00", "testcase": "alt", "requirements": [
            {"id": "A-1", "title": "Eins", "verdict": "failed"}]},
        {"started": "2026-11-13T10:00:00", "testcase": "neu", "requirements": [
            {"id": "A-1", "title": "Eins", "verdict": "passed"}]},
    ]
    rows = {r["id"]: r for r in rq.coverage(results, {"A-9": {"title": "Neun"}})}
    check("juengster Lauf bestimmt das Urteil", rows["A-1"]["verdict"] == "passed" and rows["A-1"]["testcase"] == "neu"
          and rows["A-1"]["runs"] == 2 and rows["A-1"]["failed"] == 1, str(rows["A-1"]))
    check("Registereintrag ohne Lauf -> nicht geprueft", rows["A-9"]["verdict"] == "not_tested")
    csv_text = rq.coverage_csv(list(rows.values()))
    check("CSV mit Kopfzeile", csv_text.splitlines()[0].startswith("id;title;verdict"))
    check("Markdown nennt Urteil auf Deutsch", "nicht geprüft" in rq.coverage_markdown(list(rows.values())))


def section_editor() -> None:
    print("editor -- Pruefungsdialog")
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication
    import testcase_model as tm
    from check_dialog import CheckDialog
    from testcase_tab import COL_CHECK, TestcaseTab
    QApplication.instance() or QApplication([])
    dialog = CheckDialog({"enabled": True, "field": "voltage", "min": 0, "max": 1, "abort": False,
                          "requirements": ["SSR-001"]}, is_arb=False, requirement_ids=["SSR-001", "SSR-001(a)"])
    check("Dialog zeigt die Anforderungen", dialog._req_edit.text() == "SSR-001")
    dialog._req_edit.setText("SSR-001(a); BS-1, SSR-001(a)")
    check("Dialog liefert bereinigte Liste", dialog.params()["requirements"] == ["SSR-001(a)", "BS-1"],
          str(dialog.params()["requirements"]))
    completer = dialog._req_edit.completer()
    check("Vervollstaendigung nur fuer das letzte Element", completer.splitPath("SSR-001(a), SS") == ["SS"])
    tab = TestcaseTab()
    path = DATA / "editor.json"
    tm.save_steps([tm.TestStep(device_kind="psu", action="PSU_VOLT", check_enabled=True, requirements=["A-1"])], path)
    tab._load_path(path)
    check("Schritt behaelt Anforderungen", tab.steps()[0].requirements == ["A-1"])
    page = tab._table.cellWidget(0, COL_CHECK)
    page._check_params = dict(page._check_params, requirements=["A-1", "A-2"])
    check("Aenderung im Dialog landet im Schritt", tab.steps()[0].requirements == ["A-1", "A-2"])
    page._check_params = dict(page._check_params, requirements=[])
    check("Leeren im Dialog leert den Schritt", tab.steps()[0].requirements == [])
    check("Editor gilt danach als geaendert", tab.is_modified())
    tab.deleteLater()


def section_cli() -> None:
    print("cli -- Lauf mit Register, dann coverage")
    import device_worker as dw
    import testcase_model as tm
    (DATA / "settings.json").write_text(json.dumps({
        "simulation_mode": True, "requirement_registers": [str(DATA / "reg.sdoc")]}), encoding="utf-8")
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    case = DATA / "req_case.json"
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=0.4, check_enabled=True,
                               check_field="voltage", check_min=0, check_max=100, requirements=["SSR-001(a)"])],
                  case, meta={"requirements": ["SSR-001"]})
    out = DATA / "out"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    child = [sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child"]
    proc = subprocess.run(child + ["run", str(case), "--simulation", "--json", str(out / "run_1.json"),
                                   "--report", str(out / "r.html")],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    check("Lauf bestanden", proc.returncode == 0, proc.stdout[-400:] + proc.stderr[-400:])
    result = json.loads((out / "run_1.json").read_text(encoding="utf-8")) if (out / "run_1.json").exists() else {}
    reqs = {r["id"]: r for r in result.get("requirements", [])}
    check("run-result: Urteile mit Titel aus dem Register",
          reqs.get("SSR-001(a)", {}).get("verdict") == "passed"
          and reqs.get("SSR-001(a)", {}).get("title") == "Every mechanism shall report a fault."
          and reqs.get("SSR-001", {}).get("verdict") == "passed", str(reqs))
    report = (out / "r.html").read_text(encoding="utf-8") if (out / "r.html").exists() else ""
    check("Bericht: Abschnitt Anforderungen", "Anforderungen" in report and "SSR-001(a)" in report
          and "bestanden" in report)
    proc = subprocess.run(child + ["coverage", "--results", str(out), "--csv", str(out / "cov.csv"),
                                   "--md", str(out / "cov.md")],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    csv_text = (out / "cov.csv").read_text(encoding="utf-8") if (out / "cov.csv").exists() else ""
    check("coverage: Exit 0, CSV mit beiden IDs", proc.returncode == 0 and "SSR-001(a);" in csv_text
          and "SSR-001;" in csv_text, proc.stdout[-300:])
    check("coverage: Zusammenfassung", "2 Anforderungen aus 1 Läufen" in proc.stdout, proc.stdout[-200:])


def main() -> int:
    section_register()
    section_verdicts()
    section_coverage()
    section_editor()
    section_cli()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

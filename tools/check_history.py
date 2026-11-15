"""Prueft FT-11 Ergebnis-Uebersicht und Trends (PI-57 a, 0.68.0): run_history (Laden, Historie, wackelnde
Pruefungen, Trend, Vergleich, HTML), `labcontrol history`, Ergebnisdateien auch bei Laeufen ohne Fenster und den
Reiter "Ergebnisse".

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_history.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-history-check-"))
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


def result(testcase: str, started: str, host: str, outcome: str, value: float, verdict: str,
           variant: str = "", extra_check: bool = True) -> dict:
    checks = [{"index": 2, "device": "Netzteil", "action": "PSU_OUT_ON", "action_label": "Ausgang EIN",
               "check": "U 4.5–5.5 V", "field": "voltage", "unit": "V", "min": 4.5, "max": 5.5,
               "values": [value], "verdict": verdict}]
    if extra_check:
        checks.append({"index": 4, "device": "Last", "action": "CURR", "action_label": "Strom",
                       "check": "I 0–1 A", "field": "current", "unit": "A", "min": 0, "max": 1,
                       "values": [0.5], "verdict": "passed"})
    return {"format": "labcontrol-run-result", "version": 1, "testcase": testcase, "host": host,
            "started": started, "duration_s": 3.0, "outcome": outcome, "exit_code": 0 if outcome == "passed" else 1,
            "meta": {"variant": variant} if variant else {}, "checks": checks,
            "timing_checks": [{"step": 5, "mode": "band", "check": "Band", "passed": True, "value": 0.1, "unit": "V"}]}


def make_folders() -> list[Path]:
    pc = DATA / "share" / "Labor" / "reports"
    pi = DATA / "share" / "raspberrypi" / "reports"
    for folder in (pc, pi):
        folder.mkdir(parents=True)
    rows = [("pc", "2026-11-10T08:00:00", "passed", 5.0, "passed"),
            ("pi", "2026-11-11T08:00:00", "failed_checks", 5.8, "failed"),
            ("pc", "2026-11-12T08:00:00", "passed", 5.1, "passed"),
            ("pi", "2026-11-13T08:00:00", "failed_checks", 4.2, "failed"),
            ("pc", "2026-11-14T08:00:00", "passed", 5.2, "passed")]
    for n, (where, started, outcome, value, verdict) in enumerate(rows):
        folder = pc if where == "pc" else pi
        data = result("Ladetest", started, "Labor" if where == "pc" else "raspberrypi", outcome, value, verdict,
                      variant="PowerUnit" if n % 2 else "")
        (folder / f"run_{n}.json").write_text(json.dumps(data), encoding="utf-8")
    (pc / "run_x.json").write_text(json.dumps(result("Anderer", "2026-11-09T08:00:00", "Labor", "passed", 5, "passed",
                                                     extra_check=False)), encoding="utf-8")
    (pc / "run_kaputt.json").write_text("{", encoding="utf-8")
    (pc / "run_fremd.json").write_text(json.dumps({"format": "etwas"}), encoding="utf-8")
    (pc / "run_0.html").write_text("<html>Bericht</html>", encoding="utf-8")
    return [pc, pi]


def section_unit(folders) -> None:
    print("unit -- run_history")
    import run_history as rh

    cache: dict = {}
    runs = rh.load_runs(folders, cache)
    check("6 gueltige Laeufe, kaputte/fremde uebergangen", len(runs) == 6, str(len(runs)))
    check("aelteste zuerst", runs[0].testcase == "Anderer" and runs[-1].started.startswith("2026-11-14"))
    check("Cache wird genutzt", len(cache) == 8 and rh.load_runs(folders, cache)[0] is runs[0])
    check("Ablaeufe mit Anzahl", rh.testcases(runs) == [("Anderer", 1), ("Ladetest", 5)], str(rh.testcases(runs)))
    lade = rh.for_testcase(runs, "Ladetest")
    check("Bestanden-Quote", abs(rh.pass_rate(lade) - 0.6) < 1e-9)
    check("Variante gelesen", [r.variant for r in lade] == ["", "PowerUnit", "", "PowerUnit", ""])
    flaky = rh.flaky_checks(lade)
    check("eine wackelnde Pruefung mit 4 Wechseln", len(flaky) == 1 and flaky[0]["flips"] == 4
          and flaky[0]["passed"] == 3 and flaky[0]["failed"] == 2, str(flaky))
    check("Bezeichnung nennt Schritt und Geraet", "Schritt 2" in flaky[0]["label"] and "Netzteil" in flaky[0]["label"])
    keys = rh.check_keys(lade)
    check("Pruefungen mit Messwert in Schrittfolge (inkl. Zeitpruefung)", [k for k, _l in keys][:2]
          == ["2:PSU_OUT_ON:voltage", "4:CURR:current"] and any(k.startswith("timing:") for k, _l in keys), str(keys))
    points = rh.trend(lade, "2:PSU_OUT_ON:voltage")
    check("Trend: Werte, Grenzen, Urteile", [p["value"] for p in points] == [5.0, 5.8, 5.1, 4.2, 5.2]
          and points[0]["min"] == 4.5 and points[1]["verdict"] == "failed")
    rows = rh.compare(lade[0], lade[1])
    changed = [r for r in rows if r["changed"]]
    check("Vergleich: genau die Spannung geaendert", len(changed) == 1 and changed[0]["a_value"] == 5.0
          and changed[0]["b_value"] == 5.8, str(changed))
    other = rh.for_testcase(runs, "Anderer")[0]
    rows = rh.compare(other, lade[0])
    check("Vergleich: fehlende Pruefung als –", any(r["a_verdict"] == "–" for r in rows))
    page = rh.html_overview(runs)
    check("HTML: beide Ablaeufe, Trend als SVG, wackelnd", "Ladetest" in page and "Anderer" in page
          and "<svg" in page and "Wackelnde" in page)
    check("Ordnerliste wie coverage", rh.default_folders(str(DATA / "share"), DATA / "lokal")[:2] == sorted(folders))


def section_cli(folders) -> None:
    print("cli -- labcontrol history, Ergebnisdatei ohne Fenster")
    env = dict(os.environ, LABCONTROL_DATA_DIR=str(DATA), PYTHONIOENCODING="utf-8")
    out_html = DATA / "h.html"
    args = [sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "history",
            "--results", str(folders[0]), "--results", str(folders[1]), "--html", str(out_html)]
    proc = subprocess.run(args, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    check("history: Exit 0", proc.returncode == 0, proc.stdout[-400:] + proc.stderr[-400:])
    check("history: Zusammenfassung je Ablauf", "Ladetest" in proc.stdout and "60 %" in proc.stdout, proc.stdout[-400:])
    check("history: wackelnde Pruefung genannt", "wackelt" in proc.stdout, proc.stdout[-400:])
    check("history: HTML geschrieben", out_html.exists() and "<svg" in out_html.read_text("utf-8"))
    proc = subprocess.run(args[:4] + ["--results", str(folders[0]), "--testcase", "Gibtsnicht"], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    check("history: unbekannter Ablauf -> Exit 3", proc.returncode == 3, proc.stdout[-300:])

    import testcase_model as tm
    data = DATA / "run"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    path = data / "kurz.json"
    tm.save_steps([tm.TestStep(device_kind="psu", device_id="psu:SIM", action="PSU_VOLT", value=5.0, duration=0.2)],
                  path)
    env = dict(env, LABCONTROL_DATA_DIR=str(data))
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "run", str(path),
                           "--simulation", "--wait-devices", "8"], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    files = sorted((data / "reports").glob("run_*.json")) if (data / "reports").exists() else []
    check("Lauf ohne Fenster legt run-result.json im Berichtsordner ab", proc.returncode == 0 and len(files) == 1,
          f"{proc.returncode} {files} {proc.stdout[-300:]}")
    if files:
        check("... mit HTML-Bericht daneben", files[0].with_suffix(".html").exists())


def section_ui(folders) -> None:
    print("ui -- Reiter Ergebnisse")
    from PySide6.QtWidgets import QApplication

    from results_tab import ResultsTab

    QApplication.instance() or QApplication([])
    tab = ResultsTab(lambda: folders)
    tab.reload()
    check("Ablaufauswahl: juengster Ablauf zuerst gewaehlt", tab._testcase.currentData() == "Ladetest"
          and tab._testcase.count() == 2)
    check("Laeufe neueste zuerst", tab._runs_table.rowCount() == 5
          and tab._runs_table.item(0, 0).text().startswith("2026-11-14"))
    check("Zusammenfassung", "60 %" in tab._summary.text(), tab._summary.text())
    check("wackelnde Pruefung in der Tabelle", tab._flaky_table.rowCount() == 1)
    check("Trend hat 5 Punkte", len(tab._chart._points) == 5)
    tab._chart.resize(400, 200)
    tab._chart.grab()
    tab._runs_table.selectRow(0)
    check("ein Lauf: Bericht nur, wenn vorhanden", not tab._report_button.isEnabled())
    tab._runs_table.clearSelection()
    tab._runs_table.selectRow(4)
    check("aeltester Lauf hat Bericht", tab._report_button.isEnabled())
    opened: list = []
    tab.open_report_requested.connect(opened.append)
    tab._open_report()
    check("Bericht oeffnen", opened and opened[0].endswith("run_0.html"))
    from PySide6.QtCore import QItemSelectionModel
    tab._runs_table.clearSelection()
    model = tab._runs_table.selectionModel()
    for row in (0, 1):
        model.select(tab._runs_table.model().index(row, 0),
                     QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    check("zwei Laeufe: Vergleich gefuellt", tab._compare_table.rowCount() >= 2 and tab._tabs.currentIndex() == 2,
          str(tab._compare_table.rowCount()))
    tab._testcase.setCurrentIndex(tab._testcase.findData("Anderer"))
    check("anderer Ablauf: ein Lauf, nichts wackelt", tab._runs_table.rowCount() == 1
          and tab._flaky_table.rowCount() == 0)


def main() -> int:
    folders = make_folders()
    section_unit(folders)
    section_cli(folders)
    section_ui(folders)
    print(f"\n{'ALLES OK' if not FAILURES else f'{len(FAILURES)} FEHLER: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)

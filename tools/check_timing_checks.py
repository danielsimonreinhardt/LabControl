"""Prueft FT-7 Zeitpruefungen live (0.63.0): Block "Ueberwachen … Ende" mit band / reach / can_gap.

- unit    -- timing_checks.Monitor: Band, Erreichen mit Reaktionszeit, zu grobe Aufloesung, CAN-Luecke (auch ohne
             Frames), Standardwerte
- runner  -- TestRunner mit nachgebildeten Messwerten/Frames: bestanden, verletzt ohne/mit Abbruch, Reaktionszeit,
             Frist, CAN-Luecke; monitor_result mit Verlauf und Aufloesung
- results -- timing_rows, run-result.json "timing_checks", JUnit-Testfaelle, Bericht "Zeitprüfungen" mit Kurve
- editor  -- Block einfuegen, Parameter ueber die Zeile, Speichern/Laden, "Ende (Überwachen)"
- dialog  -- MonitorDialog blendet je Art die passenden Felder ein, params()
- cli     -- `main.py run` mit Ueberwachung am simulierten Netzteil: Band bestanden -> 0, Schwelle nie erreicht -> 1

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_timing_checks.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-timing-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def child_main() -> int:
    import no_hardware
    no_hardware.block()
    import headless
    return headless.main(["labcontrol", *sys.argv[2:]])


def section_unit() -> None:
    print("unit")
    import timing_checks as tc
    m = tc.Monitor({"mode": "band", "min": 11.0, "max": 13.0}, 0.0)
    for i in range(10):
        m.feed(i * 0.1, 12.0 + (0.5 if i == 5 else 0.0))
    v = m.finish(1.0)
    check("band bestanden, Extremwert, Aufloesung 100 ms", v.passed and v.value == 12.5
          and abs(v.resolution_s - 0.1) < 1e-9, str(v))
    m = tc.Monitor({"mode": "band", "min": 11.0, "max": 13.0}, 0.0)
    first = [m.feed(i * 0.1, 10.0 if i >= 3 else 12.0) for i in range(6)]
    check("band verletzt beim ersten Wert ausserhalb, nur einmal gemeldet",
          first[3] is not None and not first[3].passed and sum(1 for x in first if x) == 1, str(first))
    m = tc.Monitor({"mode": "reach", "op": ">=", "value": 5.0, "within_s": 1.0}, 0.0)
    for i in range(20):
        m.feed(i * 0.02, 6.0 if i >= 15 else 0.0)
    v = m.finish(0.5)
    check("reach: Reaktionszeit 0,3 s", v.passed and abs(v.value - 0.3) < 1e-9, str(v))
    m = tc.Monitor({"mode": "reach", "op": ">=", "value": 5.0, "within_s": 0.5}, 0.0)
    check("reach: Frist abgelaufen -> Verletzung", m.tick(0.6) is not None and not m.finish(0.7).passed)
    m = tc.Monitor({"mode": "reach", "op": ">=", "value": 5.0, "within_s": 0.5}, 0.0)
    m.feed(0.0, 0.0)
    m.feed(0.4, 6.0)
    v = m.finish(0.5)
    check("reach: Aufloesung 400 ms zu grob fuer 0,5 s", not v.passed and "zu grob" in v.message, str(v))
    m = tc.Monitor({"mode": "can_gap", "can_id": 0x123, "max_gap_ms": 100.0}, 0.0)
    for i in range(5):
        m.feed(i * 0.05)
    check("can_gap: 50 ms Abstand bestanden", m.tick(0.25) is None)
    check("can_gap: Luecke ohne Frames erkannt", m.tick(0.40) is not None)
    m = tc.Monitor({"mode": "can_gap", "can_id": 0x123, "max_gap_ms": 100.0}, 0.0)
    check("can_gap: gar keine Botschaft", not m.finish(0.5).passed)
    c = tc.clean_config({"mode": "x", "can_id": "0x1A0"})
    check("Standardwerte, CAN-ID als Text", c["mode"] == "band" and c["can_id"] == 0x1A0 and c["abort"] is False)


def monitor_block(mode_cfg: dict, body: list, kind: str = "psu", field: str = "voltage"):
    import testcase_model as tm
    start = tm.TestStep(step_type="monitor", cond_device_kind=kind, cond_device_id=f"{kind}:FAKE",
                        cond_field=field, params={"monitor": mode_cfg})
    return [start, *body, tm.TestStep(step_type="end")]


def run(app, steps, feeder, timeout: float = 8.0):
    from run_record import RunRecorder
    from testcase_runner import TestRunner
    runner = TestRunner()
    recorder = RunRecorder()
    for sig, slot in ((runner.step_started, recorder.on_step_started), (runner.step_result, recorder.on_step_result),
                      (runner.step_failed, recorder.on_step_failed), (runner.run_finished, recorder.on_run_finished),
                      (runner.monitor_result, recorder.on_monitor_result)):
        sig.connect(slot)
    done = {"v": False}
    runner.run_finished.connect(lambda: done.update(v=True))
    runner.step_failed.connect(lambda *_: done.update(v=True))
    recorder.begin(steps, "zeit")
    t0 = time.perf_counter()
    runner.start(steps)
    end = t0 + timeout
    while not done["v"] and time.perf_counter() < end:
        feeder(runner, time.perf_counter() - t0)
        app.processEvents()
        time.sleep(0.01)
    return recorder.record(), done["v"]


def periodic(fn, period: float = 0.05):
    state = {"next": 0.0}

    def feed(runner, t):
        if t >= state["next"]:
            state["next"] = t + period
            fn(runner, t)
    return feed


def section_runner(app) -> None:
    print("runner")
    import testcase_model as tm
    wait = tm.TestStep(step_type="wait", duration=0.6)
    psu = lambda value_of: periodic(lambda r, t: r.on_psu_measurement("psu:FAKE", value_of(t), 0.1, False))
    record, done = run(app, monitor_block({"mode": "band", "min": 11.0, "max": 13.0}, [wait]), psu(lambda t: 12.0))
    check("band im Lauf bestanden", done and record.outcome == "passed" and len(record.monitors) == 1
          and record.monitors[0]["passed"], record.outcome)
    result = record.monitors[0] if record.monitors else {}
    check("... monitor_result mit Verlauf und Aufloesung ~50 ms", len(result.get("series", [])) >= 5
          and 0.03 < (result.get("resolution_s") or 0) < 0.08, str(result.get("resolution_s")))
    record, done = run(app, monitor_block({"mode": "band", "min": 11.0, "max": 13.0}, [wait]),
                       psu(lambda t: 10.0 if 0.2 < t < 0.3 else 12.0))
    check("band verletzt ohne Abbruch: Lauf zu Ende, nicht bestanden", done and record.outcome == "failed_checks"
          and record.checks_failed == 1, record.outcome)
    record, done = run(app, monitor_block({"mode": "band", "min": 11.0, "max": 13.0, "abort": True},
                                          [wait, tm.TestStep(step_type="wait", duration=2.0)]),
                       psu(lambda t: 10.0 if t > 0.2 else 12.0))
    import run_session
    check("band verletzt mit Abbruch: Lauf endet sofort, Exit 1", done and record.outcome == "error"
          and record.error_index == 0 and run_session.exit_code(record) == 1, f"{record.outcome} {record.error_index}")
    record, done = run(app, monitor_block({"mode": "reach", "op": ">=", "value": 5.0, "within_s": 1.0}, [wait]),
                       psu(lambda t: 6.0 if t > 0.3 else 1.0))
    value = record.monitors[0]["value"] if record.monitors else -1
    check("reach: bestanden, Reaktionszeit ~0,3 s", done and record.outcome == "passed" and 0.2 < value < 0.45,
          f"{record.outcome} {value}")
    record, done = run(app, monitor_block({"mode": "reach", "op": ">=", "value": 5.0, "within_s": 0.3}, [wait]),
                       psu(lambda t: 1.0))
    check("reach: Frist verpasst -> nicht bestanden", done and record.outcome == "failed_checks", record.outcome)
    frames = periodic(lambda r, t: r.on_can_frame("can:FAKE", 0x123, "00", False, 0.0) if t < 0.25 else None)
    record, done = run(app, monitor_block({"mode": "can_gap", "can_id": 0x123, "max_gap_ms": 120.0}, [wait],
                                          kind="can", field=""), frames)
    check("can_gap: Botschaft bleibt aus -> Luecke erkannt", done and record.outcome == "failed_checks"
          and "Lücke" in (record.monitors[0]["message"] if record.monitors else ""), record.outcome)
    frames = periodic(lambda r, t: r.on_can_frame("can:FAKE", 0x123, "00", False, 0.0), 0.03)
    record, done = run(app, monitor_block({"mode": "can_gap", "can_id": 0x123, "max_gap_ms": 150.0}, [wait],
                                          kind="can", field=""), frames)
    check("can_gap: regelmaessig -> bestanden", done and record.outcome == "passed", record.outcome)
    loop = [tm.TestStep(step_type="loop", loop_count=2),
            *monitor_block({"mode": "band", "min": 0.0, "max": 20.0}, [tm.TestStep(step_type="wait", duration=0.2)]),
            tm.TestStep(step_type="end")]
    record, done = run(app, loop, psu(lambda t: 12.0))
    check("in einer Schleife: zwei Ergebnisse", done and len(record.monitors) == 2, str(len(record.monitors)))

    import run_report
    import run_results
    record, _done = run(app, monitor_block({"mode": "band", "min": 11.0, "max": 13.0}, [wait]),
                        psu(lambda t: 10.0 if 0.2 < t < 0.3 else 12.0))
    record.ended_at = time.time()
    rows = run_results.timing_rows(record)
    check("timing_rows: Schritt, Art, Einheit, Aufloesung", rows and rows[0]["step"] == 1 and rows[0]["mode"] == "band"
          and rows[0]["unit"] == "V" and rows[0]["resolution_ms"], str(rows[:1]))
    junit = run_results.junit_tree(record).getroot().find("testsuite")
    check("JUnit: Zeitpruefung als Testfall mit failure", junit.get("tests") == "1" and junit.get("failures") == "1"
          and junit.find("testcase/failure") is not None)
    check("run-result.json: timing_checks", run_results.result_dict(record)["timing_checks"][0]["passed"] is False)
    text = run_report.write_html_report(record, DATA / "zeit.html").read_text(encoding="utf-8")
    check("Bericht: Zeitprüfungen mit Kurve", "Zeitprüfungen" in text and "Überwachen" in text
          and text.count("data:image/png") >= 1)


def section_editor(app) -> None:
    print("editor")
    import testcase_model as tm
    import testcase_tab as tt
    tab = tt.TestcaseTab()
    tab._table.selectRow(0)
    tab._insert_block("monitor")
    app.processEvents()
    steps = tab.steps()
    index = next(i for i, s in enumerate(steps) if s.step_type == "monitor")
    container = tab._table.cellWidget(index, tt.COL_VALUE)
    container._cond_params.update(cond_device_kind="psu", cond_field="voltage",
                                  monitor={"mode": "reach", "op": ">=", "value": 5.0, "within_s": 2.0})
    container._refresh_summary()
    tab._revalidate_structure()
    steps = tab.steps()
    check("Block eingefuegt, Parameter in params['monitor']", steps[index].params.get("monitor", {}).get("mode")
          == "reach" and steps[index + 1].step_type == "end")
    end_label = tab._table.cellWidget(index + 1, tt.COL_DEVICE).text()
    check("Ende-Zeile zeigt 'Ende (Überwachen)'", end_label == "Ende (Überwachen)", end_label)
    path = DATA / "ueberwachen.json"
    tm.save_steps(steps, path)
    tab2 = tt.TestcaseTab()
    tab2._load_path(path)
    loaded = tab2.steps()
    check("Speichern/Laden behaelt die Ueberwachung", loaded[index].step_type == "monitor"
          and loaded[index].params["monitor"]["within_s"] == 2.0)
    check("Zeile zeigt die Kurzbeschreibung", "innerhalb 2 s" in tab2._table.cellWidget(index, tt.COL_VALUE)
          .findChild(tt.QLabel).text())
    tab.deleteLater()
    tab2.deleteLater()


def section_dialog(app) -> None:
    print("dialog")
    from monitor_dialog import MonitorDialog
    items = [("Netzteil (automatisch)", "psu", ""), ("CAN (automatisch)", "can", "")]
    dialog = MonitorDialog({"cond_device_kind": "psu", "cond_field": "voltage",
                            "monitor": {"mode": "band", "min": 11.0, "max": 13.0}}, items)
    dialog.show()
    app.processEvents()
    check("band: Min/Max sichtbar, Vergleich/Frist nicht", dialog._min_spin.isVisible()
          and not dialog._within_spin.isVisible() and not dialog._m_op_combo.isVisible())
    dialog._mode_combo.setCurrentIndex(dialog._mode_combo.findData("reach"))
    app.processEvents()
    check("reach: Vergleich, Wert und Frist sichtbar", dialog._m_op_combo.isVisible() and dialog._within_spin.isVisible())
    dialog._mode_combo.setCurrentIndex(dialog._mode_combo.findData("can_gap"))
    dialog._can_id_edit.setText("0x130")
    app.processEvents()
    params = dialog.params()
    check("can_gap: CAN-ID und Luecke in params", params["monitor"]["mode"] == "can_gap"
          and params["monitor"]["can_id"] == 0x130 and dialog._gap_spin.isVisible(), str(params["monitor"]))
    dialog.close()


def run_cli(*args: str, timeout: float = 90.0) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, __file__, "--child", *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def section_cli() -> None:
    print("cli")
    import device_worker as dw
    import testcase_model as tm
    (DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)

    def case(name, cfg):
        path = DATA / f"{name}.json"
        tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=0.5),
                       tm.TestStep(step_type="monitor", cond_device_kind="psu", cond_device_id=dw.SIM_PSU_ID,
                                   cond_field="voltage", params={"monitor": cfg}),
                       tm.TestStep(step_type="wait", duration=2.5),
                       tm.TestStep(step_type="end"),
                       tm.TestStep(**psu, action="PSU_OUT_OFF")], path)
        return path
    out = DATA / "band.json.out"
    code, text = run_cli("run", str(case("band", {"mode": "band", "min": -1.0, "max": 100.0})), "--simulation",
                         "--wait-devices", "20", "--json", str(out))
    try:
        rows = json.loads(out.read_text(encoding="utf-8")).get("timing_checks", [])
    except (OSError, ValueError):
        rows = []
    check("Kommandozeile: Band bestanden -> Exit 0, Ergebnis mit Aufloesung", code == 0 and rows
          and rows[0]["passed"] and rows[0]["samples"] >= 2, f"{code} {rows[:1]} {text[-300:]}")
    code, text = run_cli("run", str(case("nie", {"mode": "reach", "op": ">=", "value": 1000.0, "within_s": 2.0})),
                         "--simulation", "--wait-devices", "20")
    check("Kommandozeile: Schwelle nie erreicht -> Exit 1", code == 1, f"{code}: {text[-300:]}")


def section_files() -> None:
    print("files (0.63.1)")
    import timing_files
    from recording import Sample
    from recording_export import export_csv, export_mf4
    t0 = 1_700_000_000.0
    samples = []
    for i in range(50):                       # 5 s, 100 ms Abstand; ab 2,0 s auf 10 V (Einbruch)
        volts = 10.0 if 2.0 <= i * 0.1 < 3.0 else 12.0
        samples.append(Sample(t0 + i * 0.1, "psu:X", "voltage", volts))
        samples.append(Sample(t0 + i * 0.1, "psu:X", "current", 0.5))
    meta = {"psu:X": ("psu", "Netzteil 1")}
    rec_csv = DATA / "aufnahme.csv"
    export_csv(rec_csv, samples, meta)
    series = timing_files.read_series(rec_csv)
    channel = next((name for name in series if name.startswith("Netzteil 1/") and "pannung" in name), None)
    check("Aufzeichnung CSV: Kanaele 'Geraet/Kanal'", channel is not None and len(series[channel][0]) == 50,
          str(sorted(series)))
    result = timing_files.check_file(rec_csv, channel, {"mode": "band", "min": 11.0, "max": 13.0})
    check("CSV: Einbruch verletzt das Band bei 2,0 s, Aufloesung 100 ms", not result["passed"]
          and "2.00 s" in result["message"] and abs(result["resolution_ms"] - 100.0) < 1.0, str(result))
    result = timing_files.check_file(rec_csv, channel, {"mode": "band", "min": 11.0, "max": 13.0}, 3.0, 4.9)
    check("CSV: Fenster nach dem Einbruch bestanden", result["passed"], str(result))
    result = timing_files.check_file(rec_csv, channel, {"mode": "reach", "op": ">=", "value": 11.5,
                                                        "within_s": 1.5}, 2.0)
    check("CSV: Erholung nach 1,0 s erreicht", result["passed"] and abs(result["value"] - 1.0) < 1e-6, str(result))
    try:
        mf4 = DATA / "aufnahme.mf4"
        export_mf4(mf4, samples, meta)
        mseries = timing_files.read_series(mf4)
        mchannel = next((n for n in mseries if "pannung" in n), None)
        result = timing_files.check_file(mf4, mchannel, {"mode": "band", "min": 11.0, "max": 13.0})
        check("MF4: gleiches Ergebnis wie CSV", mchannel is not None and not result["passed"]
              and "2.00 s" in result["message"], f"{sorted(mseries)} {result}")
    except ImportError:
        print("  (asammdf fehlt -- MF4 uebersprungen)")
    scope = DATA / "c-test.csv"
    lines = ["# scope=picoscope:x model=2204A timestamp=0", "# sample_interval_s=1e-06 trigger_index=0 triggered=True",
             "# A: range_v=5 coupling=DC probe=1 overrange=False", "time_s,A_V,B_V"]
    lines += [f"{i * 1e-6:.9g},{(3.3 if i >= 400 else 0.0):g},0" for i in range(1000)]
    scope.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = timing_files.check_file(scope, "A", {"mode": "reach", "op": ">=", "value": 3.0, "within_s": 0.001})
    check("Oszi-Erfassung: Flanke nach 400 us, Aufloesung 1 us", result["passed"]
          and abs(result["value"] - 0.0004) < 1e-9 and abs(result["resolution_ms"] - 0.001) < 1e-6, str(result))
    try:
        timing_files.check_file(rec_csv, None, {"mode": "band"})
        check("mehrere Kanaele ohne --channel -> Fehler", False)
    except timing_files.TimingFileError as exc:
        check("mehrere Kanaele ohne --channel -> Fehler mit Kanalliste", "Netzteil 1/" in str(exc), str(exc))

    code, text = run_cli("timing", str(rec_csv), "--channel", channel, "--band", "11:13")
    check("main.py timing: Band verletzt -> Exit 1", code == 1 and "VERLETZT" in text, f"{code}: {text[-300:]}")
    out = DATA / "timing.json"
    code, text = run_cli("timing", str(rec_csv), "--channel", channel, "--reach", ">= 11.5", "--within", "1.5",
                         "--from", "2.0", "--json", str(out))
    check("main.py timing: Erholung -> Exit 0, JSON", code == 0 and json.loads(out.read_text(encoding="utf-8"))
          ["passed"], f"{code}: {text[-300:]}")
    code, text = run_cli("timing", str(scope), "--list")
    check("main.py timing --list", code == 0 and "A  (1000 Werte" in text, text[-200:])
    code, text = run_cli("timing", str(rec_csv), "--band", "11:13")
    check("main.py timing: Kanal fehlt -> Exit 3", code == 3 and "--channel" in text, f"{code}: {text[-200:]}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main()
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    section_unit()
    section_runner(app)
    section_editor(app)
    section_dialog(app)
    section_cli()
    section_files()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} Fehler:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

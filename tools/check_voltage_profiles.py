"""Prueft FT-2 schlank (0.62.0, PI-50 b): Rampe im Arbitraersignal, Intervall "Auto" (so schnell wie quittiert,
min. 100 ms), Abstand von Senden zu Senden, Soll gegen Ist und tatsaechliche Abstaende in Bericht und JSON.

- model   -- arb_value fuer die Rampe (Start, Mitte, Ziel, danach Ziel), Geraetebereich, arb_spacing_ms
- runner  -- nachgebildetes Geraet mit einstellbarer Quittungszeit: Auto -> >= 100 ms, langsame Quittung ->
             sofort danach (kein Rueckstau), festes Intervall von Senden zu Senden, Rampe endet genau am Ziel,
             setpoint_sent fuer jeden Sollwert
- results -- setpoint_runs/setpoint_summary: Zahl, Abstaende, Ausfuehrungen getrennt (Schleife)
- report  -- HTML mit "Sollwertverläufe" und Soll/Ist-Diagramm
- dialog  -- Rampe: Start/Ziel statt Offset/Amplitude, ohne Frequenz, Ziel im Geraetebereich (>= 1 V), "Auto"
- cli     -- `main.py run` mit Rampe am simulierten Netzteil: Exit 0, run-result.json mit Sollwerten

Offscreen, Simulation, keine Hardware (no_hardware.block() auch im Kindprozess).
Aufruf:  python tools/check_voltage_profiles.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-profiles-check-"))
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


def ramp_step(start: float, end: float, duration: float, interval_ms: int):
    import testcase_model as tm
    return tm.TestStep(device_kind="psu", device_id="psu:FAKE", action="PSU_ARB", arb_shape="ramp",
                       arb_target="PSU_VOLT", arb_offset=start, arb_amplitude=end, duration=duration,
                       arb_interval_ms=interval_ms)


def section_model() -> None:
    print("model")
    import testcase_model as tm
    step = ramp_step(12.0, 9.0, 3.0, 0)
    values = [round(tm.arb_value(step, t), 3) for t in (0.0, 1.5, 3.0, 5.0)]
    check("Rampe 12 -> 9 V ueber 3 s: Start, Mitte, Ziel, danach Ziel", values == [12.0, 10.5, 9.0, 9.0], str(values))
    up = ramp_step(1.0, 60.0, 1.0, 0)
    check("Rampe aufwaerts bis 60 V", round(tm.arb_value(up, 1.0), 3) == 60.0)
    clamp = ramp_step(5.0, 0.2, 1.0, 0)
    check("Ziel unter 1 V wird auf den Geraetebereich begrenzt", tm.arb_value(clamp, 1.0) == 1.0,
          str(tm.arb_value(clamp, 1.0)))
    check("Auto-Intervall = 100 ms, festes bleibt", tm.arb_spacing_ms(0) == 100 and tm.arb_spacing_ms(250) == 250)
    check("Kurzbeschreibung der Rampe", "12 → 9" in tm.arb_summary("ramp", "Spannung", 12.0, 9.0, 1.0))


class FakeDevice:
    """Quittiert execute_action nach `ack_ms` (wie das Netzteil ueber die serielle Schnittstelle)."""

    def __init__(self, runner, ack_ms: int) -> None:
        from PySide6.QtCore import QTimer
        self.sent: list[tuple[float, float]] = []
        self._timer = QTimer
        self._runner = runner
        self.ack_ms = ack_ms
        runner.execute_action.connect(self._on_action)

    def _on_action(self, _device_id, _kind, _action, value, _channel) -> None:
        self.sent.append((time.perf_counter(), value))   # monotonic hat unter Windows nur 15,6 ms Aufloesung
        self._timer.singleShot(self.ack_ms, lambda: self._runner.on_action_completed(True, "", value))


def run_steps(app, steps, ack_ms: int, timeout: float = 10.0):
    from run_record import RunRecorder
    from testcase_runner import TestRunner
    runner = TestRunner()
    recorder = RunRecorder()
    runner.step_started.connect(recorder.on_step_started)
    runner.setpoint_sent.connect(recorder.on_setpoint_sent)
    runner.run_finished.connect(recorder.on_run_finished)
    runner.step_failed.connect(recorder.on_step_failed)
    device = FakeDevice(runner, ack_ms)
    emitted: list[tuple] = []
    runner.setpoint_sent.connect(lambda *a: emitted.append(a))
    done = {"v": False}
    runner.run_finished.connect(lambda: done.update(v=True))
    runner.step_failed.connect(lambda *_a: done.update(v=True))
    recorder.begin(steps, "profil")
    runner.start(steps)
    end = time.monotonic() + timeout
    while not done["v"] and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.002)
    return device, emitted, recorder.record(), done["v"]


def gaps_ms(sent) -> list[float]:
    return [(b[0] - a[0]) * 1000.0 for a, b in zip(sent, sent[1:])]


def section_runner(app) -> None:
    print("runner")
    device, emitted, record, done = run_steps(app, [ramp_step(12.0, 9.0, 1.0, 0)], ack_ms=20)
    gaps = gaps_ms(device.sent)
    check("Auto, schnelle Quittung: Lauf endet", done and record.outcome == "passed", record.outcome)
    check("... Abstaende >= 100 ms (Senden zu Senden)", gaps and min(gaps) >= 95, str([round(g, 1) for g in gaps]))
    check("... aber nicht viel mehr (kein ack+Intervall)", gaps and max(gaps[:-1]) < 160, str([round(g) for g in gaps]))
    check("... Rampe endet genau am Ziel", device.sent and device.sent[-1][1] == 9.0, str(device.sent[-1:]))
    check("... setpoint_sent fuer jeden Sollwert", len(emitted) == len(device.sent)
          and all(e[2] == "PSU_VOLT" for e in emitted), f"{len(emitted)} / {len(device.sent)}")

    device, _emitted, record, done = run_steps(app, [ramp_step(12.0, 9.0, 1.2, 0)], ack_ms=250)
    gaps = gaps_ms(device.sent)
    check("Auto, langsame Quittung (250 ms): naechster Wert direkt danach", done and gaps
          and all(240 <= g < 330 for g in gaps), str([round(g) for g in gaps]))

    device, _emitted, record, done = run_steps(app, [ramp_step(5.0, 6.0, 1.0, 300)], ack_ms=20)
    gaps = gaps_ms(device.sent)
    check("festes Intervall 300 ms gilt von Senden zu Senden", done and gaps
          and all(280 <= g < 360 for g in gaps[:-1]), str([round(g) for g in gaps]))

    import testcase_model as tm
    loop = [tm.TestStep(step_type="loop", loop_count=2), ramp_step(10.0, 11.0, 0.5, 0), tm.TestStep(step_type="end")]
    _device, _emitted, record, done = run_steps(app, loop, ack_ms=10)
    import run_results
    runs = run_results.setpoint_runs(record)
    check("Schleife: zwei Ausfuehrungen getrennt", done and len(runs) == 1 and len(runs[0]["executions"]) == 2,
          str([(r["index"], len(r["executions"])) for r in runs]))
    check("... Abstaende nur innerhalb einer Ausfuehrung", runs and runs[0]["interval_ms"]["max"] < 200,
          str(runs[0]["interval_ms"] if runs else None))
    summary = run_results.setpoint_summary(record)
    check("setpoint_summary: Schritt, Zahl, Start/Ziel", summary and summary[0]["step"] == 2
          and summary[0]["first"] == 10.0 and summary[0]["last"] == 11.0 and summary[0]["executions"] == 2,
          str(summary))


def section_report(app) -> None:
    print("report")
    import run_report
    from recording import Sample
    _device, _emitted, record, _done = run_steps(app, [ramp_step(12.0, 9.0, 0.6, 0)], ack_ms=10)
    base = record.setpoints[0][0]
    record.device_meta["psu:FAKE"] = ("psu", "Netzteil Test")
    for i in range(8):
        record.samples.append(Sample(base + i * 0.1, "psu:FAKE", "voltage", 12.0 - i * 0.4))
    record.ended_at = time.time()
    path = run_report.write_html_report(record, DATA / "bericht.html")
    text = path.read_text(encoding="utf-8")
    check("Bericht: Abschnitt Sollwertverläufe mit Start -> Ziel", "Sollwertverläufe" in text and "12 → 9" in text)
    check("Bericht: Soll/Ist-Diagramm eingebettet", "Soll gegen Ist" in text and text.count("data:image/png") >= 2)


def section_dialog(app) -> None:
    print("dialog")
    from signal_dialog import SignalDialog
    dialog = SignalDialog("psu", 3.0, {"shape": "ramp", "target": "PSU_VOLT", "offset": 12.0, "amplitude": 9.0,
                                       "frequency": 1.0, "interval_ms": 0, "duty": 0.5})
    dialog.show()
    app.processEvents()
    form = dialog._form
    check("Rampe: Start/Ziel statt Offset/Amplitude", form.labelForField(dialog._offset_spin).text() == "Start:"
          and form.labelForField(dialog._amplitude_spin).text() == "Ziel:")
    check("... ohne Frequenz", not dialog._frequency_spin.isVisible())
    check("... Ziel im Geraetebereich ab 1 V", dialog._amplitude_spin.minimum() == 1.0)
    check("... Intervall 'Auto'", dialog._interval_spin.value() == 0 and dialog._interval_spin.text().startswith("Auto"))
    params = dialog.params()
    check("... Werte bleiben erhalten", params["offset"] == 12.0 and params["amplitude"] == 9.0
          and params["interval_ms"] == 0, str(params))
    dialog._shape_combo.setCurrentIndex(dialog._shape_combo.findData("sine"))
    app.processEvents()
    check("Sinus: Amplitude/Offset und Frequenz wieder da", dialog._frequency_spin.isVisible()
          and form.labelForField(dialog._amplitude_spin).text().startswith("Amplitude"))
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
    case = DATA / "rampe.json"
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=12.0),
                   tm.TestStep(**psu, action="PSU_ARB", arb_shape="ramp", arb_target="PSU_VOLT", arb_offset=12.0,
                               arb_amplitude=9.0, duration=1.0, arb_interval_ms=0),
                   tm.TestStep(**psu, action="PSU_OUT_OFF")], case)
    out = DATA / "out.json"
    code, text = run_cli("run", str(case), "--simulation", "--wait-devices", "20", "--json", str(out))
    check("Kommandozeile: Rampe am simulierten Netzteil, Exit 0", code == 0, f"{code}: {text[-500:]}")
    try:
        result = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {}
    points = result.get("setpoints") or [{}]
    check("run-result.json: Sollwerte des Schritts 2 von 12 bis 9 V mit Abstaenden",
          points[0].get("step") == 2 and points[0].get("first") == 12.0 and points[0].get("last") == 9.0
          and (points[0].get("interval_ms") or {}).get("min", 0) >= 95, str(points)[:300])


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main()
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    section_model()
    section_runner(app)
    section_report(app)
    section_dialog(app)
    section_cli()
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

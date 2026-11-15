"""Prueft FT-10 Messliste und Kachel (0.64.3): Geraet "Pruefling (XCP)" erscheint im Simulationsmodus mit der
Nachbildung, die Kachel startet/stoppt die Abfrage der Messliste, die Werte kommen in Kachel, Uebersicht, Verlauf
und Aufzeichnung an; ein Ablauf mit XCP-Bedingung und Ueberwachen-Block schaltet die Abfrage selbst ein und aus.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_xcp_tile.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-xcptile-check-"))
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


def main() -> int:
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication
    from main_window import XCP_DEVICE_ID, MainWindow
    from settings import Settings
    import testcase_model as tm
    app = QApplication.instance() or QApplication([])

    print("Kachel")
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    spin(app, 8.0, lambda: XCP_DEVICE_ID in window.control_tab._sections
         and XCP_DEVICE_ID in window._online_devices.get("xcp", set()))
    tile = window.control_tab._sections.get(XCP_DEVICE_ID)
    check("Kachel 'Pruefling (XCP)' online mit der Nachbildung", tile is not None
          and XCP_DEVICE_ID in window._online_devices.get("xcp", set()))
    check("Kachel in der Uebersicht", XCP_DEVICE_ID in window.dashboard._panels)
    check("Vorgabe-Messliste der Nachbildung", window._xcp.measure_names()
          == ["Sim.Counter", "Sim.Supply_mV", "Sim.Temperature"], str(window._xcp.measure_names()))
    tile._poll_button.click()
    got = spin(app, 4.0, lambda: "Sim.Supply_mV = 13800 mV" in tile._values_label.text())
    check("Start: Werte in der Kachel", got, tile._values_label.text())
    check("Status 'misst 3 Größen mit 10 Hz'", "misst 3" in tile._status.text(), tile._status.text())
    counters = []
    spin(app, 1.0, lambda: counters.append(window._xcp_last) and False)
    series = window.timeline_tab._series
    check("Verlauf: Reihen am Geraet xcp:dut", any(s.device_id == XCP_DEVICE_ID and s.field == "Sim.Temperature"
                                                    for s in series.values()), str(list(series)[:5]))
    rate_samples = [s for s in series.values() if s.field == "Sim.Counter"]
    n = len(rate_samples[0].data) if rate_samples else 0
    check("etwa 10 Werte pro Sekunde", n >= 10, str(n))
    tile._poll_button.click()
    spin(app, 0.5)
    check("Stopp", not window._xcp.polling() and "Messliste" in tile._status.text(), tile._status.text())

    print("Ablauf mit XCP-Bedingung und Ueberwachen")
    x = dict(cond_device_kind="xcp", cond_device_id="")
    steps = [tm.TestStep(device_kind="xcp", device_id="xcp:dut", action="XCP_READ", params={"name": "Sim.Counter"}),
             tm.TestStep(step_type="monitor", **x, cond_field="Sim.Supply_mV",
                         params={"monitor": {"mode": "band", "min": 13700, "max": 13900}}),
             tm.TestStep(step_type="wait", duration=1.0),
             tm.TestStep(step_type="end"),
             tm.TestStep(step_type="if", cond_source="measurement", **x, cond_field="Sim.Temperature",
                         cond_op=">", cond_value=20.0),
             tm.TestStep(step_type="set_var", var_name="warm", value=1.0),
             tm.TestStep(step_type="end")]
    path = DATA / "xcp_ablauf.json"
    tm.save_steps(steps, path)
    window.testcase_tab._load_path(path)
    polling_seen = {"v": False}
    window._xcp.poll_state.connect(lambda st: polling_seen.update(v=polling_seen["v"] or st.get("active")))
    window._on_run_requested()
    spin(app, 10.0, lambda: not window._test_runner.is_running() and window._run_recorder.record() is not None
         and window._run_recorder.record().outcome != "running")
    record = window._run_recorder.record()
    check("Lauf bestanden (Band 13700–13900 mV, Bedingung Temperatur > 20)", record is not None
          and record.outcome == "passed" and len(record.monitors) == 1 and record.monitors[0]["passed"],
          f"{record.outcome if record else None} {record.monitors if record else None}"[:300])
    check("... Abfrage vom Lauf eingeschaltet und danach wieder aus", polling_seen["v"] and not window._xcp.polling())
    window.close()
    app.processEvents()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

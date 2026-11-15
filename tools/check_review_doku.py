"""Prueft die Code-Punkte aus dem Doku-Review (0.70.0): DR-3 Simulationsmodus auch in der .exe mit Chip
"SIMULATION", DR-4 ALLE AUS setzt microHIL-PWM zurueck, DR-5 Ablauf-Eigenschaft "Am Ende ALLE AUS".

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_review_doku.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-review-doku-check-"))
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


def main() -> int:
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    def spin(seconds: float, until=None) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return
            time.sleep(0.01)

    print("DR-3 -- Simulationsmodus auch in der .exe")
    import paths
    import settings as settings_module
    saved = paths.IS_FROZEN
    paths.IS_FROZEN = True
    try:
        s = settings_module.Settings()
        check("Simulationsmodus gilt auch eingefroren", s.simulation_mode is True)
        s.set_simulation_mode(False)
        check("... und laesst sich ausschalten", s.simulation_mode is False)
        s.set_simulation_mode(True)
    finally:
        paths.IS_FROZEN = saved

    import testcase_model as tm
    from main_window import MainWindow
    from settings import Settings

    window = MainWindow(Settings())
    window.show()
    spin(6, lambda: window._directory.is_online("psu:SIM") and "hil:SIM" in window._worker._hils)
    check("Chip SIMULATION sichtbar", window._sim_chip.isVisibleTo(window) and "SIMULATION" in window._sim_chip.text())
    check("Haekchen in den Einstellungen sichtbar", not window.settings_tab._sim_checkbox.isHidden())

    print("DR-4 -- ALLE AUS setzt PWM zurueck")
    hil = window._worker._hils.get("hil:SIM")
    check("simulierter microHIL da", hil is not None)
    if hil is not None:
        hil.set_pwm(1, 500)
        hil.set_pwm(4, 900)
        window._worker.all_outputs_off("check")
        check("PWM 1..4 nach ALLE AUS auf 0", [hil.get_pwm(ch) for ch in range(1, 5)] == [0, 0, 0, 0],
              str([hil.get_pwm(ch) for ch in range(1, 5)]))

    print("DR-5 -- Am Ende ALLE AUS")
    check("neuer Ablauf: Eigenschaft an", window.testcase_tab.meta().get("all_off_at_end") is True,
          str(window.testcase_tab.meta()))
    from testcase_properties import TestcasePropertiesDialog
    dialog = TestcasePropertiesDialog({"all_off_at_end": True})
    check("Dialog zeigt das Haekchen", dialog._all_off.isChecked())
    dialog._all_off.setChecked(False)
    check("Dialog: aus -> Eigenschaft faellt weg", "all_off_at_end" not in dialog.meta())

    offs: list[str] = []
    window._request_all_off.connect(offs.append)
    steps = [tm.TestStep(device_kind="psu", device_id="psu:SIM", action="PSU_VOLT", value=5.0, duration=0.2)]
    runner = window._test_runner

    def run(meta: dict | None, name: str) -> list[str]:
        path = DATA / f"{name}.json"
        tm.save_steps(steps, path, meta=meta)
        window.testcase_tab._load_path(path)
        offs.clear()
        window._on_run_requested()
        spin(5, lambda: not runner.is_running())
        spin(0.5)
        return list(offs)

    old = run(None, "alt")
    check("alter Ablauf ohne Eigenschaft: kein ALLE AUS am Ende", old == [], str(old))
    new = run({"all_off_at_end": True}, "neu")
    check("mit Eigenschaft: ALLE AUS am Ende", any("Am Ende ALLE AUS" in o for o in new), str(new))
    loaded = tm.load_testcase(DATA / "neu.json")[1]
    check("Eigenschaft wird gespeichert und geladen", loaded.get("all_off_at_end") is True, str(loaded))

    window._safe_stop("check")
    window.close()
    print(f"\n{'ALLES OK' if not FAILURES else f'{len(FAILURES)} FEHLER: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)

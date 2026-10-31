"""Misst die Prozessorlast der GUI im Simulationsmodus (offscreen, ohne Hardware): CPU-Zeit je
Wandsekunde und wie oft/wie lange das Dashboard-Layout neu berechnet wird. Zum Vergleich vor/nach
einer Optimierung; die absoluten Werte hängen vom Rechner ab.

Aufruf:  python tools/bench_gui_load.py [Sekunden] [--compact]   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    seconds = next((float(a) for a in sys.argv[1:] if not a.startswith("--")), 10.0)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    import settings as settings_mod
    import device_registry as registry_mod
    tmp = pathlib.Path(tempfile.mkdtemp())
    settings_mod.SETTINGS_PATH = tmp / "settings.json"
    registry_mod.LABELS_PATH = tmp / "device_labels.json"
    settings_mod.SETTINGS_PATH.write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

    import device_worker as dw
    dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
    dw.HCS34xx.discover_ports = staticmethod(lambda: [])
    dw.MicroHIL.discover = staticmethod(lambda: None)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [])
    dw.picoscope_usb_present = lambda: False

    import dashboard as dashboard_mod
    calls = {"n": 0, "s": 0.0}
    original = dashboard_mod.DashboardWidget._relayout_panels

    def counted(self, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            return original(self, *args, **kwargs)
        finally:
            calls["n"] += 1
            calls["s"] += time.perf_counter() - t0

    dashboard_mod.DashboardWidget._relayout_panels = counted

    from main_window import MainWindow
    window = MainWindow(settings_mod.Settings())
    window.resize(1400, 900)
    window.show()
    if "--compact" in sys.argv:
        window.dashboard.set_compact(True)

    def spin(duration: float) -> None:
        end = time.monotonic() + duration
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.002)

    spin(4.0)   # Geraete verbinden, einschwingen
    calls["n"], calls["s"] = 0, 0.0
    cpu0, wall0 = time.process_time(), time.perf_counter()
    spin(seconds)
    cpu, wall = time.process_time() - cpu0, time.perf_counter() - wall0
    print(f"CPU-Zeit:   {cpu:6.2f} s in {wall:5.1f} s Wand  ->  {100 * cpu / wall:5.1f} % eines Kerns")
    print(f"Dashboard._relayout_panels: {calls['n']:5d} Aufrufe, {calls['s']:6.2f} s gesamt "
          f"({calls['n'] / wall:5.1f} /s, {1000 * calls['s'] / max(1, calls['n']):6.2f} ms je Aufruf)")
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

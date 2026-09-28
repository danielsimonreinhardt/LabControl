"""Prueft das Abdocken des Dashboards in ein eigenes Fenster (0.19.0).

  - Knopf unten rechts im Dashboard: Dashboard wandert in ein eigenes Fenster,
    das Hauptfenster zeigt es nicht mehr.
  - Fenster schliessen (Kreuz oder derselbe Knopf): Dashboard wieder im
    Hauptfenster, an alter Stelle unter dem Sicherheitsbanner.
  - Position/Groesse des Fensters und der Zustand "abgedockt" ueberleben einen
    Neustart; Beenden der App schliesst das Fenster mit.

Startet die komplette App im Simulationsmodus (offscreen) mit umgelenkter
settings.json/device_labels.json -- der echte Stand des Nutzers bleibt
unberuehrt, echte Geraete werden nicht gesucht.

Aufruf:  python tools/check_dashboard_detach.py [--shots VERZEICHNIS]   (aus dem Repo-Stamm)
         --shots legt Bildschirmfotos beider Fenster ab (zum Ansehen).
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
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    shots = None
    if "--shots" in sys.argv:
        shots = pathlib.Path(sys.argv[sys.argv.index("--shots") + 1])
        shots.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    import settings as settings_mod
    import device_registry as registry_mod
    tmp = pathlib.Path(tempfile.mkdtemp())
    settings_mod.SETTINGS_PATH = tmp / "settings.json"
    registry_mod.LABELS_PATH = tmp / "device_labels.json"
    settings_mod.SETTINGS_PATH.write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

    # Keine echten Geraete suchen (an ihnen haengt womoeglich ein Aufbau).
    import device_worker as dw
    dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
    dw.HCS34xx.discover_ports = staticmethod(lambda: [])
    dw.MicroHIL.discover = staticmethod(lambda: None)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [])
    dw.picoscope_usb_present = lambda: False

    from dashboard_window import DashboardWindow
    from main_window import MainWindow

    def wait_for(predicate, seconds=10.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def dash_windows():
        return [w for w in QApplication.topLevelWidgets() if isinstance(w, DashboardWindow) and w.isVisible()]

    def docked(window) -> bool:
        layout = window._central_layout
        return (layout.indexOf(window.dashboard) == layout.indexOf(window._safety_banner) + 1
                and window.dashboard.window() is window and window.dashboard.isVisible())

    def shot(widget, name):
        if shots is not None:
            widget.grab().save(str(shots / f"{name}.png"))

    window = MainWindow(settings_mod.Settings())
    window.show()
    button = window.dashboard._detach_button

    print("Ausgangslage")
    check("simuliertes Netzteil verbunden", wait_for(lambda: "psu:SIM" in window.dashboard._panels))
    check("Dashboard im Hauptfenster, kein eigenes Fenster", docked(window) and not dash_windows())
    check("Knopf-Tooltip: abdocken", "eigenem Fenster" in button.toolTip(), button.toolTip())
    shot(window, "1_angedockt")

    print("Abdocken per Knopf")
    button.click()
    app.processEvents()
    wins = dash_windows()
    check("eigenes Fenster offen", len(wins) == 1)
    dash_win = wins[0] if wins else None
    check("Dashboard sitzt im neuen Fenster", dash_win is not None and window.dashboard.window() is dash_win)
    check("Hauptfenster zeigt es nicht mehr", window._central_layout.indexOf(window.dashboard) == -1)
    check("Reiter bleiben im Hauptfenster", window.tabs.window() is window and window.tabs.isVisible())
    check("Fenstertitel nennt das Dashboard", dash_win is not None and "Dashboard" in dash_win.windowTitle())
    check("Knopf-Tooltip: zurueckholen", "Hauptfenster" in button.toolTip(), button.toolTip())
    check("Zustand gespeichert", settings_mod.Settings().dashboard_detached is True)
    panel = window.dashboard._panels.get("psu:SIM")
    check("Kachel sichtbar und bekommt weiter Werte",
          panel is not None and panel.isVisible()
          and wait_for(lambda: any(v.text() not in ("", "--") for v in panel._value_labels.values()), 5))
    check("zweiter Klick auf Abdocken im Hauptfenster-Zustand oeffnet kein zweites Fenster",
          (window._detach_dashboard(), len(dash_windows()) == 1)[1])
    wait_for(lambda: False, 0.5)  # Layouts der Reiter nach der Hoehenaenderung setzen lassen
    shot(window, "2_hauptfenster_ohne_dashboard")
    if dash_win is not None:
        shot(dash_win, "3_dashboard_fenster")

    print("Fenster schliessen -> zurueck ins Hauptfenster")
    target_width = app.primaryScreen().availableGeometry().width() - 100
    if dash_win is not None:
        # Lage innerhalb des (offscreen nur 800 px breiten) Bildschirms, sonst
        # rueckt restoreGeometry das Fenster zu Recht wieder herein.
        dash_win.move(QPoint(20, 30))
        dash_win.resize(target_width, dash_win.height() + 50)
        app.processEvents()
        dash_win.close()
    app.processEvents()
    check("Dashboard wieder im Hauptfenster (unter dem Banner)", docked(window))
    check("kein Dashboard-Fenster mehr", not dash_windows())
    check("Zustand gespeichert", settings_mod.Settings().dashboard_detached is False)
    check("Fensterlage gespeichert", settings_mod.Settings().dashboard_window_geometry != "")
    check("Knopf-Tooltip wieder: abdocken", "eigenem Fenster" in button.toolTip())

    print("Erneut abdocken -> alte Lage, Zurueckholen per Knopf")
    button.click()
    app.processEvents()
    wins = dash_windows()
    check("Fenster an alter Position und Breite",
          len(wins) == 1 and wins[0].pos() == QPoint(20, 30) and wins[0].width() == target_width,
          f"{wins[0].pos() if wins else None} {wins[0].width() if wins else None}")
    button.click()
    app.processEvents()
    check("Knopf im Fenster holt es zurueck", docked(window) and not dash_windows())

    print("Neustart im abgedockten Zustand")
    button.click()
    app.processEvents()
    window.close()
    app.processEvents()
    check("Beenden schliesst das Dashboard-Fenster mit", not dash_windows())
    check("Zustand 'abgedockt' bleibt fuer den naechsten Start", settings_mod.Settings().dashboard_detached is True)
    window = MainWindow(settings_mod.Settings())
    window.show()
    check("nach dem Start wieder im eigenen Fenster",
          wait_for(lambda: len(dash_windows()) == 1 and window._central_layout.indexOf(window.dashboard) == -1, 3))
    wins = dash_windows()
    check("an alter Position", bool(wins) and wins[0].pos() == QPoint(20, 30), str(wins[0].pos() if wins else None))
    window.close()
    app.processEvents()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER:")
        for name in FAILURES:
            print("  - " + name)
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

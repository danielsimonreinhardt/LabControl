"""Einstellung "Nicht verbundene Geräte ausblenden" (1.0.3): Übersicht und Einstellungen.

- aus (Standard): getrennte Kacheln bleiben sichtbar und ausgegraut wie bisher
- an: getrennte Kacheln verschwinden, verbundene rücken auf; wieder verbunden -> Kachel kommt an ihrer Stelle zurück
- Verschieben sichtbarer Kacheln behält den Platz ausgeblendeter in der gespeicherten Reihenfolge
- Einstellung wird gespeichert und beim Start angewendet (MainWindow im Simulationsmodus)

Nur Oberfläche (offscreen), keine Hardware.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def section_dashboard(app) -> None:
    from dashboard import DashboardWidget
    print("dashboard -- Übersicht direkt")
    dash = DashboardWidget()
    dash.resize(1400, 700)
    dash.show()
    for kind, device_id, label in (("psu", "psu:A", "NT A"), ("load", "load:B", "Last B"), ("psu", "psu:C", "NT C")):
        dash.on_device_known(kind, device_id, label)
    dash.set_panel_order(["psu:A", "load:B", "psu:C"])
    app.processEvents()
    visible = lambda d: dash._panels[d].isVisible()  # noqa: E731
    check("alle verbunden: drei sichtbar", all(visible(d) for d in ("psu:A", "load:B", "psu:C")))

    dash.set_psu_online("psu:A", False)
    app.processEvents()
    check("Einstellung aus: getrennte Kachel bleibt sichtbar", visible("psu:A"))
    check("Einstellung aus: Reihenfolge mit getrennter", dash._current_order() == ["psu:A", "load:B", "psu:C"],
          str(dash._current_order()))

    dash.set_hide_offline(True)
    app.processEvents()
    check("Einstellung an: getrennte Kachel ausgeblendet", not visible("psu:A"))
    check("Einstellung an: übrige sichtbar", visible("load:B") and visible("psu:C"))
    check("Einstellung an: Anordnung ohne getrennte", dash._current_order() == ["load:B", "psu:C"],
          str(dash._current_order()))

    dash._persist_panel_order(["psu:C", "load:B"])   # wie nach einem Verschieben der sichtbaren Kacheln
    check("Verschieben behält Platz der ausgeblendeten", dash._pending_panel_order == ["psu:A", "psu:C", "load:B"],
          str(dash._pending_panel_order))

    dash.set_psu_online("psu:A", True)
    app.processEvents()
    check("wieder verbunden: sichtbar", visible("psu:A"))
    check("wieder verbunden: an alter Stelle", dash._current_order() == ["psu:A", "psu:C", "load:B"],
          str(dash._current_order()))

    dash.set_psu_online("psu:C", False)
    dash.set_hide_offline(False)
    app.processEvents()
    check("Einstellung wieder aus: getrennte wieder sichtbar (ausgegraut)", visible("psu:C"))
    dash.close()


def section_main_window(app) -> None:
    print("main -- Einstellung speichern und beim Start anwenden")
    import settings as settings_mod
    import device_registry as registry_mod
    tmp = pathlib.Path(tempfile.mkdtemp())
    settings_mod.SETTINGS_PATH = tmp / "settings.json"
    registry_mod.LABELS_PATH = tmp / "device_labels.json"
    settings_mod.SETTINGS_PATH.write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

    import no_hardware
    no_hardware.block()
    import device_worker as dw
    from main_window import MainWindow

    window = MainWindow()
    try:
        app.processEvents()
        check("Standard: Häkchen aus", not window.settings_tab._hide_offline_checkbox.isChecked())
        check("Standard: Übersicht zeigt getrennte", not window.dashboard._hide_offline)
        window.settings_tab._hide_offline_checkbox.setChecked(True)
        app.processEvents()
        saved = json.loads(settings_mod.SETTINGS_PATH.read_text(encoding="utf-8"))
        check("Häkchen gespeichert", saved.get("hide_offline_tiles") is True, str(saved.get("hide_offline_tiles")))
        check("Übersicht folgt dem Häkchen", window.dashboard._hide_offline)
        psu_id = getattr(dw, "SIM_PSU_ID", None)
        if psu_id and psu_id in window.dashboard._panels:
            window._on_psu_connected(psu_id, False)
            app.processEvents()
            check("simuliertes Netzteil getrennt: ausgeblendet", window.dashboard._panels[psu_id].isHidden())
            window._on_psu_connected(psu_id, True)
            app.processEvents()
            check("wieder verbunden: sichtbar", not window.dashboard._panels[psu_id].isHidden())
    finally:
        window.close()
        app.processEvents()

    window2 = MainWindow()
    try:
        app.processEvents()
        check("Neustart: Häkchen gesetzt", window2.settings_tab._hide_offline_checkbox.isChecked())
        check("Neustart: Übersicht blendet aus", window2.dashboard._hide_offline)
    finally:
        window2.close()
        app.processEvents()


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    sections = sys.argv[1:] or ["dashboard", "main"]
    if "dashboard" in sections:
        section_dashboard(app)
    if "main" in sections:
        section_main_window(app)
    print()
    if FAILURES:
        print("FEHLGESCHLAGEN:", FAILURES)
        return 1
    print("Alles ok. (nur Oberfläche, keine Hardware)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

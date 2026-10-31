"""Prueft, dass das Dashboard sein Layout nur noch bei echten Aenderungen neu berechnet
(DashboardWidget._layout_is_current), bei echten Aenderungen aber weiterhin: eine Kachel wird
breiter/hoeher, die Reihenfolge aendert sich, die Ansicht wechselt. Offscreen, Simulationsmodus.

Aufruf:  python tools/check_dashboard_relayout.py   (aus dem Repo-Stamm)
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

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    from PySide6.QtCore import QEvent, QSize
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
    packs = {"n": 0}
    original_pack = dashboard_mod.pack_tiles_by_row

    def counted_pack(*args, **kwargs):
        packs["n"] += 1
        return original_pack(*args, **kwargs)

    dashboard_mod.pack_tiles_by_row = counted_pack

    from main_window import MainWindow
    window = MainWindow(settings_mod.Settings())
    window.resize(1400, 900)
    window.show()
    dash = window.dashboard

    def spin(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    def layout_event() -> None:
        QApplication.sendEvent(dash._container, QEvent(QEvent.Type.LayoutRequest))

    def geometry() -> dict[str, tuple[int, int, int, int]]:
        return {d: (p.x(), p.y(), p.width(), p.height()) for d, p in dash._panels.items()}

    def overlaps() -> list:
        placed = [p for p in dash._panels.values() if p.isVisible()]
        return [(a.title(), b.title()) for i, a in enumerate(placed) for b in placed[i + 1:]
                if a.geometry().intersects(b.geometry())]

    spin(4.0)
    check("simulierte Geraete verbunden", len(dash._panels) >= 4, str(list(dash._panels)))

    for compact in (False, True):
        name = "Kompaktansicht" if compact else "Normalansicht"
        print(name)
        dash.set_compact(compact)
        spin(1.0)
        check("keine Kacheln ueberlappen sich", not overlaps(), str(overlaps()))

        before_geometry = geometry()
        packs["n"] = 0
        spin(3.0)
        # Ein einzelner Vorgang ist erlaubt (die Ratsche darf bei einem neuen Spitzenwert wachsen);
        # vorher waren es rund 150 je Sekunde.
        check("Dauerbetrieb (Messwerte laufen ein): keine Dauerschleife von Neuanordnungen", packs["n"] <= 2,
              f"{packs['n']} Packvorgaenge in 3 s")
        check("Geometrie unveraendert", geometry() == before_geometry)

        # Auf einen ruhigen Moment warten: ein gerade eingelaufener, breiterer Messwert darf die
        # Ratsche legitim wachsen lassen (dann ist die Anordnung noch nicht "aktuell").
        settle_end = time.monotonic() + 3.0
        while time.monotonic() < settle_end and not dash._layout_is_current(dash._current_order()):
            app.processEvents()
        before_geometry = geometry()
        packs["n"] = 0
        layout_event()
        check("LayoutRequest ohne Aenderung rechnet nichts neu", packs["n"] == 0, f"{packs['n']}")
        check("... und aendert nichts", geometry() == before_geometry)

        # Eine Kachel wird breiter (z.B. laengerer Text): Ratsche waechst, Layout wird neu berechnet.
        victim_id = next(iter(dash._panels))
        victim = dash._panels[victim_id]
        old_hint = victim.sizeHint
        widest = max(dash._panel_width, victim.sizeHint().width()) if not compact else victim.sizeHint().width()
        grown = QSize(widest + 90, victim.sizeHint().height())
        victim.sizeHint = lambda: grown
        try:
            packs["n"] = 0
            layout_event()
            spin(0.2)
            check("breitere Kachel: Layout wird neu berechnet", packs["n"] >= 1, f"{packs['n']}")
            widths = {p.width() for p in dash._panels.values()} if not compact else {victim.width()}
            check("breitere Kachel: Breite folgt dem neuen sizeHint",
                  all(w >= grown.width() for w in widths), f"{sorted(widths)} < {grown.width()}")
        finally:
            victim.sizeHint = old_hint

        # Eine Kachel wird hoeher: Hoehen-Ratsche waechst.
        tall = QSize(victim.sizeHint().width(), victim.height() + 120)
        victim.sizeHint = lambda: tall
        try:
            packs["n"] = 0
            layout_event()
            spin(0.2)
            check("hoehere Kachel: Layout wird neu berechnet", packs["n"] >= 1, f"{packs['n']}")
        finally:
            victim.sizeHint = old_hint

        # Reihenfolge aendern (Drag & Drop legt sie ueber _persist_panel_order ab).
        order = dash._current_order()
        reversed_order = list(reversed(order))
        packs["n"] = 0
        dash._persist_panel_order(reversed_order)
        spin(0.3)
        check("neue Reihenfolge: Layout wird neu berechnet", packs["n"] >= 1, f"{packs['n']}")
        check("neue Reihenfolge wird uebernommen", dash._current_order() == reversed_order)
        xs = [dash._panels[d].x() for d in dash._current_order()]
        check("Kacheln liegen in neuer Reihenfolge (x aufsteigend je Spalte)", xs[0] <= xs[-1], str(xs))
        check("keine Kacheln ueberlappen sich (nach Umordnen)", not overlaps(), str(overlaps()))
        dash._persist_panel_order(order)
        spin(0.3)

    print("Ansichtswechsel")
    dash.set_compact(False)
    spin(0.5)
    normal = geometry()
    dash.set_compact(True)
    spin(0.5)
    compact = geometry()
    check("Kompaktansicht hat andere Breiten als die Normalansicht", normal != compact)
    dash.set_compact(False)
    spin(0.5)
    check("Rueckwechsel stellt die Normalansicht wieder her", geometry() == normal)

    window.close()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

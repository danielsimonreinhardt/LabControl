"""Prueft "Anordnen" mit herausgezoomter Uebersicht (lab_gui/arrange_overlay.py, 0.51.0):

Nutzerfeedback 12.11.2026: im Modus "Anordnen" liess sich nicht wischen, Kacheln ausserhalb des sichtbaren
Bereichs waren nicht erreichbar.
- Anordnen an (Touch): ueber Uebersicht und Steuerung liegt ein Abbild ALLER Kacheln, ganz sichtbar
- Kachel im Abbild greifen und auf eine andere ziehen: Reihenfolge (Uebersicht) bzw. Zelle (Steuerung)
  aendert sich und wird gespeichert
- Tipp ohne Bewegung aendert nichts
- Anordnen aus: Abbild weg

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_arrange_overlay.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-arrange-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True, "touch_mode": True}), encoding="utf-8")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def pump(app, n: int = 20) -> None:
    for _ in range(n):
        app.processEvents()
        QTest.qWait(10)


def drag(overlay, start: QPointF, end: QPointF) -> None:
    a, b = start.toPoint(), end.toPoint()
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    for i in range(1, 6):
        QTest.mouseMove(overlay, a + (b - a) * i / 5)
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    import no_hardware
    no_hardware.block()
    from arrange_overlay import ArrangeOverlay
    from i18n import Translator
    from main_window import MainWindow
    from settings import Settings

    settings = Settings()
    Translator.instance().set_language(settings.language)
    w = MainWindow(settings)
    w.resize(1024, 600)
    w.show()
    QTest.qWait(2000)
    pump(app)

    print("Anordnen an")
    w._top_bar._arrange_button.setChecked(True)
    w.tabs.setCurrentWidget(w._overview_page)
    pump(app)
    overlays = [o for o in w.findChildren(ArrangeOverlay) if o.isVisible()]
    check("Abbild ueber Uebersicht und Steuerung", len(w._arrange_overlays) == 2, str(len(w._arrange_overlays)))
    dash_overlay = next(o for o in w._arrange_overlays if o.parent() is w.dashboard.scroll_area())
    dash_overlay.refresh()
    rects = dash_overlay._rects
    check("Abbild zeigt alle sichtbaren Kacheln der Uebersicht", len(rects) >= 3
          and set(rects) == {d for d, p in w.dashboard._panels.items() if p.isVisible()}, str(sorted(rects)))
    inside = all(dash_overlay.rect().contains(dash_overlay._to_overlay(r).toAlignedRect()) for r in rects.values())
    check("alle Kacheln ganz im Bild (herausgezoomt)", inside and dash_overlay.scale() <= 1.0,
          f"Massstab {dash_overlay.scale():.2f}")
    check("Abbild ist sichtbar", dash_overlay in overlays)

    order_before = w.dashboard._current_order()
    first, last = order_before[0], [d for d in order_before if d in rects][-1]
    start = dash_overlay._to_overlay(rects[first]).center()
    QTest.mousePress(dash_overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start.toPoint())
    QTest.mouseRelease(dash_overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start.toPoint())
    pump(app)
    check("Tipp ohne Bewegung aendert nichts", w.dashboard._current_order() == order_before)

    target = dash_overlay._to_overlay(rects[last])
    drag(dash_overlay, start, QPointF(target.right() - 2, target.center().y()))
    pump(app)
    order_after = w.dashboard._current_order()
    check("Uebersicht: gezogene Kachel steht jetzt hinten", order_after.index(first) > order_after.index(last),
          f"{order_before} -> {order_after}")
    saved = json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("panel_order", [])
    check("Reihenfolge gespeichert", [d for d in saved if d in order_after] == order_after, str(saved))

    print("Steuerung")
    w.tabs.setCurrentWidget(w.control_tab)
    pump(app)
    ctrl_overlay = next(o for o in w._arrange_overlays if o.parent() is w.control_tab.scroll_area())
    ctrl_overlay.refresh()
    crects = ctrl_overlay._rects
    check("Abbild zeigt alle Kacheln der Steuerung", set(crects) == {d for d, s in w.control_tab._sections.items()
                                                                      if s.isVisible()}, str(sorted(crects)))
    inside = all(ctrl_overlay.rect().contains(ctrl_overlay._to_overlay(r).toAlignedRect()) for r in crects.values())
    check("Steuerung: alle Kacheln ganz im Bild", inside, f"Massstab {ctrl_overlay.scale():.2f}")
    cells_before = dict(w.control_tab._positions(w.control_tab._placed_ids()))
    ids = sorted(crects, key=lambda d: (crects[d].y(), crects[d].x()))
    a, b = ids[0], ids[-1]
    drag(ctrl_overlay, ctrl_overlay._to_overlay(crects[a]).topLeft() + QPointF(6, 6),
         ctrl_overlay._to_overlay(crects[b]).topLeft() + QPointF(6, 6))
    pump(app)
    cells_after = dict(w.control_tab._positions(w.control_tab._placed_ids()))
    check("Steuerung: Kachel hat die Zelle gewechselt", cells_after.get(a) == cells_before.get(b),
          f"{a}: {cells_before.get(a)} -> {cells_after.get(a)}, Ziel {cells_before.get(b)}")

    print("Anordnen aus")
    w._top_bar._arrange_button.setChecked(False)
    pump(app)
    check("Abbild weg", not w._arrange_overlays and not [o for o in w.findChildren(ArrangeOverlay) if o.isVisible()])

    w.close()
    pump(app)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

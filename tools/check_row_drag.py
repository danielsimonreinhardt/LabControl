"""Prueft das Verschieben von Ablaufzeilen mit der Maus (1.0.1): an der Spalte "#" greifen, ziehen, loslassen.

- einfache Zeile nach unten / ganz nach oben
- Baustein (Kopfzeile) wandert als Ganzes, Verknuepfung bleibt
- eine Zeile landet nie mitten in einem fremden Baustein
- Klick ohne Ziehen und kurzes Zucken verschieben nichts; Klick auf "#" der Kopfzeile klappt weiter ein/aus
- Ziehen ausserhalb von "#" verschiebt nichts

Offscreen, ohne Hardware.  Aufruf:  python tools/check_row_drag.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="lc-rowdrag-"))
    try:
        return run(data)
    finally:
        shutil.rmtree(data, ignore_errors=True)


def run(data: Path) -> int:
    import no_hardware
    no_hardware.block()
    os.environ["LABCONTROL_DATA_DIR"] = str(data)
    os.environ.pop("LABCONTROL_NETWORK_DIR", None)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import data_store
    import testcase_model as tm
    import testcase_tab as tt

    blocks = data_store.target_dir("blocks")
    block_file = blocks / "Paar.json"
    tm.save_block([tm.TestStep(step_type="wait", duration=10.0),
                   tm.TestStep(step_type="wait", duration=11.0)], "Paar", block_file)
    block_steps = [tm.TestStep(step_type="wait", duration=10.0), tm.TestStep(step_type="wait", duration=11.0)]
    block_steps[0].link = {"block": "Paar.json", "name": "Paar", "count": 2}
    case = data_store.target_dir("testcases") / "Ziehen.json"
    tm.save_steps([tm.TestStep(step_type="wait", duration=1.0), tm.TestStep(step_type="wait", duration=2.0),
                   *block_steps,
                   tm.TestStep(step_type="wait", duration=3.0), tm.TestStep(step_type="wait", duration=4.0)], case)

    tab = tt.TestcaseTab()
    tab.resize(1000, 700)
    tab.show()
    check("Ablauf geladen", tab._load_path(case))
    app.processEvents()
    table = tab._table
    viewport = table.viewport()

    def order() -> list[float]:
        return [s.duration for s in tab.steps()]

    def groups() -> list[tuple[int, int, str]]:
        return sorted((g.start, g.count, g.link) for g in tab._block_groups)

    def point(row: int, col: int = tt.COL_NUM, where: str = "center") -> QPoint:
        rect = table.visualRect(table.model().index(row, col))
        y = {"center": rect.center().y(), "top": rect.top() + 2, "bottom": rect.bottom() - 2}[where]
        return QPoint(rect.center().x(), y)

    def drag(start: QPoint, end: QPoint, steps: int = 6) -> None:
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        for i in range(1, steps + 1):
            p = start + (end - start) * i / steps
            QTest.mouseMove(viewport, p)
            app.processEvents()
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        app.processEvents()

    check("Ausgang: 1 2 [10 11] 3 4", order() == [1, 2, 10, 11, 3, 4], str(order()))
    check("Kopf-Tooltip an '#'", bool(table.horizontalHeaderItem(tt.COL_NUM).toolTip()))

    drag(point(0), point(1, where="bottom"))
    check("Zeile 1 hinter Zeile 2 gezogen", order() == [2, 1, 10, 11, 3, 4], str(order()))
    check("... Baustein unveraendert", groups() == [(2, 2, "Paar.json")], str(groups()))
    check("... gezogene Zeile ist markiert", tab._selected_row == 1, str(tab._selected_row))

    drag(point(5), point(0, where="top"))
    check("letzte Zeile ganz nach oben", order() == [4, 2, 1, 10, 11, 3], str(order()))
    check("... Baustein rueckt nach", groups() == [(3, 2, "Paar.json")], str(groups()))

    drag(point(3), point(0, where="top"))
    check("Baustein als Ganzes nach oben", order() == [10, 11, 4, 2, 1, 3], str(order()))
    check("... Verknuepfung und Groesse bleiben", groups() == [(0, 2, "Paar.json")], str(groups()))

    block = tab._block_groups[0]
    if block.collapsed:
        tab._toggle_group(block)
        app.processEvents()
    check("Baustein aufgeklappt", not table.isRowHidden(1))
    drag(point(5), point(1, where="top"))
    check("Zeile auf Bausteinmitte: landet nicht im Baustein",
          groups() in ([(0, 2, "Paar.json")], [(1, 2, "Paar.json")]), str(groups()))
    check("... sondern davor oder dahinter", order() in ([3, 10, 11, 4, 2, 1], [10, 11, 3, 4, 2, 1]), str(order()))

    before = order()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(4))
    app.processEvents()
    drag(point(4), point(4) + QPoint(0, 2), steps=1)
    check("Klick und kurzes Zucken verschieben nichts", order() == before, str(order()))

    header = next(g for g in tab._block_groups).start
    collapsed = next(g for g in tab._block_groups).collapsed
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(header))
    app.processEvents()
    check("Klick auf '#' der Kopfzeile klappt weiter um", next(g for g in tab._block_groups).collapsed != collapsed)

    before = order()
    last = table.rowCount() - 1
    drag(point(last, col=tt.COL_ENABLED), point(0, col=tt.COL_ENABLED, where="top"))
    check("Ziehen ausserhalb von '#' verschiebt nichts", order() == before, str(order()))

    gap = tab._row_drag.gap_at(-5)
    check("Luecke oberhalb der Tabelle = 0", gap == 0, str(gap))
    check("keine Einfuegelinie nach dem Loslassen", not tab._row_drag._line.isVisible())

    tab.close()
    print(f"\n{'Alles ok.' if not FAILURES else f'{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)

"""Prueft den Hinweis "Noch kein Testablauf" im Testablauf-Editor am PC (PI-22 a, 0.43.1):

- beim Start sichtbar, mit Ordner der Ablaeufe und den Knoepfen "Ablauf oeffnen …"/"Neuen Ablauf beginnen"
- "Neuen Ablauf beginnen" blendet ihn aus und setzt den Fokus in die erste Zeile
- Laden eines Ablaufs blendet ihn aus
- Fokus in die Tabelle (Schritt bearbeiten) blendet ihn aus
- Zeile hinzufuegen ueber den Knopf blendet ihn aus
- Englisch uebersetzt

Offscreen, keine Hardware.  Aufruf:  python tools/check_testcase_hint.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-hint-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from i18n import Translator
    from testcase_model import TestStep, save_steps
    from testcase_tab import COL_DEVICE, TestcaseTab

    tabs: list[TestcaseTab] = []

    def new_tab() -> TestcaseTab:
        tab = TestcaseTab()
        tab.resize(1000, 560)
        tab.show()
        app.processEvents()
        tabs.append(tab)
        return tab

    print("Startzustand")
    tab = new_tab()
    check("Hinweis sichtbar", tab.hint_visible() and tab._hint_box.isVisible())
    check("Titel und Ordner", tab._hint_title.text() == "Noch kein Testablauf"
          and "testcases" in tab._hint_text.text(), tab._hint_text.text())
    check("Knoepfe Oeffnen und Neu", tab._hint_open.text().startswith("Ablauf öffnen")
          and tab._hint_new.text() == "Neuen Ablauf beginnen")
    check("Tabelle hat die leere Startzeile", tab._table.rowCount() == 1)
    check("Hinweis steht ueber der Tabelle", tab._hint_box.geometry().bottom() < tab._table.geometry().top(),
          f"{tab._hint_box.geometry()} / {tab._table.geometry()}")

    print("Neuen Ablauf beginnen")
    tab._hint_new.click()
    app.processEvents()
    check("Hinweis weg", not tab.hint_visible() and not tab._hint_box.isVisible())
    focus = QApplication.focusWidget()
    device = tab._table.cellWidget(0, COL_DEVICE)
    check("Fokus in der ersten Zeile", focus is not None and (focus is device or device.isAncestorOf(focus)),
          repr(focus))

    print("Laden")
    folder = DATA / "testcases"
    folder.mkdir(exist_ok=True)
    path = folder / "Ablauf.json"
    save_steps([TestStep(step_type="wait", duration=0.5)], path)
    tab = new_tab()
    check("neuer Reiter: Hinweis sichtbar", tab.hint_visible())
    check("Laden klappt", tab._load_path(path))
    app.processEvents()
    check("nach Laden Hinweis weg", not tab.hint_visible() and not tab._hint_box.isVisible())

    print("Bearbeiten")
    tab = new_tab()
    tab._table.cellWidget(0, COL_DEVICE).setFocus()
    app.processEvents()
    check("Fokus in die Tabelle blendet aus", not tab.hint_visible())
    tab = new_tab()
    tab._add_button.click()
    app.processEvents()
    check("Zeile hinzufuegen blendet aus", not tab.hint_visible() and tab._table.rowCount() == 2)
    tab = new_tab()
    tab._hint_open.setFocus()
    app.processEvents()
    check("Fokus auf den Hinweis-Knopf blendet nicht aus", tab.hint_visible())

    print("Englisch")
    Translator.instance().set_language("en")
    app.processEvents()
    check("Titel englisch", tab._hint_title.text() == "No test sequence yet", tab._hint_title.text())
    check("Knopf englisch", tab._hint_new.text() == "Start a new sequence", tab._hint_new.text())
    Translator.instance().set_language("de")

    for t in tabs:
        t.close()
        t.deleteLater()
    app.processEvents()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

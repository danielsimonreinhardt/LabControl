"""Prueft die Ausfuehren-Ansicht des Testablauf-Reiters (lab_gui/touch_run_view.py, Phase 4 Schritt 3, PI-7 a):

- ohne Touch-Profil Editor, mit Touch-Profil Ausfuehren-Ansicht (und zurueck)
- Liste der Ablaeufe aus dem gemeinsamen Ordner (data_store), leer mit Hinweis
- Antippen laedt den Ablauf (auch in den Editor), Schritte als lesbare Zeilen, Start erst mit Ablauf
- Start -> run_requested; Fortschritt, aktueller Schritt, Status; waehrend des Laufs keine andere Datei
- Schrittfehler: Status rot, Stop quittiert (stop_requested), danach wieder startbereit

Offscreen, keine Hardware.  Aufruf:  python tools/check_touch_run.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-run-check-"))
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
    from testcase_model import TestStep, save_steps
    from testcase_tab import TestcaseTab
    from theme import ThemeManager

    tab = TestcaseTab()
    tab.resize(880, 520)
    tab.show()
    runs: list[int] = []
    stops: list[int] = []
    tab.run_requested.connect(lambda: runs.append(1))
    tab.stop_requested.connect(lambda: stops.append(1))
    rv = tab._run_view

    print("Umschalten")
    check("ohne Touch: Editor", tab._page_stack.currentWidget() is tab._editor_page)
    ThemeManager.instance().set_touch(True)
    app.processEvents()
    check("mit Touch: Ausfuehren-Ansicht", tab._page_stack.currentWidget() is rv)
    # Leerer Zustand (GUI-Review UX-7): Hinweis mittig statt leerer Liste, keine leeren Schritt-Rahmen
    check("leerer Ordner: Hinweis mittig mit Ordner und Neu einlesen",
          rv._files.count() == 0 and not rv._empty_box.isHidden() and rv._files.isHidden()
          and "testcases" in rv._empty_text.text().replace("​", "") and not rv._empty_refresh.isHidden(),
          rv._empty_text.text())
    check("ohne Ablauf keine leeren Rahmen (Schritte, Fortschritt, Status)",
          rv._steps.isHidden() and rv._progress.isHidden() and rv._status_label.isHidden())
    check("ohne Ablauf kein Start", not rv._start_button.isEnabled())

    print("Liste und Laden")
    folder = DATA / "testcases"
    folder.mkdir(exist_ok=True)
    save_steps([TestStep(step_type="wait", duration=0.5), TestStep(step_type="loop", loop_count=3),
                TestStep(device_kind="psu", action="PSU_VOLT", value=12.0, duration=1.0),
                TestStep(step_type="end")], folder / "B Ablauf.json")
    save_steps([TestStep(step_type="wait", duration=0.1)], folder / "a erster.json")
    rv.refresh_requested.emit()
    names = [rv._files.item(i).text() for i in range(rv._files.count())]
    check("Ablaeufe sortiert", names == ["a erster", "B Ablauf"], str(names))
    rv._on_file_clicked(rv._files.item(1))
    app.processEvents()
    lines = [rv._steps.item(i).text() for i in range(rv._steps.count())]
    check("Name und Schritte", rv._name_label.text() == "B Ablauf" and len(lines) == 4, str(lines))
    check("Schritte lesbar", lines[0].startswith("1.  Warten") and "Schleife (3×)" in lines[1] and "(12)" in lines[2]
          and lines[3].endswith("Ende"), str(lines))
    check("auch im Editor geladen", tab._table.rowCount() == 4 and tab.current_testcase_name() == "B Ablauf")
    check("Start frei", rv._start_button.isEnabled() and not rv._stop_button.isEnabled())

    print("Lauf")
    rv._start_button.click()
    check("Start -> run_requested", runs == [1])
    tab.on_run_started()
    tab.on_step_started(2, tab.steps()[2])
    app.processEvents()
    check("Fortschritt und aktueller Schritt", rv._progress.value() == 3 and rv._current_step == 2)
    check("Status im Grossformat", rv._status_label.text().startswith("Schritt 3/4"), rv._status_label.text())
    check("waehrend des Laufs: Start gesperrt, Stop frei, Liste gesperrt",
          not rv._start_button.isEnabled() and rv._stop_button.isEnabled() and not rv._files.isEnabled())
    rv._on_file_clicked(rv._files.item(0))
    check("andere Datei waehrend des Laufs ignoriert", tab.current_testcase_name() == "B Ablauf")
    tab.on_run_finished()
    check("fertig: wieder startbereit", rv._start_button.isEnabled() and not rv._stop_button.isEnabled()
          and rv._status_label.text().startswith("Fertig"))

    print("Schrittfehler")
    tab.on_run_started()
    tab.on_step_failed(2, "Netzteil antwortet nicht")
    check("Fehler rot, Stop zum Quittieren", rv._status_kind == "error" and rv._stop_button.isEnabled()
          and "Netzteil antwortet nicht" in rv._status_label.text())
    rv._stop_button.click()
    check("Stop quittiert", stops == [1] and rv._start_button.isEnabled() and rv._status_label.text() == "Gestoppt")

    print("Zurueck ohne Touch")
    ThemeManager.instance().set_touch(False)
    app.processEvents()
    check("Editor wieder sichtbar, Ablauf bleibt", tab._page_stack.currentWidget() is tab._editor_page
          and tab._table.rowCount() == 4)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

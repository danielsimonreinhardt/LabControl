"""Prueft die Konfliktpruefung beim Speichern eines Testablaufs (PI-30 a, 0.46.0):

- unveraenderte Datei: Speichern ohne Rueckfrage
- anderswo geaendert: Rueckfrage; "Abbrechen" laesst die fremde Fassung stehen, "Als Kopie speichern"
  legt "<Name> (Kopie).json" (dann "(Kopie 2)") an, "Ueberschreiben" ersetzt sie
- nach dem eigenen Speichern gilt die neue Fassung als Stand (kein Fehlalarm beim naechsten Speichern)
- Speichern unter einem anderen Namen fragt nicht

Offscreen, keine Hardware.  Aufruf:  python tools/check_testcase_save_conflict.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-save-check-"))
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
    import testcase_tab as tt
    from testcase_model import TestStep, load_steps, save_steps

    folder = DATA / "testcases"
    folder.mkdir(exist_ok=True)
    path = folder / "Ablauf.json"
    save_steps([TestStep(step_type="wait", duration=1.0)], path)

    target = {"path": str(path)}
    tt.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (target["path"], ""))
    tab = tt.TestcaseTab()
    asked: list[str] = []
    answer = {"choice": "cancel"}

    def ask(p):
        asked.append(p.name)
        return answer["choice"]

    tab._ask_save_conflict = ask
    tab._load_path(path)

    def durations(p: Path) -> list[float]:
        return [s.duration for s in load_steps(p)]

    print("unveraendert")
    tab._save_to_file()
    check("keine Rueckfrage", not asked)

    print("anderswo geaendert")
    save_steps([TestStep(step_type="wait", duration=9.0)], path)   # "der Pi" speichert
    answer["choice"] = "cancel"
    tab._save_to_file()
    check("Rueckfrage kommt", asked == ["Ablauf.json"], str(asked))
    check("Abbrechen: fremde Fassung bleibt", durations(path) == [9.0], str(durations(path)))

    answer["choice"] = "copy"
    tab._save_to_file()
    copy1 = folder / "Ablauf (Kopie).json"
    check("Als Kopie: neue Datei mit eigener Fassung", copy1.exists() and durations(copy1) == [1.0])
    check("... fremde Fassung bleibt", durations(path) == [9.0])
    check("... Editor arbeitet jetzt mit der Kopie", tab._current_path == copy1, str(tab._current_path))

    asked.clear()
    tab._load_path(path)
    save_steps([TestStep(step_type="wait", duration=7.0)], path)
    tab._save_to_file()
    check("zweite Kopie heisst '(Kopie 2)'", (folder / "Ablauf (Kopie 2).json").exists())

    asked.clear()
    tab._load_path(path)
    save_steps([TestStep(step_type="wait", duration=5.0)], path)
    answer["choice"] = "overwrite"
    tab._save_to_file()
    check("Ueberschreiben ersetzt die fremde Fassung", asked and durations(path) == [7.0], str(durations(path)))
    asked.clear()
    tab._save_to_file()
    check("danach kein Fehlalarm", not asked, str(asked))

    print("anderer Name")
    other = folder / "Anderer.json"
    save_steps([TestStep(step_type="wait", duration=3.0)], other)
    target["path"] = str(other)
    save_steps([TestStep(step_type="wait", duration=2.0)], path)   # geladene Datei anderswo geaendert
    tab._save_to_file()
    check("Speichern unter anderem Namen fragt nicht", not asked, str(asked))

    tab.close()
    tab.deleteLater()
    app.processEvents()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

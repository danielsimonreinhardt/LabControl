"""Prueft verknuepfte Bausteine (0.61.2, PI-49 b): "Baustein einfuegen" verweist auf die Baustein-Datei statt
die Schritte zu kopieren.

- model  -- Feld `link` am ersten Schritt, Format v3 nur mit Verknuepfung, Aufloesen beim Laden (aktueller
            Bausteininhalt, Geraetenamen des Bausteins), fehlende Datei -> gespeicherte Kopie + Hinweis,
            Bausteine selbst bleiben flach
- editor -- Einfuegen legt eine verknuepfte Gruppe an (auch bei einem Schritt), Zeilen gesperrt, Kette am Kopf,
            Speichern/Laden behaelt die Gruppe, geaenderter Baustein wird vor dem Lauf nachgeladen,
            "Verknuepfung loesen" gibt die Zeilen frei, eine eingefuegte Zeile loest die Verknuepfung
- cli    -- `main.py run` fuehrt den aktuellen Bausteininhalt aus; ohne Baustein-Datei die Kopie

Offscreen, Simulation, keine Hardware (no_hardware.block() auch im Kindprozess).
Aufruf:  python tools/check_linked_blocks.py
"""
from __future__ import annotations

import json
import os
import subprocess
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


def child_main() -> int:
    import no_hardware
    no_hardware.block()
    import headless
    return headless.main(["labcontrol", *sys.argv[2:]])


def section_model() -> None:
    print("model")
    import testcase_model as tm
    folder = Path(tempfile.mkdtemp(prefix="lc-link-model-"))
    blocks = folder / "blocks"
    blocks.mkdir()
    tm.save_block([tm.TestStep(device_kind="hil", device_id="hil:A", action="HIL_RELAY_ON", hil_channel=1),
                   tm.TestStep(step_type="wait", duration=0.5)], "Kabelbruch", blocks / "kabelbruch.json",
                  {"hil:A": ("hil", "microHIL Prüfstand")})
    check("Verweis relativ zum Baustein-Ordner", tm.block_ref(blocks / "kabelbruch.json", blocks) == "kabelbruch.json")

    plain = folder / "ohne.json"
    tm.save_steps([tm.TestStep(step_type="wait", duration=1.0)], plain)
    check("ohne Verknuepfung bleibt Format 2 ohne 'link'",
          json.loads(plain.read_text(encoding="utf-8"))["version"] == 2
          and "link" not in json.loads(plain.read_text(encoding="utf-8"))["steps"][0])

    copy = [tm.TestStep(device_kind="hil", device_id="hil:A", action="HIL_RELAY_ON", hil_channel=7)]
    copy[0].link = {"block": "kabelbruch.json", "name": "Kabelbruch", "count": 1}
    case = folder / "ablauf.json"
    tm.save_steps([tm.TestStep(step_type="wait", duration=1.0), *copy,
                   tm.TestStep(step_type="wait", duration=2.0)], case)
    data = json.loads(case.read_text(encoding="utf-8"))
    check("mit Verknuepfung Format 3, link nur am ersten Schritt",
          data["version"] == 3 and data["steps"][1].get("link", {}).get("count") == 1
          and "link" not in data["steps"][0] and "link" not in data["steps"][2], str(data["steps"])[:200])

    steps, _meta, devices, problems = tm.load_testcase_linked(case, blocks)
    check("Laden: aktueller Bausteininhalt statt Kopie (2 statt 1 Schritt)",
          [s.step_type for s in steps] == ["wait", "action", "wait", "wait"] and steps[1].hil_channel == 1,
          str([(s.step_type, s.hil_channel) for s in steps]))
    check("link mit neuer Schrittzahl, Folgeschritte ohne link",
          steps[1].link == {"block": "kabelbruch.json", "name": "Kabelbruch", "count": 2} and not steps[2].link,
          str(steps[1].link))
    check("Geraetenamen des Bausteins ergaenzt", devices.get("hil:A", {}).get("label") == "microHIL Prüfstand",
          str(devices))
    check("keine Probleme", problems == [], str(problems))

    tm.save_block(steps, "Verschachtelt", blocks / "flach.json")
    _name, inner = tm.load_block(blocks / "flach.json")
    check("Baustein aus verknuepften Schritten wird flach gespeichert", not any(s.link for s in inner))

    (blocks / "kabelbruch.json").unlink()
    steps, _meta, _devices, problems = tm.load_testcase_linked(case, blocks)
    check("Baustein-Datei fehlt: gespeicherte Kopie laeuft", [s.hil_channel for s in steps if s.step_type == "action"]
          == [7] and len(steps) == 3, str([(s.step_type, s.hil_channel) for s in steps]))
    check("... Verknuepfung bleibt erhalten", steps[1].link.get("block") == "kabelbruch.json")
    check("... mit Hinweis", len(problems) == 1 and "Kabelbruch" in problems[0], str(problems))


def section_editor() -> None:
    print("editor")
    data = Path(tempfile.mkdtemp(prefix="lc-link-editor-"))
    os.environ["LABCONTROL_DATA_DIR"] = str(data)
    os.environ.pop("LABCONTROL_NETWORK_DIR", None)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import data_store
    import testcase_model as tm
    import testcase_tab as tt

    blocks = data_store.target_dir("blocks")
    block_file = blocks / "Aufheben.json"
    tm.save_block([tm.TestStep(step_type="wait", duration=0.25),
                   tm.TestStep(step_type="set_var", var_name="x", value=1.0)], "Aufheben", block_file)
    single_file = blocks / "Einzeln.json"
    tm.save_block([tm.TestStep(step_type="wait", duration=0.75)], "Einzeln", single_file)

    case = data_store.target_dir("testcases") / "Ablauf.json"
    picks = {"open": str(block_file), "save": str(case)}
    tt.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (picks["open"], ""))
    tt.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (picks["save"], ""))

    tab = tt.TestcaseTab()
    tab._table.selectRow(0)
    tab._insert_block_from_file()
    app.processEvents()
    linked = [g for g in tab._block_groups if g.link]
    check("Einfuegen legt eine verknuepfte Gruppe an", len(linked) == 1 and linked[0].link == "Aufheben.json",
          str([(g.start, g.count, g.link) for g in tab._block_groups]))
    group = linked[0]
    rows = range(group.start, group.start + group.count)
    widgets = [tab._table.cellWidget(r, c) for r in rows for c in range(1, tab._table.columnCount())]
    widgets = [w for w in widgets if w is not None]
    check("verknuepfte Zeilen gesperrt", widgets and not any(w.isEnabled() for w in widgets))
    overlay = group.overlay
    check("Kopfzeile: 'Baustein (verknüpft)' und Name", overlay is not None
          and "verknüpft" in overlay.device_label.text() and overlay.action_label.text() == "Aufheben",
          overlay.device_label.text() if overlay else "kein Overlay")
    steps = tab.steps()
    check("steps(): link am ersten Schritt", steps[group.start].link.get("count") == 2
          and not steps[group.start + 1].link, str(steps[group.start].link))

    picks["open"] = str(single_file)
    tab._table.selectRow(tab._table.rowCount() - 1)
    tab._insert_block_from_file()
    app.processEvents()
    check("auch ein einzelner Schritt wird verknuepft",
          sum(1 for g in tab._block_groups if g.link == "Einzeln.json" and g.count == 1) == 1)

    tab._save_to_file()
    saved = json.loads(case.read_text(encoding="utf-8"))
    check("Datei enthaelt beide Verknuepfungen",
          sorted(s["link"]["block"] for s in saved["steps"] if s.get("link")) == ["Aufheben.json", "Einzeln.json"])

    tab2 = tt.TestcaseTab()
    tab2._load_path(case)
    app.processEvents()
    check("Laden stellt die Gruppen wieder her",
          sorted((g.link, g.count) for g in tab2._block_groups) == [("Aufheben.json", 2), ("Einzeln.json", 1)],
          str([(g.link, g.count) for g in tab2._block_groups]))
    check("... unveraendert", not tab2.is_modified())

    tm.save_block([tm.TestStep(step_type="wait", duration=0.5),
                   tm.TestStep(step_type="set_var", var_name="x", value=2.0),
                   tm.TestStep(step_type="wait", duration=0.1)], "Aufheben", block_file)
    tab2.refresh_linked_blocks()
    app.processEvents()
    group = next(g for g in tab2._block_groups if g.link == "Aufheben.json")
    steps = tab2.steps()
    check("geaenderter Baustein vor dem Lauf nachgeladen", group.count == 3
          and steps[group.start].duration == 0.5 and steps[group.start + 1].value == 2.0,
          str([(s.step_type, s.duration, s.value) for s in steps]))
    before = [g.link for g in tab2._block_groups]
    tab2.refresh_linked_blocks()
    check("ohne Aenderung kein erneutes Laden noetig", [g.link for g in tab2._block_groups] == before)

    tab2._table.selectRow(group.start + 1)
    app.processEvents()
    tab2._selected_row = group.start + 1
    tab2._update_unlink_enabled()
    check("Knopf 'Verknuepfung loesen' aktiv in verknuepfter Zeile", tab2._block_unlink_button.isEnabled())
    tab2._unlink_selected_block()
    app.processEvents()
    group = next(g for g in tab2._block_groups if g.start == group.start)
    rows = range(group.start, group.start + group.count)
    widgets = [tab2._table.cellWidget(r, c) for r in rows for c in range(1, tab2._table.columnCount())]
    check("geloest: Zeilen wieder bedienbar", any(w is not None and w.isEnabled() for w in widgets))
    check("geloest: kein link mehr in steps()", not tab2.steps()[group.start].link)
    check("... Kopfzeile ohne 'verknüpft'", "verknüpft" not in group.overlay.device_label.text())

    tab3 = tt.TestcaseTab()
    tab3._load_path(case)
    group = next(g for g in tab3._block_groups if g.link == "Aufheben.json")
    tab3._insert_row(group.start + 1, tm.TestStep(step_type="wait", duration=9.0))
    tab3._revalidate_structure()
    check("eingefuegte Zeile im Baustein loest die Verknuepfung", not group.link and group.count == 4)
    for widget in (tab, tab2, tab3):
        widget.deleteLater()
    app.processEvents()


def run_cli(data: Path, *args: str, timeout: float = 90.0) -> tuple[int, str]:
    env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    env.pop("LABCONTROL_NETWORK_DIR", None)
    proc = subprocess.run([sys.executable, __file__, "--child", *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def section_cli() -> None:
    print("cli")
    import device_worker as dw
    import testcase_model as tm
    data = Path(tempfile.mkdtemp(prefix="lc-link-cli-"))
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    blocks = data / "blocks"
    blocks.mkdir()
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    good = tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=0.5, check_enabled=True,
                       check_field="voltage", check_min=0.0, check_max=100.0)
    bad = tm.TestStep(**psu, action="PSU_VOLT", value=5.0, duration=0.5, check_enabled=True,
                      check_field="voltage", check_min=-2.0, check_max=-1.0)
    tm.save_block([good, tm.TestStep(**psu, action="PSU_OUT_OFF")], "Messen", blocks / "messen.json")
    bad.link = {"block": "messen.json", "name": "Messen", "count": 1}
    case = data / "ablauf.json"
    tm.save_steps([bad], case)

    code, text = run_cli(data, "run", str(case), "--simulation", "--wait-devices", "20")
    check("Kommandozeile fuehrt den aktuellen Baustein aus (Exit 0 statt 1 der Kopie)", code == 0,
          f"{code}: {text[-500:]}")
    (blocks / "messen.json").unlink()
    code, text = run_cli(data, "run", str(case), "--simulation", "--wait-devices", "20")
    check("ohne Baustein-Datei laeuft die Kopie (Exit 1) mit Hinweis",
          code == 1 and "gespeicherte Kopie" in text, f"{code}: {text[-500:]}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main()
    import no_hardware
    no_hardware.block()
    section_model()
    section_editor()
    section_cli()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} Fehler:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Prueft FT-5 Varianten und Parametersaetze (PI-55 a, 0.66.0): variants.py (Datei, Rollen, Parameter, Matrix),
Laeufe mit --variant/--matrix ueber `labcontrol run` in der Simulation, Editor (Rollen in der Geraeteauswahl,
Werte aus Parametern), Varianten-Dialog.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_variants.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-variants-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def section_unit() -> None:
    print("unit -- variants.py")
    import variants as v
    from testcase_model import TestStep

    folder = DATA / "unit" / "varianten"
    pu = v.Variant("PowerUnit", {"Versorgung": "psu:0001", "Last": "load:A1"}, {"U_nom": 12.0}, "PU")
    path = v.save_variant(pu, folder)
    check("Datei geschrieben", path.name == "PowerUnit.json" and json.loads(path.read_text("utf-8"))["format"]
          == v.FORMAT)
    v.save_variant(v.Variant("Drive Unit", {"Versorgung": "@raspberrypi/psu:HCS16"}, {"U_nom": 24}), folder)
    found, problems = v.list_variants(folder)
    check("zwei Varianten gelesen", [x.name for x in found] == ["Drive Unit", "PowerUnit"] and not problems,
          str([x.name for x in found]))
    (folder / "kaputt.json").write_text("{", encoding="utf-8")
    _found, problems = v.list_variants(folder)
    check("kaputte Datei gemeldet, Rest gelesen", len(_found) == 2 and problems and "kaputt" in problems[0])
    check("finden per Name", v.find_variant("PowerUnit", folder).vars == {"U_nom": 12.0})
    check("finden ohne Gross/klein", v.find_variant("drive unit", folder).name == "Drive Unit")
    check("finden per Datei", v.find_variant(str(path), None).name == "PowerUnit")
    try:
        v.find_variant("BCU", folder)
        check("unbekannte Variante -> Fehler", False)
    except ValueError:
        check("unbekannte Variante -> Fehler", True)
    for bad in ({"name": "x", "vars": {"1a": 1}}, {"name": "x", "vars": {"a": "zwölf"}},
                {"name": "x", "roles": {"a/b": "psu:1"}}, {"vars": {}}):
        try:
            v.parse_variant(bad)
            check(f"abgelehnt {bad}", False)
        except ValueError:
            check(f"abgelehnt {bad}", True)
    check("Komma-Zahl", v.parse_variant({"name": "x", "vars": {"a": "1,5"}}).vars == {"a": 1.5})
    check("Rollen und Arten", v.known_roles(found) == {"Versorgung": "psu", "Last": "load"}, str(v.known_roles(found)))
    check("Art aus Partner-ID", v.device_kind_of("@raspberrypi/psu:HCS16") == "psu")

    steps = [TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_VOLT"),
             TestStep(step_type="while", cond_device_kind="load", cond_device_id="role:Last"),
             TestStep(device_kind="psu", device_id="psu:7", action="PSU_OUT_ON")]
    check("benutzte Rollen", v.used_roles(steps) == {"Versorgung", "Last"})
    problems = v.apply_roles(steps, pu)
    check("Rollen eingesetzt", not problems and steps[0].device_id == "psu:0001" and steps[1].cond_device_id
          == "load:A1" and steps[2].device_id == "psu:7", str(problems))
    steps = [TestStep(device_kind="psu", device_id="role:Last", action="PSU_VOLT")]
    check("falsche Geraeteart gemeldet", "load:A1" in " ".join(v.apply_roles(steps, pu)))
    steps = [TestStep(device_kind="psu", device_id="role:Heizung", action="PSU_VOLT")]
    check("nicht belegte Rolle gemeldet", "Heizung" in " ".join(v.apply_roles(steps, pu)))
    steps = [TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_VOLT")]
    check("Rolle ohne Variante gemeldet", "braucht eine Variante" in " ".join(v.apply_roles(steps, None)))

    step = TestStep(device_kind="psu", action="PSU_VOLT", params={"refs": {"value": "U_nom", "check_max": "U_max"}})
    check("Parameter eingesetzt", v.resolve_refs(step, {"U_nom": 5, "U_max": 5.5}) == "" and step.value == 5.0
          and step.check_max == 5.5)
    check("fehlender Parameter -> Meldung", "U_max" in v.resolve_refs(step, {"U_nom": 5}))
    check("Startvariablen: --var geht vor", v.run_variables(pu, {"U_nom": 13.0, "x": 1}) == {"U_nom": 13.0, "x": 1})

    matrix = DATA / "unit" / "m.csv"
    matrix.write_text("variant;U_nom;I_max\nPowerUnit;12;2,5\nDrive Unit;24;\n", encoding="utf-8")
    rows = v.read_matrix(matrix)
    check("Matrix mit Semikolon", rows[0] == {"variant": "PowerUnit", "vars": {"U_nom": 12.0, "I_max": 2.5},
                                               "label": "PowerUnit, U_nom=12, I_max=2.5"}, str(rows[0]))
    check("leere Zelle = nicht gesetzt", rows[1]["vars"] == {"U_nom": 24.0}, str(rows[1]))
    matrix.write_text("U_nom,U_min\n5,4\nx,4\n", encoding="utf-8")
    try:
        v.read_matrix(matrix)
        check("Matrix: keine Zahl -> Fehler", False)
    except ValueError as exc:
        check("Matrix: keine Zahl -> Fehler mit Zeile", "Zeile 3" in str(exc), str(exc))


def run_cli(name: str, steps, *extra: str, variants_files: dict | None = None,
            files: dict | None = None) -> tuple[int, str, Path]:
    import testcase_model as tm
    data = DATA / name
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    folder = data / "varianten"
    folder.mkdir()
    for file_name, content in (variants_files or {}).items():
        (folder / file_name).write_text(json.dumps(content), encoding="utf-8")
    for file_name, content in (files or {}).items():
        (data / file_name).write_text(content, encoding="utf-8")
    path = data / "ablauf.json"
    tm.save_steps(steps, path)
    env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    args = [a.replace("{data}", str(data)) for a in extra]
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "run", str(path),
                           "--simulation", "--json", str(data / "r.json"), "--wait-devices", "8", *args],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    return proc.returncode, proc.stdout + proc.stderr, data


def psu_steps():
    from testcase_model import TestStep
    volt = TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_VOLT", value=1.0, duration=0.3,
                    params={"refs": {"value": "U_nom"}})
    on = TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_OUT_ON", duration=1.5,
                  check_enabled=True, check_field="voltage", check_min=0, check_max=100,
                  params={"refs": {"check_min": "U_min", "check_max": "U_max"}})
    off = TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_OUT_OFF", duration=0.2)
    return [volt, on, off]


SIM = {"name": "Sim", "roles": {"Versorgung": "psu:SIM"}, "vars": {"U_nom": 5, "U_min": 4.5, "U_max": 5.5}}


def section_runs() -> None:
    print("runs -- labcontrol run --variant/--matrix (Simulation)")
    code, out, data = run_cli("pass", psu_steps(), "--variant", "Sim", variants_files={"sim.json": SIM})
    result = json.loads((data / "r.json").read_text("utf-8")) if (data / "r.json").exists() else {}
    check("Lauf mit Variante bestanden", code == 0, out[-600:])
    check("Variante und Parameter im Ergebnis", result.get("meta", {}).get("variant") == "Sim"
          and result.get("meta", {}).get("parameters", {}).get("U_nom") == 5.0, str(result.get("meta")))
    check("Grenzen aus Parametern", any(c.get("min") == 4.5 and c.get("max") == 5.5 for c in result.get("checks", [])),
          str(result.get("checks")))
    code, out, _data = run_cli("var", psu_steps(), "--variant", "Sim", "--var", "U_nom=8",
                               variants_files={"sim.json": SIM})
    check("--var ueberschreibt Parameter -> Pruefung verletzt (Exit 1)", code == 1, out[-400:])
    code, out, _data = run_cli("norole", psu_steps())
    check("Rolle ohne Variante -> nicht gestartet (Exit 3)", code == 3 and "Variante" in out, out[-300:])
    code, out, _data = run_cli("unknown", psu_steps(), "--variant", "Gibtsnicht", variants_files={"sim.json": SIM})
    check("unbekannte Variante -> Exit 3", code == 3 and "Gibtsnicht" in out, out[-300:])
    missing = dict(SIM, vars={"U_nom": 5})
    code, out, _data = run_cli("missingvar", psu_steps(), "--variant", "Sim", variants_files={"sim.json": missing})
    check("fehlender Parameter -> Abbruch (Exit 2)", code == 2 and "U_min" in out, out[-300:])
    code, out, data = run_cli("matrix", psu_steps(), "--matrix", "{data}/m.csv", variants_files={"sim.json": SIM},
                              files={"m.csv": "variant,U_nom\nSim,5\nSim,8\n"})
    check("Matrix: schlimmster Exit (1)", code == 1, out[-600:])
    check("Matrix: je Zeile eine Ergebnisdatei", (data / "r_01.json").exists() and (data / "r_02.json").exists())
    check("Matrix: Zusammenfassung", "Matrix-Ergebnis" in out and "U_nom=8" in out, out[-300:])
    if (data / "r_02.json").exists():
        second = json.loads((data / "r_02.json").read_text("utf-8"))
        check("Matrix Zeile 2 mit U_nom=8", second.get("meta", {}).get("parameters", {}).get("U_nom") == 8.0)


def section_editor() -> None:
    print("editor -- Rollen, Parameter, Dialog")
    from PySide6.QtWidgets import QApplication

    import data_store
    import testcase_model as tm
    import variants as v
    from testcase_tab import COL_CHECK, COL_DEVICE, TestcaseTab
    from variant_dialog import RefsDialog, VariantDialog

    QApplication.instance() or QApplication([])
    folder = data_store.target_dir(v.FOLDER)
    v.save_variant(v.Variant("Sim", {"Versorgung": "psu:SIM"}, {"U_nom": 5.0}), folder)
    tab = TestcaseTab()
    tab.set_variant("Sim")
    check("Variante in der Leiste", tab._variant_combo.currentText() == "Sim" and tab.current_variant() == "Sim")
    seen: list[str] = []
    tab.variant_changed.connect(seen.append)
    tab._variant_combo.setCurrentIndex(0)
    check("Auswahl gemeldet", seen == [""] and tab.current_variant() == "")
    tab._variant_combo.setCurrentIndex(1)
    path = DATA / "editor.json"
    tm.save_steps(psu_steps(), path)
    tab._load_path(path)
    combo = tab._table.cellWidget(0, COL_DEVICE)
    check("Rolle in der Geraeteauswahl", "⟨Versorgung⟩" in combo.currentText(), combo.currentText())
    step = tab.steps()[0]
    check("Rolle bleibt im Schritt", step.device_id == "role:Versorgung" and step.params.get("refs") == {"value": "U_nom"},
          f"{step.device_id} {step.params}")
    page = tab._table.cellWidget(0, COL_CHECK)
    check("Zusammenfassung nennt Parameter", "U_nom" in page._summary_label.text(), page._summary_label.text())
    page._refs = {}
    check("Parameter entfernt -> refs weg", "refs" not in tab.steps()[0].params)
    check("Parameternamen fuer den Dialog", "U_nom" in tab._parameter_names())
    dialog = RefsDialog({"value": "U_nom"}, ["U_nom", "U_max"])
    dialog._combos["check_max"].setEditText("U_max")
    dialog._accept()
    check("Refs-Dialog", dialog.refs() == {"value": "U_nom", "check_max": "U_max"}, str(dialog.refs()))
    dialog._combos["duration"].setEditText("1x")
    dialog.setResult(0)
    dialog._accept()
    check("Refs-Dialog lehnt ungueltigen Namen ab", "Ungültig" in dialog._error.text())

    vd = VariantDialog(folder, {"psu:SIM": ("psu", "Netzteil Sim"), "load:SIM": ("load", "Last Sim")})
    check("Dialog listet Variante", vd._list.count() == 1 and vd._name.text() == "Sim")
    vd._add_role("Last", "load:SIM")
    vd._add_var("I_max", "2,5")
    vd._name.setText("Sim PU")
    vd._save()
    saved = v.find_variant("Sim PU", folder)
    check("Dialog speichert (umbenannt, alte Datei weg)", saved.roles == {"Versorgung": "psu:SIM", "Last": "load:SIM"}
          and saved.vars == {"U_nom": 5.0, "I_max": 2.5} and not (folder / "Sim.json").exists(),
          f"{saved.roles} {saved.vars} {sorted(p.name for p in folder.iterdir())}")
    vd._new_variant()
    check("neue Variante", vd._list.count() == 2)


def main() -> int:
    section_unit()
    section_runs()
    section_editor()
    print(f"\n{'ALLES OK' if not FAILURES else f'{len(FAILURES)} FEHLER: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

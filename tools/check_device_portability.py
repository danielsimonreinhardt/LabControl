"""Prueft, dass Testablaeufe zwischen PC und Raspberry Pi austauschbar sind (Entscheidung PI-1, 10.11.2026),
und den Datenordner unter Linux.

- Datei traegt je verwendetem Geraet Art und Namen ("devices"); beim Laden wird eine unbekannte ID dem
  bekannten Geraet gleicher Art und gleichen Namens zugeordnet (auch Bedingungs-Geraet, auch Bausteine).
- Kein, mehrdeutiger oder artfremder Treffer: ID bleibt. Alte Dateien ohne "devices" laden wie bisher.
- TestcaseTab: Laden ordnet zu und meldet es in der Statuszeile.
- paths.app_dir(): LABCONTROL_DATA_DIR, Linux-Standard $XDG_DATA_HOME/labcontrol, Rueckfall ohne Abbruch.

Offscreen, ohne Hardware. Aufruf:  python tools/check_device_portability.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

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


PC_DEVICES = {
    "psu:COM6": ("psu", "Netzteil 16V-30A"),
    "psu:0001": ("psu", "Netzteil 60V-8A"),
    "load:48DC385B3435": ("load", "Last 150W"),
    "battsim:vector:59177:1": ("battsim", "Batterie-Simulator 1"),
}
PI_DEVICES = {
    "psu:usb-1-1.2:1.0": ("psu", "Netzteil 16V-30A"),
    "psu:0001": ("psu", "Netzteil 60V-8A"),
    "load:48DC385B3435": ("load", "Last 150W"),
    "battsim:socketcan:can0": ("battsim", "Batterie-Simulator 1"),
}


def make_steps(tm):
    return [
        tm.TestStep(device_kind="psu", device_id="psu:COM6", action="PSU_VOLT", value=12.0),
        tm.TestStep(device_kind="psu", device_id="psu:0001", action="PSU_VOLT", value=24.0),
        tm.TestStep(device_kind="battsim", device_id="battsim:vector:59177:1", action="BS_REMOTE_ON"),
        tm.TestStep(step_type="while", cond_device_kind="psu", cond_device_id="psu:COM6", device_id=""),
        tm.TestStep(device_kind="load", device_id="", action="CURR", value=1.0),
    ]


def check_model() -> None:
    import testcase_model as tm
    tmp = pathlib.Path(tempfile.mkdtemp())
    print("Speichern am PC, Laden am Pi")
    path = tmp / "ablauf.json"
    tm.save_steps(make_steps(tm), path, PC_DEVICES)
    payload = json.loads(path.read_text(encoding="utf-8"))
    check("Datei nennt nur verwendete Geraete mit Art und Name",
          payload["devices"] == {k: {"kind": v[0], "label": v[1]} for k, v in PC_DEVICES.items()
                                 if k != "load:48DC385B3435"}, str(payload["devices"]))
    check("Format-Version unveraendert", payload["version"] == tm.FILE_FORMAT_VERSION)
    steps = tm.load_steps(path)
    mapping = tm.remap_devices(steps, tm.load_device_map(path), PI_DEVICES)
    check("Zuordnung ueber den Namen", mapping == {"psu:COM6": "psu:usb-1-1.2:1.0",
                                                    "battsim:vector:59177:1": "battsim:socketcan:can0"}, str(mapping))
    check("Schritte umgestellt", [s.device_id for s in steps]
          == ["psu:usb-1-1.2:1.0", "psu:0001", "battsim:socketcan:can0", "", ""], str([s.device_id for s in steps]))
    check("Bedingungs-Geraet umgestellt", steps[3].cond_device_id == "psu:usb-1-1.2:1.0")
    check("'automatisch' (leere ID) bleibt leer", steps[4].device_id == "")

    print("Grenzfaelle")
    steps = make_steps(tm)
    ambiguous = dict(PI_DEVICES)
    ambiguous["psu:usb-1-1.4:1.0"] = ("psu", "Netzteil 16V-30A")
    mapping = tm.remap_devices(steps, tm.load_device_map(path), ambiguous)
    check("Zwei Geraete gleichen Namens: keine Zuordnung", "psu:COM6" not in mapping and steps[0].device_id == "psu:COM6")
    steps = make_steps(tm)
    other_kind = {"load:x": ("load", "Netzteil 16V-30A")}
    check("Anderer Geraetetyp gleichen Namens: keine Zuordnung",
          tm.remap_devices(steps, tm.load_device_map(path), other_kind) == {})
    steps = make_steps(tm)
    check("Auf dem Rechner bekannte ID bleibt (auch wenn gleicher Name woanders)",
          tm.remap_devices(steps, tm.load_device_map(path), PC_DEVICES) == {})
    old = tmp / "alt.json"
    old.write_text(json.dumps({"format": "labor-testcase", "version": tm.FILE_FORMAT_VERSION,
                               "steps": [{"device_kind": "psu", "device_id": "psu:COM6", "action": "PSU_VOLT"}]}),
                   encoding="utf-8")
    check("Alte Datei ohne 'devices': laedt, keine Zuordnung",
          len(tm.load_steps(old)) == 1 and tm.load_device_map(old) == {})
    legacy = tmp / "v1.json"
    legacy.write_text(json.dumps([{"device": "load", "action": "CURR"}]), encoding="utf-8")
    check("Sehr altes Format (nacktes Array): load_device_map = {}", tm.load_device_map(legacy) == {})

    print("Baustein")
    block = tmp / "block.json"
    tm.save_block(make_steps(tm)[:2], "Netzteile", block, PC_DEVICES)
    name, block_steps = tm.load_block(block)
    mapping = tm.remap_devices(block_steps, tm.load_device_map(block), PI_DEVICES)
    check("Baustein: Zuordnung ueber den Namen", name == "Netzteile" and block_steps[0].device_id == "psu:usb-1-1.2:1.0",
          str(mapping))


def check_tab() -> None:
    print("Testablauf-Reiter")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import testcase_model as tm
    import testcase_tab as tt
    tmp = pathlib.Path(tempfile.mkdtemp())
    path = tmp / "vom-pc.json"
    tm.save_steps(make_steps(tm), path, PC_DEVICES)
    tab = tt.TestcaseTab()
    for device_id, (kind, label) in PI_DEVICES.items():
        tab.on_device_known(kind, device_id, label)
    real = tt.QFileDialog.getOpenFileName
    tt.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
    try:
        tab._load_from_file()
    finally:
        tt.QFileDialog.getOpenFileName = real
    app.processEvents()
    ids = [s.device_id for s in tab.steps()]
    check("geladene Zeilen nennen die Pi-Geraete", ids[:3] == ["psu:usb-1-1.2:1.0", "psu:0001", "battsim:socketcan:can0"],
          str(ids))
    text = tab._status_label.text()
    check("Statuszeile meldet die Zuordnung",
          "Netzteil 16V-30A" in text and "Batterie-Simulator 1" in text and "Netzteil 60V-8A" not in text, text)

    out = tmp / "gespeichert.json"
    real_save = tt.QFileDialog.getSaveFileName
    tt.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))
    try:
        tab._save_to_file()
    finally:
        tt.QFileDialog.getSaveFileName = real_save
    saved = json.loads(out.read_text(encoding="utf-8"))
    check("Speichern aus dem Reiter schreibt 'devices'",
          saved["devices"].get("psu:usb-1-1.2:1.0") == {"kind": "psu", "label": "Netzteil 16V-30A"}, str(saved["devices"]))


def check_paths() -> None:
    print("Datenordner")
    import paths
    tmp = pathlib.Path(tempfile.mkdtemp())
    saved_env = {k: os.environ.get(k) for k in (paths.DATA_DIR_ENV, "XDG_DATA_HOME")}
    saved_platform, saved_frozen = sys.platform, paths.IS_FROZEN
    try:
        os.environ[paths.DATA_DIR_ENV] = str(tmp / "ssd" / "lab")
        check("LABCONTROL_DATA_DIR gilt und wird angelegt",
              paths.app_dir() == tmp / "ssd" / "lab" and (tmp / "ssd" / "lab").is_dir())
        del os.environ[paths.DATA_DIR_ENV]
        sys.platform = "linux"
        paths.IS_FROZEN = False
        os.environ["XDG_DATA_HOME"] = str(tmp / "xdg")
        check("Linux aus dem Repo: $XDG_DATA_HOME/labcontrol", paths.app_dir() == tmp / "xdg" / "labcontrol")
        blocker = tmp / "datei"
        blocker.write_text("x")
        os.environ["XDG_DATA_HOME"] = str(blocker)   # Ordner unter einer Datei geht nicht
        check("nicht anlegbar: Rueckfall neben dem Skript, kein Abbruch",
              paths.app_dir() == pathlib.Path(paths.__file__).resolve().parent)
        sys.platform = "win32"
        check("Windows aus dem Repo: wie bisher lab_gui/", paths.app_dir() == pathlib.Path(paths.__file__).resolve().parent)
    finally:
        sys.platform, paths.IS_FROZEN = saved_platform, saved_frozen
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    check_model()
    check_tab()
    check_paths()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

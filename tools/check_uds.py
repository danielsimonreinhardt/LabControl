"""Prueft FT-1 Grundlage (0.60.0): ODX-Leser, Schluessel, UDS-Client gegen das nachgebildete Steuergeraet ueber
den echten CAN-Lesethread des Workers (CanTap), UdsService im Hauptfenster (Simulation) und den Abschnitt
"Pruefling" in den Einstellungen.

- odx     -- uds_odx: ODX der Nachbildung; falls das neXtBSW-Repo hier liegt, auch ein frischer diaggen-Export
- keys    -- uds_keys: Schluesseldatei, Namen, Ableitung
- window  -- UdsService im Hauptfenster: lesen/dekodieren, Session, NRCs, Entsperren, schreiben, DTCs, Routine,
             Reset (Startzaehler), roh, falscher Schluessel aus dem gemeinsamen Ordner

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_uds.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-uds-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
NEXTBSW = Path("C:/WORK/vscode_workspace/S32K14x basic software")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return bool(until()) if until is not None else False


def section_odx() -> None:
    print("odx -- ODX-Leser")
    import uds_odx
    from uds_sim import sim_odx
    odx = sim_odx()
    check("Sessions, Security", odx.sessions == {"Default": 1, "Extended": 3}
          and odx.security["Application"].key_bytes == 4 and odx.security["Application"].sessions == ["Extended"])
    conditions = odx.dids[0x0120]
    check("DID 0x0120: 11 Byte, 6 Elemente, mV", conditions.size == 11 and len(conditions.elements) == 6
          and conditions.element("supply").unit == "mV")
    raw = bytes([2]) + (13800).to_bytes(2, "big") + bytes([0, 3]) + (1234).to_bytes(4, "big") + bytes(2)
    check("dekodieren (big endian)", conditions.decode(raw)["supply"] == 13800
          and conditions.decode(raw)["uptime"] == 1234)
    session = odx.dids[0xF186].elements[0]
    check("Wertetabelle", session.decode(b"\x03") == "Extended" and session.encode("Default") == b"\x01")
    scaled = uds_odx.Element("t", 0, 16, factor=0.1, offset=-40.0)
    check("LINEAR hin und zurueck", abs(scaled.decode(scaled.encode(23.5)) - 23.5) < 1e-9)
    little = uds_odx.Element("x", 0, 16, big_endian=False)
    check("little endian", little.decode(b"\x34\x12") == 0x1234)
    check("Schreibrechte aus PRE-CONDITION", odx.dids[0xFD00].write and odx.dids[0xFD00].write_security == ["Application"])
    check("Kommunikationsparameter", (odx.request_id, odx.response_id, odx.filler) == (0x7E0, 0x7E8, 0xCC))
    python = NEXTBSW / ".venv" / "Scripts" / "python.exe"
    if python.exists():
        out = DATA / "demo.pdx"
        proc = subprocess.run([str(python), "-m", "tools.diaggen", "pdx", "projects/evalboard_s32k144_demo", "-o",
                               str(out)], cwd=NEXTBSW, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0 and out.exists():
            demo = uds_odx.load(out)
            check("neXtBSW-Demo: DIDs 0x0120/0x0130/0xF18C, Routine, DTCs",
                  {0x0120, 0x0130, 0xF18C} <= set(demo.dids) and 0x0301 in demo.routines and len(demo.dtcs) >= 5,
                  f"{len(demo.dids)} DIDs")
            stats = demo.dids[0x0130]
            check("neXtBSW-Demo: Startzaehler bei Byte 4", stats.element("starts").byte_pos == 4 and stats.size == 43)
        else:
            print(f"  (neXtBSW-Export nicht moeglich: {proc.stderr.strip()[-200:]})")


def section_keys() -> None:
    print("keys -- Schluessel")
    import uds_keys
    folder = DATA / "shared"
    (folder / "keys").mkdir(parents=True)
    (folder / "keys" / "board.key").write_text("# Test\n" + "ab" * 32 + "\n", encoding="utf-8")
    check("Schluesseldatei mit Kommentar", uds_keys.load_master_key(folder, "board") == bytes([0xAB]) * 32)
    for bad, label in (("../x", "Pfad im Namen"), ("", "leerer Name")):
        try:
            uds_keys.key_path(folder, bad)
            ok = False
        except uds_keys.KeyError_:
            ok = True
        check(f"abgelehnt: {label}", ok)
    try:
        uds_keys.load_master_key(folder, "fehlt")
        ok = False
    except uds_keys.KeyError_ as exc:
        ok = "fehlt" in str(exc)
    check("fehlende Datei -> klare Meldung", ok)
    key = uds_keys.app_key(uds_keys.device_key(b"m" * 32, b"u" * 16), 1, b"\x01\x02\x03\x04", 4)
    check("Ableitung 4 Byte", len(key) == 4)


def section_window() -> None:
    print("window -- UdsService im Hauptfenster (Simulation)")
    import no_hardware
    no_hardware.block()
    import device_worker as dw
    from PySide6.QtWidgets import QApplication
    from main_window import MainWindow
    from settings import Settings

    app = QApplication.instance() or QApplication([])
    window = MainWindow(Settings())
    window.show()
    spin(app, 5.0, lambda: dw.SIM_CAN_ID in window._online_devices.get("can", set()))
    uds = window._uds
    check("Pruefling 'Simulation' ohne Einstellung", uds.configured() and uds.odx().name == "labcontrol_sim_ecu")
    check("Status in den Einstellungen", "labcontrol_sim_ecu" in window.settings_tab.dut_editor._status.text())

    t0 = time.monotonic()
    result = uds.call("read_did", {"did": "EcuConditions"})
    elapsed = time.monotonic() - t0
    check("DID per Name lesen und dekodieren", result.ok and result.values["decoded"]["supply"] == 13800,
          result.describe())
    check("Antwortzeit < 0,5 s", elapsed < 0.5, f"{elapsed:.3f} s")
    check("unbekannte DID -> NRC 0x31", uds.call("read_did", {"did": 0x1234}).nrc == 0x31)
    check("Schreiben in Default -> NRC 0x7F", uds.call("write_did", {"did": 0xFD00, "values": {"ramTestValue": 5}}).nrc == 0x7F)
    check("Session Extended per Name", uds.call("session", {"session": "Extended"}).ok)
    check("Schreiben gesperrt -> NRC 0x33", uds.call("write_did", {"did": "RamTestValue", "values": {"ramTestValue": 5}}).nrc == 0x33)
    check("Entsperren (Testschluessel der Simulation)", uds.call("unlock", {}).ok)
    check("Schreiben ueber ODX-Werte", uds.call("write_did", {"did": "RamTestValue", "values": {"ramTestValue": 77}}).ok)
    check("zurueckgelesen", uds.call("read_did", {"did": 0xFD00}).values["decoded"]["ramTestValue"] == 77)
    dtcs = uds.call("read_dtcs", {"mask": 0xFF})
    check("DTCs mit Texten aus dem ODX", dtcs.ok and len(dtcs.values["dtcs"]) == 2
          and all("Simulierter Fehler" in t for t in dtcs.values["texts"].values()), str(dtcs.values))
    check("Routine per Name", uds.call("routine", {"routine": "LedSelfTest", "step": "start"}).ok)
    check("DTCs loeschen", uds.call("clear_dtcs", {}).ok and uds.call("read_dtcs", {}).values["dtcs"] == [])
    starts = uds.call("read_did", {"did": 0x0130}).values["decoded"]["starts"]
    check("Reset", uds.call("reset", {"type": 1}).ok)
    time.sleep(0.2)
    check("Startzaehler +1 nach Reset", uds.call("read_did", {"did": 0x0130}).values["decoded"]["starts"] == starts + 1)
    raw = uds.call("raw", {"request": "22 F1 86"})
    check("roh: positive Antwort mit SID", raw.ok and raw.data[:3] == bytes([0x62, 0xF1, 0x86]), raw.data.hex())

    # echter Pruefling-Weg: Schluessel aus dem gemeinsamen Ordner (hier falsch) -> NRC 0x35
    import uds_sim
    shared = Path(window._uds._shared_root())
    (shared / "keys").mkdir(parents=True, exist_ok=True)
    (shared / "keys" / "falsch.key").write_text("00" * 32, encoding="utf-8")
    window._settings.set_dut_config(name="Test", can_device=dw.SIM_CAN_ID, odx="", key="falsch")
    from uds_sim import sim_odx_text
    odx_file = DATA / "sim.odx-d"
    odx_file.write_text(sim_odx_text(), encoding="utf-8")
    window._settings.set_dut_config(odx=str(odx_file))
    spin(app, 1.0)
    check("Pruefling aus Datei", uds.configured() and uds.odx().source == str(odx_file))
    check("Session Extended", uds.call("session", {"session": 3}).ok)
    wrong = uds.call("unlock", {"level": "Application"})
    check("falscher Schluessel aus dem gemeinsamen Ordner -> NRC 0x35", wrong.nrc == 0x35, wrong.describe())
    window._settings.set_dut_config(key="fehlt")
    missing = uds.call("unlock", {})
    check("fehlende Schluesseldatei -> Meldung", not missing.ok and "nicht gefunden" in missing.message, missing.message)
    (shared / "keys" / "richtig.key").write_text(uds_sim.SIM_MASTER_KEY.hex(), encoding="utf-8")
    window._settings.set_dut_config(key="richtig")
    uds.call("session", {"session": 3})
    check("richtiger Schluessel aus dem gemeinsamen Ordner", uds.call("unlock", {}).ok)
    window._settings.set_dut_config(odx=str(DATA / "gibtsnicht.pdx"))
    spin(app, 0.5)
    check("fehlende ODX -> Fehler im Status", not uds.configured()
          and "ODX-Fehler" in window.settings_tab.dut_editor._status.text())
    window.close()
    app.processEvents()


def main() -> int:
    section_odx()
    section_keys()
    section_window()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

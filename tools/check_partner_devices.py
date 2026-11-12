"""Prueft die Bruecke fuer Partnergeraete (lab_gui/partner_devices.py, 0.52.0):

- Rundlauf: Worker-Zustand -> LiveState-Felder (Freigabe) -> partner_devices -> Worker-gleicher Zustand
  (Batterie-Simulator, Funktionsgenerator, Netzteil mit Ausgang/Sollwerten, Last mit Eingang)
- Schnappschuss -> Signale: bekannt, online, bedienbar (Freigabe "Steuern", Hauptschalter, frei, nicht veraltet),
  vergessen; Durchreich-Kacheln des Partners ("@...") werden nicht gespiegelt
- Bedienung in einer Partnerkachel -> richtiger Aktionscode/Wert/Kanal (Netzteil, Last, Batterie-Simulator,
  Funktionsgenerator, microHIL); gesperrte Kachel sendet nicht

Qt ohne Fenster, keine Hardware.  Aufruf:  python tools/check_partner_devices.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class FakeLink(QObject):
    snapshot_changed = Signal(object)
    action_finished = Signal(int, str, str, bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple] = []

    def name(self) -> str:
        return "Pi"

    def send_action(self, device_id, action, value=None, channel=None, label=""):
        self.sent.append((device_id, action, value, channel))
        return len(self.sent)


def tiles_from(state, ids) -> list[dict]:
    """Kacheln wie /api/v1/tiles aus einem LiveState (nur Felder, wie share_api sie liefert)."""
    snap = state.snapshot()
    out = []
    for device_id in ids:
        entry = snap["devices"][device_id]
        out.append({"id": device_id, "kind": entry["kind"], "label": entry["label"], "online": True,
                    "stale": False, "control": True, "control_available": True,
                    "fields": [{"key": k, "value": v} if not isinstance(v, str) else {"key": k, "text": v}
                               for k, v in entry["fields"].items()]})
    return out


def main() -> int:
    QApplication.instance() or QApplication([])
    from live_state import LiveState
    from partner_client import PartnerSnapshot
    from partner_devices import PartnerDevices, battsim_state_from, fg_state_from, fields_of

    print("Rundlauf Zustand -> Freigabe -> Partnerkachel")
    state = LiveState()
    bs = {"status_name": "Fernsteuerung", "output_on": True, "remote": True, "stack_voltage": 50.4,
          "voltage_limit": 4.3, "fail_reason_name": "", "controller_on": True, "supply_current": 0.42,
          "fw_version": "0.5.7", "foreign_sender": False,
          "cells": [{"index": n, "voltage": 3.6, "setpoint": 3.6, "enabled": n != 3, "status_name": "OK"}
                    for n in range(1, 15)]}
    fg = {"outputs": [True, False], "phase": 90.0,
          "channels": [{"waveform": 1, "frequency": 1000.0, "amplitude": 2.0, "offset": 0.5, "duty": 30.0},
                       {"waveform": 6, "frequency": 50.0, "amplitude": 1.0, "offset": 0.0, "duty": 50.0}]}
    for kind, device_id in (("battsim", "battsim:x"), ("fg", "fg:x"), ("psu", "psu:x"), ("load", "load:x")):
        state.on_device_known(kind, device_id, device_id)
        state.set_online(device_id, True)
    state.on_battsim_state("battsim:x", bs)
    state.on_fg_state("fg:x", fg)
    state.on_psu_measurement("psu:x", 12.0, 0.5, True)
    state.on_psu_output_state("psu:x", True)
    state.on_psu_setpoints("psu:x", 12.5, 1.5)
    state.on_load_measurement("load:x", 11.9, 1.0, 11.9)
    state.on_load_input_state("load:x", True)
    try:
        tiles = tiles_from(state, ["battsim:x", "fg:x", "psu:x", "load:x"])
    except Exception as exc:  # noqa: BLE001
        check("LiveState-Schnappschuss lesbar", False, f"{type(exc).__name__}: {exc}")
        return 1
    by_id = {t["id"]: fields_of(t) for t in tiles}
    back = battsim_state_from(by_id["battsim:x"])
    same = {k: back[k] for k in ("status_name", "output_on", "remote", "stack_voltage", "voltage_limit",
                                 "controller_on", "supply_current", "fw_version", "foreign_sender")}
    check("Batterie-Simulator: Kopfwerte", same == {k: bs[k] for k in same}, str(same))
    check("Batterie-Simulator: 14 Zellen mit Ist/Soll/Freigabe/Status",
          [(c["index"], c["voltage"], c["setpoint"], c["enabled"], c["status_name"]) for c in back["cells"]]
          == [(c["index"], c["voltage"], c["setpoint"], c["enabled"], c["status_name"]) for c in bs["cells"]])
    check("Funktionsgenerator: Rundlauf", fg_state_from(by_id["fg:x"]) == fg, str(fg_state_from(by_id["fg:x"])))
    psu = by_id["psu:x"]
    check("Netzteil: Ausgang und Sollwerte in der Freigabe",
          psu.get("output") == 1 and psu.get("set_voltage") == 12.5 and psu.get("set_current") == 1.5, str(psu))
    check("Last: Eingang in der Freigabe", by_id["load:x"].get("input") == 1, str(by_id["load:x"]))

    print("Schnappschuss -> Signale")
    link = FakeLink()
    pd = PartnerDevices(link)
    got: dict[str, list] = {}
    for name in ("device_known", "device_forgotten", "online_changed", "control_changed", "psu_measurement",
                 "psu_output_state", "psu_setpoints", "battsim_state", "fg_state", "load_input_state"):
        getattr(pd, name).connect(lambda *args, n=name: got.setdefault(n, []).append(args))
    passthrough = {"id": "@PC/psu:1", "kind": "psu", "label": "x", "online": True, "fields": []}
    snap = PartnerSnapshot(host="pi", remote_effective=True, lock="free", tiles=tiles + [passthrough])
    pd.on_snapshot(snap)
    known = {args[1] for args in got.get("device_known", [])}
    check("bekannt als @pi/<id>, Durchreich-Kachel nicht gespiegelt",
          known == {"@pi/battsim:x", "@pi/fg:x", "@pi/psu:x", "@pi/load:x"}, str(known))
    check("online und bedienbar gemeldet", all(a[2] for a in got.get("online_changed", []))
          and all(a[1] for a in got.get("control_changed", [])))
    check("Netzteil-Werte", got.get("psu_measurement") == [("@pi/psu:x", 12.0, 0.5, True)]
          and got.get("psu_output_state") == [("@pi/psu:x", True)]
          and got.get("psu_setpoints") == [("@pi/psu:x", 12.5, 1.5)], str(got.get("psu_measurement")))
    got.clear()
    pd.on_snapshot(snap)
    check("unveraendert: nicht erneut bekannt/online gemeldet", not got.get("device_known")
          and not got.get("online_changed") and got.get("psu_measurement"))
    locked = PartnerSnapshot(host="pi", remote_effective=False, lock="free", tiles=tiles)
    got.clear()
    pd.on_snapshot(locked)
    check("Hauptschalter dort aus -> nicht bedienbar", got.get("control_changed")
          and not any(a[1] for a in got["control_changed"]))
    link.sent.clear()
    check("gesperrte Kachel sendet nicht", pd.send("@pi/psu:x", "PSU_OUT_ON") == 0 and not link.sent)
    pd.on_snapshot(snap)
    stale = [dict(t, stale=True) if t["id"] == "psu:x" else t for t in tiles]
    got.clear()
    pd.on_snapshot(PartnerSnapshot(host="pi", remote_effective=True, lock="free", tiles=stale))
    check("veraltete Werte -> getrennt, nicht bedienbar",
          ("psu", "@pi/psu:x", False) in got.get("online_changed", []) and ("@pi/psu:x", False)
          in got.get("control_changed", []) and not got.get("psu_measurement"))
    pd.on_snapshot(snap)
    got.clear()
    pd.on_snapshot(PartnerSnapshot(host="pi", remote_effective=True, lock="free", tiles=tiles[:3]))
    check("nicht mehr freigegeben -> vergessen", got.get("device_forgotten") == [("load", "@pi/load:x")])
    pd.on_snapshot(snap)

    print("Bedienung -> Aktionen")
    from control_tab import BattSimControlGroup, FgControlGroup, HilControlGroup, LoadControlGroup, PsuControlGroup
    cases = [
        (PsuControlGroup, "psu", "@pi/psu:x", [
            (lambda s, d: s.set_voltage.emit(d, 7.5), ("psu:x", "PSU_VOLT", 7.5, None)),
            (lambda s, d: s.set_current.emit(d, 1.2), ("psu:x", "PSU_CURR", 1.2, None)),
            (lambda s, d: s.set_output.emit(d, False), ("psu:x", "PSU_OUT_OFF", None, None))]),
        (LoadControlGroup, "load", "@pi/load:x", [
            (lambda s, d: s.apply_setpoint.emit(d, "CURR", 2.0), ("load:x", "CURR", 2.0, None)),
            (lambda s, d: s.set_input.emit(d, True), ("load:x", "OUT_ON", None, None))]),
        (BattSimControlGroup, "battsim", "@pi/battsim:x", [
            (lambda s, d: s.set_remote.emit(d, True), ("battsim:x", "BS_REMOTE_ON", None, None)),
            (lambda s, d: s.set_cell_voltage.emit(d, 4, 3.7), ("battsim:x", "BS_CELL_VOLT", 3.7, 4)),
            (lambda s, d: s.set_cell_enabled.emit(d, 2, False), ("battsim:x", "BS_CELL_OFF", None, 2)),
            (lambda s, d: s.set_all_voltages.emit(d, 3.5), ("battsim:x", "BS_ALL_VOLT", 3.5, None)),
            (lambda s, d: s.reset_fail.emit(d), ("battsim:x", "BS_RESET_FAIL", None, None))]),
        (FgControlGroup, "fg", "@pi/fg:x", [
            (lambda s, d: s.set_waveform.emit(d, 2, 1), ("fg:x", "FG_WAVE_SQUARE", None, 2)),
            (lambda s, d: s.set_frequency.emit(d, 1, 500.0), ("fg:x", "FG_FREQ", 500.0, 1)),
            (lambda s, d: s.set_output.emit(d, 1, True), ("fg:x", "FG_OUT_ON", None, 1))]),
    ]
    pd.on_snapshot(PartnerSnapshot(host="pi", remote_effective=True, lock="free", tiles=tiles + [
        {"id": "hil:x", "kind": "hil", "label": "HIL", "online": True, "stale": False, "control": True,
         "control_available": True, "fields": []}]))
    cases.append((HilControlGroup, "hil", "@pi/hil:x", [
        (lambda s, d: s.set_relay.emit(d, 3, True), ("hil:x", "HIL_RELAY_ON", None, 3)),
        (lambda s, d: s.set_analog_output.emit(d, 1, 1500), ("hil:x", "HIL_AOUT", 1500, 1))]))
    for cls, kind, rid, actions in cases:
        section = cls(rid, "x")
        pd.wire_section(kind, rid, section)
        for trigger, expected in actions:
            link.sent.clear()
            trigger(section, rid)
            check(f"{kind}: {expected[1]}", link.sent == [expected], str(link.sent))
        section.deleteLater()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

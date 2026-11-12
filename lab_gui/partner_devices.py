"""Geraete des Partners als ganz normale Kacheln in Uebersicht und Steuerung (Nutzerwunsch 12.11.2026).

Bis 0.51 hatte der Verbund einen eigenen Reiter mit allgemeinen Partnerkacheln. Jetzt erscheinen die
freigegebenen Geraete des anderen Rechners in Uebersicht und Steuerung mit denselben Kacheln wie eigene
Geraete; einziger Unterschied ist ein Symbol in der Kachel (Geraet haengt am anderen Rechner).

Dieses Modul uebersetzt in beide Richtungen:
  * Schnappschuss des Partners (PartnerLink.snapshot_changed, Kacheln mit Feldern) -> dieselben Signale,
    die sonst der DeviceWorker sendet (psu_measurement, battsim_state, ...), mit der Kennung
    "@<rechner>/<id>" (verbund_ids.qualify). MainWindow verbindet sie mit Uebersicht und Steuerung.
  * Bedienung in einer Partnerkachel (Signale der *ControlGroup) -> Aktion beim Partner
    (PartnerLink.send_action, dieselben Codes wie Netzwerk-Freigabe und MCP, remote_actions.py).

Was der Partner nicht fernsteuern laesst (Handbedienung, OVP/OCP, Speicherplaetze, PWM, CAN senden),
ist in der Kachel gesperrt; die Kachel selbst ist nur bedienbar, wenn der Partner es erlaubt
(control_available) und dort die Fernsteuerung an ist.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from verbund_ids import qualify, split

# Geraetearten mit eigener Kachel; alles andere (Oszilloskope, J-Link) bleibt am eigenen Rechner
KINDS = ("psu", "load", "can", "battsim", "fg", "hil")
FG_WAVE_ACTIONS = {0: "FG_WAVE_SINE", 1: "FG_WAVE_SQUARE", 2: "FG_WAVE_PULSE", 3: "FG_WAVE_TRIANGLE",
                   6: "FG_WAVE_DC"}
FG_WAVE_CODES = {"Sinus": 0, "Rechteck": 1, "Puls": 2, "Dreieck": 3, "Teilsinus": 4, "CMOS": 5, "DC": 6,
                 "Halbwelle": 7}


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _flag(value):
    return None if value is None else bool(_num(value))


def fields_of(tile: dict) -> dict:
    """Felder einer Partnerkachel als {key: Wert} (Text statt Zahl, wo es eine Textanzeige gibt)."""
    out = {}
    for field in tile.get("fields") or []:
        key = field.get("key")
        if key:
            out[key] = field.get("text") if "text" in field else field.get("value")
    return out


def battsim_state_from(fields: dict, cell_count: int = 14) -> dict:
    """Umkehrung von LiveState.on_battsim_state: Felder -> Zustand wie device_worker.battsim_state."""
    cells = []
    for n in range(1, cell_count + 1):
        if f"cell{n}_voltage" not in fields and f"cell{n}_setpoint" not in fields:
            continue
        status_name = fields.get(f"cell{n}_status") or ""
        enabled = _flag(fields.get(f"cell{n}_on"))
        cells.append({
            "index": n, "voltage": fields.get(f"cell{n}_voltage"), "setpoint": _num(fields.get(f"cell{n}_setpoint")),
            "enabled": enabled, "status_name": status_name,
            # Die Freigabe gibt nur den gemeldeten Zustand weiter; angefordert = gemeldet, Fehler/OL aus dem Status
            # (Namen wie battsim.driver.CELL_STATUS_NAMES)
            "enable": bool(enabled), "error": status_name == "Sollwertfehler", "below_floor": status_name == "OL",
        })
    status_name = fields.get("status") or ""
    return {
        "status_name": status_name, "failed": status_name == "FAIL", "output_on": _flag(fields.get("output")),
        "output_requested": bool(_flag(fields.get("output"))),
        "remote": bool(_flag(fields.get("remote"))), "stack_voltage": fields.get("stack_voltage"),
        # ohne Meldung der Treiber-Grundwert (battsim: 4,30 V), sonst setzte die Kachel ihren Bereich auf 0 V
        "voltage_limit": _num(fields.get("voltage_limit"), 4.3) or 4.3,
        "fail_reason_name": fields.get("fail_reason") or "",
        "controller_on": _flag(fields.get("controller")), "supply_current": fields.get("supply_current"),
        "fw_version": fields.get("fw_version"), "foreign_sender": bool(_flag(fields.get("foreign_sender"))),
        "cells": cells,
    }


def fg_state_from(fields: dict) -> dict:
    """Umkehrung von LiveState.on_fg_state."""
    channels, outputs = [], []
    for n in (1, 2):
        outputs.append(bool(_flag(fields.get(f"out{n}"))))
        channels.append({
            "waveform": FG_WAVE_CODES.get(str(fields.get(f"wave{n}") or ""), 0),
            "frequency": _num(fields.get(f"freq{n}")), "amplitude": _num(fields.get(f"ampl{n}")),
            "offset": _num(fields.get(f"offs{n}")), "duty": _num(fields.get(f"duty{n}")),
        })
    return {"outputs": outputs, "channels": channels, "phase": _num(fields.get("phase"))}


class PartnerDevices(QObject):
    """Partnerkacheln -> Worker-gleiche Signale; Bedienung -> Partner-Aktionen."""

    device_known = Signal(str, str, str)       # kind, "@host/id", Name
    device_forgotten = Signal(str, str)        # kind, "@host/id"
    online_changed = Signal(str, str, bool)    # kind, "@host/id", online
    control_changed = Signal(str, bool)        # "@host/id", bedienbar
    psu_measurement = Signal(str, float, float, bool)
    psu_output_state = Signal(str, bool)
    psu_setpoints = Signal(str, float, float)
    psu_ratings = Signal(str, float, float)
    load_measurement = Signal(str, float, float, float)
    load_function_state = Signal(str, str)
    load_input_state = Signal(str, bool)
    can_stats = Signal(str, int, int)
    battsim_state = Signal(str, object)
    fg_state = Signal(str, object)
    hil_digital_state = Signal(str, list, list)
    hil_relay_state = Signal(str, list)
    hil_analog_input = Signal(str, list)
    action_failed = Signal(str, str, str)      # "@host/id", Aktion, Grund (Bedienung aus einer Kachel)
    # PI-43 b: Name und Kachelfarbe kommen vom Besitzer, Last-Sollwert folgt dem Geraet
    label_changed = Signal(str, str, str)      # kind, "@host/id", Name
    color_changed = Signal(str, object)        # "@host/id", Farbschluessel oder None
    load_setpoint = Signal(str, str, float)    # "@host/id", Modus (CURR/VOLT/RES/POW), Wert

    def __init__(self, link, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._link = link
        self._known: dict[str, tuple[str, str]] = {}    # rid -> (kind, label)
        self._online: dict[str, bool] = {}
        self._control: dict[str, bool] = {}
        self._colors: dict[str, str | None] = {}
        self._requests: dict[int, str] = {}             # eigene Aktionsanfragen -> rid
        self._host = ""
        link.snapshot_changed.connect(self.on_snapshot)
        link.action_finished.connect(self._on_action_finished)

    def _on_action_finished(self, request_id: int, _device_id: str, action: str, ok: bool, message: str) -> None:
        rid = self._requests.pop(request_id, None)
        if rid is not None and not ok:
            self.action_failed.emit(rid, action, message)

    # -- Partner -> Kacheln --------------------------------------------------------

    def known(self) -> dict[str, tuple[str, str]]:
        return dict(self._known)

    def is_remote(self, device_id: str) -> bool:
        return device_id in self._known

    def on_snapshot(self, snap) -> None:
        if snap is None:
            for rid, (kind, _label) in self._known.items():
                self._set_online(kind, rid, False)
                self._set_control(rid, False)
            return
        self._host = snap.host or self._host
        allowed = bool(snap.remote_effective) and snap.lock == "free"
        seen = set()
        for tile in snap.tiles or []:
            kind, tile_id = tile.get("kind"), tile.get("id")
            if kind not in KINDS or not tile_id or tile_id.startswith("@"):
                continue   # Partnerkacheln des Partners (Durchreichen) nicht noch einmal spiegeln
            rid = qualify(self._host, tile_id)
            seen.add(rid)
            label = str(tile.get("label") or tile_id)
            previous = self._known.get(rid)
            if previous is None or previous[0] != kind:
                self._known[rid] = (kind, label)
                self.device_known.emit(kind, rid, label)
            elif previous[1] != label:   # am Besitzer umbenannt (auch von hier aus, PI-43 b)
                self._known[rid] = (kind, label)
                self.label_changed.emit(kind, rid, label)
            if "color" in tile and (rid not in self._colors or self._colors[rid] != tile.get("color")):
                self._colors[rid] = tile.get("color") or None
                self.color_changed.emit(rid, self._colors[rid])
            online = bool(tile.get("online", True)) and not tile.get("stale")
            self._set_online(kind, rid, online)
            self._set_control(rid, online and allowed and bool(tile.get("control"))
                              and bool(tile.get("control_available")))
            if online:
                self._emit_values(kind, rid, tile)
        for rid in [r for r in self._known if r not in seen]:
            kind, _label = self._known.pop(rid)
            self._online.pop(rid, None)
            self._control.pop(rid, None)
            self._colors.pop(rid, None)
            self.device_forgotten.emit(kind, rid)

    def forget_all(self) -> None:
        for rid, (kind, _label) in list(self._known.items()):
            self.device_forgotten.emit(kind, rid)
        self._known.clear()
        self._online.clear()
        self._control.clear()
        self._colors.clear()

    def _set_online(self, kind: str, rid: str, online: bool) -> None:
        if self._online.get(rid) != online:
            self._online[rid] = online
            self.online_changed.emit(kind, rid, online)

    def _set_control(self, rid: str, allowed: bool) -> None:
        if self._control.get(rid) != allowed:
            self._control[rid] = allowed
            self.control_changed.emit(rid, allowed)

    def _emit_values(self, kind: str, rid: str, tile: dict) -> None:
        f = fields_of(tile)
        if kind == "psu":
            self.psu_measurement.emit(rid, _num(f.get("voltage")), _num(f.get("current")), f.get("mode") == "CC")
            if "output" in f:
                self.psu_output_state.emit(rid, bool(_flag(f.get("output"))))
            if "set_voltage" in f or "set_current" in f:
                self.psu_setpoints.emit(rid, _num(f.get("set_voltage")), _num(f.get("set_current")))
            ratings = tile.get("ratings") or {}
            if ratings:
                self.psu_ratings.emit(rid, _num(ratings.get("max_voltage")), _num(ratings.get("max_current")))
        elif kind == "load":
            self.load_measurement.emit(rid, _num(f.get("voltage")), _num(f.get("current")), _num(f.get("power")))
            if f.get("mode"):
                self.load_function_state.emit(rid, str(f.get("mode")))
            if "input" in f:
                self.load_input_state.emit(rid, bool(_flag(f.get("input"))))
            if f.get("setpoint_mode") and f.get("setpoint") is not None:
                self.load_setpoint.emit(rid, str(f.get("setpoint_mode")), _num(f.get("setpoint")))
        elif kind == "can":
            self.can_stats.emit(rid, int(_num(f.get("tx_count"))), int(_num(f.get("rx_count"))))
        elif kind == "battsim":
            self.battsim_state.emit(rid, battsim_state_from(f))
        elif kind == "fg":
            self.fg_state.emit(rid, fg_state_from(f))
        elif kind == "hil":
            def series(prefix: str) -> list:
                keys = sorted((k for k in f if k.startswith(prefix) and k[len(prefix):].isdigit()),
                              key=lambda k: int(k[len(prefix):]))
                return [f[k] for k in keys]
            self.hil_digital_state.emit(rid, [bool(_flag(v)) for v in series("in")],
                                        [bool(_flag(v)) for v in series("out")])
            self.hil_relay_state.emit(rid, [bool(_flag(v)) for v in series("rel")])
            self.hil_analog_input.emit(rid, [_num(v) for v in series("ain")])

    # -- Kachel -> Partner -----------------------------------------------------------

    def send(self, rid: str, code: str, value: float | None = None, channel: int | None = None) -> int:
        """Aktion beim Partner; 0, wenn die Kachel gerade nicht bedienbar ist."""
        if not self._control.get(rid):
            self.action_failed.emit(rid, code, "remote_control_inactive")
            return 0
        _host, tile_id = split(rid)
        label = f"{self._link.name()} {self._known.get(rid, ('', tile_id))[1]}"
        request_id = self._link.send_action(tile_id, code, value, channel, label)
        if request_id:
            self._requests[request_id] = rid
        return request_id

    def send_meta(self, rid: str, meta: dict) -> int:
        """Name/Kachelfarbe einer Partnerkachel beim Besitzer aendern (PI-43 b). Sichtbar wird es mit dem
        naechsten Abgleich -- der Besitzer fuehrt."""
        if rid not in self._known:
            return 0
        _host, tile_id = split(rid)
        request_id = self._link.send_meta(tile_id, meta, self._known[rid][1])
        if request_id:
            self._requests[request_id] = rid
        return request_id

    def wire_section(self, kind: str, rid: str, section) -> None:
        """Bedien-Signale einer Partnerkachel auf Partner-Aktionen legen (statt auf den DeviceWorker)."""
        send = self.send
        if kind == "psu":
            section.set_voltage.connect(lambda d, v: send(d, "PSU_VOLT", v))
            section.set_current.connect(lambda d, a: send(d, "PSU_CURR", a))
            section.set_output.connect(lambda d, on: send(d, "PSU_OUT_ON" if on else "PSU_OUT_OFF"))
        elif kind == "load":
            section.apply_setpoint.connect(lambda d, mode, v: send(d, mode, v))
            section.set_input.connect(lambda d, on: send(d, "OUT_ON" if on else "OUT_OFF"))
        elif kind == "battsim":
            section.set_remote.connect(lambda d, on: send(d, "BS_REMOTE_ON" if on else "BS_REMOTE_OFF"))
            section.set_output.connect(lambda d, on: send(d, "BS_OUT_ON" if on else "BS_OUT_OFF"))
            section.set_cell_voltage.connect(lambda d, cell, v: send(d, "BS_CELL_VOLT", v, cell))
            section.set_all_voltages.connect(lambda d, v: send(d, "BS_ALL_VOLT", v))
            section.set_cell_enabled.connect(lambda d, cell, on: send(d, "BS_CELL_ON" if on else "BS_CELL_OFF",
                                                                      None, cell))
            section.set_all_enabled.connect(lambda d, on: send(d, "BS_ALL_ON" if on else "BS_ALL_OFF"))
            section.reset_fail.connect(lambda d: send(d, "BS_RESET_FAIL"))
        elif kind == "fg":
            section.set_output.connect(lambda d, ch, on: send(d, "FG_OUT_ON" if on else "FG_OUT_OFF", None, ch))
            section.set_waveform.connect(
                lambda d, ch, code: FG_WAVE_ACTIONS.get(code) and send(d, FG_WAVE_ACTIONS[code], None, ch))
            section.set_frequency.connect(lambda d, ch, v: send(d, "FG_FREQ", v, ch))
            section.set_amplitude.connect(lambda d, ch, v: send(d, "FG_AMPL", v, ch))
            section.set_offset.connect(lambda d, ch, v: send(d, "FG_OFFS", v, ch))
            section.set_duty.connect(lambda d, ch, v: send(d, "FG_DUTY", v, ch))
            section.set_phase.connect(lambda d, v: send(d, "FG_PHASE", v, 2))
        elif kind == "hil":
            section.set_output.connect(lambda d, ch, on: send(d, "HIL_OUT_ON" if on else "HIL_OUT_OFF", None, ch))
            section.set_relay.connect(lambda d, ch, on: send(d, "HIL_RELAY_ON" if on else "HIL_RELAY_OFF", None, ch))
            section.set_analog_output.connect(lambda d, ch, mv: send(d, "HIL_AOUT", mv, ch))

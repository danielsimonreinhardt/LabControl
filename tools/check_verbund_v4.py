"""Prueft den Verbund Pi + PC, Stufe V4 (PI-28, PI-29, PI-32, PI-33; 0.49.0) mit ZWEI Hauptfenstern:

  B = "Pi" (Besitzer): Simulation, Freigabe auf einem freien Port, Hauptschalter an
  A = "PC": Simulation, B als Verbund-Partner, eigene Freigabe (dort spricht der MCP-Server)

- api      -- share_api allein: Partnerkacheln nur fuer Aufrufer auf diesem Rechner, Pfade mit "@host/id",
              Abweisungen (eigener Hauptschalter, veralteter Stand, Wertebereich)
- mcp      -- wie der MCP-Server ueber A: Partnergeraete lesen und bedienen, Belegung "Claude (MCP)" bei B,
              Kachel dort gesperrt, ALLE AUS ueber A wirkt bei B, Freigabe nach Leerlauf, Abschalten bei
              B nach 3 s ohne Lebenszeichen
- record   -- Partnerwerte im Verlauf und in der Aufzeichnung von A (PI-33)
- warn     -- LAN-Oszilloskop an beiden Rechnern, Uhrabweichung (PI-29, PI-33)
- can      -- B fuehrt einen Batterie-Simulator: A sendet 0x130 nicht (PI-28)

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_verbund_v4.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-verbund4-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN_A, TOKEN_B = "token-A", "token-B"
HOST_B = "pi-sim"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return bool(until()) if until is not None else False


def section_api() -> None:
    print("api -- share_api allein")
    import share_api

    class State:
        def __init__(self, snap):
            self._snap = snap

        def snapshot(self):
            return json.loads(json.dumps(self._snap))

    class Executor:
        def __init__(self):
            self.calls = []

        def submit_partner_action(self, *args):
            self.calls.append(args)
            return share_api.RemoteResult("ok")

    tile = {"id": "psu:1", "kind": "psu", "label": "Netzteil 1", "online": True, "read": True, "control": True,
            "control_available": True, "control_blocked": "", "fields": [{"key": "voltage", "value": 5.0}],
            "ratings": {"voltage_max": 30.0, "current_max": 3.0}}
    base = {"devices": {}, "share": {"token": "t", "read_requires_token": False, "devices": {},
                                     "local_bypass": True},
            "lock": "free", "remote": {"active": False}, "partner": {"host": "pi", "name": "Pi", "tiles": [tile],
                                                                     "age_s": 0.5}}
    executor = Executor()
    headers = {"Authorization": "Bearer t"}

    def call(method, path, snap, local, body=None):
        response = share_api.dispatch(method, path, headers.get, State(snap), {"host": "pc"},
                                      body=json.dumps(body).encode() if body else b"", executor=executor,
                                      client="127.0.0.1", local=local)
        return response.status, json.loads(response.body or b"{}")

    status, body = call("GET", "/api/v1/values", base, True)
    ids = [t["id"] for t in body.get("tiles", [])]
    check("lokal: Partnerkachel als '@pi/psu:1' mit Partnernamen", ids == ["@pi/psu:1"]
          and body["tiles"][0]["label"] == "Pi · Netzteil 1" and body["tiles"][0]["partner"]["id"] == "psu:1",
          str(body))
    status, body = call("GET", "/api/v1/values", base, False)
    check("nicht lokal (z. B. der Partner selbst): nichts durchgereicht", body.get("tiles") == [], str(body))
    status, body = call("GET", "/api/v1/tiles", base, True)
    check("Kachelliste enthaelt die Partnerkachel", [t["id"] for t in body["tiles"]] == ["@pi/psu:1"], str(body))
    encoded = "/api/v1/tiles/" + urllib.parse.quote("@pi/psu:1", safe=":")
    status, body = call("GET", encoded, base, True)
    check("GET mit %2F (wie der MCP-Server kodiert)", status == 200 and body.get("id") == "@pi/psu:1",
          f"{status} {body}")
    status, body = call("GET", "/api/v1/tiles/a/b", base, True)
    check("andere Pfade mit '/' bleiben 404", status == 404)

    status, body = call("POST", encoded + "/actions", base, True, {"action": "PSU_VOLT", "value": 4.0})
    check("POST lokal -> an den Executor mit der ID beim Partner",
          status == 200 and executor.calls and executor.calls[-1][:4] == ("psu:1", "@pi/psu:1", "psu", "PSU_VOLT"),
          f"{status} {body} {executor.calls}")
    status, body = call("POST", encoded + "/actions", base, True, {"action": "PSU_VOLT", "value": 99.0})
    check("Wert ueber den Nennwerten des Partnergeraets -> 400", status == 400, f"{status} {body}")
    no_bypass = json.loads(json.dumps(base))
    no_bypass["share"]["local_bypass"] = False
    status, body = call("POST", encoded + "/actions", no_bypass, True, {"action": "PSU_OUT_ON"})
    check("ohne eigenen Hauptschalter -> 403 remote_control_inactive, at=local",
          status == 403 and body.get("error") == "remote_control_inactive" and body.get("at") == "local",
          f"{status} {body}")
    blocked = json.loads(json.dumps(base))
    blocked["partner"]["tiles"][0].update(control_available=False, control_blocked="remote_control_inactive")
    status, body = call("POST", encoded + "/actions", blocked, True, {"action": "PSU_OUT_ON"})
    check("Hauptschalter beim Partner aus -> 403, at=Pi", status == 403 and body.get("at") == "Pi",
          f"{status} {body}")
    stale = json.loads(json.dumps(base))
    stale["partner"]["age_s"] = 5.0
    status, body = call("POST", encoded + "/actions", stale, True, {"action": "PSU_OUT_ON"})
    check("Stand des Partners veraltet -> 409 partner_unreachable",
          status == 409 and body.get("error") == "partner_unreachable", f"{status} {body}")
    status, body = call("POST", encoded + "/actions", base, False, {"action": "PSU_OUT_ON"})
    check("POST nicht lokal -> 404 (nicht durchgereicht)", status == 404, f"{status} {body}")


def make_window(settings_data: dict):
    from main_window import MainWindow
    from settings import Settings
    settings = Settings()
    settings._data = {"simulation_mode": True, **settings_data}
    window = MainWindow(settings)
    window.resize(1280, 800)
    window.show()
    return window


def http(port: int, token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def http_async(app, *args) -> tuple[int, dict]:
    """HTTP aus einem eigenen Thread, waehrenddessen laeuft die Ereignisschleife (der Server wartet auf den
    GUI-Thread)."""
    result: list = []
    thread = threading.Thread(target=lambda: result.append(http(*args)), daemon=True)
    thread.start()
    spin(app, 25.0, lambda: bool(result))
    return result[0] if result else (0, {})


def main() -> int:
    app = QApplication.instance() or QApplication([])
    section_api()

    import no_hardware
    no_hardware.block()
    import device_worker as dw
    import verbund_ids
    import app_logging

    port_b, port_a = free_port(), free_port()
    window_b = make_window({
        "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port_b, "share_token": TOKEN_B,
        "share_devices": {dw.SIM_PSU_ID: {"read": True, "control": True},
                          dw.SIM_LOAD_ID: {"read": True, "control": True},
                          dw.SIM_BATTSIM_ID: {"read": True, "control": True}},
    })
    server_b = window_b._share_server
    server_b._app_info = {**server_b._app_info, "host": HOST_B}
    if server_b._server is not None:
        server_b._server.share_app_info = server_b._app_info
    window_b._on_share_control_toggled(True)
    window_a = make_window({"partner_enabled": True, "partner_address": f"127.0.0.1:{port_b}",
                            "partner_token": TOKEN_B, "partner_name": "Pi",
                            "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port_a,
                            "share_token": TOKEN_A, "share_local_bypass": False})
    partner_psu = verbund_ids.qualify(HOST_B, dw.SIM_PSU_ID)
    ready = spin(app, 15.0, lambda: window_a._partner.is_connected() and dw.SIM_PSU_ID in window_b._worker._psus
                 and window_a._partner.snapshot() is not None
                 and any(t.get("id") == dw.SIM_BATTSIM_ID for t in window_a._partner.snapshot().tiles))
    check("A mit B verbunden, Netzteil und Batterie-Simulator von B sichtbar", ready)
    mock_b = window_b._worker._psus.get(dw.SIM_PSU_ID)
    leases_b = window_b._live_state.leases
    quoted = "/api/v1/tiles/" + urllib.parse.quote(partner_psu, safe=":")

    print("mcp -- Claude ueber A")
    status, body = http(port_a, TOKEN_A, "GET", "/api/v1/values")
    ids = [t["id"] for t in body.get("tiles", [])]
    check("A listet die Geraete von B als '@pi-sim/...'", partner_psu in ids, str(ids))
    status, body = http_async(app, port_a, TOKEN_A, "POST", quoted + "/actions", {"action": "PSU_VOLT", "value": 4.0})
    check("ohne Hauptschalter an A -> 403 (at=local)", status == 403 and body.get("at") == "local", f"{status} {body}")
    window_a._on_share_control_toggled(True)
    status, body = http_async(app, port_a, TOKEN_A, "POST", quoted + "/actions", {"action": "PSU_VOLT", "value": 4.0})
    check("PSU_VOLT 4 V ueber A -> ok", status == 200 and body.get("ok") is True, f"{status} {body}")
    check("Sollwert bei B angekommen", mock_b is not None and mock_b.get_setpoint()[0] == 4.0,
          str(mock_b.get_setpoint() if mock_b else None))
    lease = leases_b.holder_of(dw.SIM_PSU_ID)
    check("bei B belegt durch A, Lauf 'Claude (MCP)'",
          lease is not None and lease.holder_name() == app_logging.host_name() and lease.run == "Claude (MCP)",
          str(lease and (lease.holder_name(), lease.run)))
    spin(app, 0.6)
    check("Kachel bei B gesperrt", "belegt durch" in window_b.control_tab.lease_text(dw.SIM_PSU_ID),
          window_b.control_tab.lease_text(dw.SIM_PSU_ID))
    status, body = http_async(app, port_b, TOKEN_B, "POST", f"/api/v1/tiles/{dw.SIM_PSU_ID}/actions",
                              {"action": "PSU_OUT_OFF"})
    check("fremder Client direkt bei B -> 409 leased", status == 409 and body.get("error") == "leased", str(status))
    status, body = http_async(app, port_a, TOKEN_A, "POST", quoted + "/actions", {"action": "PSU_OUT_ON"})
    check("PSU_OUT_ON ueber A -> ok, Ausgang bei B EIN",
          status == 200 and spin(app, 2.0, lambda: mock_b.get_output() is True), f"{status} {body}")
    partner_load = verbund_ids.qualify(HOST_B, dw.SIM_LOAD_ID)
    status, body = http_async(app, port_a, TOKEN_A, "POST",
                              "/api/v1/tiles/" + urllib.parse.quote(partner_load, safe=":") + "/actions",
                              {"action": "CURR", "value": 0.5})
    check("zweites Geraet (Last) -> Belegung erweitert",
          status == 200 and leases_b.holder_of(dw.SIM_LOAD_ID) is not None
          and leases_b.holder_of(dw.SIM_PSU_ID) is not None, f"{status} {body}")
    status, body = http_async(app, port_a, TOKEN_A, "POST", quoted + "/actions", {"action": "PSU_VOLT", "value": 99})
    check("Wert ueber Nennwert -> 400 schon an A", status == 400, f"{status} {body}")
    status, body = http(port_a, TOKEN_A, "GET", quoted)
    check("read_device ueber A", status == 200 and body.get("id") == partner_psu and body.get("fields"),
          f"{status}")

    status, body = http_async(app, port_a, TOKEN_A, "POST", "/api/v1/all-off", {})
    check("ALLE AUS ueber A -> Ausgang bei B AUS", spin(app, 5.0, lambda: mock_b.get_output() is False),
          f"{status} {body}")
    check("... Belegung bei B freigegeben",
          spin(app, 3.0, lambda: leases_b.holder_of(dw.SIM_PSU_ID) is None and leases_b.holder_of(dw.SIM_LOAD_ID) is None))

    status, body = http_async(app, port_a, TOKEN_A, "POST", quoted + "/actions", {"action": "PSU_OUT_ON"})
    check("wieder EIN (neue Belegung)", status == 200 and leases_b.holder_of(dw.SIM_PSU_ID) is not None)
    window_a._passthrough._last_use = time.monotonic() - 10_000
    window_a._passthrough._check_idle()
    check("Leerlauf -> Belegung freigegeben, Ausgang bleibt",
          spin(app, 3.0, lambda: leases_b.holder_of(dw.SIM_PSU_ID) is None) and mock_b.get_output() is True)

    print("record -- Partnerwerte bei A")
    import timeline_tab
    check("Verlauf kennt das Partner-Netzteil",
          timeline_tab.signal_key(partner_psu, "voltage") in window_a.timeline_tab._series)
    recorder = window_a._recorder
    recorder.set_active_signals(None)
    recorder.start()
    spin(app, 2.5)
    recorder.stop()
    samples = [s for s in recorder.samples() if s.device_id == partner_psu]
    check("Aufzeichnung enthaelt Werte des Partner-Netzteils", len(samples) >= 2, str(len(samples)))
    check("... mit Anzeigenamen 'Pi · ...'", recorder.device_meta().get(partner_psu, ("", ""))[1].startswith("Pi · "),
          str(recorder.device_meta().get(partner_psu)))

    print("warn -- LAN-Oszilloskop doppelt, Uhren")
    window_a._settings._data["scpi_scopes"] = ["192.168.178.250"]
    server_b.set_lan_scopes(["192.168.178.250"])
    warned = spin(app, 3.0, lambda: any("LAN-Oszilloskop" in w for w in window_a.verbund_warnings()))
    check("doppelt eingetragen -> Hinweis an A", warned, str(window_a.verbund_warnings()))
    check("... im Chip sichtbar", "⚠" in window_a._partner_chip.text(), window_a._partner_chip.text())
    from partner_client import PartnerSnapshot
    window_a._check_verbund_warnings(PartnerSnapshot(host=HOST_B, clock_offset_s=2.4))
    check("Uhren 2,4 s auseinander -> Hinweis NTP", any("NTP" in w for w in window_a.verbund_warnings()),
          str(window_a.verbund_warnings()))
    server_b.set_lan_scopes([])
    window_a._settings._data["scpi_scopes"] = []
    check("ohne Doppel und mit gleicher Uhr -> kein Hinweis", spin(app, 3.0, lambda: not window_a.verbund_warnings()),
          str(window_a.verbund_warnings()))

    print("can -- nur B sendet 0x130")
    check("A weiss, dass B einen Batterie-Simulator fuehrt", spin(app, 2.0, lambda: window_a._worker._partner_has_battsim))
    from partner_client import PartnerSnapshot
    window_a._on_partner_snapshot(PartnerSnapshot(host=HOST_B, tiles=[
        {"id": dw.SIM_BATTSIM_ID, "kind": "battsim", "online": False, "fields": []}]))
    check("... ein nicht antwortender Simulator zaehlt nicht",
          spin(app, 2.0, lambda: not window_a._worker._partner_has_battsim))
    spin(app, 4.0, lambda: window_a._worker._partner_has_battsim)   # naechste echte Abfrage stellt alles wieder her

    print("lost -- Lebenszeichen fehlt")
    status, body = http_async(app, port_a, TOKEN_A, "POST", quoted + "/actions", {"action": "PSU_OUT_ON"})
    check("Belegung fuer Claude aktiv", status == 200 and leases_b.holder_of(dw.SIM_PSU_ID) is not None)
    t0 = time.monotonic()
    server_b.stop()
    off = spin(app, 8.0, lambda: mock_b.get_output() is False)
    took = time.monotonic() - t0
    check("B schaltet nach ~3 s ohne Lebenszeichen ab", off and 2.5 <= took <= 5.0, f"{took:.1f} s")
    check("A gibt die Claude-Belegung auf", spin(app, 6.0, lambda: not window_a._passthrough.devices()))

    for window in (window_a, window_b):
        window.close()
    app.processEvents()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

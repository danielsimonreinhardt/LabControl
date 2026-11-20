"""Prueft den Verbund Pi + PC, Stufe V1 (PI-23 a, PI-27 a; 0.45.0):

client   -- partner_client gegen einen echten ShareServer (Partner "B" mit Stub-Executor):
            Adressen, Status mit Rechnername, Kacheln/Aktionen, Bedienen mit Verbund-Kennung,
            falscher Token, Hauptschalter aus, nicht freigegebenes Geraet, Log-Einschleusung
window   -- MainWindow "A" (Simulation, ohne Hardware) mit B als Partner:
            Reiter/Navigation/Chip nur mit Partner, Partnerkachel aus der Freigabe,
            Bedienen (Knopf und Wert) kommt bei B an, Ergebnis in der Kachel,
            ALLE AUS: Knopf/MCP/Sicherheitsabschaltung reichen weiter, Testablauf-Stop und
            ALLE AUS vom Partner selbst nicht (kein Ping-Pong), Verbindungsverlust, Verbund aus

Offscreen, keine Hardware.  Aufruf:  python tools/check_partner_link.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-partner-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # "⇄" auch in einer cp1252-Konsole

FAILURES: list[str] = []
TOKEN_B = "partner-token-B"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StubExecutor:
    """Steht fuer den Worker von B: merkt sich, was ankommt."""

    def __init__(self) -> None:
        self.actions: list[tuple] = []
        self.all_off: list[str] = []
        self.meta: list[tuple] = []

    def submit_meta(self, device_id, meta, client):
        import share_api
        self.meta.append((device_id, dict(meta)))
        return share_api.RemoteResult("ok")

    def submit_action(self, device_id, kind, action, value, channel, client, local=False, verbund=False, program=False):
        import share_api
        self.actions.append((device_id, action, value, channel, client, verbund))
        return share_api.RemoteResult("ok")

    def submit_all_off(self, client):
        import share_api
        self.all_off.append(client)
        return share_api.RemoteResult("ok")


def start_partner(app):
    from live_state import LiveState
    from share_server import ShareServer
    state = LiveState()
    state.on_device_known("psu", "psu:B1", "Netzteil 1")
    state.on_psu_measurement("psu:B1", 12.003, 0.512, False)
    state.on_device_known("load", "load:B1", "Last 1")
    state.on_load_measurement("load:B1", 11.9, 1.25, 14.875)
    state.on_device_known("fg", "fg:B1", "Funktionsgenerator 1")
    # PI-67 a: Oszilloskope und J-Link (nicht in der Freigabeliste -- der gekoppelte Partner sieht sie trotzdem)
    state.on_device_known("picoscope", "picoscope:B1", "PicoScope")
    state.on_picoscope_state("picoscope:B1", "free", "2204A", "JO123/0001")
    state.on_device_known("scpiscope", "scpiscope:B1", "UNI-T")
    state.on_picoscope_state("scpiscope:B1", "free", "MSO1104HD", "SN-1")
    state.on_device_known("jlink", "jlink:B1", "J-Link")
    state.on_jlink_state("jlink:B1", {"device": "S32K144", "interface": "SWD", "speed": 4000, "vtref": 3.31,
                                      "busy": False, "action": "JLINK_FLASH", "ok": False, "message": "Verify failed",
                                      "time": "11:22:33", "last_file": "/home/pi/fw/app.hex"})
    for device_id in ("psu:B1", "load:B1", "fg:B1", "picoscope:B1", "scpiscope:B1", "jlink:B1"):
        state.set_online(device_id, True)   # wie der Worker beim Verbinden
    # Wie der Worker: Messwerte laufend auffrischen, sonst gelten sie beim Partner als veraltet (stale)
    from PySide6.QtCore import QTimer
    refresh = QTimer(app)
    refresh.setInterval(300)
    refresh.timeout.connect(lambda: (state.on_psu_measurement("psu:B1", 12.003, 0.512, False),
                                     state.on_load_measurement("load:B1", 11.9, 1.25, 14.875)))
    refresh.start()
    port = free_port()
    cfg = {"enabled": True, "bind": "127.0.0.1", "port": port, "token": TOKEN_B,
           "read_requires_token": True, "access_log": False, "local_bypass": False,
           "devices": {"psu:B1": {"read": True, "control": True},
                       "load:B1": {"read": True, "control": False}}}
    state.set_share_config(cfg)
    state.set_remote_control(True, 600.0)
    executor = StubExecutor()
    server = ShareServer(state, executor)
    server.apply(cfg)
    app.processEvents()
    return state, server, executor, port, cfg


def section_client(app, state, executor, port) -> None:
    print("client -- partner_client gegen einen echten ShareServer")
    from partner_client import PartnerClient, PartnerError, normalize_url
    from share_server import client_label, is_verbund_client

    check("Adresse ohne Port -> 8420", normalize_url("raspberrypi") == "http://raspberrypi:8420")
    check("Adresse mit Port", normalize_url(" 192.168.1.181:8421 ") == "http://192.168.1.181:8421")
    check("Adresse mit http:// und /", normalize_url("http://pi:8420/") == "http://pi:8420")
    check("unbrauchbare Adressen -> ''", normalize_url("") == "" and normalize_url("ftp://x") == ""
          and normalize_url("pi:99999") == "" and normalize_url("pi:8420/api") == "")

    client = PartnerClient(f"http://127.0.0.1:{port}", TOKEN_B, "PC-A")
    snap = client.snapshot()
    ids = {t["id"]: t for t in snap.tiles}
    check("Status mit Rechnername und Version", bool(snap.host) and bool(snap.version), f"{snap.host} {snap.version}")
    check("Fernsteuerung dort aktiv", snap.remote_effective and snap.remote_remaining_s > 500)
    check("nur freigegebene Kacheln (Netzteil, Last; kein FG)", set(ids) == {"psu:B1", "load:B1"}, str(set(ids)))
    psu = ids.get("psu:B1", {})
    check("Netzteil mit Werten und Aktionen",
          any(f.get("value") == 12.003 for f in psu.get("fields", []))
          and {a["action"] for a in psu.get("actions", [])} >= {"PSU_VOLT", "PSU_OUT_ON", "PSU_OUT_OFF"})
    check("Last nur lesbar (keine Aktionen)", not ids.get("load:B1", {}).get("actions") and not ids["load:B1"]["control"])

    reply = client.action("psu:B1", "PSU_VOLT", 5.0)
    last = executor.actions[-1] if executor.actions else ()
    check("Bedienen kommt beim Partner an", reply.get("ok") is True and last[:3] == ("psu:B1", "PSU_VOLT", 5.0), str(last))
    check("Partner sieht die Verbund-Kennung", is_verbund_client(last[4]) and "PC-A" in last[4], str(last[4:]))

    def error_of(call) -> str:
        try:
            call()
        except PartnerError as exc:
            return exc.code
        return ""

    check("falscher Token -> unauthorized",
          error_of(lambda: PartnerClient(f"http://127.0.0.1:{port}", "falsch", "PC-A").snapshot()) == "unauthorized")
    check("Wert ausserhalb -> value_out_of_range", error_of(lambda: client.action("psu:B1", "PSU_VOLT", 99.0))
          == "value_out_of_range")
    check("nur lesbares Geraet -> control_not_permitted",
          error_of(lambda: client.action("load:B1", "OUT_ON")) == "control_not_permitted")
    check("nicht freigegebenes Geraet -> unknown_tile", error_of(lambda: client.action("fg:B1", "FG_OUT_ON", channel=1))
          == "unknown_tile")
    state.set_remote_control(False)
    check("Hauptschalter dort aus, nicht gekoppelt -> remote_control_inactive",
          error_of(lambda: client.action("psu:B1", "PSU_OUT_ON")) == "remote_control_inactive")
    # PI-39 b: der gekoppelte Partner (Verbund-Kopf UND eingetragene Adresse) braucht keinen Hauptschalter
    state.set_partner_ip("127.0.0.1")
    reply = client.action("psu:B1", "PSU_OUT_ON")
    last = executor.actions[-1] if executor.actions else ()
    check("PI-39 b: gekoppelter Partner bedient ohne Hauptschalter",
          reply.get("ok") is True and last[:2] == ("psu:B1", "PSU_OUT_ON") and last[5] is True, str(last))
    check("PI-39 b: Status meldet 'effective' fuer den Partner", client.snapshot().remote_effective)
    stranger = PartnerClient(f"http://127.0.0.1:{port}", TOKEN_B, "")
    check("PI-39 b: ohne Verbund-Kopf weiter Hauptschalter noetig",
          error_of(lambda: stranger.action("psu:B1", "PSU_OUT_ON")) == "remote_control_inactive")
    state.set_partner_ip("192.168.99.99")
    check("PI-39 b: Verbund-Kopf von fremder Adresse -> remote_control_inactive",
          error_of(lambda: client.action("psu:B1", "PSU_OUT_ON")) == "remote_control_inactive")
    # Symmetrischer Verbund (0.55.0): der gekoppelte Partner sieht und bedient alle Geraete, auch ohne Freigabeliste
    state.set_partner_ip("127.0.0.1")
    tiles = {t["id"]: t for t in client.snapshot().tiles}
    check("Verbund: Partner sieht auch nicht freigegebene Geraete (FG)", set(tiles) >= {"psu:B1", "load:B1", "fg:B1"},
          str(set(tiles)))
    check("Verbund: Partner darf alles Fernsteuerbare (Last ohne 'Steuern', FG)",
          tiles.get("load:B1", {}).get("control") is True and tiles.get("fg:B1", {}).get("control") is True)
    check("Verbund: Kachel nennt die Freigabeliste ('shared')",
          tiles.get("psu:B1", {}).get("shared") == {"read": True, "control": True}
          and tiles.get("load:B1", {}).get("shared") == {"read": True, "control": False}
          and tiles.get("fg:B1", {}).get("shared") == {"read": False, "control": False})
    reply = client.action("fg:B1", "FG_OUT_ON", channel=1)
    check("Verbund: Bedienen eines nicht freigegebenen Geraets kommt an", reply.get("ok") is True
          and executor.actions[-1][:2] == ("fg:B1", "FG_OUT_ON"))
    check("ohne Verbund weiter nur die Freigabeliste",
          {t["id"] for t in stranger.snapshot().tiles} == {"psu:B1", "load:B1"})
    # PI-43 b: Name/Farbe aendern nur der gekoppelte Partner
    client.meta("psu:B1", {"label": "Neu", "color": "teal"})
    check("Meta vom Partner kommt an", executor.meta[-1:] == [("psu:B1", {"label": "Neu", "color": "teal"})],
          str(executor.meta[-1:]))
    check("Meta von Fremden -> verbund_only", error_of(lambda: stranger.meta("psu:B1", {"label": "x"})) == "verbund_only")
    check("Meta mit Steuerzeichen im Namen -> invalid_value",
          error_of(lambda: client.meta("psu:B1", {"label": "a\nb"})) == "invalid_value")
    check("Meta fuer unbekanntes Geraet -> unknown_tile",
          error_of(lambda: client.meta("psu:XX", {"color": None})) == "unknown_tile")
    state.set_panel_color("psu:B1", "teal")
    check("Kachel traegt die Farbe des Besitzers",
          {t["id"]: t for t in client.snapshot().tiles}["psu:B1"].get("color") == "teal")
    state.set_panel_color("psu:B1", None)
    # Claude (MCP) am Partner bekommt davon nur die Freigabeliste (share_api.partner_tiles)
    import share_api
    mcp = share_api.partner_tiles({"local": True, "remote": {"active": False}, "lock": "free",
                                   "share": {"local_bypass": True},
                                   "partner": {"host": "pi", "name": "Pi", "age_s": 0.1,
                                               "tiles": list(tiles.values())}})
    check("MCP am Partner: nur freigegebene Geraete, Last nur lesbar",
          set(mcp) == {"@pi/psu:B1", "@pi/load:B1"} and mcp["@pi/psu:B1"]["control"] is True
          and mcp["@pi/load:B1"]["control"] is False and not mcp["@pi/load:B1"]["actions"]
          and "shared" not in mcp["@pi/psu:B1"], str({k: v.get("control") for k, v in mcp.items()}))
    state.set_partner_ip("")
    state.set_remote_control(True, 600.0)
    check("Partner aus -> unreachable",
          error_of(lambda: PartnerClient(f"http://127.0.0.1:{free_port()}", TOKEN_B, "x").snapshot()) == "unreachable")
    label = client_label("10.0.0.2", "evil\nname 2026-11-11 CRITICAL")
    check("Rechnername im Log ohne Zeilenumbruch/Leerzeichen", "\n" not in label and label.count(" ") == 1, repr(label))
    check("ohne Kennung kein Verbund", not is_verbund_client(client_label("10.0.0.2", None)))


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return bool(until()) if until is not None else False


class PassthroughSpy:
    """Steht an A fuer den Executor (share_remote.RemoteBridge): merkt sich, was an den Partner durchginge."""

    def __init__(self) -> None:
        self.partner_actions: list[tuple] = []

    def submit_partner_action(self, *args):
        import share_api
        self.partner_actions.append(args)
        return share_api.RemoteResult("ok")


def section_display_tiles(app, window, state, executor, cfg, rid_of) -> tuple[str, str, str]:
    """PI-67 a: PicoScope, UNI-T und J-Link des Partners als reine Anzeige-Kacheln (A und B gekoppelt)."""
    print("window -- Oszilloskop und J-Link des Partners als Anzeige-Kachel (PI-67 a)")
    import share_api
    from control_tab import JLinkControlGroup, PicoscopeControlGroup
    from picoscope_panel import PicoscopePanel
    from remote_badge import RemoteBadge
    ctrl, dash = window.control_tab, window.dashboard
    ids = ("picoscope:B1", "scpiscope:B1", "jlink:B1")
    there = spin(app, 4.0, lambda: all(ctrl.section(rid_of(t)) is not None for t in ids))
    scope_id, uni_id, jl_id = (rid_of(t) for t in ids)
    check("Kacheln fuer PicoScope, UNI-T und J-Link des Partners in Uebersicht und Steuerung",
          there and all(r in dash._panels for r in (scope_id, uni_id, jl_id)), str(window._partner_devices.known()))
    if not there:
        return scope_id, uni_id, jl_id
    scope_sec, uni_sec, jl_sec = ctrl.section(scope_id), ctrl.section(uni_id), ctrl.section(jl_id)
    scope_panel, jl_panel = dash._panels[scope_id], dash._panels[jl_id]
    check("dieselben Kacheln wie fuer eigene Geraete, mit Symbol",
          isinstance(scope_sec, PicoscopeControlGroup) and isinstance(uni_sec, PicoscopeControlGroup)
          and isinstance(jl_sec, JLinkControlGroup) and isinstance(scope_panel, PicoscopePanel)
          and all(w.findChild(RemoteBadge) is not None for w in (scope_sec, jl_sec, scope_panel, jl_panel)))
    check("PicoScope: Status frei, Modell und Seriennummer",
          spin(app, 3.0, lambda: scope_panel._status == "free" and scope_panel._variant == "2204A"
               and scope_sec._status == "free" and "2204A (JO123/0001)" in scope_sec._device_label.text()),
          f"{scope_panel._status} {scope_panel._variant} | {scope_sec._device_label.text()}")
    check("UNI-T: Modell", "MSO1104HD" in uni_sec._device_label.text(), uni_sec._device_label.text())
    check("J-Link: Ziel, Zielspannung, letzte Bedienung mit Ergebnis, Dateiname",
          spin(app, 3.0, lambda: "S32K144" in jl_sec._target_label.text() and jl_sec._vtref_label.text() == "3,31 V"
               and jl_sec._last_label.text().startswith("✕") and "Verify failed" in jl_sec._last_label.text()
               and jl_sec._file_label.text() == "app.hex"),
          f"{jl_sec._target_label.text()} | {jl_sec._vtref_label.text()} | {jl_sec._last_label.text()}")
    check("J-Link in der Uebersicht", jl_panel._value_labels["jl_target"].text() == "S32K144"
          and jl_panel._value_labels["jl_vtref"].text() == "3.31",
          f"{jl_panel._value_labels['jl_target'].text()} {jl_panel._value_labels['jl_vtref'].text()}")
    check("keine Bedienknoepfe (Trennen, Kurve, PicoScope 7; Pruefen, Reset, Anhalten, Starten, Flashen)",
          scope_sec._button_row.isHidden() and uni_sec._button_row.isHidden() and jl_sec._button_row.isHidden()
          and scope_panel._launch_button.isHidden() and scope_panel._release_button.isHidden())
    check("Umbenennen und Farbe bleiben bedienbar (gehen an den Besitzer)",
          scope_sec.isEnabled() and jl_sec.isEnabled() and not jl_sec._rename_button.isHidden()
          and not scope_sec._rename_button.isHidden())
    requested: list = []
    ctrl.jlink_action_requested.connect(lambda *args: requested.append(args))
    before = len(executor.actions)
    for button in (jl_sec._check_button, jl_sec._reset_button, jl_sec._go_button):
        button.click()
    spin(app, 0.5)
    check("J-Link-Knopf loest nichts aus, nichts geht an den Partner", not requested and len(executor.actions) == before,
          str(requested))

    state.on_picoscope_state("picoscope:B1", "mcp", "", "")
    check("Status 'Verbunden (MCP)' kommt an, Trennen bleibt verborgen",
          spin(app, 3.0, lambda: scope_panel._status == "mcp" and scope_sec._status == "mcp")
          and scope_panel._release_button.isHidden() and scope_panel._variant == "2204A")
    state.on_jlink_state("jlink:B1", {"device": "S32K144", "interface": "SWD", "speed": 4000, "vtref": 3.31,
                                      "busy": True, "action": "JLINK_FLASH", "last_file": "/home/pi/fw/app.hex"})
    check("J-Link: laufende Bedienung", spin(app, 3.0, lambda: "läuft" in jl_sec._last_label.text()),
          jl_sec._last_label.text())
    spin(app, 2.5)
    chips = [window._status_labels.get(r) for r in (scope_id, uni_id, jl_id)]
    check("Status-Chips mit '⇄'; ohne neue Meldung nach 2 s weiter verbunden (nicht 'veraltet')",
          not scope_sec.isHidden() and not jl_sec.isHidden()
          and all(c is not None and c.text().startswith("✓") and c.text().endswith("⇄") for c in chips),
          str([c.text() if c else None for c in chips]))

    before = len(executor.meta)
    window._on_rename_requested("jlink", jl_id, "JL am Pi")
    window._on_panel_color_requested(scope_id, "teal")
    spin(app, 4.0, lambda: len(executor.meta) >= before + 2)
    check("Umbenennen/Umfaerben geht an den Besitzer",
          executor.meta[before:before + 2] == [("jlink:B1", {"label": "JL am Pi"}), ("picoscope:B1", {"color": "teal"})],
          str(executor.meta[before:]))
    state.on_label_changed("jlink", "jlink:B1", "JL am Pi")
    check("Name vom Besitzer in Steuerung und Uebersicht",
          spin(app, 3.0, lambda: jl_sec.title() == "JL am Pi" and jl_panel.title() == "JL am Pi"), jl_sec.title())
    known = window.testcase_tab._known_devices
    check("Testablauf: Partner-Oszilloskop/J-Link nicht als Schrittziel (Partner-Netzteil schon)",
          not any(r in known for r in (scope_id, uni_id, jl_id)) and any(d.startswith("@") for d in known),
          str([d for d in known if d.startswith("@")]))

    # MCP an A: list_devices zeigt die Partnerkacheln (Freigabeliste von B), steuerbar sind sie nie
    cfg["devices"].update({"picoscope:B1": {"read": True, "control": True, "measure": True},
                           "jlink:B1": {"read": True, "control": True}})
    state.set_share_config(cfg)

    class LocalView:   # A, Aufruf vom selben Rechner mit "Claude (MCP) darf steuern"
        def snapshot(self):
            snap = window._live_state.snapshot()
            snap["share"] = dict(snap["share"], token="t", local_bypass=True, read_requires_token=False)
            return snap

    spy = PassthroughSpy()

    def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        resp = share_api.dispatch(method, path, lambda n: "Bearer t" if n == "Authorization" else None, LocalView(),
                                  {"version": "x", "host": "A"}, body=json.dumps(payload).encode() if payload else b"",
                                  executor=spy, client="127.0.0.1", local=True, scopes=window._scope_service)
        try:
            return resp.status, json.loads(resp.body or b"{}")
        except ValueError:
            return resp.status, {}

    def mcp_tiles() -> dict:
        return {t["id"]: t for t in call("GET", "/api/v1/values")[1].get("tiles", [])}

    spin(app, 4.0, lambda: jl_id in mcp_tiles() and scope_id in mcp_tiles())
    tiles = mcp_tiles()
    check("MCP: Partner-PicoScope/J-Link in list_devices, control/control_available false, keine Aktionen",
          all(r in tiles and tiles[r].get("control") is False and tiles[r].get("control_available") is False
              and not tiles[r].get("actions") for r in (scope_id, jl_id)),
          str({r: (tiles.get(r, {}).get("control"), tiles.get(r, {}).get("control_available")) for r in (scope_id, jl_id)}))
    status, reply = call("POST", f"/api/v1/tiles/{jl_id}/actions", {"action": "JLINK_RESET"})
    status2, reply2 = call("POST", f"/api/v1/tiles/{scope_id}/actions", {"action": "PSU_OUT_ON"})
    check("MCP: Aktion an Partner-J-Link/-Oszilloskop -> 403 control_not_permitted, nichts durchgereicht",
          (status, reply.get("error"), status2, reply2.get("error"))
          == (403, "control_not_permitted", 403, "control_not_permitted") and not spy.partner_actions,
          f"{status} {reply} | {status2} {reply2} | {spy.partner_actions}")
    status, scopes = call("GET", "/api/v1/scopes")
    status2, _reply = call("POST", f"/api/v1/scopes/{scope_id}/acquire", {"duration_s": 0.01})
    check("MCP: Partner-Oszilloskop nicht unter den Oszilloskopen, Erfassen dort -> 404",
          status == 200 and not any(str(s.get("id", "")).startswith("@") for s in scopes.get("scopes", []))
          and status2 == 404, f"{status} {scopes.get('scopes')} | acquire {status2}")

    # Geloescht beim Besitzer (LiveState.forget, 1.0.1) -> Kachel und Chip auch hier weg
    state.forget("scpiscope:B1")
    check("beim Besitzer geloeschtes Oszilloskop verschwindet auch hier",
          spin(app, 4.0, lambda: uni_id not in dash._panels and ctrl.section(uni_id) is None
               and uni_id not in window._status_labels))
    return scope_id, uni_id, jl_id


def section_window(app, state, server, executor, port, cfg) -> None:
    print("window -- Hauptfenster A mit B als Partner")
    import no_hardware
    no_hardware.block()
    from main_window import MainWindow
    from settings import Settings

    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    app.processEvents()
    # Seit 0.52.0 kein Verbund-Reiter mehr: Partnergeraete stehen in Uebersicht und Steuerung
    check("kein Verbund-Reiter", not hasattr(window, "partner_tab")
          and "Verbund" not in [window.tabs.tabText(i) for i in range(window.tabs.count())])
    check("ohne Partner: Chip verborgen", window._partner_chip.isHidden())

    window.settings_tab.set_partner_config({"enabled": True, "address": f"127.0.0.1:{port}",
                                            "token": TOKEN_B, "name": "Pi"})
    window.settings_tab._emit_partner_config()
    check("Einstellung gespeichert", window._settings.partner_config["address"] == f"127.0.0.1:{port}")
    pd = window._partner_devices

    def rid_of(tile_id: str) -> str:
        return next((r for r in pd.known() if r.endswith("/" + tile_id)), "")

    connected = spin(app, 6.0, lambda: window._partner.is_connected() and window.control_tab.section(rid_of("psu:B1")))
    check("verbunden, Partnergeraete in Uebersicht und Steuerung", bool(connected))
    psu_id, load_id = rid_of("psu:B1"), rid_of("load:B1")
    section = window.control_tab.section(psu_id)
    if section is None:
        window.close()
        return
    from control_tab import PsuControlGroup
    from remote_badge import RemoteBadge
    panel = window.dashboard._panels.get(psu_id)
    check("Kennung mit Rechnername", psu_id.startswith("@") and psu_id.endswith("/psu:B1"), psu_id)
    check("Chip '⇄ Pi' sichtbar", window._partner_chip.isVisible() and window._partner_chip.text() == "⇄ Pi",
          window._partner_chip.text())
    check("Uebersicht: normale Netzteil-Kachel mit Partnernamen, Symbol als einziges Merkmal",
          panel is not None and panel.title() == "Netzteil 1" and panel.findChild(RemoteBadge) is not None,
          panel.title() if panel else "-")
    check("Steuerung: dieselbe Netzteil-Kachel wie fuer eigene Geraete, mit Symbol",
          isinstance(section, PsuControlGroup) and section.title() == "Netzteil 1"
          and section.findChild(RemoteBadge) is not None)
    check("nicht fernsteuerbar ausgeblendet (Handbedienung, OVP); Umbenennen da (geht an den Besitzer, PI-43 b)",
          section._manual_button.isHidden() and not section._ovp_row.isVisible()
          and not section._rename_button.isHidden())
    # 1.0.1: OVP/OCP des Partners sind unbekannt (Felder auf 1 V / 0 A) -- keine OVP/OCP-Warnung, auch nach Eingabe
    section._voltage_spin.setValue(12.0)
    section._current_spin.setValue(0.5)
    chip = window._status_labels.get(psu_id)
    check("1.0.1: Status-Chip auch fuer das Partnergeraet, mit '⇄' und Rechnername im Tooltip",
          chip is not None and chip.text().startswith("✓") and chip.text().endswith("⇄")
          and chip.toolTip().startswith("Pi · "), (chip.text() + " | " + chip.toolTip()) if chip else "kein Chip")
    check("keine OVP/OCP-Warnung in der Partnerkachel (12 V / 0,5 A eingegeben)",
          section._limit_warning.isHidden() and not section._limit_warning.text(), section._limit_warning.text())
    spin(app, 2.0, lambda: "12.0" in panel._values["voltage"].text() if hasattr(panel, "_values") else True)
    check("Messwert des Partners in der Uebersicht", any("12" in lbl.text() for lbl in panel.findChildren(QLabel)),
          str([lbl.text() for lbl in panel.findChildren(QLabel)][:8]))
    load_section = window.control_tab.section(load_id)
    check("Last ohne Steuern-Freigabe: Kachel da, nicht bedienbar",
          load_section is not None and not load_section.isEnabled())
    check("Netzteil bedienbar", section.isEnabled())
    check("Status in den Einstellungen", "Verbunden mit Pi" in window.settings_tab._partner_status.text(),
          window.settings_tab._partner_status.text())

    before = len(executor.actions)
    section._voltage_spin.setValue(7.5)
    section._voltage_button.click()
    spin(app, 4.0, lambda: len(executor.actions) > before)
    check("Spannung 7,5 V aus der Kachel kommt bei B an", executor.actions[-1][:3] == ("psu:B1", "PSU_VOLT", 7.5),
          str(executor.actions[-1:]))
    before = len(executor.actions)
    section._output_on_button.click()
    spin(app, 4.0, lambda: len(executor.actions) >= before + 3)
    check("Ausgang EIN: erst Sollwerte, dann Ausgang (in dieser Reihenfolge)",
          [a[1] for a in executor.actions[before:before + 3]] == ["PSU_VOLT", "PSU_CURR", "PSU_OUT_ON"],
          str(executor.actions[before:]))

    state.set_remote_control(False)
    spin(app, 3.0, lambda: not section.isEnabled())
    check("Hauptschalter dort aus: Kachel gesperrt", not section.isEnabled())
    check("Chip nennt den Hauptschalter", "Fernsteuerung dort aus" in window._partner_chip.toolTip(),
          window._partner_chip.toolTip())
    failed: list = []
    pd.action_failed.connect(lambda *args: failed.append(args))
    section.set_output.emit(psu_id, True)   # wie ein Tipp, falls die Kachel noch nicht gesperrt war
    check("Bedienen bei gesperrter Kachel wird abgelehnt, nicht gesendet",
          failed and failed[-1][2] == "remote_control_inactive", str(failed))
    state.set_remote_control(True, 600.0)
    spin(app, 3.0, lambda: section.isEnabled())
    check("Hauptschalter wieder an: bedienbar", section.isEnabled())

    print("window -- gekoppelt (B kennt A als Partner): symmetrischer Verbund und PI-43 b")
    state.set_partner_ip("127.0.0.1")
    state.set_remote_control(False)
    check("gekoppelt: Last ohne 'Steuern' bedienbar, auch ohne Hauptschalter",
          spin(app, 4.0, lambda: load_section.isEnabled() and section.isEnabled()))
    check("gekoppelt: auch nicht freigegebenes Geraet (FG) als Kachel da",
          spin(app, 3.0, lambda: window.control_tab.section(rid_of("fg:B1")) is not None))
    state.on_psu_setpoints("psu:B1", 9.0, 1.5)
    check("PI-43 b: Netzteil-Sollwerte von B stehen in den Feldern",
          spin(app, 3.0, lambda: abs(section._voltage_spin.value() - 9.0) < 1e-6
               and abs(section._current_spin.value() - 1.5) < 1e-6),
          f"{section._voltage_spin.value()} / {section._current_spin.value()}")
    section._voltage_spin.setValue(3.3)
    section._voltage_spin.setFocus()
    spin(app, 1.5)
    check("PI-43 b: gleicher Stand ueberschreibt eine laufende Eingabe nicht",
          abs(section._voltage_spin.value() - 3.3) < 1e-6, str(section._voltage_spin.value()))
    window.setFocus()
    state.on_load_setpoint("load:B1", "VOLT", 12.5)
    check("PI-43 b: Last-Modus und -Sollwert von B in der Kachel",
          spin(app, 3.0, lambda: load_section._mode_combo.currentData() == "VOLT"
               and abs(load_section._setpoint_spin.value() - 12.5) < 1e-6),
          f"{load_section._mode_combo.currentData()} {load_section._setpoint_spin.value()}")
    window._settings.set_panel_colors_enabled(True)   # Anzeige der Kachelfarben ist je Rechner schaltbar
    state.set_panel_color("psu:B1", "teal")
    check("PI-43 b: Kachelfarbe des Besitzers", spin(app, 3.0, lambda: section._color_key == "teal"
                                                       and window.dashboard._panels[psu_id]._color_key == "teal"),
          str(section._color_key))
    state.on_label_changed("psu", "psu:B1", "NT am Pi")
    check("PI-43 b: Name vom Besitzer in Steuerung und Uebersicht",
          spin(app, 3.0, lambda: section.title() == "NT am Pi" and window.dashboard._panels[psu_id].title() == "NT am Pi"),
          section.title())
    before = len(executor.meta)
    window._on_rename_requested("psu", psu_id, "Vom PC")
    window._on_panel_color_requested(psu_id, "orange")
    spin(app, 4.0, lambda: len(executor.meta) >= before + 2)
    check("PI-43 b: Umbenennen/Umfaerben einer Partnerkachel geht an den Besitzer",
          executor.meta[before:before + 2] == [("psu:B1", {"label": "Vom PC"}), ("psu:B1", {"color": "orange"})],
          str(executor.meta[before:]))
    check("... und wird hier nicht lokal gespeichert", window._settings.panel_color(psu_id) is None)
    scope_id, uni_id, jl_id = section_display_tiles(app, window, state, executor, cfg, rid_of)
    state.set_remote_control(True, 600.0)

    def all_off_count_after(action) -> int:
        before = len(executor.all_off)
        action()
        spin(app, 3.0, lambda: len(executor.all_off) > before)
        spin(app, 0.3)
        return len(executor.all_off) - before

    check("ALLE AUS (Knopf) -> auch beim Partner", all_off_count_after(window._all_off_button.click) == 1)
    check("... mit Verbund-Kennung (B reicht nicht zurueck)",
          bool(executor.all_off) and "Verbund:" in executor.all_off[-1], str(executor.all_off[-1:]))
    check("ALLE AUS per MCP/Netzwerk -> auch beim Partner",
          all_off_count_after(lambda: window._on_share_all_off(0, "127.0.0.1")) == 1)
    check("ALLE AUS vom Partner selbst -> NICHT zurueck",
          all_off_count_after(lambda: window._on_share_all_off(0, "10.0.0.9 Verbund:raspberrypi")) == 0)
    check("Sicherheitsabschaltung -> auch beim Partner",
          all_off_count_after(lambda: window._on_safety_tripped("psu:SIM", "Test")) == 1)
    window._safety_banner.hide()
    check("Testablauf-Stop -> nur lokal",
          all_off_count_after(lambda: window._safe_stop("stop button")) == 0)

    server.stop()
    lost = spin(app, 8.0, lambda: not window._partner.is_connected())
    check("Partner weg: getrennt erkannt", lost)
    check("Chip zeigt ✕, Grund im Tooltip", window._partner_chip.text().startswith("✕")
          and "nicht erreichbar" in window._partner_chip.toolTip(), window._partner_chip.toolTip())
    check("Partnerkacheln getrennt (Steuerung verborgen, Uebersicht getrennt)",
          spin(app, 2.0, lambda: not section.isVisible()), f"sichtbar={section.isVisible()}")
    chip = window._status_labels.get(psu_id)
    check("1.0.1: Chip des Partnergeraets zeigt getrennt (✕)", chip is not None and chip.text().startswith("✕"),
          chip.text() if chip else "kein Chip")
    check("PI-67 a: Oszilloskop/J-Link des Partners getrennt (Steuerung verborgen, Chips ✕)",
          all(window.control_tab.section(r) is not None and not window.control_tab.section(r).isVisible()
              and r in window._status_labels and window._status_labels[r].text().startswith("✕")
              for r in (scope_id, jl_id)),
          str([window._status_labels[r].text() for r in (scope_id, jl_id) if r in window._status_labels]))
    server.apply(cfg)
    check("Partner wieder da: neu verbunden, Kachel wieder da",
          spin(app, 8.0, lambda: window._partner.is_connected() and section.isVisible()))

    window.settings_tab._partner_token_edit.setText("falsch")
    window.settings_tab._emit_partner_config()
    check("falscher Token: Grund in den Einstellungen",
          spin(app, 6.0, lambda: "Token falsch" in window.settings_tab._partner_status.text()),
          window.settings_tab._partner_status.text())

    window.settings_tab._partner_enabled_checkbox.setChecked(False)
    app.processEvents()
    check("Verbund aus: Chip verborgen, Partnerkacheln weg", window._partner_chip.isHidden()
          and psu_id not in window.dashboard._panels and window.control_tab.section(psu_id) is None)
    check("1.0.1: Verbund aus: Chips der Partnergeraete weg",
          not any(d.startswith("@") for d in window._status_labels), str(list(window._status_labels)))
    check("PI-67 a: Verbund aus: Oszilloskop-/J-Link-Kacheln des Partners weg",
          not any(r in window.dashboard._panels or window.control_tab.section(r) is not None
                  for r in (scope_id, jl_id)))
    before = len(executor.all_off)
    window._all_off_button.click()
    spin(app, 1.0)
    check("Verbund aus: ALLE AUS bleibt lokal", len(executor.all_off) == before)

    window.close()
    app.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    state, server, executor, port, cfg = start_partner(app)
    try:
        section_client(app, state, executor, port)
        section_window(app, state, server, executor, port, cfg)
    finally:
        server.stop()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

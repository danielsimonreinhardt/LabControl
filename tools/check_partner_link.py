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

from PySide6.QtWidgets import QApplication  # noqa: E402

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

    def submit_action(self, device_id, kind, action, value, channel, client, local=False):
        import share_api
        self.actions.append((device_id, action, value, channel, client))
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
    check("Adresse mit Port", normalize_url(" 192.168.178.181:8421 ") == "http://192.168.178.181:8421")
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
    check("Hauptschalter dort aus -> remote_control_inactive",
          error_of(lambda: client.action("psu:B1", "PSU_OUT_ON")) == "remote_control_inactive")
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
    index = window.tabs.indexOf(window.partner_tab)
    check("ohne Partner: Reiter, Navigation und Chip verborgen",
          not window.tabs.isTabVisible(index) and window._partner_nav_button.isHidden()
          and window._partner_chip.isHidden())

    window.settings_tab.set_partner_config({"enabled": True, "address": f"127.0.0.1:{port}",
                                            "token": TOKEN_B, "name": "Pi"})
    window.settings_tab._emit_partner_config()
    check("Einstellung gespeichert", window._settings.partner_config["address"] == f"127.0.0.1:{port}")
    check("Reiter sichtbar", window.tabs.isTabVisible(index))
    connected = spin(app, 6.0, lambda: window._partner.is_connected() and window.partner_tab.tile("psu:B1"))
    check("verbunden, Partnerkachel da", connected)
    tile = window.partner_tab.tile("psu:B1")
    if tile is None:
        window.close()
        return
    check("Chip '⇄ Pi' sichtbar", window._partner_chip.isVisible() and window._partner_chip.text() == "⇄ Pi",
          window._partner_chip.text())
    check("Kachel-Titel mit Partnername", tile.title() == "Pi · Netzteil 1", tile.title())
    check("Last-Kachel ohne Bedienung", window.partner_tab.tile("load:B1") is not None
          and not window.partner_tab.tile("load:B1")._switch_buttons)
    check("Schaltknoepfe und Wertzeile", {b.text() for b in tile._switch_buttons} >= {"Ausgang EIN", "Ausgang AUS"}
          and tile._value_row.isVisibleTo(tile) and tile._send_button.isEnabled())
    check("Status in den Einstellungen", "Verbunden mit Pi" in window.settings_tab._partner_status.text(),
          window.settings_tab._partner_status.text())

    before = len(executor.actions)
    next(b for b in tile._switch_buttons if b.text() == "Ausgang EIN").click()
    spin(app, 4.0, lambda: len(executor.actions) > before and "✓" in tile._result.text())
    check("Knopf 'Ausgang EIN' kommt bei B an", executor.actions[-1][1] == "PSU_OUT_ON", str(executor.actions[-1:]))
    check("Ergebnis in der Kachel", tile._result.text().startswith("✓ PSU_OUT_ON"), tile._result.text())
    tile._action_combo.setCurrentIndex(tile._action_combo.findData("PSU_VOLT"))
    check("Wertebereich vom Partner", tile._value_spin.minimum() == 1.0 and tile._value_spin.maximum() == 60.0,
          f"{tile._value_spin.minimum()}..{tile._value_spin.maximum()}")
    tile._value_spin.setValue(7.5)
    before = len(executor.actions)
    tile._send_button.click()
    spin(app, 4.0, lambda: len(executor.actions) > before)
    check("Wert 7,5 V kommt bei B an", executor.actions[-1][1:3] == ("PSU_VOLT", 7.5), str(executor.actions[-1:]))

    state.set_remote_control(False)
    spin(app, 3.0, lambda: not tile._send_button.isEnabled() and not tile._value_row.isEnabled())
    check("Hauptschalter dort aus: Bedienung gesperrt, Grund genannt",
          not tile._value_row.isEnabled() and "Fernsteuerung dort aus" in tile._state.text(), tile._state.text())
    check("Kopf nennt den Hauptschalter", "Fernsteuerung dort aus" in window.partner_tab._detail.text())
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
    check("Chip zeigt ✕", window._partner_chip.text().startswith("✕"), window._partner_chip.text())
    check("Kacheln weg, Kopf nennt den Grund", window.partner_tab.tile("psu:B1") is None
          and "nicht erreichbar" in window.partner_tab._detail.text(), window.partner_tab._detail.text())
    server.apply(cfg)
    check("Partner wieder da: neu verbunden",
          spin(app, 8.0, lambda: window._partner.is_connected() and window.partner_tab.tile("psu:B1") is not None))

    window.settings_tab._partner_token_edit.setText("falsch")
    window.settings_tab._emit_partner_config()
    check("falscher Token: Grund im Kopf",
          spin(app, 6.0, lambda: "Token falsch" in window.partner_tab._detail.text()), window.partner_tab._detail.text())

    window.settings_tab._partner_enabled_checkbox.setChecked(False)
    app.processEvents()
    check("Verbund aus: Reiter, Navigation, Chip verborgen",
          not window.tabs.isTabVisible(index) and window._partner_nav_button.isHidden()
          and window._partner_chip.isHidden())
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

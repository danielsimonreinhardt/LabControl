"""Prueft die Kopplung zweier LabControl-Instanzen (Token austauschen statt abtippen, 0.50.0):

- table   -- pairing.PairingTable allein (eine Anfrage gleichzeitig, Frist, Abbruch, Sperre danach,
             Token erst nach Bestaetigung, Abschluss genau einmal)
- api     -- Routen /api/v1/pair* ohne Token, auch wenn Lesen einen Token verlangt
- windows -- zwei Hauptfenster: A fragt, beide zeigen denselben Code, B bestaetigt -> beide tragen den
             anderen als Verbund-Partner ein und verbinden sich; Ablehnen und Abbrechen

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_pairing.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-pairing-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


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


def section_table() -> None:
    print("table -- PairingTable")
    import pairing
    now = [100.0]
    table = pairing.PairingTable(clock=lambda: now[0])
    req = table.request("10.0.0.5", "pc", "", 8420)
    check("Anfrage mit 6-stelligem Code und geheimer ID", len(req.code) == 6 and req.code.isdigit()
          and len(req.pair_id) >= 20)
    try:
        table.request("10.0.0.6", "x", "", 8420)
        busy = False
    except pairing.PairBusy:
        busy = True
    check("zweite Anfrage waehrend der ersten -> belegt", busy)
    check("falsche ID -> unbekannt", table.status("falsch") is None)
    check("vor der Bestaetigung kein Abschluss", not table.complete(req.pair_id, "tok-a"))
    shown = table.take_new()
    check("GUI bekommt die Anfrage genau einmal", shown is not None and table.take_new() is None)
    check("Bestaetigen", table.decide(req.pair_id, True))
    status = table.status(req.pair_id)
    check("Stand 'accepted'", status is not None and status.status == "accepted")
    check("Abschluss mit Token von A", table.complete(req.pair_id, "tok-a"))
    done = table.take_done()
    check("Ergebnis einmal fuer die Einstellungen", done is not None and done.peer_token == "tok-a"
          and done.address() == "10.0.0.5:8420" and table.take_done() is None)
    check("kein zweiter Abschluss", not table.complete(req.pair_id, "tok-b"))

    req = table.request("10.0.0.5", "pc", "", 8420)
    table.take_new()
    now[0] += pairing.PAIR_TTL_S + 1
    check("ohne Bestaetigung nach der Frist abgelaufen", table.status(req.pair_id).status == "expired")
    check("abgelaufen: Bestaetigen geht nicht mehr", not table.decide(req.pair_id, True))
    req = table.request("10.0.0.5", "pc", "", 8420)
    check("Abbruch durch A", table.cancel(req.pair_id) and table.current().status == "cancelled")
    try:
        table.request("10.0.0.5", "pc", "", 8420)
        blocked = False
    except pairing.PairBusy:
        blocked = True
    check("kurz nach Abbruch keine neue Anfrage (gegen Dialog-Flut)", blocked)
    now[0] += pairing.REJECT_COOLDOWN_S + 0.1
    req = table.request("10.0.0.5", "pc", "", 8420)
    table.decide(req.pair_id, False)
    check("Ablehnen -> 'rejected'", table.status(req.pair_id).status == "rejected")


def section_api() -> None:
    print("api -- Routen ohne Token")
    import share_api
    from pairing import PairingTable

    class State:
        def __init__(self):
            self.pairing = PairingTable()

        def snapshot(self):
            return {"devices": {}, "share": {"token": "tok-b", "read_requires_token": True, "devices": {},
                                             "port": 8421}, "lock": "free", "remote": {"active": False}}

    state = State()

    def call(method, path, body=None, client="10.0.0.5"):
        response = share_api.dispatch(method, path, lambda _h: None, state, {"host": "pi"},
                                      body=json.dumps(body).encode() if body is not None else b"", client=client)
        return response.status, json.loads(response.body or b"{}")

    status, body = call("GET", "/api/v1/status")
    check("Gegenprobe: Status ohne Token -> 401", status == 401)
    status, body = call("POST", "/api/v1/pair", {"host": "pc", "name": "", "port": 8420})
    check("POST /pair ohne Token -> Code", status == 200 and len(body.get("code", "")) == 6 and body.get("host") == "pi",
          f"{status} {body}")
    pair = body.get("pair", "")
    status, _ = call("POST", "/api/v1/pair", {"host": "x", "port": 8420})
    check("zweite Anfrage -> 409 pair_busy", status == 409)
    status, body = call("GET", f"/api/v1/pair/{pair}")
    check("Stand pending, noch kein Token", status == 200 and body.get("status") == "pending" and "token" not in body)
    status, _ = call("GET", "/api/v1/pair/unbekannt")
    check("unbekannte ID -> 404", status == 404)
    status, _ = call("POST", f"/api/v1/pair/{pair}/complete", {"token": "tok-a"})
    check("Abschluss vor Bestaetigung -> 409", status == 409)
    req = state.pairing.take_new()
    check("Anfrage mit Absenderadresse und Port", req.address() == "10.0.0.5:8420", req.address())
    state.pairing.decide(pair, True)
    status, body = call("GET", f"/api/v1/pair/{pair}")
    check("nach Bestaetigung: Token und Port von B", body.get("status") == "accepted" and body.get("token") == "tok-b"
          and body.get("port") == 8421, str(body))
    status, body = call("POST", f"/api/v1/pair/{pair}/complete", {"token": "tok-a"})
    check("Abschluss mit Token von A", status == 200 and state.pairing.take_done().peer_token == "tok-a")
    status, _ = call("POST", "/api/v1/pair", {"host": "pc", "port": "x"})
    check("ungueltiger Port -> 400", status == 400)


def make_window(settings_data: dict):
    from main_window import MainWindow
    from settings import Settings
    settings = Settings()
    settings._data = {"simulation_mode": True, **settings_data}
    window = MainWindow(settings)
    window.resize(1280, 800)
    window.show()
    return window


def main() -> int:
    app = QApplication.instance() or QApplication([])
    section_table()
    section_api()

    print("windows -- zwei Hauptfenster")
    import no_hardware
    no_hardware.block()
    port_a, port_b = free_port(), free_port()
    window_b = make_window({"share_enabled": True, "share_bind": "127.0.0.1", "share_port": port_b,
                            "share_token": "token-B"})
    window_a = make_window({"share_enabled": True, "share_bind": "127.0.0.1", "share_port": port_a,
                            "share_token": "token-A"})
    spin(app, 1.0)
    check("Knopf 'Koppeln…' erst mit Adresse aktiv", not window_a.settings_tab._partner_pair_button.isEnabled())
    window_a.settings_tab._partner_address_edit.setText(f"127.0.0.1:{port_b}")
    check("... dann aktiv", window_a.settings_tab._partner_pair_button.isEnabled())

    window_a.settings_tab._partner_pair_button.click()
    shown = spin(app, 6.0, lambda: window_b._pair_confirm is not None and window_b._pair_confirm.isVisible()
                 and window_a._pair_dialog is not None and window_a._pair_dialog._code.isVisible())
    check("B zeigt die Anfrage, A den Code", shown)
    from pairing import format_code
    code_b = window_b._live_state.pairing.current().code if window_b._live_state.pairing.current() else ""
    check("beide zeigen denselben Code", window_a._pair_dialog._code.text() == format_code(code_b), code_b)
    window_b._pair_confirm._decide(True)
    paired = spin(app, 6.0, lambda: window_a._settings.partner_config["token"] == "token-B"
                  and window_b._settings.partner_config["token"] == "token-A")
    check("Token ausgetauscht", paired, f"{window_a._settings.partner_config} / {window_b._settings.partner_config}")
    cfg_a, cfg_b = window_a._settings.partner_config, window_b._settings.partner_config
    check("A: Verbund an, Adresse wie eingetragen", cfg_a["enabled"] and cfg_a["address"] == f"127.0.0.1:{port_b}")
    check("B: Verbund an, Adresse = Absender + Port von A", cfg_b["enabled"] and cfg_b["address"] == f"127.0.0.1:{port_a}",
          cfg_b["address"])
    check("A meldet 'Gekoppelt'", "Gekoppelt" in window_a._pair_dialog._status.text(), window_a._pair_dialog._status.text())
    connected = spin(app, 10.0, lambda: window_a._partner.is_connected() and window_b._partner.is_connected())
    check("beide verbinden sich mit dem ausgetauschten Token", connected,
          f"{window_a._partner.reason()} / {window_b._partner.reason()}")
    window_a._pair_dialog.accept()

    window_a._pair_dialog = None
    spin(app, 3.5)   # Sperre nach einer Anfrage gilt nur nach Ablehnen/Abbruch, hier zur Sicherheit warten
    window_a._start_pairing(f"127.0.0.1:{port_b}")
    spin(app, 6.0, lambda: window_b._pair_confirm is not None and window_b._pair_confirm.isVisible())
    window_b._pair_confirm._decide(False)
    rejected = spin(app, 4.0, lambda: "abgelehnt" in window_a._pair_dialog._status.text())
    check("Ablehnen an B -> A meldet 'abgelehnt'", rejected, window_a._pair_dialog._status.text())
    check("... Token bei A unveraendert", window_a._settings.partner_config["token"] == "token-B")
    window_a._pair_dialog.accept()

    spin(app, 3.5)   # Sperre nach Ablehnen
    window_a._start_pairing(f"127.0.0.1:{port_b}")
    spin(app, 6.0, lambda: window_b._pair_confirm is not None and window_b._pair_confirm.isVisible())
    window_a._pair_dialog._on_button()   # Abbrechen
    closed = spin(app, 4.0, lambda: not window_b._pair_confirm or not window_b._pair_confirm.isVisible())
    check("Abbrechen an A schliesst den Dialog an B", closed)
    check("A meldet 'Abgebrochen'", spin(app, 3.0, lambda: "Abgebrochen" in window_a._pair_dialog._status.text()),
          window_a._pair_dialog._status.text())

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

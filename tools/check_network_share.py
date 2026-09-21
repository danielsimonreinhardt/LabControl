"""Prueft die Netzwerk-Freigabe (siehe lab_gui/share_server.py) auf allen
vier Ebenen -- reine Logik, echter Socket, Einstellungen, ganze App.

Nicht Teil der laufenden App und kein Testframework: das Projekt kommt
bewusst ohne pytest aus, deshalb ein eigenstaendiges Skript nach dem
Muster der uebrigen tools/-Skripte. Von Hand aufzurufen, nachdem an
share_api.py, share_render.py, share_server.py, live_state.py oder am
Netzwerk-Reiter in settings_tab.py etwas geaendert wurde.

    python tools/check_network_share.py              # alles
    python tools/check_network_share.py pure server  # nur diese Abschnitte

Abschnitte:
  pure      share_api/share_render -- Routing, Zugriffspruefung, Freigabe-
            liste, HTML-Escaping. Laeuft OHNE QApplication und OHNE Socket;
            faellt dieser Abschnitt beim Import um, hat sich versehentlich
            ein Qt-Import in die reine Schicht geschlichen.
  remote    Schreib-Endpoints (Fernsteuerung) der reinen API-Schicht mit einem
            Stub-Executor: Token, Reihenfolge der Abweisungen, Validierung,
            Sicherheits-Grenzwerte, ALLE AUS. Ohne Qt, ohne Socket.
  server    ShareServer an einem echt gebundenen Port: Start/Stopp,
            Portwechsel, belegter Port, Nebenlaeufigkeit, sauberes Beenden.
  settings  Der Freigabe-Block in settings.py inkl. Deep-Merge gegen eine
            kaputt editierte settings.json.
  i18n      Vollstaendigkeit der Sprachdateien und Live-Umschaltung.
  windowed  Anfragen bei sys.stderr is None -- so verhaelt sich die
            gebaute .exe (--windowed). Faengt den Absturz ab, der sonst
            NUR im Release auftritt.
  app       Die komplette App im Simulationsmodus gegen echte HTTP-Anfragen.

settings.json und device_labels.json werden fuer die Abschnitte "settings"
und "app" in ein Temp-Verzeichnis umgelenkt -- der echte Stand des Nutzers
bleibt unberuehrt.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                    # fuer microhil, can_bus, ...
sys.path.insert(0, str(ROOT / "lab_gui"))        # fuer share_api, live_state, ...

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  OK   " if ok else "  FEHL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(name)


def head(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------- pure ----

class _Stub:
    """Ersetzt LiveState: liefert einen festen Schnappschuss."""

    def __init__(self, snap):
        self._snap = snap

    def snapshot(self, device_ids=None):
        return self._snap


def _snap(lock="free", read_token=False, token=""):
    return {
        "devices": {
            "psu:SIM": {"kind": "psu", "label": "Netzteil 1", "online": True,
                        "fields": {"voltage": 12.003, "current": 0.512, "mode": "CV"},
                        "age_s": 0.12, "stale": False},
            # Label mit Markup: Geraetenamen sind Nutzereingaben und muessen
            # im HTML escaped werden.
            "load:SIM": {"kind": "load", "label": "Last <b>1</b>", "online": True,
                         "fields": {"voltage": 11.9, "current": 1.25,
                                    "power": 14.875, "mode": "CC"},
                         "age_s": 0.2, "stale": False},
            "hil:SIM": {"kind": "hil", "label": "HIL", "online": True,
                        "fields": {"ain1": 1234.0}, "age_s": 0.1, "stale": False},
        },
        "share": {
            "read_requires_token": read_token, "token": token,
            "devices": {"psu:SIM": {"read": True, "control": False},
                        "load:SIM": {"read": True, "control": False},
                        "hil:SIM": {"read": False, "control": False}},
        },
        "labels": {("psu", "voltage"): "Voltage", ("psu", "current"): "Current",
                   ("psu", "mode"): "Mode"},
        "lock": lock, "safety": "armed", "test_running": lock == "test_running",
    }


def section_pure() -> None:
    head("pure -- share_api / share_render (ohne Qt, ohne Socket)")
    import share_api
    import share_render

    check("keine Qt-Abhaengigkeit in der reinen Schicht", "PySide6" not in sys.modules)

    app_info = {"version": "0.0.0", "simulation": True, "uptime_s": 1.0,
                "base_url": "http://127.0.0.1:8420"}
    no_header = lambda _name: None

    def get(path, hdr=no_header, snap=None):
        return share_api.dispatch("GET", path, hdr, _Stub(snap or _snap()), app_info)

    def body(resp):
        return json.loads(resp.body.decode("utf-8"))

    check("status zaehlt nur freigegebene Kacheln", body(get("/api/v1/status"))["tiles"] == 2)
    check("Schreibzugriff immer tokenpflichtig",
          body(get("/api/v1/status"))["auth"]["control_token_required"] is True)

    ids = [t["id"] for t in body(get("/api/v1/tiles"))["tiles"]]
    check("nicht freigegebene Kachel fehlt in der Liste", "hil:SIM" not in ids, str(ids))

    tile = body(get("/api/v1/tiles/psu:SIM"))
    check("Feldreihenfolge aus dem Katalog",
          [f["key"] for f in tile["fields"]] == ["voltage", "current", "mode"])
    check("uebersetzter Feldname wird benutzt", tile["fields"][0]["name"] == "Voltage")
    check("numerisch -> value, Text -> text",
          "value" in tile["fields"][0] and "text" not in tile["fields"][0]
          and "text" in tile["fields"][2] and "value" not in tile["fields"][2])

    a, b = get("/api/v1/tiles/hil:SIM"), get("/api/v1/tiles/psu:GIBTESNICHT")
    check("gesperrt und nicht existent sind ununterscheidbar",
          (a.status, a.body) == (b.status, b.body) == (404, b'{"v":1,"error":"unknown_tile"}'))

    check("prozentkodierte Geraete-ID", get("/api/v1/tiles/psu%3ASIM").status == 200)
    check("Sammelabruf filtert Nichtfreigegebenes",
          [t["id"] for t in body(get("/api/v1/values?tiles=psu:SIM,hil:SIM"))["tiles"]] == ["psu:SIM"])
    check("Obergrenze von 20 Kachel-IDs greift",
          len(share_api._requested_ids({"tiles": [",".join(f"x{n}" for n in range(50))]},
                                       "tiles")) == 20)

    guarded = _snap(read_token=True, token="geheim")
    bearer = lambda n: "Bearer geheim" if n == "Authorization" else None
    wrong = lambda n: "Bearer falsch" if n == "Authorization" else None
    check("ohne Token abgewiesen", get("/api/v1/tiles", snap=guarded).status == 401)
    check("WWW-Authenticate gesetzt",
          bool(get("/api/v1/tiles", snap=guarded).headers.get("WWW-Authenticate")))
    check("Token als Query akzeptiert", get("/api/v1/tiles?token=geheim", snap=guarded).status == 200)
    check("Token als Bearer akzeptiert", get("/api/v1/tiles", bearer, guarded).status == 200)
    check("falscher Token abgewiesen", get("/api/v1/tiles", wrong, guarded).status == 401)
    check("auch /display ist geschuetzt", get("/display", snap=guarded).status == 401)
    check("Token verlangt aber keiner gesetzt -> 503 statt 401",
          get("/api/v1/tiles", snap=_snap(read_token=True, token="")).status == 503)

    check("POST auf einen Lese-Pfad -> 405",
          share_api.dispatch("POST", "/api/v1/tiles", no_header, _Stub(_snap()), app_info).status == 405)
    check("GET auf .../actions -> 404 (nur POST)",
          get("/api/v1/tiles/psu:SIM/actions").status == 404)
    check("kein Dateisystem-Pfad erreichbar",
          get("/etc/passwd").status == 404 and get("/../../settings.json").status == 404)

    display = get("/display?tile=psu:SIM&refresh=5")
    check("Display-Seite unter 1500 Byte", len(display.body) < 1500, f"{len(display.body)} Byte")
    check("Meta-Refresh gesetzt", b'http-equiv="refresh" content="5"' in display.body)
    check("refresh=0 laesst Meta-Refresh weg",
          b"http-equiv" not in get("/display?tile=psu:SIM&refresh=0").body)

    escaped = get("/display?tile=load:SIM").body.decode("utf-8")
    check("Geraetelabel wird HTML-escaped", "<b>1</b>" not in escaped and "&lt;b&gt;" in escaped)

    text = get("/display?tile=psu:SIM&fmt=text").body.decode("utf-8")
    check("Textformat liefert key=value",
          text.startswith("lock=free") and "voltage=12.003 V" in text and "mode=CV" in text)

    stale = _snap()
    stale["devices"]["psu:SIM"].update({"online": False, "stale": True, "age_s": 7.5})
    marked = get("/display?tile=psu:SIM", snap=stale).body.decode("utf-8")
    check("getrennter Wert wird markiert", 'class="v s"' in marked and "getrennt" in marked)
    check("Sperrgrund steht auf der Seite",
          "Sicherheitsabschaltung" in
          get("/display?tile=psu:SIM", snap=_snap(lock="safety_tripped")).body.decode("utf-8"))

    index = get("/").body.decode("utf-8")
    check("Startseite listet nur Freigegebenes",
          "/display?tile=psu:SIM" in index and "hil:SIM" not in index)
    check("Token ist eindeutig", share_api.new_token() != share_api.new_token())

    fv = share_render.format_value
    check("Zahlformatierung je Einheit",
          (fv(12.0, "V"), fv(14.875, "W"), fv(1234.0, "mV"), fv(42, ""), fv(True, ""))
          == ("12.000", "14.88", "1234", "42", "1"))


# -------------------------------------------------------------- remote ----

class _Executor:
    """Ersetzt share_remote.RemoteBridge: merkt sich Aufrufe und liefert ein
    vorgegebenes Ergebnis."""

    def __init__(self, share_api):
        self.api = share_api
        self.calls: list = []
        self.all_off_calls: list = []
        self.next_result = share_api.RemoteResult("ok")

    def submit_action(self, device_id, kind, action, value, channel, client):
        self.calls.append((device_id, kind, action, value, channel, client))
        return self.next_result

    def submit_all_off(self, client):
        self.all_off_calls.append(client)
        return self.next_result


def _snap_rw(lock="free", remote=True, token="tok", control=True, online=True, limits=None):
    """Schnappschuss mit Fernsteuer-Zustand. psu:SIM/load:SIM/hil:SIM/can:SIM
    sind lesbar; steuerbar sind sie nur, wenn `control` gesetzt ist."""
    snap = _snap(token=token)
    snap["devices"]["psu:SIM"]["online"] = online
    snap["devices"]["hil:SIM"] = {"kind": "hil", "label": "HIL", "online": True,
                                  "fields": {"out1": 0}, "age_s": 0.1, "stale": False}
    snap["devices"]["can:SIM"] = {"kind": "can", "label": "CAN", "online": True,
                                  "fields": {"tx_count": 1}, "age_s": 0.1, "stale": False}
    snap["share"]["devices"] = {
        "psu:SIM": {"read": True, "control": control},
        "load:SIM": {"read": True, "control": control},
        "hil:SIM": {"read": True, "control": control},
        "can:SIM": {"read": True, "control": True},   # von Hand editiert: darf NIE steuerbar sein
    }
    snap["lock"] = lock
    snap["test_running"] = lock == "test_running"
    snap["remote"] = {"active": remote, "remaining_s": 1800.0 if remote else 0.0}
    snap["safety_limits"] = limits or {}
    return snap


def section_remote() -> None:
    head("remote -- Schreib-Endpoints der reinen API-Schicht (Stub-Executor)")
    import share_api
    import remote_actions

    app_info = {"version": "0.0.0", "simulation": True, "uptime_s": 1.0, "base_url": ""}
    bearer = lambda n: "Bearer tok" if n == "Authorization" else None
    no_header = lambda _n: None

    def post(path, payload=None, snap=None, hdr=bearer, raw=None, executor=None):
        ex = executor or _Executor(share_api)
        body = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode()
        resp = share_api.dispatch("POST", path, hdr, _Stub(snap or _snap_rw()), app_info,
                                  body=body, executor=ex, client="10.0.0.9")
        return resp, ex

    def data(resp):
        return json.loads(resp.body.decode("utf-8"))

    act = "/api/v1/tiles/psu:SIM/actions"
    good = {"action": "PSU_VOLT", "value": 12.0}

    # -- Token --------------------------------------------------------------
    check("ohne Token -> 401", post(act, good, hdr=no_header)[0].status == 401)
    check("falscher Token -> 401",
          post(act, good, hdr=lambda n: "Bearer falsch" if n == "Authorization" else None)[0].status == 401)
    query_only = share_api.dispatch("POST", act + "?token=tok", no_header, _Stub(_snap_rw()), app_info,
                                    body=json.dumps(good).encode(), executor=_Executor(share_api))
    check("Token in der URL gilt beim Schreiben NICHT", query_only.status == 401)
    check("kein Token konfiguriert -> 503 statt 401",
          post(act, good, snap=_snap_rw(token=""))[0].status == 503)
    open_read = _snap_rw()
    open_read["share"]["read_requires_token"] = False
    check("Lesen tokenfrei macht Schreiben nicht tokenfrei",
          post(act, good, snap=open_read, hdr=no_header)[0].status == 401)

    # -- Routen -------------------------------------------------------------
    check("POST auf Lese-Pfad -> 405 (auch ohne Token)",
          post("/api/v1/tiles", hdr=no_header)[0].status == 405)
    check("POST auf unbekannten Pfad -> 404", post("/api/v1/gibtesnicht")[0].status == 404)
    check("Unterpfad-Injektion -> 404", post("/api/v1/tiles/psu:SIM/x/actions", good)[0].status == 404)
    hidden, nonexist = post("/api/v1/tiles/nicht:da/actions", good)[0], None
    snap_hidden = _snap_rw()
    snap_hidden["share"]["devices"]["psu:SIM"]["read"] = False
    nonexist = post("/api/v1/tiles/nicht:da/actions", good, snap=snap_hidden)[0]
    hidden = post(act, good, snap=snap_hidden)[0]
    check("nicht freigegeben und nicht existent sind ununterscheidbar",
          (hidden.status, hidden.body) == (nonexist.status, nonexist.body) and hidden.status == 404)

    # -- Reihenfolge der Abweisungen -------------------------------------------
    resp, ex = post(act, good, snap=_snap_rw(control=False))
    check("Kachel nicht fuer Steuern freigegeben -> 403",
          resp.status == 403 and data(resp)["error"] == "control_not_permitted" and not ex.calls)
    resp, ex = post(act, good, snap=_snap_rw(remote=False))
    check("Hauptschalter aus -> 403",
          resp.status == 403 and data(resp)["error"] == "remote_control_inactive" and not ex.calls)
    resp, ex = post(act, good, snap=_snap_rw(lock="test_running"))
    check("Testlauf -> 409 mit Sperrgrund",
          resp.status == 409 and data(resp)["lock"] == "test_running" and not ex.calls)
    resp, ex = post(act, good, snap=_snap_rw(lock="safety_tripped"))
    check("Sicherheitsabschaltung -> 409", resp.status == 409 and data(resp)["lock"] == "safety_tripped")
    resp, ex = post(act, good, snap=_snap_rw(online=False))
    check("Geraet offline -> 409", resp.status == 409 and data(resp)["error"] == "device_offline")
    resp, ex = post("/api/v1/tiles/can:SIM/actions", {"action": "CAN_SEND"})
    check("CAN nie steuerbar, auch wenn von Hand freigegeben",
          resp.status == 403 and not ex.calls)

    # -- Rumpf ---------------------------------------------------------------
    for label, raw in (("kein JSON", b"{kaputt"), ("leer", b""), ("Liste statt Objekt", b"[1,2]"),
                       ("zu gross", b'{"action":"' + b"x" * 5000 + b'"}')):
        resp, ex = post(act, raw=raw)
        check(f"Rumpf {label} -> 400", resp.status == 400 and data(resp)["error"] == "invalid_body"
              and not ex.calls)

    # -- Validierung -----------------------------------------------------------
    cases = [
        ({"action": "GIBTESNICHT"}, "unknown_action"),
        ({"action": "CAN_SEND"}, "unknown_action"),
        ({"action": "OUT_ON"}, "unknown_action"),              # Last-Aktion an einem Netzteil
        ({}, "unknown_action"),
        ({"action": 5}, "unknown_action"),
        ({"action": "PSU_VOLT"}, "value_required"),
        ({"action": "PSU_VOLT", "value": "12"}, "invalid_value"),
        ({"action": "PSU_VOLT", "value": True}, "invalid_value"),
        ({"action": "PSU_VOLT", "value": 0.5}, "value_out_of_range"),   # < 1 V wuerde ignoriert
        ({"action": "PSU_VOLT", "value": 60.1}, "value_out_of_range"),
        ({"action": "PSU_CURR", "value": -1}, "value_out_of_range"),
    ]
    for payload, expected in cases:
        resp, ex = post(act, payload)
        check(f"{payload} -> {expected}",
              resp.status == 400 and data(resp)["error"] == expected and not ex.calls,
              f"{resp.status} {data(resp).get('error')}")
    resp, _ = post(act, raw=b'{"action":"PSU_VOLT","value":NaN}')
    check("NaN wird abgelehnt", resp.status == 400 and data(resp)["error"] == "invalid_value")
    resp, _ = post(act, raw=b'{"action":"PSU_VOLT","value":Infinity}')
    check("Infinity wird abgelehnt", resp.status == 400)

    hil = "/api/v1/tiles/hil:SIM/actions"
    for payload, expected in (({"action": "HIL_OUT_ON"}, "channel_required"),
                              ({"action": "HIL_OUT_ON", "channel": 0}, "channel_out_of_range"),
                              ({"action": "HIL_OUT_ON", "channel": 9}, "channel_out_of_range"),
                              ({"action": "HIL_RELAY_ON", "channel": 5}, "channel_out_of_range"),
                              ({"action": "HIL_OUT_ON", "channel": "1"}, "invalid_channel"),
                              ({"action": "HIL_AOUT", "channel": 1}, "value_required"),
                              ({"action": "HIL_AOUT", "channel": 3, "value": 100}, "channel_out_of_range"),
                              ({"action": "HIL_AOUT", "channel": 1, "value": 99999}, "value_out_of_range")):
        resp, ex = post(hil, payload)
        check(f"HIL {payload} -> {expected}",
              resp.status == 400 and data(resp)["error"] == expected and not ex.calls,
              f"{resp.status} {data(resp).get('error')}")

    # -- Sicherheits-Grenzwerte ---------------------------------------------------
    limits = {"psu:SIM": {"max_voltage": {"enabled": True, "value": 12.0},
                          "max_current": {"enabled": False, "value": 1.0}}}
    resp, ex = post(act, {"action": "PSU_VOLT", "value": 12.5}, snap=_snap_rw(limits=limits))
    check("Sollwert ueber aktivem Grenzwert -> 400",
          resp.status == 400 and data(resp)["error"] == "exceeds_safety_limit"
          and data(resp)["limit"] == 12.0 and not ex.calls)
    resp, ex = post(act, {"action": "PSU_VOLT", "value": 12.0}, snap=_snap_rw(limits=limits))
    check("Sollwert genau auf dem Grenzwert erlaubt", resp.status == 200 and len(ex.calls) == 1)
    resp, ex = post(act, {"action": "PSU_CURR", "value": 9.0}, snap=_snap_rw(limits=limits))
    check("nicht aktivierter Grenzwert zaehlt nicht", resp.status == 200)

    # -- Erfolg ----------------------------------------------------------------
    resp, ex = post(act, {"action": "PSU_VOLT", "value": 12, "channel": 7, "extra": "ignoriert"})
    check("Aktion wird an den Executor uebergeben",
          resp.status == 200 and ex.calls == [("psu:SIM", "psu", "PSU_VOLT", 12.0, 0, "10.0.0.9")],
          str(ex.calls))
    check("Antwort nennt Geraet, Aktion, Wert",
          data(resp)["ok"] is True and data(resp)["device"] == "psu:SIM"
          and data(resp)["action"] == "PSU_VOLT" and data(resp)["value"] == 12.0)
    resp, ex = post("/api/v1/tiles/load:SIM/actions", {"action": "OUT_ON", "value": 99})
    check("Aktion ohne Wert: Wert wird ignoriert",
          resp.status == 200 and ex.calls[0][3] == 0.0 and "value" not in data(resp))
    resp, ex = post(hil, {"action": "HIL_AOUT", "channel": 2, "value": 5000})
    check("HIL: Kanal wird uebergeben", resp.status == 200 and ex.calls[0][4] == 2)
    check("Kachel-ID prozentkodiert",
          post("/api/v1/tiles/psu%3ASIM/actions", good)[0].status == 200)

    # -- Ergebnis des Executors ------------------------------------------------------
    R = share_api.RemoteResult
    for result, status, err in ((R("pending"), 202, None),
                                (R("error", "device_error", "Netzteil nicht verbunden"), 502, "device_error"),
                                (R("error", "locked"), 409, "locked"),
                                (R("error", "inactive"), 403, "remote_control_inactive"),
                                (R("error", "rate_limited"), 429, "rate_limited"),
                                (R("error", "busy"), 429, "busy")):
        ex = _Executor(share_api)
        ex.next_result = result
        resp, _ = post(act, good, executor=ex)
        body_ = data(resp)
        check(f"Executor {result.status}/{result.code} -> HTTP {status}",
              resp.status == status and body_.get("error") == err,
              f"{resp.status} {body_}")
    ex = _Executor(share_api)
    ex.next_result = R("error", "rate_limited")
    check("429 nennt Retry-After", post(act, good, executor=ex)[0].headers.get("Retry-After") == "1")
    ex = _Executor(share_api)
    ex.next_result = R("error", "device_error", "Netzteil nicht verbunden")
    check("Fehlermeldung des Geraets wird durchgereicht",
          data(post(act, good, executor=ex)[0])["message"] == "Netzteil nicht verbunden")

    # -- ALLE AUS ------------------------------------------------------------------
    for label, snap in (("normal", _snap_rw()),
                        ("Hauptschalter aus", _snap_rw(remote=False)),
                        ("Testlauf", _snap_rw(lock="test_running")),
                        ("nach Abschaltung", _snap_rw(lock="safety_tripped")),
                        ("nichts steuerbar freigegeben", _snap_rw(control=False))):
        resp, ex = post("/api/v1/all-off", snap=snap)
        check(f"ALLE AUS geht: {label}", resp.status == 200 and ex.all_off_calls == ["10.0.0.9"])
    check("ALLE AUS braucht trotzdem den Token",
          post("/api/v1/all-off", hdr=no_header)[0].status == 401)
    ex = _Executor(share_api)
    ex.next_result = R("error", "all_off_incomplete", "psu:COM6;load:X")
    resp, _ = post("/api/v1/all-off", executor=ex)
    check("ALLE AUS meldet fehlgeschlagene Geraete",
          resp.status == 502 and data(resp)["failures"] == ["psu:COM6", "load:X"], str(data(resp)))
    check("ALLE AUS ohne Executor -> 501",
          share_api.dispatch("POST", "/api/v1/all-off", bearer, _Stub(_snap_rw()), app_info).status == 501)

    # -- Lesende Antworten: Fernsteuer-Zustand sichtbar ----------------------------------
    def get(path, snap):
        return json.loads(share_api.dispatch("GET", path, no_header, _Stub(snap), app_info).body)

    tile = get("/api/v1/tiles/psu:SIM", _snap_rw())
    check("Kachel listet ihre Aktionen", {a["action"] for a in tile["actions"]}
          == set(remote_actions.REMOTE_ACTIONS["psu"]))
    check("Kachel meldet: steuerbar jetzt", tile["control"] is True and tile["control_available"] is True
          and tile["control_blocked"] == "")
    volt = next(a for a in tile["actions"] if a["action"] == "PSU_VOLT")
    check("Aktionsbeschreibung nennt Bereich und Einheit",
          volt["value"] == {"unit": "V", "min": 1, "max": 60})
    for snap, reason in ((_snap_rw(remote=False), "remote_control_inactive"),
                         (_snap_rw(lock="test_running"), "locked"),
                         (_snap_rw(online=False), "device_offline")):
        t = get("/api/v1/tiles/psu:SIM", snap)
        check(f"Kachel nennt Sperrgrund {reason}",
              t["control_available"] is False and t["control_blocked"] == reason)
    t = get("/api/v1/tiles/psu:SIM", _snap_rw(control=False))
    check("ohne Steuern-Freigabe keine Aktionen", t["actions"] == [] and t["control"] is False)
    check("CAN-Kachel zeigt nie Aktionen",
          get("/api/v1/tiles/can:SIM", _snap_rw())["actions"] == [])
    status = get("/api/v1/status", _snap_rw())
    check("Status nennt den Hauptschalter",
          status["remote_control"] == {"active": True, "remaining_s": 1800.0})
    check("Status: Hauptschalter aus",
          get("/api/v1/status", _snap_rw(remote=False))["remote_control"]["active"] is False)

    # -- Katalog gegen den Testeditor ------------------------------------------------------
    # Nur hier wird Qt geladen (testcase_model importiert i18n); "pure" prueft
    # vorher, dass die reine Schicht es nicht braucht.
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import testcase_model as tm
    mismatch = []
    for kind, actions in remote_actions.REMOTE_ACTIONS.items():
        for code, definition in actions.items():
            expected = tm.ACTION_VALUE_RANGE.get(code)
            if expected is None:
                mismatch.append(f"{code}: fehlt in testcase_model")
            elif definition.needs_value and expected != (definition.unit, definition.minimum, definition.maximum):
                mismatch.append(f"{code}: {expected} != {(definition.unit, definition.minimum, definition.maximum)}")
    check("Wertebereiche stimmen mit testcase_model.ACTION_VALUE_RANGE ueberein",
          not mismatch, "; ".join(mismatch))
    check("HIL-Kanalzahlen stimmen mit testcase_model.HIL_CHANNEL_COUNTS ueberein",
          all(remote_actions.REMOTE_ACTIONS["hil"][c].channels == n
              for c, n in tm.HIL_CHANNEL_COUNTS.items() if c in remote_actions.REMOTE_ACTIONS["hil"]))
    known = set(tm.LOAD_ACTIONS) | set(tm.PSU_ACTIONS) | set(tm.HIL_ACTIONS)
    check("jede Fernsteuer-Aktion ist ein bekannter Testeditor-Aktionscode",
          all(c in known for a in remote_actions.REMOTE_ACTIONS.values() for c in a))
    check("nie enthalten: CAN_SEND, PICO_*, ARB, Lese-Aktionen",
          not any(c in {"CAN_SEND", "ARB", "PSU_ARB", "HIL_IN_READ", "HIL_AIN_READ"} or c.startswith("PICO_")
                  for a in remote_actions.REMOTE_ACTIONS.values() for c in a))


# -------------------------------------------------------------- server ----

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def section_server() -> None:
    head("server -- ShareServer an einem echten Socket")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from live_state import LiveState
    from share_server import ShareServer, lan_address

    port = _free_port()
    state = LiveState()
    state.on_device_known("psu", "psu:SIM", "Netzteil 1")
    state.on_psu_measurement("psu:SIM", 12.003, 0.512, False)
    state.on_device_known("load", "load:SIM", "Last 1")
    state.on_load_measurement("load:SIM", 11.9, 1.25, 14.875)

    cfg = {"enabled": True, "bind": "127.0.0.1", "port": port, "token": "",
           "read_requires_token": False, "access_log": False,
           "devices": {"psu:SIM": {"read": True, "control": False}}}
    state.set_share_config(cfg)

    server = ShareServer(state)
    events: list[str] = []
    server.started.connect(lambda _u: events.append("started"))
    server.stopped.connect(lambda: events.append("stopped"))
    server.error.connect(lambda _m: events.append("error"))

    def fetch(path, at=None, token=None, timeout=5):
        req = urllib.request.Request(f"http://127.0.0.1:{at or port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    print(f"  (LAN-Adresse dieses Rechners: {lan_address()}, Testport {port})")
    server.apply(cfg)
    check("Server gestartet", events == ["started"])

    status, _, headers = fetch("/api/v1/status")
    check("Statusabfrage beantwortet", status == 200)
    check("Sicherheits-Header gesetzt",
          headers.get("X-Content-Type-Options") == "nosniff"
          and headers.get("Cache-Control") == "no-store")
    check("kein CORS-Header", "Access-Control-Allow-Origin" not in headers)
    check("Content-Length immer gesetzt", "Content-Length" in headers)

    check("freigegebene Kachel liefert Werte", b"12.003" in fetch("/api/v1/tiles/psu:SIM")[1])
    check("nicht freigegebene Kachel -> 404", fetch("/api/v1/tiles/load:SIM")[0] == 404)

    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/status", method="HEAD")
    with urllib.request.urlopen(req, timeout=5) as r:
        check("HEAD ohne Rumpf, aber mit Laenge",
              r.read() == b"" and int(r.headers["Content-Length"]) > 0)

    # Freigabe und Token wirken ohne Neustart
    cfg2 = dict(cfg, devices=dict(cfg["devices"], **{"load:SIM": {"read": True, "control": False}}))
    state.set_share_config(cfg2)
    server.apply(cfg2)
    check("Freigabe live wirksam", fetch("/api/v1/tiles/load:SIM")[0] == 200)
    cfg3 = dict(cfg2, read_requires_token=True, token="geheim123")
    state.set_share_config(cfg3)
    server.apply(cfg3)
    check("Token live wirksam",
          fetch("/api/v1/tiles")[0] == 401 and fetch("/api/v1/tiles", token="geheim123")[0] == 200)
    check("kein unnoetiger Neustart", events.count("started") == 1)

    # Portwechsel
    port2 = _free_port()
    server.apply(dict(cfg3, port=port2))
    old_dead = False
    try:
        fetch("/api/v1/status", timeout=2)
    except Exception:
        old_dead = True
    check("alter Port nach Wechsel tot", old_dead)
    check("neuer Port bedient", fetch("/api/v1/status", at=port2, token="geheim123")[0] == 200)

    # Belegter Port
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", _free_port() if False else port))
    blocker.listen(1)
    events.clear()
    server.apply(dict(cfg3, port=port))
    check("belegter Port meldet Fehler statt zu stuerzen",
          "error" in events and not server.is_running())
    blocker.close()

    # Nebenlaeufigkeit
    server.apply(dict(cfg3, port=port2))
    codes: list = []
    threads = [threading.Thread(
        target=lambda: codes.append(fetch("/api/v1/status", at=port2, token="geheim123")[0]))
        for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("30 parallele Anfragen beantwortet", codes.count(200) == 30,
          str({c: codes.count(c) for c in set(codes)}))

    # Sauberes Beenden waehrend laufender Anfragen
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
                fetch("/api/v1/status", at=port2, token="geheim123", timeout=1)
            except Exception:
                pass

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    time.sleep(0.3)
    began = time.monotonic()
    server.stop()
    stop.set()
    took = time.monotonic() - began
    check("stop() beendet zuegig", took < 3.0, f"{took:.2f} s")

    # allow_reuse_address = False darf einen sofortigen Neustart nicht verhindern
    time.sleep(0.2)
    again = ShareServer(state)
    again.apply(dict(cfg3, port=port2))
    check("Port sofort wieder bindbar", again.is_running())
    again.stop()

    server.apply(dict(cfg3, enabled=False))
    off = False
    try:
        fetch("/api/v1/status", at=port2, timeout=2)
    except Exception:
        off = True
    check("deaktiviert lauscht nichts", off)


# ------------------------------------------------------------ settings ----

def _temp_settings():
    """Lenkt settings.json in ein Temp-Verzeichnis um."""
    import settings as settings_mod
    tmp = pathlib.Path(tempfile.mkdtemp())
    settings_mod.SETTINGS_PATH = tmp / "settings.json"
    return settings_mod, tmp


def section_settings() -> None:
    head("settings -- Freigabe-Block in settings.py")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    settings_mod, tmp = _temp_settings()
    Settings = settings_mod.Settings

    s = Settings()
    seen: list = []
    s.share_config_changed.connect(seen.append)
    cfg = s.share_config
    check("Voreinstellung ist vollstaendig inert",
          cfg["enabled"] is False and cfg["bind"] == "127.0.0.1"
          and cfg["read_requires_token"] is True and cfg["devices"] == {})

    s.set_share_enabled(True)
    s.set_share_device("psu:SIM", True, False)
    before = len(seen)
    s.set_share_enabled(True)
    s.set_share_device("psu:SIM", True, False)
    check("kein Signal bei unveraendertem Wert", len(seen) == before)

    s.set_share_bind("8.8.8.8")
    check("fremde Bindeadresse abgelehnt", s.share_config["bind"] == "127.0.0.1")
    s.set_share_bind("0.0.0.0")
    check("LAN-Adresse akzeptiert", s.share_config["bind"] == "0.0.0.0")
    s.set_share_port(80)
    check("Port auf Minimum geklemmt", s.share_config["port"] == 1024)
    s.set_share_port(999999)
    check("Port auf Maximum geklemmt", s.share_config["port"] == 65535)

    s.set_share_device("psu:SIM", False, False)
    raw = json.loads(settings_mod.SETTINGS_PATH.read_text(encoding="utf-8"))
    check("Default-Eintrag wird nicht mitgeschleppt",
          "psu:SIM" not in raw.get("share_devices", {}))

    s.set_share_device("load:SIM", True, False)
    check("Persistenz ueber Neustart",
          Settings().share_config["devices"].get("load:SIM") == {"read": True, "control": False})

    # Kaputt editierte Datei darf im Server-Thread keinen KeyError ausloesen
    raw = json.loads(settings_mod.SETTINGS_PATH.read_text(encoding="utf-8"))
    raw.update({"share_port": "keine Zahl", "share_bind": None,
                "share_devices": {"psu:SIM": "kaputt"}})
    settings_mod.SETTINGS_PATH.write_text(json.dumps(raw), encoding="utf-8")
    broken = Settings().share_config
    check("kaputte settings.json faengt sich",
          broken["port"] == 8420 and broken["bind"] == "127.0.0.1" and broken["devices"] == {})

    fresh = Settings()
    fresh.set_share_device("psu:SIM", True, False)
    fresh.reset_device_settings()
    check("Geraetezuordnung loeschen raeumt die Freigabe mit ab",
          fresh.share_config["devices"] == {})
    check("Server-Einstellungen bleiben beim Zuruecksetzen erhalten",
          fresh.share_config["port"] == 8420)


# ---------------------------------------------------------------- i18n ----

def _tr_keys(path: pathlib.Path) -> set[str]:
    """Alle literalen tr("...")-Argumente -- per AST statt Regex, damit
    Umbrueche und Anfuehrungszeichen im Text nicht stoeren."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "tr" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
    return found


# Strings der Netzwerk-Freigabe -- nur diese werden geprueft. Der Rest der
# App verlaesst sich teilweise bewusst auf den Deutsch-Fallback.
SHARE_KEYS = {
    "Netzwerk", "Freigabe im lokalen Netzwerk aktivieren", "Erreichbar für",
    "Nur diesen PC", "Alle Geräte im lokalen Netzwerk", "Port", "Adresse",
    "Diese Adresse im ESP32-Sketch oder Browser verwenden", "Token",
    "Neuen Token erzeugen (macht den alten ungültig)",
    "Token in die Zwischenablage kopieren", "Lesezugriff ohne Token erlauben",
    "Zugriffe protokollieren (eigene Datei share_access.log)", "Geräte-ID",
    "Lesen", "Steuern",
    "Server läuft auf {url}", "Server gestoppt",
    "Fernsteuerung aktiv", "Schaltet sich ab nach", "Fernsteuerung aus",
    "Fernsteuerung abgelaufen", "Fernsteuerung aktiv – noch {time}",
    "Fernsteuerung dieses Geräts erlauben (wirkt nur bei aktivem Hauptschalter)",
    "Dieses Gerät lässt sich nicht fernsteuern",
}


def section_i18n() -> None:
    head("i18n -- Sprachdateien und Live-Umschaltung")
    de = json.loads((ROOT / "lab_gui/translations/de.json").read_text(encoding="utf-8"))
    en = json.loads((ROOT / "lab_gui/translations/en.json").read_text(encoding="utf-8"))

    used = _tr_keys(ROOT / "lab_gui/settings_tab.py") | _tr_keys(ROOT / "lab_gui/main_window.py")
    # Die langen Hinweistexte stehen mehrzeilig im Quelltext -- ueber die
    # Schnittmenge mit den tatsaechlich verwendeten Strings mitpruefen.
    keys = (SHARE_KEYS | {k for k in used if "Netzwerk" in k or "Freigabe je Gerät" in k})

    missing_de = sorted(k for k in keys if k not in de)
    missing_en = sorted(k for k in keys if k not in en)
    check("alle Freigabe-Strings in de.json", not missing_de, str(missing_de))
    check("alle Freigabe-Strings in en.json", not missing_en, str(missing_en))
    check("de.json fuehrt sie als Identitaet",
          all(de[k] == k for k in keys if k in de))
    check("en.json uebersetzt sie tatsaechlich",
          all(en[k] != k for k in keys if k in en and k not in ("Port", "Token")))

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from i18n import Translator
    from settings_tab import SettingsTab

    tab = SettingsTab()
    Translator.instance().set_language("de")
    german = tab._subtabs.tabText(4)
    columns_de = [tab._share_table.horizontalHeaderItem(c).text() for c in range(4)]
    Translator.instance().set_language("en")
    english = tab._subtabs.tabText(4)
    columns_en = [tab._share_table.horizontalHeaderItem(c).text() for c in range(4)]
    Translator.instance().set_language("de")
    check("Reitertitel uebersetzt", (german, english) == ("Netzwerk", "Network"))
    check("Tabellenspalten uebersetzt",
          columns_en == ["Device", "Device ID", "Read", "Control"], str(columns_de))


# Client-Skript fuer die MCP-Stufe von section_app. Laeuft im Interpreter der
# labcontrol_mcp/.venv (nur dort ist das Paket "mcp" installiert) und sieht den
# Server so, wie es ein echter MCP-Client tut: als Kindprozess ueber stdin/stdout.
MCP_PROBE = r"""
import asyncio, json, os, sys
from mcp import Client
from mcp.client.stdio import StdioServerParameters

async def main():
    params = StdioServerParameters(command=sys.executable, args=[sys.argv[1]], env={**os.environ})
    out = {}
    async with Client(params) as c:
        async def call(name, args):
            r = await c.call_tool(name, args)
            text = r.content[0].text if r.content else ""
            data = r.structured_content
            if data is None:
                # dict-Rueckgaben kommen als JSON-Text; Fehler sind Klartext
                try:
                    data = json.loads(text)
                except ValueError:
                    data = None
            return {"error": bool(r.is_error), "data": data, "text": text}
        out["tools"] = sorted(t.name for t in (await c.list_tools()).tools)
        out["instructions"] = c.instructions or ""
        for step in json.loads(sys.argv[2]):
            out[step["name"]] = await call(step["tool"], step.get("args", {}))
    print("RESULT " + json.dumps(out))

asyncio.run(main())
"""


def _run_mcp_probe(python: pathlib.Path, url: str, token: str, steps: list[dict]) -> dict:
    import subprocess
    env = {**os.environ, "LABCONTROL_URL": url, "LABCONTROL_TOKEN": token}
    done = subprocess.run(
        [str(python), "-c", MCP_PROBE, str(ROOT / "labcontrol_mcp" / "server.py"), json.dumps(steps)],
        capture_output=True, text=True, env=env, timeout=180)
    line = next((l for l in done.stdout.splitlines() if l.startswith("RESULT ")), None)
    if line is None:
        raise RuntimeError(f"MCP-Probe ohne Ergebnis:\n{done.stdout[-800:]}\n{done.stderr[-800:]}")
    return json.loads(line[len("RESULT "):])


# ----------------------------------------------------------------- app ----

def section_app() -> None:
    """Die komplette App im Simulationsmodus, gepruft ueber echte HTTP-Anfragen.

    Die Pruefungen laufen in einem ARBEITS-Thread, waehrend der GUI-Thread die
    Qt-Ereignisschleife bedient. Das ist noetig: ein schreibender Request geht
    Server-Thread -> GUI-Thread -> Worker-Thread und zurueck (siehe
    share_remote.py). Blockierte der Test den GUI-Thread mit einer Anfrage,
    kaeme die Antwort nie an. Zugriffe auf GUI-Objekte gehen ueber gui().
    """
    head("app -- gesamte App im Simulationsmodus gegen echte Anfragen")
    from PySide6.QtCore import QObject, QTimer, Signal, Slot
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    settings_mod, tmp = _temp_settings()
    import device_registry as registry_mod
    registry_mod.LABELS_PATH = tmp / "device_labels.json"

    port = _free_port()
    token = "pruef-token-123"
    settings_mod.SETTINGS_PATH.write_text(json.dumps({
        "simulation_mode": True, "share_enabled": True, "share_bind": "127.0.0.1",
        "share_port": port, "share_read_requires_token": False, "share_token": token,
        "share_devices": {
            "psu:SIM": {"read": True, "control": True},
            "load:SIM": {"read": True, "control": True},
            "hil:SIM": {"read": True, "control": True},
            # von Hand editiert: CAN darf nie steuerbar werden
            "can:mock:SIM": {"read": True, "control": True},
        },
    }), encoding="utf-8")

    from main_window import MainWindow

    settings = settings_mod.Settings()
    window = MainWindow(settings)
    window.show()

    class _GuiRunner(QObject):
        request = Signal(object)

        @Slot(object)
        def run(self, fn):
            fn()

    runner = _GuiRunner()
    runner.request.connect(runner.run)   # Queued: Sender ist der Arbeits-Thread

    def gui(fn):
        """Fuehrt fn im GUI-Thread aus und gibt das Ergebnis zurueck."""
        done, box = threading.Event(), {}

        def wrapper():
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc
            done.set()

        runner.request.emit(wrapper)
        if not done.wait(15):
            raise TimeoutError("GUI-Thread antwortet nicht")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    base = f"http://127.0.0.1:{port}"

    def call(method, path, payload=None, auth=True, timeout=20):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        if auth:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] == b"{" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw[:1] == b"{" else raw)

    get = lambda path, **kw: call("GET", path, **kw)
    post = lambda path, payload=None, **kw: call("POST", path, payload, **kw)

    def wait_for(predicate, seconds=5.0):
        end_at = time.monotonic() + seconds
        while time.monotonic() < end_at:
            if predicate():
                return True
            time.sleep(0.1)
        return False

    def field(tile_id, key):
        _, tile = get(f"/api/v1/tiles/{tile_id}")
        return next((f.get("value", f.get("text")) for f in tile["fields"] if f["key"] == key), None)

    # Protokoll der Fernsteuerung mitschneiden
    import logging

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__(logging.INFO)
            self.lines: list[str] = []

        def emit(self, record):
            self.lines.append(record.getMessage())

    capture = _Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.INFO)

    def checks():
        # ---------------- Lesen (unveraendert aus Phase 1) ----------------
        tiles = get("/api/v1/tiles", auth=False)[1]["tiles"]
        ids = sorted(t["id"] for t in tiles)
        check("Simulationskacheln freigegeben (auch HIL)",
              {"psu:SIM", "load:SIM", "hil:SIM", "can:mock:SIM"} <= set(ids), str(ids))
        check("Messwerte kommen an", wait_for(lambda: field("psu:SIM", "voltage") is not None))
        status = get("/api/v1/status", auth=False)[1]
        check("Simulationsmodus und Version gemeldet",
              status["simulation"] is True and status["version"] != "")
        body = get("/display?tile=psu:SIM&refresh=2", auth=False)[1]
        check("Display-Seite ausgeliefert", b"refresh" in body, f"{len(body)} Byte")

        # ---------------- Sperrzustand (Lesen bleibt) -----------------------
        gui(lambda: window._safety._trip("psu:SIM", "Pruefausloesung"))
        check("Sicherheitsabschaltung sperrt",
              get("/api/v1/status", auth=False)[1]["lock"] == "safety_tripped")
        check("Lesen bleibt trotz Trip erlaubt", get("/api/v1/tiles/psu:SIM", auth=False)[0] == 200)
        gui(window._safety.acknowledge)
        check("Quittieren gibt frei", get("/api/v1/status", auth=False)[1]["lock"] == "free")
        gui(lambda: window._live_state.set_test_running(True))
        check("Testlauf sperrt", get("/api/v1/status", auth=False)[1]["lock"] == "test_running")
        gui(lambda: window._live_state.set_test_running(False))

        # ---------------- Freigabe/Token live (Lesen) ---------------------------
        gui(lambda: settings.set_share_device("psu:SIM", False, False))
        check("Freigabe live entziehbar", get("/api/v1/tiles/psu:SIM", auth=False)[0] == 404)
        gui(lambda: settings.set_share_device("psu:SIM", True, True))
        check("Freigabe live wieder erteilbar", get("/api/v1/tiles/psu:SIM", auth=False)[0] == 200)
        gui(lambda: settings.set_share_read_requires_token(True))
        check("Lese-Token live wirksam",
              get("/api/v1/tiles", auth=False)[0] == 401 and get("/api/v1/tiles")[0] == 200)
        gui(lambda: settings.set_share_read_requires_token(False))

        # ---------------- Netzwerk-Reiter -------------------------------------------
        tabs = window.settings_tab._subtabs
        check("Netzwerk-Reiter vorhanden", tabs.count() == 5 and tabs.tabText(4) == "Netzwerk")
        table = window.settings_tab._share_table
        check("Geraetetabelle gefuellt (inkl. HIL)",
              {"psu:SIM", "load:SIM", "hil:SIM", "can:mock:SIM"} <= set(table._rows), str(sorted(table._rows)))
        check("Steuern: bedienbar fuer Netzteil/HIL, gesperrt fuer CAN",
              table._box("psu:SIM", table.COL_CONTROL).isEnabled()
              and table._box("hil:SIM", table.COL_CONTROL).isEnabled()
              and not table._box("can:mock:SIM", table.COL_CONTROL).isEnabled())
        check("Adresse zum Abtippen angezeigt",
              "/display?tile=" in window.settings_tab._share_url_edit.text())

        # ==================== FERNSTEUERUNG =====================================
        _, tile = get("/api/v1/tiles/psu:SIM")
        check("Kachel: Aktionen gelistet, aber wegen Hauptschalter noch nicht verfuegbar",
              len(tile["actions"]) == 4 and tile["control"] is True
              and tile["control_available"] is False
              and tile["control_blocked"] == "remote_control_inactive")
        check("CAN-Kachel zeigt trotz Handeintrag keine Aktionen",
              get("/api/v1/tiles/can:mock:SIM")[1]["actions"] == [])
        check("Freigabe wurde beim Laden normalisiert (CAN control=False)",
              gui(lambda: settings.share_config["devices"]["can:mock:SIM"]["control"]) is False)

        act = "/api/v1/tiles/psu:SIM/actions"
        check("Hauptschalter aus -> 403",
              post(act, {"action": "PSU_VOLT", "value": 5.0})[0] == 403)
        check("ohne Token -> 401", post(act, {"action": "PSU_VOLT", "value": 5.0}, auth=False)[0] == 401)
        query_status, _ = call("POST", act + f"?token={token}", {"action": "PSU_VOLT", "value": 5.0}, auth=False)
        check("Token in der URL -> 401", query_status == 401)

        # Hauptschalter wie ein Nutzer: ueber den Haken im Netzwerk-Reiter
        gui(lambda: window.settings_tab._share_control_checkbox.setChecked(True))
        st = get("/api/v1/status", auth=False)[1]["remote_control"]
        check("Hauptschalter an: Status meldet Restzeit",
              st["active"] is True and 3500 < st["remaining_s"] <= 3600, str(st))
        check("Statusleiste zeigt die Fernsteuerung",
              gui(lambda: window._remote_status_label.isVisible()
                  and "Fernsteuerung" in window._remote_status_label.text()))
        check("Kachel: jetzt verfuegbar",
              get("/api/v1/tiles/psu:SIM")[1]["control_available"] is True)

        # -- Netzteil ------------------------------------------------------------
        code, resp = post(act, {"action": "PSU_VOLT", "value": 5.0})
        check("PSU_VOLT 5 V -> 200", code == 200 and resp["ok"] is True, f"{code} {resp}")
        check("Messwert folgt dem Sollwert (Mock)", wait_for(lambda: field("psu:SIM", "voltage") == 5.0))
        code, resp = post(act, {"action": "PSU_OUT_ON"})
        check("PSU_OUT_ON -> 200", code == 200, f"{code} {resp}")
        check("Strom auf mindestens 0,1 A gehoben", wait_for(lambda: (field("psu:SIM", "current") or 0) >= 0.1))
        check("Control-Tab zeigt das Netzteil als EIN",
              wait_for(lambda: gui(lambda: window.control_tab._sections["psu:SIM"]._output_on is True)))
        code, resp = post(act, {"action": "PSU_OUT_OFF"})
        check("PSU_OUT_OFF -> 200, Strom 0 A",
              code == 200 and wait_for(lambda: field("psu:SIM", "current") == 0.0))
        check("Control-Tab zeigt das Netzteil als AUS",
              wait_for(lambda: gui(lambda: window.control_tab._sections["psu:SIM"]._output_on is False)))
        code, resp = post(act, {"action": "PSU_CURR", "value": 0.5})
        check("PSU_CURR > 0 schaltet wieder ein (Control-Tab folgt)",
              code == 200 and wait_for(lambda: gui(lambda: window.control_tab._sections["psu:SIM"]._output_on is True)))

        # -- Last --------------------------------------------------------------------
        load = "/api/v1/tiles/load:SIM/actions"
        code, _ = post(load, {"action": "CURR", "value": 2.5})
        check("Last CURR 2,5 A -> 200", code == 200)
        check("Last: Messwert und Modus CC",
              wait_for(lambda: field("load:SIM", "current") == 2.5 and field("load:SIM", "mode") == "CC"))
        code, _ = post(load, {"action": "OUT_ON"})
        check("Last OUT_ON -> 200", code == 200)
        check("Control-Tab folgt dem Lasteingang (Hardware-Rueckfrage)",
              wait_for(lambda: gui(lambda: window.control_tab._sections["load:SIM"]._input_on is True)))

        # -- microHIL -----------------------------------------------------------------
        hil = "/api/v1/tiles/hil:SIM/actions"
        code, resp = post(hil, {"action": "HIL_OUT_ON", "channel": 3})
        check("HIL_OUT_ON Kanal 3 -> 200", code == 200 and resp["channel"] == 3, f"{code} {resp}")
        check("HIL: Ausgang 3 im naechsten Poll gesetzt",
              wait_for(lambda: field("hil:SIM", "out3") == 1, 5.0))
        code, _ = post(hil, {"action": "HIL_OUT_OFF", "channel": 3})
        check("HIL_OUT_OFF -> Ausgang 3 wieder aus",
              code == 200 and wait_for(lambda: field("hil:SIM", "out3") == 0, 5.0))
        check("HIL_AOUT Kanal 2 -> 200", post(hil, {"action": "HIL_AOUT", "channel": 2, "value": 5000})[0] == 200)

        # -- Ablehnungen ueber die echte Kette ------------------------------------------
        cases = [
            ({"action": "GIBTESNICHT"}, 400, "unknown_action"),
            ({"action": "PSU_VOLT", "value": 999}, 400, "value_out_of_range"),
            ({"action": "PSU_VOLT", "value": 0.2}, 400, "value_out_of_range"),
            ({"action": "PSU_VOLT"}, 400, "value_required"),
        ]
        for payload, want_status, want_error in cases:
            code, resp = post(act, payload)
            check(f"{payload} -> {want_error}", code == want_status and resp["error"] == want_error,
                  f"{code} {resp}")
        check("HIL ohne Kanal -> 400",
              post(hil, {"action": "HIL_OUT_ON"})[1]["error"] == "channel_required")
        check("CAN nie steuerbar -> 403",
              post("/api/v1/tiles/can:mock:SIM/actions", {"action": "CAN_SEND"})[0] == 403)

        # -- Sicherheits-Grenzwert -------------------------------------------------------------
        gui(lambda: settings.set_safety_limit("psu:SIM", "max_voltage", True, 12.0))
        code, resp = post(act, {"action": "PSU_VOLT", "value": 20.0})
        check("Sollwert ueber aktivem Sicherheits-Grenzwert -> 400",
              code == 400 and resp["error"] == "exceeds_safety_limit" and resp["limit"] == 12.0, f"{code} {resp}")
        check("Sollwert unter dem Grenzwert erlaubt", post(act, {"action": "PSU_VOLT", "value": 11.0})[0] == 200)
        gui(lambda: settings.set_safety_limit("psu:SIM", "max_voltage", False, 12.0))

        # -- Geraet nicht im Worker ---------------------------------------------------------------
        def add_ghost():
            window._live_state.on_device_known("psu", "psu:GHOST", "Geist")
            window._live_state.on_psu_measurement("psu:GHOST", 1.0, 1.0, False)
            settings.set_share_device("psu:GHOST", True, True)
        gui(add_ghost)
        code, resp = post("/api/v1/tiles/psu:GHOST/actions", {"action": "PSU_VOLT", "value": 5.0})
        check("Geraet dem Worker unbekannt -> 502 mit Meldung des Geraets",
              code == 502 and resp["error"] == "device_error" and "nicht verbunden" in resp.get("message", ""),
              f"{code} {resp}")

        # -- Sperre in der Luecke zwischen Server-Thread und GUI-Thread ------------------------------
        # Der Schnappschuss soll "frei" melden, obwohl der Watchdog ausgeloest hat: dann
        # muss die zweite Pruefung im GUI-Thread (main_window._on_share_action) greifen.
        gui(lambda: window._safety._trip("psu:SIM", "Luecken-Test"))
        gui(lambda: window._live_state.on_safety_state_changed("armed"))
        code, resp = post(act, {"action": "PSU_VOLT", "value": 6.0})
        check("Zweite Pruefung im GUI-Thread faengt einen veralteten Schnappschuss ab",
              code == 409 and resp["error"] == "locked", f"{code} {resp}")
        check("... und das Kommando hat das Geraet nicht erreicht", field("psu:SIM", "voltage") != 6.0)

        # -- ALLE AUS geht immer ------------------------------------------------------------------------
        gui(lambda: window._live_state.on_safety_state_changed("tripped"))
        check("waehrend Trip: normale Aktion -> 409", post(act, {"action": "PSU_VOLT", "value": 6.0})[0] == 409)
        code, resp = post("/api/v1/all-off")
        check("ALLE AUS trotz Trip -> 200", code == 200 and resp["ok"] is True, f"{code} {resp}")
        check("ALLE AUS: Netzteil aus", wait_for(lambda: field("psu:SIM", "current") == 0.0))
        check("ALLE AUS: Lasteingang aus (Control-Tab)",
              wait_for(lambda: gui(lambda: window.control_tab._sections["load:SIM"]._input_on is False), 5.0))
        check("ALLE AUS braucht den Token", post("/api/v1/all-off", auth=False)[0] == 401)
        gui(window._safety.acknowledge)

        # -- Rate-Begrenzung; ALLE AUS bleibt erreichbar ---------------------------------------------------
        codes = [post(act, {"action": "PSU_VOLT", "value": 5.0})[0] for _ in range(60)]
        check("schnelle Schreibserie wird gedrosselt (429)", 429 in codes,
              str({c: codes.count(c) for c in set(codes)}))
        check("... ALLE AUS bleibt trotzdem frei", post("/api/v1/all-off")[0] == 200)

        # -- Protokoll -------------------------------------------------------------------------------------------
        joined = "\n".join(capture.lines)
        check("Aktionen stehen im Protokoll (mit Aufrufer)",
              "Fernsteuerung von 127.0.0.1: psu:SIM PSU_VOLT" in joined)
        check("Abweisungen stehen im Protokoll", "Fernsteuerung abgewiesen von 127.0.0.1" in joined)
        check("ALLE AUS steht im Protokoll", "ALLE AUS" in joined)
        check("Hauptschalter im Protokoll", "Fernsteuerung freigegeben fuer 60 min" in joined)

        # -- MCP-Server (echter stdio-Kindprozess) ----------------------------------------------------------
        mcp_python = ROOT / "labcontrol_mcp" / ".venv" / "Scripts" / "python.exe"
        if not mcp_python.exists():
            print("  ...  MCP-Stufe uebersprungen: labcontrol_mcp/.venv fehlt "
                  "(python -m venv labcontrol_mcp/.venv && pip install -r labcontrol_mcp/requirements.txt)")
        else:
            time.sleep(3.0)   # Token-Eimer der Drosselung wieder fuellen
            steps = [
                {"name": "status", "tool": "get_status"},
                {"name": "devices", "tool": "list_devices"},
                {"name": "read", "tool": "read_device", "args": {"device_id": "psu:SIM"}},
                {"name": "set", "tool": "control_device",
                 "args": {"device_id": "psu:SIM", "action": "PSU_VOLT", "value": 7.0}},
                {"name": "bad_value", "tool": "control_device",
                 "args": {"device_id": "psu:SIM", "action": "PSU_VOLT", "value": 500}},
                {"name": "can", "tool": "control_device",
                 "args": {"device_id": "can:mock:SIM", "action": "CAN_SEND"}},
                {"name": "relay", "tool": "control_device",
                 "args": {"device_id": "hil:SIM", "action": "HIL_RELAY_ON", "channel": 2}},
                {"name": "unknown", "tool": "read_device", "args": {"device_id": "psu:NICHTDA"}},
                {"name": "alloff", "tool": "all_off"},
            ]
            out = _run_mcp_probe(mcp_python, base, token, steps)
            check("MCP: fuenf Werkzeuge angeboten",
                  out["tools"] == ["all_off", "control_device", "get_status", "list_devices", "read_device"],
                  str(out["tools"]))
            check("MCP: Sicherheitshinweise fuer den Assistenten mitgeliefert",
                  "Hardware" in out["instructions"] and "all_off" in out["instructions"])
            check("MCP get_status: Fernsteuerung aktiv",
                  not out["status"]["error"] and out["status"]["data"]["remote_control"]["active"] is True)
            devices = {t["id"]: t for t in out["devices"]["data"]["tiles"]}
            check("MCP list_devices: Netzteil mit Aktionen und Verfuegbarkeit",
                  devices["psu:SIM"]["control_available"] is True and len(devices["psu:SIM"]["actions"]) == 4)
            check("MCP read_device liefert Messwerte", not out["read"]["error"]
                  and any("value" in f for f in out["read"]["data"]["fields"]))
            check("MCP control_device: ok vom Geraet bestaetigt",
                  not out["set"]["error"] and out["set"]["data"]["ok"] is True and out["set"]["data"]["value"] == 7.0,
                  str(out["set"]))
            check("MCP: Wirkung am Messwert sichtbar", wait_for(lambda: field("psu:SIM", "voltage") == 7.0))
            check("MCP: Wert ausser Bereich -> Werkzeugfehler mit Hinweis",
                  out["bad_value"]["error"] and "value_out_of_range" in out["bad_value"]["text"]
                  and "Hinweis" in out["bad_value"]["text"], out["bad_value"]["text"][:120])
            check("MCP: CAN nie steuerbar -> Werkzeugfehler",
                  out["can"]["error"] and "control_not_permitted" in out["can"]["text"])
            check("MCP: HIL-Relais mit Kanal", not out["relay"]["error"] and out["relay"]["data"]["channel"] == 2)
            check("MCP: unbekanntes Geraet -> Werkzeugfehler",
                  out["unknown"]["error"] and "unknown_tile" in out["unknown"]["text"])
            check("MCP all_off ok", not out["alloff"]["error"] and out["alloff"]["data"]["ok"] is True)
            check("MCP-Aufrufer steht im Protokoll",
                  "Fernsteuerung von 127.0.0.1: psu:SIM PSU_VOLT wert=7" in "\n".join(capture.lines))

            wrong = _run_mcp_probe(mcp_python, base, "falscher-token", [
                {"name": "set", "tool": "control_device",
                 "args": {"device_id": "psu:SIM", "action": "PSU_VOLT", "value": 8.0}}])
            check("MCP: falscher Token -> Werkzeugfehler mit Hinweis",
                  wrong["set"]["error"] and "unauthorized" in wrong["set"]["text"]
                  and "Token" in wrong["set"]["text"])
            gone = _run_mcp_probe(mcp_python, f"http://127.0.0.1:{_free_port()}", token,
                                  [{"name": "status", "tool": "get_status"}])
            check("MCP: LabControl nicht erreichbar -> verstaendlicher Werkzeugfehler",
                  gone["status"]["error"] and "nicht erreichbar" in gone["status"]["text"])

        # -- Ablauf des Zeitfensters ---------------------------------------------------------------------------------
        gui(lambda: window._live_state.set_remote_control(True, 1.5))
        check("kurzes Zeitfenster: Aktion zunaechst erlaubt", post(act, {"action": "PSU_VOLT", "value": 5.0})[0] in (200, 429))
        time.sleep(2.2)
        check("nach Ablauf -> 403 (ohne dass die GUI etwas tun musste)",
              post(act, {"action": "PSU_VOLT", "value": 5.0})[0] == 403)
        check("Haken im Reiter springt zurueck, Statusleiste versteckt",
              wait_for(lambda: gui(lambda: not window.settings_tab._share_control_checkbox.isChecked()
                                   and not window._remote_status_label.isVisible())))

        # -- Von Hand ausschalten -----------------------------------------------------------------------------------------
        gui(lambda: window.settings_tab._share_control_checkbox.setChecked(True))
        time.sleep(1.2)   # Drosselung abklingen lassen
        check("wieder an: Aktion geht", post(act, {"action": "PSU_VOLT", "value": 5.0})[0] == 200)
        gui(lambda: window.settings_tab._share_control_checkbox.setChecked(False))
        check("von Hand aus: Aktion -> 403", post(act, {"action": "PSU_VOLT", "value": 5.0})[0] == 403)

        # -- Steuern-Haken entziehen -----------------------------------------------------------------------------------------------
        gui(lambda: window.settings_tab._share_control_checkbox.setChecked(True))
        gui(lambda: table._box("psu:SIM", table.COL_CONTROL).setChecked(False))
        time.sleep(1.0)
        check("Steuern in der Tabelle abgewaehlt: Aktion -> 403, Lesen bleibt",
              post(act, {"action": "PSU_VOLT", "value": 5.0})[0] == 403
              and get("/api/v1/tiles/psu:SIM", auth=False)[0] == 200)
        gui(lambda: window.settings_tab._share_control_checkbox.setChecked(False))

        # -- Token: wird bei aktivierter Freigabe automatisch erzeugt ---------------------------------
        gui(lambda: settings.set_share_token(""))
        fresh = gui(lambda: settings.share_config["token"])
        check("leerer Token wird bei aktivierter Freigabe sofort neu erzeugt",
              bool(fresh) and fresh != token and len(fresh) >= 30, fresh[:6] + "..." if fresh else "")

    result = {"error": None}

    def worker_thread():
        try:
            checks()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            result["error"] = exc
            check("Pruefungen laufen ohne Ausnahme durch", False, repr(exc))

    def finish():
        try:
            window.close()
            gone = False
            try:
                urllib.request.urlopen(f"{base}/api/v1/status", timeout=2)
            except Exception:
                gone = True
            check("Server stoppt beim Schliessen", gone)
        finally:
            logging.getLogger().removeHandler(capture)
            app.exit(0)

    def start():
        thread = threading.Thread(target=worker_thread, daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                QTimer.singleShot(200, poll)
            else:
                finish()
        poll()

    # Zeit lassen, damit der DeviceWorker mindestens einen Poll-Zyklus
    # abgeschlossen hat -- bei angeschlossener echter Hardware dauert ein
    # Zyklus deutlich laenger als im reinen Simulationsbetrieb.
    QTimer.singleShot(4000, start)
    app.exec()


# ------------------------------------------------------------ windowed ----

def section_windowed() -> None:
    """Ahmt die --windowed-Bedingung nach: in der gebauten .exe ist
    sys.stderr None, und BaseHTTPRequestHandler.log_message()/log_error()
    schreiben dorthin bedingungslos. Ohne die Overrides in share_server.py
    wuerfe JEDE Anfrage in der .exe einen AttributeError -- im Dev-Betrieb
    voellig unauffaellig. Das ist der teuerste Fehler, den dieses Skript
    verhindern kann."""
    head("windowed -- Anfragen ohne sys.stderr (wie in der gebauten .exe)")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from live_state import LiveState
    from share_server import ShareServer

    port = _free_port()
    state = LiveState()
    state.on_device_known("psu", "psu:SIM", "Netzteil 1")
    state.on_psu_measurement("psu:SIM", 12.0, 0.5, False)
    cfg = {"enabled": True, "bind": "127.0.0.1", "port": port, "token": "",
           "read_requires_token": False, "access_log": False,
           "devices": {"psu:SIM": {"read": True, "control": False}}}
    state.set_share_config(cfg)
    server = ShareServer(state)
    server.apply(cfg)

    real_stderr, sys.stderr = sys.stderr, None
    ok, detail = True, ""
    try:
        for path in ("/api/v1/status", "/api/v1/tiles", "/api/v1/tiles/psu:SIM",
                     "/display?tile=psu:SIM", "/display?tile=psu:SIM&fmt=text",
                     "/", "/gibtesnicht", "/api/v1/tiles/nicht:da"):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                    code, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                code, payload = e.code, e.read()
            if not payload or code not in (200, 404):
                ok, detail = False, f"{path} -> HTTP {code}"
                break
        # Kaputte Anfragezeile laeuft durch log_error() statt log_message()
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        raw.sendall(b"NICHTEINEMETHODE / HTTP/1.0\r\n\r\n")
        raw.recv(64)
        raw.close()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    finally:
        sys.stderr = real_stderr
    server.stop()
    check("Anfragen ohne sys.stderr beantwortet", ok, detail)


SECTIONS = {
    "pure": section_pure,
    "remote": section_remote,
    "server": section_server,
    "settings": section_settings,
    "i18n": section_i18n,
    "windowed": section_windowed,
    "app": section_app,
}


def main(argv: list[str]) -> int:
    wanted = argv or list(SECTIONS)
    unknown = [name for name in wanted if name not in SECTIONS]
    if unknown:
        print(f"Unbekannter Abschnitt: {', '.join(unknown)}")
        print(f"Verfuegbar: {', '.join(SECTIONS)}")
        return 2
    # Feste Reihenfolge wie in SECTIONS: "pure" muss vor allem laufen, was Qt
    # laedt, und "app" zuletzt, da es die komplette App startet.
    order = list(SECTIONS)
    for name in sorted(wanted, key=order.index):
        SECTIONS[name]()
    head("ERGEBNIS")
    if FAILURES:
        print(f"{len(FAILURES)} FEHLGESCHLAGEN:")
        for name in FAILURES:
            print("  -", name)
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

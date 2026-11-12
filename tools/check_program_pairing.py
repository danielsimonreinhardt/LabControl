"""Prueft das Koppeln von Programmen (PI-41 c / PI-44 b, 0.57.0):

- tools/labcontrol_pair.py fragt an und zeigt einen Code; LabControl zeigt die Anfrage als "Programm ...",
  "Code gleich – koppeln" traegt das Programm ein (nur Fingerabdruck des Schluessels) und das Skript bekommt
  den Schluessel genau einmal
- mit dem Schluessel: Lesen auch ohne allgemeinen Token, Steuern ohne Hauptschalter (Freigabeliste gilt),
  Protokoll nennt das Programm; ohne Kopplung mit dem allgemeinen Token nur lesen
- Ablehnen -> Skript meldet es; Kopplung aufheben (Liste in den Einstellungen) -> Schluessel ungueltig
- der alte Hauptschalter ist aus der GUI verschwunden

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_program_pairing.py
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-program-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
TOKEN = "t" * 32


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
(DATA / "settings.json").write_text(json.dumps({
    "simulation_mode": True, "share_enabled": True, "share_token": TOKEN, "share_port": PORT,
    "share_local_bypass": False,
    "share_devices": {"psu:SIM": {"read": True, "control": True}, "load:SIM": {"read": True, "control": False}},
}), encoding="utf-8")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def call(method: str, path: str, body: dict | None = None, key: str = "") -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


LOG_LINES: list[str] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        LOG_LINES.append(record.getMessage())


def main() -> int:
    app = QApplication.instance() or QApplication([])
    logging.getLogger().addHandler(_Capture())
    logging.getLogger().setLevel(logging.INFO)
    import no_hardware
    no_hardware.block()
    import labcontrol_pair
    from main_window import MainWindow
    from settings import Settings

    def spin(seconds: float, until=None) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.02)
        return bool(until()) if until is not None else False

    w = MainWindow(Settings())
    w.show()
    check("Freigabe laeuft, Simulation verbunden", spin(8.0, lambda: w._share_server.is_running()
                                                         and w.control_tab.section("psu:SIM") is not None))
    print("GUI")
    st = w.settings_tab
    check("kein Hauptschalter/Zeitfenster mehr in den Einstellungen",
          not hasattr(st, "_share_control_checkbox") and not hasattr(st, "_share_control_timeout_spin")
          and not hasattr(w, "_remote_status_label"))
    check("Liste 'Gekoppelte Programme' leer", st._programs_title.text() == "Gekoppelte Programme"
          and not st._programs_empty.isHidden())

    print("ohne Kopplung")
    act = "/api/v1/tiles/psu:SIM/actions"
    check("allgemeiner Token: lesen ja", call("GET", "/api/v1/tiles", key=TOKEN)[0] == 200)
    code, resp = call("POST", act, {"action": "PSU_VOLT", "value": 5.0}, key=TOKEN)
    check("allgemeiner Token: steuern nein (fremder Aufrufer)", code == 403
          and resp.get("error") == "remote_control_inactive", f"{code} {resp}")

    def run_pair(result: dict) -> None:
        try:
            result["key"] = labcontrol_pair.pair(f"127.0.0.1:{PORT}", "Messskript")
        except Exception as exc:   # noqa: BLE001 -- Ergebnis fuer die Pruefung
            result["error"] = str(exc)

    print("Koppeln")
    result: dict = {}
    thread = threading.Thread(target=run_pair, args=(result,), daemon=True)
    thread.start()
    check("Bestaetigungsdialog erscheint als Programm-Anfrage",
          spin(6.0, lambda: w._pair_confirm is not None and w._pair_confirm.isVisible()))
    texts = [lbl.text() for lbl in w._pair_confirm.findChildren(QLabel)] if w._pair_confirm else []
    check("... mit Name und 'Programm'", any("Programm „Messskript“" in t for t in texts), str(texts))
    w._pair_confirm._decide(True)
    check("Skript bekommt den Schluessel", spin(8.0, lambda: not thread.is_alive()) and bool(result.get("key")),
          str(result))
    key = result.get("key", "")
    programs = w._settings.share_config["programs"]
    check("Programm eingetragen, nur mit Fingerabdruck",
          len(programs) == 1 and programs[0]["name"] == "Messskript" and programs[0]["ip"] == "127.0.0.1"
          and key not in json.dumps(w._settings._data), str(programs))
    check("Liste in den Einstellungen zeigt es", spin(1.0, lambda: st._programs_empty.isHidden()
                                                        and st._programs_box.count() == 1))
    code, again = call("GET", "/api/v1/pair/x")
    check("Schluessel nur einmal (Anfrage abgeschlossen)", code == 404)

    print("mit Schluessel")
    code, status = call("GET", "/api/v1/status", key=key)
    check("Status: Programm erkannt, Steuern wirksam", code == 200 and status["remote_control"]["program"] == "Messskript"
          and status["remote_control"]["effective"] is True, str(status.get("remote_control")))
    # Steuerbefehle warten auf den GUI-Thread -> Aufruf aus einem eigenen Thread, hier die Ereignisschleife drehen
    answer: dict = {}
    worker = threading.Thread(target=lambda: answer.update(
        r=call("POST", act, {"action": "PSU_VOLT", "value": 5.0}, key=key)), daemon=True)
    worker.start()
    spin(10.0, lambda: not worker.is_alive())
    code, resp = answer.get("r", (0, {}))
    check("Steuern ohne Hauptschalter -> 200", code == 200 and resp.get("ok") is True, f"{code} {resp}")
    check("Protokoll nennt das Programm", any("Programm:Messskript" in line and "PSU_VOLT" in line
                                              for line in LOG_LINES), str(LOG_LINES[-3:]))
    code, _ = call("POST", "/api/v1/tiles/load:SIM/actions", {"action": "OUT_ON"}, key=key)
    check("Freigabeliste gilt (Last ohne 'Steuern' -> 403)", code == 403)
    code, _ = call("GET", "/api/v1/tiles/fg:SIM", key=key)
    check("Freigabeliste gilt (FG nicht freigegeben -> 404)", code == 404)
    check("Schluessel nicht als ?token= (nur Header)",
          call("GET", f"/api/v1/tiles?token={key}")[0] == 401)

    print("Ablehnen")
    result2: dict = {}
    w._live_state.pairing._blocked_until = 0.0
    thread = threading.Thread(target=lambda: result2.update(
        error=_pair_error(labcontrol_pair, f"127.0.0.1:{PORT}", "Fremd")), daemon=True)
    thread.start()
    check("zweite Anfrage erscheint", spin(6.0, lambda: w._pair_confirm is not None and w._pair_confirm.isVisible()))
    if w._pair_confirm is not None:
        w._pair_confirm._decide(False)
    check("abgelehnt: Skript meldet es, nichts eingetragen",
          spin(8.0, lambda: not thread.is_alive()) and "abgelehnt" in str(result2.get("error"))
          and len(w._settings.share_config["programs"]) == 1, str(result2))

    print("Aufheben")
    st.program_remove_requested.emit(programs[0]["key_sha256"])
    spin(0.5)
    check("Kopplung aufgehoben: Liste leer", not w._settings.share_config["programs"] and not st._programs_empty.isHidden())
    code, _ = call("POST", act, {"action": "PSU_VOLT", "value": 6.0}, key=key)
    check("alter Schluessel ungueltig -> 401", code == 401)

    w.close()
    app.processEvents()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


def _pair_error(module, address: str, name: str) -> str:
    try:
        module.pair(address, name)
    except Exception as exc:   # noqa: BLE001
        return str(exc)
    return ""


if __name__ == "__main__":
    sys.exit(main())

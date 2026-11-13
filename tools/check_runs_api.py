"""Prueft 0.59.0: Testlaeufe ueber REST (/api/v1/runs, /api/v1/testcases) und die Kommandozeile ueber die
laufende Instanz (FP-2 c).

- api     -- share_api mit Stub: Routen, Token, Steuerrecht, "path" nur lokal, Sperre, Dateien erst nach Ende
- window  -- echtes Hauptfenster mit Freigabe: Start per HTTP, Stand, Ergebnisdateien, unbekannter Ablauf,
             Pfad-Ausbruch, ungespeicherter Editor, fehlendes Geraet, Stopp
- cli     -- `main.py run` als Kindprozess, waehrend "die Instanz" (dieser Prozess) die Sperre haelt

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_runs_api.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-runs-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN = "runs-check-token"


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
    print("api -- share_api mit Stub")
    import share_api
    from run_registry import RunRegistry

    class State:
        def __init__(self):
            self.runs = RunRegistry()
            self.list_testcases = lambda: ["a.json", "sub/b.json"]
            self.lock = "free"
            self.bypass = True
            self.remote = False   # steht fuer einen fernen Aufrufer mit Steuerrecht (gekoppeltes Programm)

        def snapshot(self):
            return {"devices": {}, "share": {"token": TOKEN, "read_requires_token": True, "devices": {},
                                             "local_bypass": self.bypass}, "lock": self.lock,
                    "remote": {"active": self.remote}}

    class Executor:
        def __init__(self):
            self.calls = []

        def submit_run(self, command, payload, client):
            self.calls.append((command, payload))
            if command == "start":
                return share_api.RemoteResult("ok", message=state.runs.begin("a", client, 2))
            return share_api.RemoteResult("ok")

    state, executor = State(), Executor()

    def call(method, path, body=None, local=True, token=TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = share_api.dispatch(method, path, headers.get, state, {"host": "pc"},
                                      body=json.dumps(body).encode() if body is not None else b"",
                                      executor=executor, client="127.0.0.1", local=local)
        try:
            payload = json.loads(response.body or b"{}")
        except ValueError:
            payload = {"_raw": response.body}
        return response.status, payload

    status, body = call("GET", "/api/v1/testcases", token="")
    check("Ablaeufe lesen ohne Token -> 401", status == 401)
    status, body = call("GET", "/api/v1/testcases")
    check("Ablaeufe mit Token", status == 200 and body.get("testcases") == ["a.json", "sub/b.json"], str(body))
    status, body = call("POST", "/api/v1/runs", {"testcase": "a.json"}, token="")
    check("Start ohne Token -> 401", status == 401)
    state.bypass = False
    status, body = call("POST", "/api/v1/runs", {"testcase": "a.json"})
    check("Start ohne Steuerrecht -> 403", status == 403 and body.get("error") == "remote_control_inactive")
    state.bypass = True
    state.lock = "test_running"
    status, body = call("POST", "/api/v1/runs", {"testcase": "a.json"})
    check("Start bei laufendem Ablauf -> 409 locked", status == 409 and body.get("error") == "locked")
    state.lock = "free"
    state.remote = True
    status, body = call("POST", "/api/v1/runs", {"path": "/tmp/x.json"}, local=False)
    check("'path' von einem anderen Rechner -> 403", status == 403 and body.get("error") == "path_only_local",
          f"{status} {body}")
    state.remote = False
    status, body = call("POST", "/api/v1/runs", {"testcase": "a.json", "vars": {"n": "x"}})
    check("Variable ohne Zahl -> 400", status == 400 and body.get("error") == "invalid_vars")
    status, body = call("POST", "/api/v1/runs", {"testcase": "a.json", "vars": {"n": 3}})
    run_id = body.get("run", "")
    check("Start -> 202 mit Lauf-ID", status == 202 and run_id, str(body))
    check("Variablen als Zahl weitergereicht", executor.calls[-1][1]["vars"] == {"n": 3.0})
    status, body = call("GET", f"/api/v1/runs/{run_id}?events=0")
    check("Stand: laeuft", status == 200 and body.get("running") is True and body.get("events") == [], str(body))
    status, body = call("GET", f"/api/v1/runs/{run_id}/result")
    check("Ergebnis vor dem Ende -> 409 not_ready", status == 409 and body.get("error") == "not_ready")
    result_file = DATA / "api_result.json"
    result_file.write_text('{"outcome": "passed"}', encoding="utf-8")
    state.runs.step_result(run_id, 0, True, 1.5)
    state.runs.finish(run_id, "passed", 0, {"result": str(result_file)})
    status, body = call("GET", f"/api/v1/runs/{run_id}?events=0")
    check("Stand nach Ende: exit 0, Meldungen", body.get("running") is False and body.get("exit_code") == 0
          and [e["kind"] for e in body.get("events", [])] == ["result", "end"], str(body))
    status, body = call("GET", f"/api/v1/runs/{run_id}/result")
    check("Ergebnisdatei aus dem Register", status == 200 and body.get("outcome") == "passed")
    status, body = call("GET", f"/api/v1/runs/{run_id}/report")
    check("nicht geschriebene Datei -> 409", status == 409)
    status, body = call("GET", "/api/v1/runs/gibtsnicht")
    check("unbekannter Lauf -> 404", status == 404)
    status, body = call("POST", "/api/v1/runs/gibtsnicht/stop", {})
    check("Stopp unbekannter Lauf -> 404", status == 404)
    status, body = call("GET", "/api/v1/runs/x/../../etc")
    check("Pfad-Ausbruch in der Route -> kein Treffer", status == 404)


def write_settings(port: int) -> None:
    (DATA / "settings.json").write_text(json.dumps({
        "simulation_mode": True, "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port,
        "share_token": TOKEN, "share_local_bypass": True,
    }), encoding="utf-8")


def http(method: str, port: int, path: str, body=None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, {"_raw": raw.decode("utf-8", "replace")}


class Async:
    """HTTP-Anfrage im Hintergrund, waehrend der Test die Qt-Ereignisschleife dreht."""

    def __init__(self, *args, **kwargs):
        import threading
        self.result = None
        self._thread = threading.Thread(target=self._run, args=args, kwargs=kwargs, daemon=True)
        self._thread.start()

    def _run(self, *args, **kwargs):
        self.result = http(*args, **kwargs)


def ask(app, *args, **kwargs):
    call = Async(*args, **kwargs)
    spin(app, 25.0, lambda: call.result is not None)
    return call.result or (0, {})


def section_window() -> tuple:
    print("window -- Hauptfenster mit Freigabe")
    import no_hardware
    no_hardware.block()
    import device_worker as dw
    import testcase_model as tm
    from PySide6.QtWidgets import QApplication
    import data_store

    app = QApplication.instance() or QApplication([])
    port = free_port()
    write_settings(port)
    from main_window import MainWindow
    from settings import Settings
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    spin(app, 3.0, lambda: dw.SIM_PSU_ID in window._online_devices.get("psu", set()))
    folder = data_store.target_dir("testcases")
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    tm.save_steps([
        tm.TestStep(**psu, action="PSU_VOLT", value=4.0, duration=0.4, check_enabled=True, check_field="voltage",
                    check_min=0.0, check_max=100.0, requirements=["SSR-9"]),
        tm.TestStep(step_type="set_var", var_name="n", value=1.0),
        tm.TestStep(**psu, action="PSU_OUT_OFF"),
    ], folder / "kurz.json")
    (folder / "unter").mkdir(exist_ok=True)
    tm.save_steps([tm.TestStep(**psu, action="PSU_VOLT", value=3.0, duration=30.0)], folder / "unter" / "lang.json")
    tm.save_steps([tm.TestStep(device_kind="psu", device_id="psu:GIBTSNICHT", action="PSU_VOLT", value=2.0)],
                  folder / "fehlt.json")

    status, body = ask(app, "GET", port, "/testcases")
    check("GET /testcases nennt Unterordner", status == 200 and "unter/lang.json" in body.get("testcases", []),
          str(body))
    status, body = ask(app, "POST", port, "/runs", {"testcase": "kurz", "vars": {"x": 2}})
    run_id = body.get("run", "")
    check("Start per HTTP -> 202", status == 202 and run_id, f"{status} {body}")
    check("Editor zeigt den Ablauf", window.testcase_tab.current_path() == folder / "kurz.json")
    done = spin(app, 20.0, lambda: (window._live_state.runs.get(run_id) or {}).get("running") is False)
    entry = window._live_state.runs.get(run_id, 0) or {}
    check("Lauf beendet: bestanden, Exit 0", done and entry.get("status") == "passed" and entry.get("exit_code") == 0,
          str(entry))
    check("Quelle = Aufrufer", entry.get("source", "").startswith("127.0.0.1"), entry.get("source", ""))
    status, result = ask(app, "GET", port, f"/runs/{run_id}/result")
    check("run-result.json ueber HTTP", status == 200 and result.get("checks", [{}])[0].get("requirements") == ["SSR-9"],
          str(result)[:200])
    status, junit = ask(app, "GET", port, f"/runs/{run_id}/junit")
    check("JUnit ueber HTTP", status == 200 and "<testsuite" in junit.get("_raw", ""))
    status, report = ask(app, "GET", port, f"/runs/{run_id}/report")
    check("Bericht ueber HTTP", status == 200 and "Testablauf-Report" in report.get("_raw", ""))
    status, body = ask(app, "GET", port, "/runs")
    check("Liste enthaelt den Lauf", status == 200 and body.get("runs", [{}])[0].get("id") == run_id)

    status, body = ask(app, "POST", port, "/runs", {"testcase": "gibtsnicht.json"})
    check("unbekannter Ablauf -> 404", status == 404 and body.get("error") == "unknown_testcase", str(body))
    status, body = ask(app, "POST", port, "/runs", {"testcase": "../settings.json"})
    check("Ausbruch aus dem Ordner -> 404", status == 404, f"{status} {body}")
    status, body = ask(app, "POST", port, "/runs", {"testcase": "fehlt.json"})
    check("Geraet fehlt -> 409 devices_missing", status == 409 and body.get("error") == "devices_missing", str(body))
    window.testcase_tab._insert_new_step(tm.TestStep(step_type="wait", duration=0.1))
    check("Editor gilt als geaendert", window.testcase_tab.is_modified())
    status, body = ask(app, "POST", port, "/runs", {"testcase": "kurz.json"})
    check("ungespeicherter Editor -> 409 editor_modified", status == 409 and body.get("error") == "editor_modified",
          str(body))
    window.testcase_tab._load_path(folder / "kurz.json")
    check("nach dem Laden nicht geaendert", not window.testcase_tab.is_modified())

    status, body = ask(app, "POST", port, "/runs", {"testcase": "unter/lang.json"})
    long_id = body.get("run", "")
    check("langer Lauf gestartet", status == 202 and long_id, f"{status} {body}")
    spin(app, 1.0)
    status, body = ask(app, "POST", port, "/runs", {"testcase": "kurz.json"})
    check("zweiter Start waehrend des Laufs -> 409", status == 409, f"{status} {body}")
    status, body = ask(app, "POST", port, f"/runs/{long_id}/stop", {})
    check("Stopp -> 200", status == 200, f"{status} {body}")
    spin(app, 5.0, lambda: (window._live_state.runs.get(long_id) or {}).get("running") is False)
    entry = window._live_state.runs.get(long_id) or {}
    check("gestoppt, Exit 2", entry.get("status") == "stopped" and entry.get("exit_code") == 2, str(entry))
    spin(app, 4.0)   # Sicherheitsabschaltung/ALLE AUS abklingen lassen
    return app, window, port, folder


def section_cli(app, window, port: int, folder: Path) -> None:
    print("cli -- Kommandozeile ueber die laufende Instanz")
    from instance_lock import InstanceLock

    lock = InstanceLock(DATA)
    check("Sperre wie die laufende Instanz geholt", lock.acquire())
    reasons: list[str] = []
    window._request_all_off.connect(reasons.append)
    out = DATA / "cli_out"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, str(ROOT / "lab_gui" / "main.py"), "run", str(folder / "kurz.json"),
                             "--json", str(out / "r.json"), "--junit", str(out / "r.xml"), "--var", "k=3"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    # 120 s: in der Komplett-Pruefung unter Last (Windows und WSL parallel) reichten 60 s nicht (13.11.2026)
    spin(app, 120.0, lambda: proc.poll() is not None)
    if proc.poll() is None:
        proc.kill()
    text = proc.stdout.read() if proc.stdout else ""
    check("ueber die Instanz -> Exit 0", proc.returncode == 0, f"{proc.returncode}: {text[-500:]}")
    check("meldet den Weg ueber die Instanz", "laufende Instanz" in text, text[-300:])
    try:
        result = json.loads((out / "r.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {}
    check("Ergebnisdatei geholt", result.get("outcome") == "passed", str(result)[:200])
    check("JUnit geholt", (out / "r.xml").exists())
    check("am Ende ALLE AUS nur hier (FP-4 a)", "cli: Lauf beendet" in reasons, str(reasons))
    count = len(reasons)
    status, body = ask(app, "POST", port, "/runs", {"testcase": "kurz.json"})
    spin(app, 20.0, lambda: (window._live_state.runs.get(body.get("run", "")) or {}).get("running") is False)
    check("REST-Lauf ohne all_off_at_end: kein ALLE AUS am Ende", "cli: Lauf beendet" not in reasons[count:],
          str(reasons[count:]))
    proc = subprocess.Popen([sys.executable, str(ROOT / "lab_gui" / "main.py"), "run", str(folder / "kurz.json"),
                             "--simulation"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    spin(app, 30.0, lambda: proc.poll() is not None)
    check("--simulation bei laufender Instanz -> Exit 3", proc.returncode == 3)
    lock.release()


def main() -> int:
    section_api()
    app, window, port, folder = section_window()
    section_cli(app, window, port, folder)
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

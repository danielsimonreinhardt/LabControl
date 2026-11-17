"""Prueft PI-72 a / PI-73 a (1.0.1): Oszilloskop scharf schalten und auf Erfassung warten.

- core    -- ScopeService allein (PicoScope-Mock, UNI-T-Simulator): arm kehrt zurueck, sobald scharf; zweites arm/
             acquire -> scope_armed; wait_capture vor dem Trigger -> "armed", nach dem Trigger -> Bericht; Frist ->
             trigger_timeout; release bricht ab; eigener Thread je Geraet (das andere Scope bleibt bedienbar);
             Testablauf-Sperre (Netzwerk/MCP gesperrt, Lauf darf, MCP-Erfassung wird abgebrochen); Haltezeit 60 s
- rest    -- share_api: POST /scopes/{id}/arm, POST /captures/{cid}/wait, GET /captures/{cid} vorher "armed",
             measure -> capture_pending, release -> cancelled, Freigabe "Messen" noetig
- mcp     -- echter MCP-Server (labcontrol_mcp/.venv) gegen einen ShareServer: arm, wait_capture ("armed", dann nach
             dem Schalten das Ergebnis), get_capture mit der Kennung, release_scope bricht ab
- model   -- Format 5 nur mit den neuen Schritten (Bausteine 4), aeltere Version lehnt ab, Parameter, Kurztexte,
             Pruefung mit Einheit des Kennwerts
- editor  -- Zeile anlegen, Parameter ueber den Dialog, steps()/Speichern/Laden, Ausfuehren-Ansicht, Baustein-Vorschau
- window  -- echtes Hauptfenster (Simulation), Lauf per HTTP: scharf -> naechster Schritt schaltet -> warten holt das
             Ergebnis (PicoScope und UNI-T), Bericht mit Kurve/CSV; kein Trigger -> FAIL mit Meldung; Stopp bricht ab
             und gibt frei; Ablehnungen (PICO_* am selben Scope, ohne Fenster)

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_scope_arm.py [abschnitt ...]
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
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
DATA = Path(tempfile.mkdtemp(prefix="lc-scope-arm-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN = "scope-arm-check-token"
PICO = "picoscope:SIM"
UNIT = "scpiscope:SIM"
GOOD = {"channels": [{"name": "A", "range_v": 5}], "timing": {"duration_s": 0.002, "pre_trigger_pct": 20},
        "trigger": {"source": "A", "level_v": 1.65, "edge": "rising", "timeout_s": 30},
        "measurements": ["vmax", "frequency"]}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if app is not None:
            app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return bool(until()) if until is not None else False


def quiet():
    """PicoScope-Mock: Kanal A ruhig (kein Trigger bei 1,65 V)."""
    from picoscope2000 import mock
    mock.MockPicoScope2000.signals = {"A": mock.Constant(0.2), "B": mock.Constant(0.0)}


def switch_on():
    """Das 'Schalten' im Ablauf: jetzt liegt ein Rechteck an (Trigger)."""
    from picoscope2000 import mock
    mock.MockPicoScope2000.signals = mock.default_signals()


def unit_quiet():
    from unit_mso1000hd import mock
    mock.SimulatedInstrument.signals = {**mock.default_signals(), "CH1": mock.Constant(0.0)}


def unit_switch_on():
    from unit_mso1000hd import mock
    mock.SimulatedInstrument.signals = mock.default_signals()


class SlowScope:
    """Ersatz-Adapter: scharf, Trigger erst auf fire, danach dauert das Lesen read_s (wie 250 kS ASCII am UNI-T)."""

    def __init__(self, read_s: float) -> None:
        self.read_s = read_s
        self.fire = threading.Event()
        self.closed = False

    def acquire(self, request, cancel=None, on_armed=None, on_triggered=None):
        import numpy as np
        from scope_api.base import Capture, ChannelData, ScopeError
        if on_armed:
            on_armed()
        while not self.fire.wait(0.02):
            if cancel is not None and cancel.is_set():
                raise ScopeError("cancelled", "Erfassung abgebrochen.")
        if on_triggered:
            on_triggered()
        time.sleep(self.read_s)
        t = np.arange(2000) * 1e-6
        name = request.channels[0].name
        volts = np.where(np.mod(t * 1000.0, 1.0) < 0.5, 3.3, 0.0)
        return Capture(scope_id="fake", model="Langsam", sample_interval_s=1e-6, trigger_index=0, triggered=True,
                       channels={name: ChannelData(name, 5.0, "DC", 1.0, volts)}, request=request)

    def close(self) -> None:
        self.closed = True


def inject(service, device_id: str, scope) -> None:
    """Ersatz-Adapter als offene Verbindung einsetzen (mit Waechter, wie beim echten Oeffnen)."""
    from unit_mso1000hd import guard as mso_guard
    service._executor_for(device_id).submit(lambda: None).result(timeout=10)
    assert mso_guard.guard_for(device_id).try_acquire(mso_guard.OWNER_SERVICE)
    with service._lock:
        service._open[device_id] = scope


def make_service():
    import scope_service
    from unit_mso1000hd.driver import SIM_RESOURCE, register_resource
    service = scope_service.ScopeService(Path(tempfile.mkdtemp(prefix="lc-scope-arm-cap-")))
    service.on_picoscope_connected(PICO, True)
    service.on_picoscope_state(PICO, "free", "2204A", "SIM-2204A-0001")
    register_resource(UNIT, SIM_RESOURCE)
    service.on_scope_connected(UNIT, True)
    service.on_scope_state(UNIT, "free", "MSO1154HD-S", "SIM-MSO-0001")
    return service


# -- core ------------------------------------------------------------------------------------------------

def section_core(app) -> None:
    print("core -- ScopeService")
    import scope_service
    from scope_api.base import ScopeError

    service = make_service()
    states: list[tuple[str, str]] = []
    service.capture_state.connect(lambda cid, state: states.append((cid, state)))

    quiet()
    t0 = time.monotonic()
    status = service.arm(PICO, GOOD)
    took = time.monotonic() - t0
    cid = status.get("capture_id", "")
    check("arm: sofort zurueck, scharf, mit capture_id", status.get("state") == "armed" and cid.startswith("c-")
          and took < 3.0, f"{status} {took:.2f}")
    check("describe: armed mit Kennung", service.describe(PICO).get("state") == "armed"
          and service.describe(PICO).get("armed_capture") == cid, str(service.describe(PICO)))
    for name, call in (("zweites arm", lambda: service.arm(PICO, GOOD)),
                       ("acquire", lambda: service.acquire(PICO, GOOD))):
        try:
            call()
            code = ""
        except ScopeError as exc:
            code = exc.code
        check(f"{name} waehrend scharf -> scope_armed", code == "scope_armed", code)
    # eigener Thread je Geraet: das andere Oszilloskop bleibt bedienbar
    unit_switch_on()
    t0 = time.monotonic()
    report = service.acquire(UNIT, {"channels": [{"name": "CH1", "range_v": 5}], "timing": {"duration_s": 0.001},
                                    "trigger": {"mode": "auto", "source": "CH1", "level_v": 1.0},
                                    "measurements": ["vmax"]})
    check("UNI-T erfasst, waehrend das PicoScope scharf wartet", report.get("capture_id", "").startswith("c-")
          and time.monotonic() - t0 < 10, str(time.monotonic() - t0))
    service.release(UNIT)
    early = service.wait_capture(cid, 0.3)
    check("wait_capture vor dem Trigger -> armed, bleibt scharf", early.get("state") == "armed"
          and early.get("waited_s", 0) >= 0.25 and service.describe(PICO)["state"] == "armed", str(early))
    check("Ausschnitt vor dem Trigger: nur der Zustand", service.excerpt(cid, None, None, None, 100, "minmax")
          .get("state") == "armed")
    try:
        service.measure(cid, {})
        code = ""
    except ScopeError as exc:
        code = exc.code
    check("measure vor dem Trigger -> capture_pending", code == "capture_pending", code)
    switch_on()
    result = service.wait_capture(cid, 10)
    check("nach dem Schalten: Bericht mit Kennwerten", result.get("state") == "done" and result.get("triggered")
          is True and abs(result["channels"]["A"]["frequency"] - 1000) < 20 and Path(result["csv_path"]).is_file(),
          str(result)[:300])
    check("gleiche capture_id", result.get("capture_id") == cid)
    excerpt = service.excerpt(cid, "A", None, None, 50, "minmax")
    check("danach Ausschnitt mit Daten", excerpt.get("state") == "done" and "A" in excerpt["channels"])
    expected = ["armed", "triggered", "done"]
    check("capture_state: armed, triggered, done", spin(app, 1.0, lambda: [s for c, s in states if c == cid]
                                                         == expected), str(states))
    desc = service.describe(PICO)
    check("Haltezeit: danach verbunden mit Restzeit", desc["state"] == "connected"
          and 0 < desc.get("disconnect_in_s", 0) <= scope_service.IDLE_CLOSE_S, str(desc))
    check("fertige Erfassung erneut abholbar", service.wait_capture(cid, 0)["state"] == "done")

    # Getriggert, aber das Lesen dauert: wait_capture wartet darauf statt "armed" zu melden
    slow = SlowScope(read_s=1.0)
    inject(service, UNIT, slow)
    status = service.arm(UNIT, {"channels": [{"name": "CH1", "range_v": 5}], "timing": {"duration_s": 0.002},
                                "trigger": {"source": "CH1", "level_v": 1.0}})
    early = service.wait_capture(status["capture_id"], 0.1)
    slow.fire.set()
    spin(app, 2.0, lambda: service.capture_status(status["capture_id"])["state"] != "armed")
    reading = service.capture_status(status["capture_id"])["state"]
    late = service.wait_capture(status["capture_id"], 0.1)
    check("getriggert, Daten unterwegs: state triggered, wait_capture wartet sie ab", early["state"] == "armed"
          and reading == "triggered" and late.get("state") == "done", f"{early['state']} {reading} {late.get('state')}")
    service.release(UNIT)
    check("Ersatz-Adapter getrennt", slow.closed)

    # Frist von arm abgelaufen -> trigger_timeout
    quiet()
    status = service.arm(PICO, {**GOOD, "trigger": {**GOOD["trigger"], "timeout_s": 0.4}})
    try:
        service.wait_capture(status["capture_id"], 10)
        code = ""
    except ScopeError as exc:
        code = exc.code
    check("arm-Frist ohne Trigger -> trigger_timeout", code == "trigger_timeout", code)
    check("danach wieder frei", service.blocked_reason(PICO) == "")

    # release bricht ab
    status = service.arm(PICO, GOOD)
    released = service.release(PICO)
    try:
        service.wait_capture(status["capture_id"], 5)
        code = ""
    except ScopeError as exc:
        code = exc.code
    check("release bricht ab (cancelled) und trennt", code == "cancelled" and released["released"] is True
          and released["cancelled"] == [status["capture_id"]], f"{code} {released}")
    check("nach release idle", service.describe(PICO)["state"] == "idle", str(service.describe(PICO)))

    # Leerlauf-Trennung haelt eine scharfe Erfassung nicht an
    saved = scope_service.IDLE_CLOSE_S
    scope_service.IDLE_CLOSE_S = 0.0
    try:
        status = service.arm(PICO, GOOD)
        service._close_idle()
        service._executor_for(PICO).submit(lambda: None)
        time.sleep(0.2)
        check("Leerlauf-Trennung laesst scharfe Erfassung in Ruhe", service.describe(PICO)["state"] == "armed")
        service.cancel(status["capture_id"])
        service._executor_for(PICO).submit(lambda: None).result(timeout=5)
        service._close_idle()
        service._executor_for(PICO).submit(lambda: None).result(timeout=5)
        check("danach trennt der Leerlauf", service.describe(PICO)["state"] == "idle", str(service.describe(PICO)))
    finally:
        scope_service.IDLE_CLOSE_S = saved

    # Vorrang des Testablaufs
    status = service.arm(PICO, GOOD)
    service.begin_test(PICO, close=False)
    try:
        service.wait_capture(status["capture_id"], 5)
        code = ""
    except ScopeError as exc:
        code = exc.code
    check("begin_test bricht MCP-Erfassung ab", code == "cancelled", code)
    try:
        service.arm(PICO, GOOD)
        code = ""
    except ScopeError as exc:
        code = exc.code
    check("waehrend Testablauf: arm ueber Netzwerk -> scope_in_test_run", code == "scope_in_test_run", code)
    run_status = service.arm(PICO, GOOD, owner=scope_service.OWNER_RUN)
    check("der Lauf selbst darf scharf schalten", run_status["state"] == "armed", str(run_status))
    check("release ueber Netzwerk laesst dem Lauf das Oszilloskop", service.release(PICO)["released"] is False
          and service.capture_status(run_status["capture_id"])["state"] == "armed")
    service.end_run({PICO})
    service.end_test()
    spin(app, 3.0, lambda: service.describe(PICO)["state"] == "idle")
    check("end_run: abgebrochen und getrennt", service.capture_status(run_status["capture_id"])["state"] == "cancelled"
          and service.describe(PICO)["state"] == "idle", str(service.describe(PICO)))

    # UNI-T: scharf, dann Signal
    unit_quiet()
    status = service.arm(UNIT, {"channels": [{"name": "CH1", "range_v": 5}], "timing": {"duration_s": 0.001},
                                "trigger": {"source": "CH1", "level_v": 1.0, "timeout_s": 30},
                                "measurements": ["vmax", "frequency"]})
    check("UNI-T: arm -> armed", status.get("state") == "armed", str(status))
    early = service.wait_capture(status["capture_id"], 0.3)
    unit_switch_on()
    result = service.wait_capture(status["capture_id"], 10)
    check("UNI-T: vorher armed, nach dem Schalten Ergebnis", early.get("state") == "armed"
          and result.get("state") == "done" and result["channels"]["CH1"]["vmax"] > 1.0, str(result)[:200])
    unit_quiet()
    status = service.arm(UNIT, {"channels": [{"name": "CH1", "range_v": 5}], "timing": {"duration_s": 0.001},
                                "trigger": {"source": "CH1", "level_v": 1.0, "timeout_s": 30}})
    service.release(UNIT)
    check("UNI-T: release bricht ab", spin(app, 3.0, lambda: service.capture_status(status["capture_id"])["state"]
                                               == "cancelled"), str(service.capture_status(status["capture_id"])))
    unit_switch_on()
    try:
        service.arm(PICO, {**GOOD, "trigger": {"mode": "none"}})
        code = ""
    except ScopeError as exc:
        code = exc.code
    check("arm ohne Trigger (mode none) -> invalid_request", code == "invalid_request", code)
    service.shutdown()


# -- rest ------------------------------------------------------------------------------------------------

def section_rest(app) -> None:
    print("rest -- share_api")
    import share_api
    from check_network_share import _Stub, _snap_scope

    service = make_service()
    info = {"version": "0.0.0", "simulation": True, "uptime_s": 1.0, "base_url": ""}
    bearer = lambda n: "Bearer tok" if n == "Authorization" else None  # noqa: E731

    def call(method, path, payload=None, snap=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        resp = share_api.dispatch(method, path, bearer, _Stub(snap or _snap_scope()), info, body=body,
                                  client="10.0.0.9", local=False, scopes=service)
        try:
            return resp.status, json.loads(resp.body.decode("utf-8"))
        except ValueError:
            return resp.status, {}

    quiet()
    check("arm ohne Messen -> 403", call("POST", f"/api/v1/scopes/{PICO}/arm", GOOD,
                                         snap=_snap_scope(measure=False))[0] == 403)
    check("GET auf arm -> 405", call("GET", f"/api/v1/scopes/{PICO}/arm")[0] == 405)
    status, body = call("POST", f"/api/v1/scopes/{PICO}/arm", GOOD)
    cid = body.get("capture_id", "")
    check("POST arm -> 200 armed mit capture_id", status == 200 and body.get("state") == "armed" and cid, str(body))
    status, body = call("GET", "/api/v1/scopes")
    entry = body["scopes"][0]
    check("GET /scopes: armed, armed_capture, measure_blocked scope_armed", entry["state"] == "armed"
          and entry.get("armed_capture") == cid and entry["measure_blocked"] == "scope_armed", str(entry))
    status, body = call("GET", f"/api/v1/captures/{cid}")
    check("GET capture vorher -> 200 state armed ohne Daten", status == 200 and body.get("state") == "armed"
          and "channels" not in body, str(body))
    status, body = call("POST", f"/api/v1/captures/{cid}/measure", {})
    check("measure vorher -> 409 capture_pending", status == 409 and body.get("error") == "capture_pending")
    status, body = call("POST", f"/api/v1/scopes/{PICO}/acquire", GOOD)
    check("acquire waehrend scharf -> 409 scope_armed", status == 409 and body.get("error") == "scope_armed")
    status, body = call("POST", f"/api/v1/captures/{cid}/wait", {"timeout_s": 0.2})
    check("wait vor dem Trigger -> 200 armed", status == 200 and body.get("state") == "armed", str(body))
    check("wait: kaputte Zeit -> 400", call("POST", f"/api/v1/captures/{cid}/wait", {"timeout_s": "x"})[0] == 400)
    switch_on()
    status, body = call("POST", f"/api/v1/captures/{cid}/wait", {"timeout_s": 10})
    check("wait nach dem Schalten -> 200 done mit Kennwerten", status == 200 and body.get("state") == "done"
          and body["channels"]["A"]["vmax"] > 3.0 and body.get("capture_id") == cid, str(body)[:300])
    status, body = call("GET", f"/api/v1/captures/{cid}?max_points=50")
    check("GET capture danach -> Ausschnitt", status == 200 and "A" in body.get("channels", {}), str(body)[:200])
    check("Erfassung eines nicht freigegebenen Scopes -> 404",
          call("POST", f"/api/v1/captures/{cid}/wait", {}, snap=_snap_scope(measure=False))[0] == 404)
    quiet()
    status, body = call("POST", f"/api/v1/scopes/{PICO}/arm", GOOD)
    cid = body.get("capture_id", "")
    status, body = call("POST", f"/api/v1/scopes/{PICO}/release", {})
    check("release -> cancelled nennt die Erfassung", status == 200 and body.get("cancelled") == [cid], str(body))
    status, body = call("POST", f"/api/v1/captures/{cid}/wait", {"timeout_s": 5})
    check("wait nach release -> 409 cancelled", status == 409 and body.get("error") == "cancelled", str(body))
    check("unbekannte Kennung -> 404", call("POST", "/api/v1/captures/c-00000000-000000-00/wait", {})[0] == 404)
    switch_on()
    service.shutdown()


# -- mcp -------------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def section_mcp(app) -> None:
    print("mcp -- MCP-Server ueber HTTP")
    mcp_python = ROOT / "labcontrol_mcp" / ".venv" / "Scripts" / "python.exe"
    if not mcp_python.is_file():
        mcp_python = ROOT / "labcontrol_mcp" / ".venv" / "bin" / "python"
    if not mcp_python.is_file():
        print("  (labcontrol_mcp/.venv fehlt -- Abschnitt uebersprungen)")
        FAILURES.append("mcp: .venv fehlt")
        return
    from check_network_share import MCP_PROBE, _Stub, _snap_scope
    from share_server import ShareServer

    service = make_service()
    snap = _snap_scope(token=TOKEN)
    port = free_port()
    server = ShareServer(_Stub(snap), scopes=service)
    server.apply({"enabled": True, "bind": "127.0.0.1", "port": port})
    quiet()
    args = {"scope_id": PICO, "channels": [{"name": "A", "range_v": 5}], "trigger_level_v": 1.65,
            "duration_s": 0.002, "trigger_source": "A", "measurements": ["vmax", "frequency"]}
    steps = [
        {"name": "arm", "tool": "arm", "args": args},
        {"name": "early", "tool": "wait_capture", "args": {"capture_id": "$arm.capture_id", "timeout_s": 0.3}},
        {"name": "get", "tool": "get_capture", "args": {"capture_id": "$arm.capture_id"}},
        {"name": "done", "tool": "wait_capture", "args": {"capture_id": "$arm.capture_id", "timeout_s": 20}},
        {"name": "excerpt", "tool": "get_capture", "args": {"capture_id": "$arm.capture_id", "max_points": 50}},
        # Pegel ueber dem Rechteck: triggert nie, bleibt scharf bis release_scope
        {"name": "arm2", "tool": "arm", "args": {**args, "trigger_level_v": 4.5, "arm_timeout_s": 120}},
        {"name": "release", "tool": "release_scope", "args": {"scope_id": PICO}},
        {"name": "cancelled", "tool": "wait_capture", "args": {"capture_id": "$arm2.capture_id", "timeout_s": 5}},
        {"name": "acquire", "tool": "acquire", "args": {"scope_id": PICO, "channels": [{"name": "A", "range_v": 5}],
                                                         "duration_s": 0.002, "trigger_level_v": 1.65}},
    ]
    box: dict = {}

    def run():
        env = {**os.environ, "LABCONTROL_URL": f"http://127.0.0.1:{port}", "LABCONTROL_TOKEN": TOKEN}
        done = subprocess.run([str(mcp_python), "-c", MCP_PROBE, str(ROOT / "labcontrol_mcp" / "server.py"),
                               json.dumps(steps)], capture_output=True, text=True, env=env, timeout=180)
        box["out"] = done

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    # Das "Schalten" der Session: 2 s nachdem die erste Erfassung scharf ist (frueher Warteaufruf und get_capture
    # sind dann durch), liegt das Signal an.
    def first_armed():
        with service._lock:
            return any(p.state == "armed" for p in service._pending.values())
    spin(app, 60.0, first_armed)
    spin(app, 2.0)
    switch_on()
    spin(app, 120.0, lambda: "out" in box)
    server.stop()
    service.shutdown()
    done = box.get("out")
    line = next((l for l in (done.stdout if done else "").splitlines() if l.startswith("RESULT ")), None)
    if line is None:
        check("MCP-Probe lief", False, ((done.stdout if done else "")[-500:] + (done.stderr if done else "")[-800:]))
        return
    out = json.loads(line[len("RESULT "):])
    check("MCP: arm und wait_capture angeboten", {"arm", "wait_capture"} <= set(out["tools"]), str(out["tools"]))
    check("MCP: Hinweise nennen arm/wait_capture", "wait_capture" in out["instructions"])
    data = lambda name: out[name]["data"] or {}  # noqa: E731
    check("MCP arm -> armed mit capture_id", not out["arm"]["error"] and data("arm").get("state") == "armed"
          and data("arm").get("capture_id"), str(out["arm"])[:300])
    check("MCP wait_capture vor dem Schalten -> armed", data("early").get("state") == "armed", str(out["early"])[:300])
    check("MCP get_capture mit der Kennung vorher -> armed", data("get").get("state") == "armed", str(out["get"])[:200])
    check("MCP wait_capture nach dem Schalten -> Ergebnis", data("done").get("state") == "done"
          and data("done")["channels"]["A"]["vmax"] > 3.0, str(out["done"])[:300])
    check("MCP get_capture danach -> Daten", "A" in data("excerpt").get("channels", {}), str(out["excerpt"])[:200])
    check("MCP release_scope bricht ab", out["cancelled"]["error"] and "cancelled" in out["cancelled"]["text"]
          and data("release").get("cancelled") == [data("arm2").get("capture_id")], str(out["cancelled"])[:300])
    check("MCP acquire unveraendert", not out["acquire"]["error"] and data("acquire").get("capture_id"),
          str(out["acquire"])[:200])


# -- model -----------------------------------------------------------------------------------------------

def section_model() -> None:
    print("model -- Format, Parameter, Kurztexte")
    import scope_steps
    import testcase_model as tm

    folder = Path(tempfile.mkdtemp(prefix="lc-scope-arm-format-"))
    arm = tm.TestStep(device_kind="picoscope", device_id=PICO, action="SCOPE_ARM",
                      params={"channel": "B", "range_v": 10, "probe": 10, "edge": "falling", "level_v": 6.0,
                              "duration_s": 0.02, "pre_trigger_pct": 25, "junk": 1})
    wait = tm.TestStep(device_kind="scpiscope", device_id="", action="SCOPE_WAIT",
                       params={"timeout_s": 5, "measurement": "rise_time", "channel": "CH2"},
                       check_enabled=True, check_field="scope_value", check_min=0.0, check_max=0.001, store_var="tr")
    steps = [arm, tm.TestStep(device_kind="psu", action="PSU_OUT_ON"), wait]
    path = folder / "neu.json"
    tm.save_steps(steps, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    check("mit Oszilloskop-Schritten: Format 5", data["version"] == 5, str(data["version"]))
    loaded, _meta = tm.load_testcase(path)
    check("Laden: Aktion/Geraet/Parameter zurueck", (loaded[0].action, loaded[0].device_kind, loaded[0].params["level_v"],
                                                     loaded[2].device_kind, loaded[2].params["measurement"])
          == ("SCOPE_ARM", "picoscope", 6.0, "scpiscope", "rise_time"))
    tm.save_steps(loaded, folder / "neu2.json")
    check("Laden+Speichern verlustfrei", json.loads((folder / "neu2.json").read_text(encoding="utf-8")) == data)
    old_max = tm.FILE_FORMAT_VERSION
    try:
        tm.FILE_FORMAT_VERSION = 4
        try:
            tm.load_testcase(path)
            refused = False
        except ValueError as exc:
            refused = "neueren Programmversion" in str(exc)
    finally:
        tm.FILE_FORMAT_VERSION = old_max
    check("Version mit Format 4 lehnt die Datei ab", refused)
    tm.save_steps([tm.TestStep(step_type="confirm", params={"text": "x"})], folder / "v4.json")
    check("Bestaetigung ohne Oszilloskop-Schritt weiter Format 4",
          json.loads((folder / "v4.json").read_text(encoding="utf-8"))["version"] == 4)
    tm.save_steps([tm.TestStep(device_kind="picoscope", action="PICO_VMAX")], folder / "alt.json")
    check("alte PicoScope-Kennwerte weiter Format 2", json.loads((folder / "alt.json").read_text(encoding="utf-8"))
          ["version"] == 2)
    tm.save_block(steps, "Einschalten", folder / "b.json")
    block = json.loads((folder / "b.json").read_text(encoding="utf-8"))
    name, block_steps = tm.load_block(folder / "b.json")
    check("Baustein: Format 4, Schritte zurueck", block["version"] == 4 and name == "Einschalten"
          and [s.action for s in block_steps] == ["SCOPE_ARM", "PSU_OUT_ON", "SCOPE_WAIT"], str(block["version"]))
    clean = scope_steps.clean_params("SCOPE_ARM", arm.params, "picoscope")
    check("Parameter bereinigt (fremde fallen weg)", "junk" not in clean and clean["channel"] == "B"
          and clean["edge"] == "falling")
    check("Kanal passt sich der Geraeteart an", scope_steps.clean_params("SCOPE_ARM", {"channel": "A"},
                                                                          "scpiscope")["channel"] == "CH1")
    body = scope_steps.arm_body(arm.params, "picoscope")
    check("Anfrage: Einzelerfassung, Quelle = Kanal, Tastkopf, Vortrigger",
          body["trigger"]["mode"] == "single" and body["trigger"]["source"] == "B"
          and body["channels"][0]["probe"] == 10.0 and body["timing"]["pre_trigger_pct"] == 25.0)
    check("Kurztext Scharf schalten", scope_steps.summary("SCOPE_ARM", arm.params, "picoscope")
          == "B ±10 V, 10:1, ↓ 6 V, 20 ms, 25 % vor", scope_steps.summary("SCOPE_ARM", arm.params, "picoscope"))
    check("Kurztext Auf Erfassung warten", scope_steps.summary("SCOPE_WAIT", wait.params, "scpiscope")
          == "≤ 5 s, Anstiegszeit CH2", scope_steps.summary("SCOPE_WAIT", wait.params, "scpiscope"))
    check("Pruefung mit Einheit des Kennwerts", tm.check_summary(wait) == "rise_time: 0–0.001 s", tm.check_summary(wait))
    check("Warten seit bietet Scharf schalten, nicht das Warten",
          "SCOPE_ARM" in tm.wait_since_actions("scpiscope") and "SCOPE_WAIT" not in tm.wait_since_actions("scpiscope"))
    check("UNI-T im Editor waehlbar", tm.kind_label("scpiscope") == "Oszilloskop (LAN)"
          and set(tm.DEVICE_ACTIONS["scpiscope"]) == {"SCOPE_ARM", "SCOPE_WAIT"}
          and {"PICO_VMAX", "SCOPE_ARM"} <= set(tm.DEVICE_ACTIONS["picoscope"]))
    value, channel, reason = scope_steps.measured_value({"channels": {"A": {"vmax": 3.3}}}, {"measurement": "vmax"},
                                                        "A")
    check("Kennwert aus dem Bericht (Kanal der Erfassung)", (value, channel) == (3.3, "A"))
    value, _channel, reason = scope_steps.measured_value(
        {"channels": {"A": {"frequency": None, "unavailable": {"frequency": "keine Flanke"}}}},
        {"measurement": "frequency"}, "A")
    check("nicht bestimmbarer Kennwert mit Grund", value is None and reason == "keine Flanke", reason)


# -- editor ----------------------------------------------------------------------------------------------

def section_editor(app) -> None:
    print("editor -- Zeile, Dialog, Speichern/Laden")
    import testcase_model as tm
    from block_dialog import _step_line
    from scope_step_dialog import ScopeStepDialog
    from testcase_tab import COL_ACTION, COL_DEVICE, COL_VALUE, TestcaseTab, _device_key

    tab = TestcaseTab()
    tab.on_device_known("picoscope", PICO, "Pico")
    tab.on_device_known("scpiscope", UNIT, "UNI-T")
    tab._add_row_clicked()
    row = tab._table.rowCount() - 1
    device_combo = tab._table.cellWidget(row, COL_DEVICE)
    keys = [device_combo.itemData(i) for i in range(device_combo.count())]
    check("UNI-T in der Geraeteauswahl", _device_key("scpiscope", UNIT) in keys, str(keys))
    device_combo.setCurrentIndex(device_combo.findData(_device_key("scpiscope", UNIT)))
    action_combo = tab._table.cellWidget(row, COL_ACTION)
    codes = [action_combo.itemData(i) for i in range(action_combo.count())]
    check("UNI-T: Scharf schalten / Auf Erfassung warten", codes == ["SCOPE_ARM", "SCOPE_WAIT"], str(codes))
    action_combo.setCurrentIndex(action_combo.findData("SCOPE_ARM"))
    stack = tab._table.cellWidget(row, COL_VALUE)
    check("Parameterseite mit Zusammenfassung", stack.currentIndex() == 6)
    dialog = ScopeStepDialog("SCOPE_ARM", "scpiscope", {}, tab)
    dialog.channel.setCurrentIndex(dialog.channel.findData("CH3"))
    dialog.level_v.setValue(6.0)
    dialog.duration_ms.setValue(20.0)
    dialog.pre_pct.setValue(30)
    dialog.edge.setCurrentIndex(dialog.edge.findData("falling"))
    stack.widget(6)._params = dialog.params()
    stack.widget(6)._refresh()
    step = tab.steps()[row]
    check("Dialog -> Schritt-Parameter", (step.action, step.device_kind, step.params["channel"], step.params["level_v"],
                                          step.params["duration_s"], step.params["edge"])
          == ("SCOPE_ARM", "scpiscope", "CH3", 6.0, 0.02, "falling"), str(step.params))
    from PySide6.QtWidgets import QLabel
    check("Zusammenfassung in der Zeile", any("CH3 ±5 V" in w.text() for w in stack.widget(6).findChildren(QLabel)))
    tab._add_row_clicked()
    row2 = tab._table.rowCount() - 1
    device_combo = tab._table.cellWidget(row2, COL_DEVICE)
    device_combo.setCurrentIndex(device_combo.findData(_device_key("scpiscope", UNIT)))
    action_combo = tab._table.cellWidget(row2, COL_ACTION)
    action_combo.setCurrentIndex(action_combo.findData("SCOPE_WAIT"))
    wait_dialog = ScopeStepDialog("SCOPE_WAIT", "scpiscope", {}, tab)
    wait_dialog.timeout_s.setValue(7.5)
    wait_dialog.measurement.setCurrentIndex(wait_dialog.measurement.findData("vpp"))
    tab._table.cellWidget(row2, COL_VALUE).widget(6)._params = wait_dialog.params()
    steps = tab.steps()
    check("Warteschritt: Hoechstzeit und Kennwert", (steps[row2].params["timeout_s"], steps[row2].params["measurement"])
          == (7.5, "vpp"), str(steps[row2].params))
    check("Ausfuehren-Ansicht", tab._describe_step(steps[row]).startswith("UNI-T – Scharf schalten (CH3 ±5 V, ↓ 6 V")
          and "≤ 7.5 s, Spitze-Spitze" in tab._describe_step(steps[row2]), tab._describe_step(steps[row]))
    check("Baustein-Vorschau", "Scharf schalten (CH3" in _step_line(steps[row]), _step_line(steps[row]))
    path = DATA / "editor_scope.json"
    tm.save_steps(steps, path)
    check("gespeichert in Format 5", json.loads(path.read_text(encoding="utf-8"))["version"] == 5)
    tab2 = TestcaseTab()
    tab2.on_device_known("scpiscope", UNIT, "UNI-T")
    check("Laden", tab2._load_path(path))
    again = tab2.steps()
    check("Laden: gleiche Schritte", [(s.action, s.device_id, s.params) for s in again]
          == [(s.action, s.device_id, s.params) for s in steps])
    check("Laden: Aktion in der Auswahl", tab2._table.cellWidget(row, COL_ACTION).currentData() == "SCOPE_ARM")
    check("nach dem Laden unveraendert", not tab2.is_modified())
    reopened = ScopeStepDialog("SCOPE_ARM", "scpiscope", again[row].params, tab)
    check("Dialog zeigt gespeicherte Werte", reopened.channel.currentData() == "CH3"
          and abs(reopened.duration_ms.value() - 20.0) < 1e-6 and reopened.edge.currentData() == "falling")
    tab.deleteLater()
    tab2.deleteLater()


# -- window ----------------------------------------------------------------------------------------------

def http(method: str, port: int, path: str, body=None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, {"_raw": raw.decode("utf-8", "replace")}


def ask(app, *args, **kwargs):
    box: dict = {}
    thread = threading.Thread(target=lambda: box.setdefault("r", http(*args, **kwargs)), daemon=True)
    thread.start()
    spin(app, 25.0, lambda: "r" in box)
    return box.get("r", (0, {}))


def section_window(app) -> None:
    print("window -- Hauptfenster, Lauf per HTTP")
    import no_hardware
    no_hardware.block()
    import data_store
    import device_worker as dw
    import testcase_model as tm
    from scope_steps import SCOPE_ARM, SCOPE_WAIT

    port = free_port()
    (DATA / "settings.json").write_text(json.dumps({
        "simulation_mode": True, "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port,
        "share_token": TOKEN, "share_local_bypass": True}), encoding="utf-8")
    from main_window import MainWindow
    from settings import Settings
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    online = window._online_devices
    spin(app, 8.0, lambda: dw.SIM_PSU_ID in online.get("psu", set()) and PICO in online.get("picoscope", set())
         and UNIT in online.get("scpiscope", set()))
    check("Simulation: Netzteil, PicoScope und UNI-T verbunden", PICO in online.get("picoscope", set())
          and UNIT in online.get("scpiscope", set()), str({k: sorted(v) for k, v in online.items() if v}))
    service = window._scope_service
    folder = data_store.target_dir("testcases")
    psu = dict(device_kind="psu", device_id=dw.SIM_PSU_ID)
    pico_arm = tm.TestStep(device_kind="picoscope", device_id=PICO, action=SCOPE_ARM,
                           params={"channel": "A", "range_v": 5.0, "level_v": 1.65, "duration_s": 0.002,
                                   "pre_trigger_pct": 20})
    pico_wait = tm.TestStep(device_kind="picoscope", device_id=PICO, action=SCOPE_WAIT,
                            params={"timeout_s": 5.0, "measurement": "vmax"}, check_enabled=True,
                            check_field="scope_value", check_min=3.0, check_max=3.6, store_var="spitze")
    tm.save_steps([pico_arm, tm.TestStep(**psu, action="PSU_OUT_ON"), tm.TestStep(step_type="wait", duration=0.3),
                   pico_wait, tm.TestStep(**psu, action="PSU_OUT_OFF")], folder / "einschalten.json")

    def run_entry(run_id):
        return window._live_state.runs.get(run_id) or {}

    def run_error() -> str:
        # Meldung des fehlgeschlagenen Schritts wie in get_run ("error" des juengsten Laufs im Register; seit dem
        # Bugfix in 1.0.1 auch fuer Laeufe im Fenster gefuellt)
        runs = window._live_state.runs.list()
        return (runs[0].get("error") or "") if runs else ""

    # Das "Schalten": wenn Schritt 2 (Netzteil EIN) startet, liegt am Oszilloskop das Signal an -- vorher muss
    # die Erfassung schon scharf sein.
    seen: dict = {}

    def on_started(index, _step):
        if index == 1 and "state" not in seen:
            with service._lock:
                seen["state"] = [p.state for p in service._pending.values()
                                 if p.device_id == PICO and p.owner == "run"][-1:]
            switch_on()
    runner = window._run_session.runner
    runner.step_started.connect(on_started)
    quiet()
    status, body = ask(app, "POST", port, "/runs", {"testcase": "einschalten.json"})
    run_id = body.get("run", "")
    check("Start per HTTP", status == 202 and run_id, f"{status} {body}")
    spin(app, 30.0, lambda: run_entry(run_id).get("running") is False)
    entry = run_entry(run_id)
    check("PicoScope: scharf, bevor das Netzteil schaltet", seen.get("state") == ["armed"], str(seen))
    record = window._run_recorder.record()
    captures = list(record.scope_captures) if record is not None else []
    check("PicoScope: Lauf bestanden (Pruefung vmax 3,0–3,6 V)", entry.get("status") == "passed", str(entry)[:400])
    check("Laufaufzeichnung: Erfassung mit Kennwert, CSV und Bild", len(captures) == 1
          and 3.0 <= (captures[0].get("value") or 0) <= 3.6 and Path(captures[0].get("csv_path", "")).is_file()
          and (captures[0].get("png") or b"")[:4] == b"\x89PNG", str([{k: v for k, v in c.items() if k != "png"}
                                                                       for c in captures])[:400])
    notes = [e.message for e in record.events if e.kind == "note"] if record is not None else []
    check("Vermerke: scharf und getriggert", any(n.startswith("scharf (A ±5 V") for n in notes)
          and any("getriggert nach" in n and "Maximum A" in n for n in notes), str(notes))
    status, report = ask(app, "GET", port, f"/runs/{run_id}/report")
    html = report.get("_raw", "")
    check("Bericht: Oszilloskop-Erfassungen, Kurve als Bild, CSV", "Oszilloskop-Erfassungen" in html
          and "– Maximum A = 3." in html and captures and Path(captures[0]["csv_path"]).name in html
          and html.count("data:image/png") >= 1, html[html.find("Oszilloskop-Erfassungen"):][:600])
    status, result = ask(app, "GET", port, f"/runs/{run_id}/result")
    rows = result.get("scope_captures") or []
    check("run-result.json: scope_captures ohne Bild", len(rows) == 1 and rows[0]["measurement"] == "vmax"
          and "png" not in rows[0] and rows[0]["arm_step"] == 1 and rows[0]["step"] == 4, str(rows)[:300])
    checks = result.get("checks") or []
    check("run-result.json: Pruefung mit Einheit V", checks and checks[0]["unit"] == "V"
          and checks[0]["field"] == "scope_value", str(checks)[:300])
    spin(app, 3.0, lambda: service.describe(PICO)["state"] == "idle")
    check("nach dem Lauf: Oszilloskop freigegeben", service.describe(PICO)["state"] == "idle",
          str(service.describe(PICO)))
    runner.step_started.disconnect(on_started)

    # Kein Trigger innerhalb der Hoechstzeit -> FAIL mit Meldung
    quiet()
    tm.save_steps([pico_arm, tm.TestStep(device_kind="picoscope", device_id=PICO, action=SCOPE_WAIT,
                                         params={"timeout_s": 0.6, "measurement": "vmax"})], folder / "still.json")
    t0 = time.monotonic()
    status, body = ask(app, "POST", port, "/runs", {"testcase": "still.json"})
    run_id = body.get("run", "")
    spin(app, 20.0, lambda: run_entry(run_id).get("running") is False)
    entry = run_entry(run_id)
    check("kein Trigger: Schritt FAIL mit Meldung", entry.get("status") == "error"
          and "Kein Trigger innerhalb von 0.6 s" in run_error() and run_error().startswith("Schritt 2"), run_error())
    check("... erst nach der Hoechstzeit", time.monotonic() - t0 >= 0.6)
    spin(app, 3.0, lambda: service.describe(PICO)["state"] == "idle")
    with service._lock:
        active = [p.capture_id for p in service._pending.values() if p.state in ("arming", "armed")]
    check("... Erfassung abgebrochen, Oszilloskop frei", not active and service.describe(PICO)["state"] == "idle",
          f"{active} {service.describe(PICO)}")

    # Stopp, waehrend scharf -> abgebrochen und frei; Netzwerk/MCP solange gesperrt
    tm.save_steps([pico_arm, tm.TestStep(step_type="wait", duration=20.0),
                   tm.TestStep(device_kind="picoscope", device_id=PICO, action=SCOPE_WAIT, params={"timeout_s": 5})],
                  folder / "stopp.json")
    status, body = ask(app, "POST", port, "/runs", {"testcase": "stopp.json"})
    run_id = body.get("run", "")
    spin(app, 15.0, lambda: (run_entry(run_id).get("step") or 0) >= 2)
    with service._lock:
        armed = [p for p in service._pending.values() if p.state == "armed" and p.owner == "run"]
    check("waehrend des Laufs scharf, Scope im Testablauf", len(armed) == 1
          and service.describe(PICO)["state"] == "test" and service.blocked_reason(PICO) == "scope_in_test_run",
          str(service.describe(PICO)))
    status, body = ask(app, "POST", port, f"/runs/{run_id}/stop", {})
    spin(app, 10.0, lambda: run_entry(run_id).get("running") is False)
    spin(app, 3.0, lambda: service.describe(PICO)["state"] == "idle")
    check("Stopp: Erfassung abgebrochen, Oszilloskop frei", armed and armed[0].state == "cancelled"
          and service.describe(PICO)["state"] == "idle" and run_entry(run_id).get("status") == "stopped",
          f"{armed and armed[0].state} {service.describe(PICO)}")

    # UNI-T: scharf -> Netzteil schaltet -> warten
    unit_quiet()
    tm.save_steps([tm.TestStep(device_kind="scpiscope", device_id=UNIT, action=SCOPE_ARM,
                               params={"channel": "CH1", "range_v": 5.0, "level_v": 1.0, "duration_s": 0.005}),
                   tm.TestStep(**psu, action="PSU_OUT_ON"),
                   tm.TestStep(device_kind="scpiscope", device_id="", action=SCOPE_WAIT,
                               params={"timeout_s": 5.0, "measurement": "frequency"}, check_enabled=True,
                               check_field="scope_value", check_min=900, check_max=1100),
                   tm.TestStep(**psu, action="PSU_OUT_OFF")], folder / "unit.json")
    seen.clear()

    def on_unit_started(index, _step):
        if index == 1 and "state" not in seen:
            with service._lock:
                seen["state"] = [p.state for p in service._pending.values() if p.device_id == UNIT][-1:]
            unit_switch_on()
        if index == 2:   # Stand beim Warten (fuer die Fehlermeldung)
            from unit_mso1000hd import mock
            sim = getattr(getattr(service._open.get(UNIT), "_device", None), "_t", None)
            seen["at_wait"] = (getattr(sim, "status", None), getattr(sim, "trig_level", None),
                               getattr(sim, "trig_source", None), getattr(sim, "_pending_trigger", None),
                               type(mock.SimulatedInstrument.signals.get("CH1")).__name__)
    runner.step_started.connect(on_unit_started)
    status, body = ask(app, "POST", port, "/runs", {"testcase": "unit.json"})
    run_id = body.get("run", "")
    spin(app, 30.0, lambda: run_entry(run_id).get("running") is False)
    runner.step_started.disconnect(on_unit_started)
    entry = run_entry(run_id)
    check("UNI-T: scharf vor dem Schalten, Lauf bestanden (1 kHz)", seen.get("state") == ["armed"]
          and entry.get("status") == "passed", f"{seen} {run_error()}")
    spin(app, 3.0, lambda: service.describe(UNIT)["state"] == "idle")
    check("UNI-T: danach freigegeben", service.describe(UNIT)["state"] == "idle", str(service.describe(UNIT)))

    # Rechtzeitig getriggert, aber das Lesen dauert laenger als die Hoechstzeit: kein FAIL
    slow = SlowScope(read_s=1.5)
    inject(service, UNIT, slow)
    tm.save_steps([tm.TestStep(device_kind="scpiscope", device_id=UNIT, action=SCOPE_ARM,
                               params={"channel": "CH1", "range_v": 5.0, "level_v": 1.0, "duration_s": 0.002}),
                   tm.TestStep(**psu, action="PSU_OUT_ON"),
                   tm.TestStep(device_kind="scpiscope", device_id=UNIT, action=SCOPE_WAIT,
                               params={"timeout_s": 0.5, "measurement": "vmax"})], folder / "langsam.json")

    def on_slow_started(index, _step):
        if index == 1:
            slow.fire.set()
    runner.step_started.connect(on_slow_started)
    status, body = ask(app, "POST", port, "/runs", {"testcase": "langsam.json"})
    run_id = body.get("run", "")
    spin(app, 20.0, lambda: run_entry(run_id).get("running") is False)
    runner.step_started.disconnect(on_slow_started)
    check("getriggert, Lesen dauert 1,5 s > Hoechstzeit 0,5 s: bestanden", run_entry(run_id).get("status") == "passed"
          and slow.closed, f"{run_error()} closed={slow.closed}")

    # Ablehnungen
    session = window._run_session
    mixed = [pico_arm, tm.TestStep(device_kind="picoscope", device_id=PICO, action="PICO_VMAX", value=7)]
    check("PICO_* und Scharf schalten am selben PicoScope -> abgelehnt", "selben Oszilloskop" in session.refusal(mixed),
          session.refusal(mixed))
    partner = [tm.TestStep(device_kind="picoscope", device_id="@pi/picoscope:default", action=SCOPE_ARM)]
    check("Partner-Oszilloskop -> abgelehnt", "Verbund-Partner" in session.refusal(partner))
    saved, session._scope_service = session._scope_service, None
    try:
        check("ohne Oszilloskop-Dienst (Kommandozeile) -> abgelehnt", "Fenster" in session.refusal([pico_arm]))
    finally:
        session._scope_service = saved
    tm.save_steps([tm.TestStep(device_kind="picoscope", device_id=PICO, action=SCOPE_WAIT, params={"timeout_s": 1})],
                  folder / "ohne.json")
    status, body = ask(app, "POST", port, "/runs", {"testcase": "ohne.json"})
    run_id = body.get("run", "")
    spin(app, 10.0, lambda: run_entry(run_id).get("running") is False)
    check("Warten ohne vorheriges Scharf schalten -> Meldung", run_entry(run_id).get("status") == "error"
          and "nichts scharf geschaltet" in run_error(), run_error())
    switch_on()
    unit_switch_on()
    spin(app, 2.0)
    window.close()
    app.processEvents()


def main() -> int:
    wanted = set(sys.argv[1:]) or {"core", "rest", "mcp", "model", "editor", "window"}
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    if "model" in wanted:
        section_model()
    if "core" in wanted:
        section_core(app)
    if "rest" in wanted:
        section_rest(app)
    if "mcp" in wanted:
        section_mcp(app)
    if "editor" in wanted:
        section_editor(app)
    if "window" in wanted:
        section_window(app)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

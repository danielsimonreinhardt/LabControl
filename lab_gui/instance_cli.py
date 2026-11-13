"""Kommandozeile ueber die laufende LabControl-Instanz (FP-2 c, 0.59.0).

Laeuft LabControl schon (Kiosk am Pi, Fenster am PC), oeffnet `labcontrol run` die Geraete nicht selbst,
sondern startet den Lauf ueber die REST-Schnittstelle dieser Instanz (POST /api/v1/runs mit "path"), fragt
den Stand ab und holt am Ende run-result.json, JUnit und Bericht. Exit-Codes wie ohne Fenster.

Voraussetzungen an der Instanz: Netzwerk-Freigabe an, und "Claude (MCP) darf steuern" (Programme auf
diesem Rechner duerfen steuern). Token und Port stehen in derselben settings.json.
"""
from __future__ import annotations

import json
import signal
import time
import urllib.error
import urllib.request
from pathlib import Path

from run_session import EXIT_ABORTED, EXIT_INTERRUPTED, EXIT_SETUP

POLL_S = 0.5
HTTP_TIMEOUT_S = 20.0


def _say(text: str) -> None:
    print(text, flush=True)


class InstanceError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(f"{status} {code} {message}".strip())
        self.status, self.code, self.message = status, code, message


class Instance:
    def __init__(self, port: int, token: str) -> None:
        self.base = f"http://127.0.0.1:{port}/api/v1"
        self.token = token

    def request(self, method: str, path: str, body: dict | None = None, raw: bool = False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("User-Agent", "labcontrol-cli")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
                content = response.read()
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except ValueError:
                payload = {}
            raise InstanceError(exc.code, payload.get("error", "http_error"), payload.get("message", "")) from None
        except (urllib.error.URLError, OSError) as exc:
            raise InstanceError(0, "unreachable", str(exc)) from None
        return content if raw else json.loads(content.decode("utf-8"))


HINTS = {
    "remote_control_inactive": "an der laufenden Instanz „Claude (MCP) darf steuern“ einschalten "
                               "(Einstellungen → Netzwerk)",
    "unreachable": "Netzwerk-Freigabe der laufenden Instanz einschalten (Einstellungen → Netzwerk)",
    "editor_modified": "im Editor der laufenden Instanz gibt es ungespeicherte Änderungen",
    "locked": "es läuft schon ein Ablauf oder eine Sicherheitsabschaltung steht an",
}


def _vars(items: list[str]) -> dict[str, float]:
    from headless import _parse_vars
    return _parse_vars(items)


def main(args, settings) -> int:
    if args.command != "run":
        return _devices(settings)
    if getattr(args, "simulation", False):
        _say("Fehler: LabControl läuft bereits – --simulation geht nur ohne laufende Instanz")
        return EXIT_SETUP
    try:
        variables = _vars(args.var)
    except ValueError as exc:
        _say(f"Fehler: {exc}")
        return EXIT_SETUP
    path = Path(args.testcase).resolve()
    if not path.is_file():
        _say(f"Fehler: Datei nicht gefunden: {path}")
        return EXIT_SETUP
    share = settings.share_config
    if not share.get("enabled"):
        _say("LabControl läuft bereits; Lauf über die Instanz geht nicht: " + HINTS["unreachable"])
        return EXIT_SETUP
    instance = Instance(int(share["port"]), share.get("token", ""))

    _say(f"LabControl läuft bereits – Lauf über die laufende Instanz ({instance.base})")
    deadline = time.monotonic() + max(0.0, args.wait_devices)
    while True:
        try:
            run_id = instance.request("POST", "/runs", {"path": str(path), "vars": variables,
                                                        "all_off_at_end": True})["run"]
            break
        except InstanceError as exc:
            if exc.code == "devices_missing" and time.monotonic() < deadline:
                time.sleep(1.0)
                continue
            hint = HINTS.get(exc.code, "")
            _say(f"Nicht gestartet: {exc.code}" + (f" – {exc.message}" if exc.message else "")
                 + (f" ({hint})" if hint else ""))
            return EXIT_SETUP
    _say(f"Start: {path.stem} (Lauf {run_id})")

    state = {"interrupted": False}

    def interrupt(_signum, _frame) -> None:
        state["interrupted"] = True

    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        status = _follow(instance, run_id, args, state)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if status is None:
        return EXIT_ABORTED
    _fetch_outputs(instance, run_id, args)
    reported = status.get("exit_code")
    code = EXIT_INTERRUPTED if state["interrupted"] else (EXIT_ABORTED if reported is None else int(reported))
    verdict = {0: "BESTANDEN", 1: "NICHT BESTANDEN", 2: "ABGEBROCHEN", 3: "NICHT GESTARTET",
               130: "UNTERBROCHEN"}.get(code, str(code))
    _say(f"Ergebnis: {verdict} (Exit {code})")
    return code


def _follow(instance: Instance, run_id: str, args, state: dict) -> dict | None:
    events_next = 0
    stop_sent = False
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    failures = 0
    while True:
        if (state["interrupted"] or (deadline is not None and time.monotonic() > deadline)) and not stop_sent:
            _say("Unterbrochen – Lauf wird angehalten (ALLE AUS)" if state["interrupted"]
                 else f"Zeitlimit {args.timeout:g} s überschritten – Lauf wird angehalten")
            try:
                instance.request("POST", f"/runs/{run_id}/stop", {})
            except InstanceError as exc:
                _say(f"Anhalten: {exc.code}")
            stop_sent = True
        try:
            status = instance.request("GET", f"/runs/{run_id}?events={events_next}")
            failures = 0
        except InstanceError as exc:
            failures += 1
            if failures > 20:
                _say(f"Verbindung zur Instanz verloren: {exc}")
                return None
            time.sleep(POLL_S)
            continue
        for event in status.get("events", []):
            if event.get("kind") == "result":
                ok = "bestanden" if event.get("passed") else "NICHT bestanden"
                _say(f"Schritt {event.get('step')}: {ok} ({event.get('value', 0):g})")
            elif event.get("kind") == "failed":
                _say(f"Schritt {event.get('step')}: {event.get('message')}")
        events_next = status.get("events_next", events_next)
        if not status.get("running"):
            return status
        time.sleep(POLL_S)


def _fetch_outputs(instance: Instance, run_id: str, args) -> None:
    for option, kind in ((args.json, "result"), (args.junit, "junit"), (args.report, "report")):
        if not option:
            continue
        for _attempt in range(10):   # die Instanz schreibt die Dateien direkt nach dem Laufende
            try:
                content = instance.request("GET", f"/runs/{run_id}/{kind}", raw=True)
                target = Path(option)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                _say(f"Geschrieben: {option}")
                break
            except InstanceError as exc:
                if exc.code == "not_ready":
                    time.sleep(0.5)
                    continue
                _say(f"Konnte {option} nicht holen: {exc.code}")
                break
            except OSError as exc:
                _say(f"Konnte {option} nicht schreiben: {exc}")
                break


def _devices(settings) -> int:
    share = settings.share_config
    if not share.get("enabled"):
        _say("LabControl läuft bereits; " + HINTS["unreachable"])
        return EXIT_SETUP
    instance = Instance(int(share["port"]), share.get("token", ""))
    try:
        tiles = instance.request("GET", "/tiles").get("tiles", [])
    except InstanceError as exc:
        _say(f"Fehler: {exc.code}")
        return EXIT_SETUP
    for tile in tiles:
        _say(f"{tile.get('kind', ''):10s} {tile.get('id', ''):32s} {tile.get('label', '')}"
             + ("" if tile.get("online", True) else "  (getrennt)"))
    return 0

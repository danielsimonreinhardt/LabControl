"""Ein Programm mit LabControl koppeln (PI-41 c / PI-44 b): danach darf es mit seinem eigenen Schluessel steuern.

Ablauf wie die Kopplung zweier LabControl-Rechner:
  1. dieses Skript fragt an und zeigt einen 6-stelligen Code,
  2. LabControl zeigt "Programm <Name> (<Adresse>) möchte koppeln und steuern" mit demselben Code,
  3. dort bei gleichem Code "Code gleich – koppeln" tippen,
  4. das Skript bekommt den Schluessel (einmal) und gibt ihn aus bzw. speichert ihn mit --save.

Der Schluessel wird wie der Token benutzt: Kopfzeile "Authorization: Bearer <Schluessel>", z. B. als Token des
MCP-Servers (labcontrol_mcp) auf einem anderen Rechner. In LabControl unter Einstellungen -> Netzwerk ->
"Gekoppelte Programme" laesst sich die Kopplung jederzeit wieder aufheben.

Nur Standardbibliothek.  Aufruf:
    python tools/labcontrol_pair.py 192.168.178.181:8420 "Messskript Laptop" [--save schluessel.txt]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8420
TIMEOUT_S = 70.0   # LabControl laesst eine Anfrage 60 s offen


def base_url(address: str) -> str:
    text = address.strip().rstrip("/")
    if "://" not in text:
        text = "http://" + text
    host = text.split("://", 1)[1]
    if ":" not in host.rsplit("]", 1)[-1]:
        text += f":{DEFAULT_PORT}"
    return text


def call(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {}


def pair(address: str, name: str) -> str:
    """Koppelt und liefert den Schluessel; wirft RuntimeError mit Grund."""
    url = base_url(address)
    status, payload = call("POST", f"{url}/api/v1/pair", {"kind": "program", "name": name})
    if status != 200 or not payload.get("pair"):
        raise RuntimeError(f"Anfrage abgelehnt: {payload.get('error') or status}")
    code = str(payload.get("code", ""))
    print(f"Code: {code[:3]} {code[3:]}  -- in LabControl ({payload.get('host') or url}) bei gleichem Code "
          f"„Code gleich – koppeln“ tippen")
    pair_id = payload["pair"]
    end = time.monotonic() + TIMEOUT_S
    while time.monotonic() < end:
        time.sleep(1.0)
        status, state = call("GET", f"{url}/api/v1/pair/{pair_id}")
        if status != 200:
            raise RuntimeError(f"Anfrage nicht mehr bekannt ({state.get('error') or status})")
        if state.get("key"):
            return str(state["key"])
        if state.get("status") in ("rejected", "cancelled", "expired"):
            raise RuntimeError({"rejected": "in LabControl abgelehnt", "cancelled": "abgebrochen",
                                "expired": "abgelaufen"}[state["status"]])
    call("POST", f"{url}/api/v1/pair/{pair_id}/cancel", {})
    raise RuntimeError("keine Bestätigung in LabControl")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Programm mit LabControl koppeln (eigener Schlüssel zum Steuern)")
    parser.add_argument("address", help="Adresse von LabControl, z. B. 192.168.178.181:8420")
    parser.add_argument("name", help="Name des Programms, so steht es in LabControl (max. 24 Zeichen)")
    parser.add_argument("--save", type=Path, help="Schlüssel in diese Datei schreiben statt ihn anzuzeigen")
    args = parser.parse_args(argv)
    try:
        key = pair(args.address, args.name)
    except (RuntimeError, OSError) as exc:
        print(f"Kopplung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    if args.save:
        args.save.write_text(key + "\n", encoding="utf-8")
        print(f"Gekoppelt. Schlüssel in {args.save} (wie ein Passwort behandeln).")
    else:
        print(f"Gekoppelt. Schlüssel (wird nur jetzt angezeigt): {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

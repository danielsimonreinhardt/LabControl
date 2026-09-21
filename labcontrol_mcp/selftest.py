"""Prueft die Einrichtung des LabControl-MCP-Servers, OHNE etwas zu steuern.

    labcontrol_mcp\\.venv\\Scripts\\python.exe labcontrol_mcp\\selftest.py

Startet server.py so, wie ein MCP-Client es tut (ueber stdin/stdout), fragt die
Werkzeugliste, den Status und die freigegebenen Geraete ab und meldet, was noch
fehlt (LabControl nicht gestartet, Token falsch, Hauptschalter aus, ...).
Liest dieselben Umgebungsvariablen wie der Server: LABCONTROL_URL und
LABCONTROL_TOKEN.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

SERVER = Path(__file__).resolve().parent / "server.py"
EXPECTED_TOOLS = {"get_status", "list_devices", "read_device", "control_device", "all_off"}


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


async def main() -> int:
    problems: list[str] = []
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env={**os.environ})
    url = os.environ.get("LABCONTROL_URL", "http://127.0.0.1:8420")
    print(f"LabControl-Adresse: {url}")
    print(f"Token gesetzt:      {'ja' if os.environ.get('LABCONTROL_TOKEN') else 'NEIN'}")

    async with Client(params) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        print(f"Werkzeuge:          {', '.join(sorted(tools))}")
        if tools != EXPECTED_TOOLS:
            problems.append(f"Werkzeugliste weicht ab: {sorted(tools ^ EXPECTED_TOOLS)}")

        status = await client.call_tool("get_status", {})
        if status.is_error:
            print("\nget_status fehlgeschlagen:\n" + status.content[0].text)
            return 1
        info = _payload(status)
        remote = info.get("remote_control", {})
        print(f"\nLabControl {info.get('version')} | Sperre: {info.get('lock')} | "
              f"Watchdog: {info.get('safety')}")
        print(f"Fernsteuerung:      {'AKTIV, noch %.0f s' % remote.get('remaining_s', 0) if remote.get('active') else 'aus'}")
        print(f"Freigegebene Geraete: {info.get('tiles')}")

        devices = await client.call_tool("list_devices", {})
        if devices.is_error:
            print("\nlist_devices fehlgeschlagen:\n" + devices.content[0].text)
            return 1
        for tile in _payload(devices).get("tiles", []):
            state = "steuerbar" if tile.get("control_available") else (
                f"nicht steuerbar ({tile.get('control_blocked')})" if tile.get("control") else "nur lesbar")
            print(f"  {tile['id']:24s} {tile['label']:24s} online={tile['online']!s:5s} {state}")

    if not info.get("tiles"):
        problems.append("Keine Kachel freigegeben (Einstellungen -> Netzwerk -> 'Lesen').")
    if not remote.get("active"):
        problems.append("Fernsteuerung ist aus -- zum Steuern den Hauptschalter in LabControl einschalten.")
    print()
    if problems:
        print("Hinweise:")
        for problem in problems:
            print("  -", problem)
    else:
        print("Alles bereit.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

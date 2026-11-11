"""Geraete-IDs im Verbund Pi + PC (Stufe V2, PI-24 a).

Ein Testablauf kann Geraete beider Rechner nutzen. Ein Partnergeraet heisst im Ablauf
"@<Rechner>/<ID>", z. B. "@raspberrypi/psu:0001"; eigene Geraete behalten ihre ID ("fg:SIM").

Damit dieselbe Datei auf beiden Rechnern dasselbe meint:
  * Speichern mit aktivem Verbund: auch eigene Geraete bekommen den eigenen Rechnernamen
    (qualify_local) -- am anderen Rechner sind sie sonst nicht von dessen eigenen zu unterscheiden.
  * Laden/Ausfuehren (localize): "@<eigener Rechner>/x" -> "x"; "@<Partner>/x" bleibt; ein dritter
    Rechnername -> "x", falls x hier als eigenes Geraet bekannt ist (Geraet wurde umgesteckt),
    sonst bleibt die ID (Zeile zeigt "nicht verbunden").
Rechnernamen werden ohne Gross-/Kleinschreibung verglichen (Windows meldet sie gross).
Qt-frei.
"""
from __future__ import annotations

PREFIX = "@"


def qualify(host: str, device_id: str) -> str:
    return f"{PREFIX}{host}/{device_id}"


def split(device_id: str) -> tuple[str | None, str]:
    """"@pi/psu:1" -> ("pi", "psu:1"); "psu:1" -> (None, "psu:1")."""
    if device_id.startswith(PREFIX) and "/" in device_id:
        host, _, rest = device_id[len(PREFIX):].partition("/")
        if host and rest:
            return host, rest
    return None, device_id


def is_qualified(device_id: str) -> bool:
    return split(device_id)[0] is not None


def same_host(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.lower() == b.lower()


def qualify_local(device_id: str, own_host: str) -> str:
    """Eigenes Geraet ohne Rechnernamen -> mit eigenem Rechnernamen ("" bleibt "" = automatisch)."""
    if not device_id or is_qualified(device_id):
        return device_id
    return qualify(own_host, device_id)


def localize(device_id: str, own_host: str, partner_host: str, local_ids) -> str:
    host, rest = split(device_id)
    if host is None:
        return device_id
    if same_host(host, own_host):
        return rest
    if same_host(host, partner_host):
        return qualify(partner_host, rest)   # Schreibweise des Partners vereinheitlichen
    if rest in local_ids:
        return rest
    return device_id


def partner_device(device_id: str, partner_host: str) -> str | None:
    """ID beim Partner, wenn device_id ein Partnergeraet ist, sonst None."""
    host, rest = split(device_id)
    return rest if same_host(host, partner_host) else None

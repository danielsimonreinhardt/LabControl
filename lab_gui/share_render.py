"""Darstellung der freigegebenen Kacheln fuer schwache Clients (ESP32-Display).

Reine Formatierung: kein Qt, kein Zustand, keine Netzwerk-Logik -- rein
gehen die von share_api gebauten Kachel-Dicts, raus kommt ein String. Damit
laesst sich dieses Modul ohne QApplication und ohne offenen Socket pruefen.

Der Kniff fuer den ESP32 ist <meta http-equiv="refresh">: das Geraet braucht
weder JavaScript-Engine noch einen eigenen Timer -- es laedt die Seite, und
sie erneuert sich von selbst. Ein Web-UI mit Fetch-Schleife waere auf so
einem Client weder noetig noch bezahlbar.

WICHTIG -- CSS und HTML bleiben hier im Python-Code inline. Das ist kein
Schoenheitsfehler, sondern Absicht: sobald eine statische Datei dazukaeme,
braeuchte LabControl.spec einen datas-Eintrag, und der hat dort eine
dokumentierte Falle (der Zielpfad darf das "lab_gui/"-Praefix NICHT tragen,
siehe den Kommentarblock in LabControl.spec und i18n._TRANSLATIONS_DIR).
Solange alles hier steht, ist die .exe ohne jede Spec-Aenderung korrekt.
"""
from __future__ import annotations

import html

# Nachkommastellen je Einheit. Die Last liefert 3 Stellen bei V/A, Leistung
# ist mit 2 ausreichend genau, die microHIL-Rohwerte in mV/mA sind ganzzahlig
# gemeint. Bewusst nach Einheit statt nach Feldname: ein neues Feld mit
# bekannter Einheit wird damit automatisch richtig dargestellt.
_DECIMALS = {"V": 3, "A": 3, "W": 2, "mV": 0, "mA": 0}

# Grenze, ab der ein Wert auf dem Display als "steht" markiert wird. Bewusst
# groesser als live_state.STALE_TIMEOUT_S (2 s): der Watchdog soll frueh
# ausloesen, ein Display soll nicht bei jedem verpassten Poll-Zyklus rot
# blinken.
DISPLAY_STALE_S = 3.0

_STYLE = (
    "body{margin:0;background:#000;color:#e8e6e1;font:16px sans-serif}"
    "h1{margin:2px 6px;font-size:15px;color:#9a9a9a;font-weight:normal}"
    ".v{margin:0 6px;font-size:34px;line-height:1.05;font-variant-numeric:tabular-nums}"
    ".u{font-size:15px;color:#9a9a9a}"
    ".s{color:#c04040}"
    ".n{margin:2px 6px;font-size:13px;color:#9a9a9a}"
    "hr{border:0;border-top:1px solid #333;margin:8px 0}"
    "a{color:#8ab4f8}"
)

# Sperrzustand -> Anzeigetext. Bewusst nicht ueber i18n.tr: der Renderer
# laeuft im Server-Thread, und Translator ist ein QObject (siehe
# live_state.py). Das Display eines Laborgeraets bleibt deutsch.
_LOCK_TEXT = {
    "test_running": "Testlauf läuft",
    "safety_tripped": "Sicherheitsabschaltung",
}


def format_value(value, unit: str) -> str:
    """Zahl oder Text einheitlich als Anzeige-String."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        # Zaehler (tx_count) und Digitalzustaende bleiben ganzzahlig; ein int
        # mit bekannter Einheit (z.B. 12 V) bekommt trotzdem die uebliche
        # Nachkommastelle, damit die Anzeige nicht springt.
        return f"{value:.{_DECIMALS[unit]}f}" if unit in _DECIMALS else str(value)
    if isinstance(value, float):
        # Unbekannte Einheit: 3 Stellen statt 0, sonst wuerde ein neues
        # Messfeld stillschweigend auf ganze Zahlen gerundet.
        return f"{value:.{_DECIMALS.get(unit, 3)}f}"
    return str(value)


def _field_line(field: dict, stale: bool) -> str:
    """Eine Wertezeile. Numerische Felder tragen "value", Text-/Enum-Felder
    "text" -- nie beides (siehe share_api._tile_detail)."""
    cls = "v s" if stale else "v"
    if "text" in field:
        return f'<div class="{cls}">{html.escape(str(field["text"]))}</div>'
    shown = html.escape(format_value(field.get("value"), field.get("unit", "")))
    unit = field.get("unit", "")
    suffix = f'<span class="u"> {html.escape(unit)}</span>' if unit else ""
    return f'<div class="{cls}">{shown}{suffix}</div>'


def _tile_block(tile: dict) -> str:
    stale = bool(tile.get("stale")) or tile.get("age_s", 0.0) > DISPLAY_STALE_S
    offline = not tile.get("online", True)
    title = html.escape(str(tile.get("label", tile.get("id", ""))))
    if offline:
        title += " — getrennt"
    elif stale:
        title += " — veraltet"
    parts = [f"<h1>{title}</h1>"]
    parts += [_field_line(f, stale or offline) for f in tile.get("fields", [])]
    return "".join(parts)


def render_display(tiles: list[dict], refresh_s: int, lock: str = "free") -> str:
    """Kompakte HTML-Seite fuer ein externes Display.

    refresh_s=0 laesst das Meta-Refresh weg -- fuer einen Client, der selbst
    pollt und die Seite nur einmal rendert.
    """
    title = tiles[0].get("label", tiles[0].get("id", "")) if len(tiles) == 1 else "LabControl"
    refresh = f'<meta http-equiv="refresh" content="{int(refresh_s)}">' if refresh_s > 0 else ""
    body = '<hr>'.join(_tile_block(t) for t in tiles) or "<h1>Keine Kachel freigegeben</h1>"
    note = ""
    if lock in _LOCK_TEXT:
        note = f'<div class="n">{html.escape(_LOCK_TEXT[lock])}</div>'
    return (
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'{refresh}<title>{html.escape(str(title))}</title>'
        f'<style>{_STYLE}</style></head><body>{body}{note}</body></html>'
    )


def render_text(tiles: list[dict], lock: str = "free") -> str:
    """key=value-Zeilen -- das Billigste, was ein ESP32 verarbeiten kann.

    Kein JSON-Parser, kein HTML-Parser: eine Zeile lesen, am ersten "="
    trennen, fertig.
    """
    lines: list[str] = [f"lock={lock}"]
    for tile in tiles:
        lines.append(f"tile={tile['id']}")
        lines.append(f"label={tile.get('label', '')}")
        lines.append(f"online={1 if tile.get('online', True) else 0}")
        lines.append(f"age_s={tile.get('age_s', 0.0)}")
        for field in tile.get("fields", []):
            if "text" in field:
                lines.append(f"{field['key']}={field['text']}")
            else:
                unit = field.get("unit", "")
                shown = format_value(field.get("value"), unit)
                lines.append(f"{field['key']}={shown}{(' ' + unit) if unit else ''}")
    return "\n".join(lines) + "\n"


def render_index(tiles: list[dict], base_url: str) -> str:
    """Winzige Einrichtungsseite: welche Kacheln sind freigegeben und unter
    welchen Adressen erreichbar.

    Zweck ist die Einrichtung des ESP32 -- man oeffnet die Seite einmal im
    Browser und kopiert die fertige URL in den Sketch, statt sie aus der
    Doku zusammenzusetzen.
    """
    if not tiles:
        rows = "<p>Es ist keine Kachel freigegeben. Einstellungen &rarr; Netzwerk.</p>"
    else:
        items = []
        for tile in tiles:
            tid = html.escape(tile["id"])
            label = html.escape(str(tile.get("label", tile["id"])))
            items.append(
                f"<li><b>{label}</b> <code>{tid}</code><br>"
                f'<a href="/display?tile={tid}">/display?tile={tid}</a><br>'
                f'<a href="/api/v1/tiles/{tid}">/api/v1/tiles/{tid}</a></li>'
            )
        rows = "<ul>" + "".join(items) + "</ul>"
    return (
        '<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>LabControl</title><style>{_STYLE}li{{margin:8px 6px}}</style>'
        f'</head><body><h1>LabControl &mdash; freigegebene Kacheln</h1>{rows}'
        f'<p class="n">Basis: {html.escape(base_url)}</p></body></html>'
    )

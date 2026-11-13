"""Schluessel fuer SecurityAccess (FT-1, 0.60.0, FP-11 b).

Verfahren wie neXtBSW (Anwendungs-Entsperrung, Methode "hmac-sha256"):
  Geraeteschluessel = HMAC-SHA256(Master, UID)              UID aus DID 0xF18C (16 Byte)
  Schluessel        = HMAC-SHA256(Geraeteschluessel, b"NXAK" + Stufe + Seed)[:key_bytes]
Der Master-Schluessel liegt als Datei im gemeinsamen Ordner (Entscheidung FP-11 b): <gemeinsam>/keys/<name>.key,
64 Hex-Zeichen, Kommentarzeilen mit #. Er wird nie geloggt, nie in Einstellungen oder Ablaeufen gespeichert.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

KEY_DIR = "keys"
APP_KEY_TAG = b"NXAK"


class KeyError_(Exception):
    """Schluesseldatei fehlt oder ist ungueltig (Name ohne Kollision mit dem eingebauten KeyError)."""


def parse_key(text: str) -> bytes:
    hexdigits = "".join(line.split("#", 1)[0].strip() for line in text.splitlines())
    hexdigits = re.sub(r"\s+", "", hexdigits)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", hexdigits):
        raise KeyError_("Schlüsseldatei: erwartet 64 Hex-Zeichen (32 Byte)")
    return bytes.fromhex(hexdigits)


def key_path(shared_root: Path, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name or ""):
        raise KeyError_(f"Ungültiger Schlüsselname '{name}'")
    return Path(shared_root) / KEY_DIR / f"{name}.key"


def load_master_key(shared_root: Path, name: str) -> bytes:
    path = key_path(shared_root, name)
    try:
        return parse_key(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KeyError_(f"Schlüssel '{name}' nicht gefunden ({path})") from None
    except OSError as exc:
        raise KeyError_(f"Schlüssel '{name}' nicht lesbar: {exc}") from None


def device_key(master: bytes, uid: bytes) -> bytes:
    return hmac.new(master, uid, hashlib.sha256).digest()


def app_key(device: bytes, level: int, seed: bytes, key_bytes: int) -> bytes:
    return hmac.new(device, APP_KEY_TAG + bytes([level & 0xFF]) + seed, hashlib.sha256).digest()[:key_bytes]

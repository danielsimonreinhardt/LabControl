"""Prueft die Uebersetzungen (PI-64 a, 1.0.0): jeder Text, den lab_gui/*.py mit tr("...") anzeigt, hat einen
Eintrag in translations/en.json, und die englische Fassung hat dieselben {Platzhalter}. (de.json muss nicht
vollstaendig sein: der deutsche Text ist selbst der Schluessel, siehe translations/README.md.)

Rein statisch (ast), keine Qt-Anwendung.  Aufruf:  python tools/check_translations.py
"""
from __future__ import annotations

import ast
import json
import re
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "lab_gui"


def placeholders(text: str) -> set[str]:
    try:
        return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name is not None}
    except ValueError:
        return {m.group(1) for m in re.finditer(r"\{([^{}:!]*)", text)}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    en = json.loads((GUI / "translations" / "en.json").read_text(encoding="utf-8"))
    keys: dict[str, str] = {}
    for path in sorted(GUI.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                keys.setdefault(node.args[0].value, path.name)
    missing_en = [(f, k) for k, f in keys.items() if k not in en]
    bad = [(keys[k], k) for k in keys if k in en and placeholders(k) != placeholders(en[k])]
    failures = 0
    for title, items in (("ohne englische Uebersetzung", missing_en), ("Platzhalter weichen ab", bad)):
        ok = not items
        failures += not ok
        print(f"  {'OK  ' if ok else 'FEHL'} {len(keys)} Texte, {title}: {len(items)}")
        for file, key in items[:15]:
            print(f"         {file}: {key[:90]!r}")
    print(f"\n{'ALLES OK' if not failures else f'{failures} FEHLER'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""Oszilloskop-Kachel: ein getrenntes Geraet zeigt "Nicht verbunden", nicht "Belegt (andere App?)".

Fehlerbild (1.0.2 gemeldet): PicoScope und UNI-T waren abgesteckt, die Kachel zeigte "Belegt", weil
set_online(False) den Status auf "busy" setzte. Nur Oberflaeche (offscreen), keine Hardware.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    QApplication.instance() or QApplication([])
    from i18n import Translator
    from picoscope_panel import PicoscopePanel
    from control_tab import PicoscopeControlGroup

    for kind, device_id, with_launch in (("picoscope", "picoscope:default", True),
                                         ("scpiscope", "scpiscope:1.2.3.4", False)):
        print(f"Uebersicht: {kind}")
        panel = PicoscopePanel(device_id, "Scope", with_launch=with_launch)
        check("vor dem ersten Zustand: Nicht verbunden", panel._status_text.text() == "Nicht verbunden",
              panel._status_text.text())
        panel.set_online(True)
        panel.set_state("free", "MSO1154HD-S", "SN1")
        check("verbunden und frei: Frei", panel._status_text.text() == "Frei", panel._status_text.text())
        panel.set_state("mcp", "", "")
        panel.set_online(False)
        check("getrennt: Nicht verbunden (nicht Belegt)", panel._status_text.text() == "Nicht verbunden",
              panel._status_text.text())
        check("getrennt: Trennen-Knopf weg", not panel._release_button.isVisibleTo(panel))
        panel.set_online(True)
        panel.set_state("busy", "", "")
        check("wieder da, von anderer App belegt: Belegt (andere App?)",
              panel._status_text.text() == "Belegt (andere App?)", panel._status_text.text())

    print("Steuerung: Anfangszustand")
    group = PicoscopeControlGroup("scpiscope:1.2.3.4", "Scope", "scpiscope")
    check("vor dem ersten Zustand: Nicht verbunden", group._status_label.text() == "Nicht verbunden",
          group._status_label.text())

    print("Englisch")
    Translator.instance().set_language("en")
    panel = PicoscopePanel("scpiscope:1.2.3.4", "Scope", with_launch=False)
    panel.set_online(False)
    check("getrennt: Not connected", panel._status_text.text() == "Not connected", panel._status_text.text())
    panel.set_online(True)
    panel.set_state("free", "", "")
    check("frei: Free", panel._status_text.text() == "Free", panel._status_text.text())
    Translator.instance().set_language("de")

    print()
    if FAILURES:
        print("FEHLGESCHLAGEN:", FAILURES)
        return 1
    print("Alles ok. (nur Oberflaeche, keine Hardware)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

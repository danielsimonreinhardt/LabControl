"""Benutzerhandbuch-Dialog (FEATURES.md Punkt 3): zeigt eine mitgelieferte
Markdown-Anleitung in einem QTextBrowser an, statt sie extern (Browser/PDF-
Reader) zu oeffnen -- funktioniert dadurch identisch im Dev-Betrieb und in
der PyInstaller-.exe, ohne Registrierung eines externen Handlers.

Der Inhalt liegt (wie translations/icons, siehe i18n.py/theme.py) als
mitgelieferte Datei je Sprache vor (manual_<code>.md), nicht ueber das
tr()-Schluessel/Wert-System -- ein zusammenhaengender Fliesstext eignet sich
nicht fuer die satzweise Uebersetzungstabelle. Fehlt eine Uebersetzung fuer
die aktuelle Sprache, faellt die Anzeige auf Deutsch zurueck.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

from i18n import Translator, tr

# Gleiches MEIPASS-Muster wie i18n._TRANSLATIONS_DIR/theme._ICONS_DIR -- im
# PyInstaller-Onefile-Build liegen mitgelieferte Daten unter sys._MEIPASS
# statt neben dieser Datei (siehe LabControl.spec: datas).
_HELP_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent) / "help"

_FALLBACK_LANGUAGE = "de"


def _manual_text(language: str) -> str:
    codes = dict.fromkeys((language, _FALLBACK_LANGUAGE))  # Reihenfolge erhalten, Duplikat entfernen
    for code in codes:
        path = _HELP_DIR / f"manual_{code}.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    # Sollte nur bei einem fehlerhaften Build vorkommen (siehe LabControl.spec:
    # datas -- 'lab_gui/help' muss mitgebuendelt sein, ein alter build/-Cache
    # kann das beim erneuten Bauen unterschlagen, siehe lab_gui/README.md).
    # Statt einer stillschweigend leeren Flaeche eine erklaerende Meldung samt
    # gesuchtem Pfad, damit ein fehlender Build sofort erkennbar ist statt wie
    # ein Anzeigefehler auszusehen.
    searched = ", ".join(f"`manual_{code}.md`" for code in codes)
    return (
        "# Benutzerhandbuch nicht gefunden\n\n"
        f"Es wurde keine Anleitungsdatei unter `{_HELP_DIR}` gefunden "
        f"(gesucht: {searched}).\n\n"
        "Vermutlich wurde die `.exe` mit einem veralteten PyInstaller-"
        "`build/`-Verzeichnis erstellt, bevor `lab_gui/help/` als Daten-"
        "Ordner existierte. Abhilfe: `build/` und `dist/` löschen und "
        "`pyinstaller LabControl.spec` erneut ausführen."
    )


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.resize(820, 720)

        layout = QVBoxLayout(self)
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setMarkdown(_manual_text(Translator.instance().language))
        layout.addWidget(self._browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setWindowTitle(tr("Benutzerhandbuch"))

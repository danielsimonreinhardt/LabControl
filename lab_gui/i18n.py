"""Einfache JSON-basierte Uebersetzungsverwaltung fuer die GUI.

Sprachdateien liegen unter lab_gui/translations/<code>.json als flache
Schluessel-Wert-Paare, wobei der deutsche Originaltext selbst als Schluessel
dient (siehe de.json). tr(text) liefert die Uebersetzung fuer die aktuell
aktive Sprache und faellt bei fehlendem Schluessel (noch nicht uebersetzter
String, oder die Sprache ist Deutsch) auf den Originaltext zurueck --
zusaetzliche Stellen lassen sich daher schrittweise auf tr() umstellen, ohne
dass ihnen sofort eine Uebersetzung fehlen darf.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# Wie bei den Icons (siehe theme.py: _ICONS_DIR) liegen die mitgelieferten
# Sprachdateien im PyInstaller-Onefile-Build unter sys._MEIPASS statt neben
# dieser Datei -- sie muessen beim Bauen per --add-data mitgegeben werden.
_TRANSLATIONS_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent) / "translations"

# Anzeigename in der jeweils eigenen Sprache (nicht uebersetzt) -- so findet
# man seine Sprache im Dropdown auch dann, wenn die GUI gerade in einer
# anderen Sprache angezeigt wird.
AVAILABLE_LANGUAGES: dict[str, str] = {
    "de": "Deutsch",
    "en": "English",
}
DEFAULT_LANGUAGE = "de"


class Translator(QObject):
    """Singleton (siehe instance()), analog zu ThemeManager in theme.py."""

    language_changed = Signal(str)

    _instance: "Translator | None" = None

    def __init__(self) -> None:
        super().__init__()
        self._language = DEFAULT_LANGUAGE
        self._strings: dict[str, str] = {}
        self._load(self._language)

    @classmethod
    def instance(cls) -> "Translator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load(self, language: str) -> None:
        path = _TRANSLATIONS_DIR / f"{language}.json"
        try:
            self._strings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._strings = {}

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        if language == self._language or language not in AVAILABLE_LANGUAGES:
            return
        self._language = language
        self._load(language)
        self.language_changed.emit(language)

    def tr(self, text: str, **kwargs: object) -> str:
        translated = self._strings.get(text, text)
        if kwargs:
            try:
                return translated.format(**kwargs)
            except (KeyError, IndexError):
                return translated
        return translated


def tr(text: str, **kwargs: object) -> str:
    return Translator.instance().tr(text, **kwargs)

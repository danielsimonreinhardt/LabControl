"""Rotierendes Datei-Logging fuer die Labor-App.

Die gebaute .exe laeuft mit --windowed (siehe .github/workflows/build-exe.yml),
stdout/stderr gehen also ins Leere. Ohne eigenes Logging waere ein
Sicherheitsabbruch (siehe safety.py) waehrend eines unbeaufsichtigten
Uebernacht-Laufs im Nachhinein nicht mehr nachvollziehbar.
"""
from __future__ import annotations

import logging
import logging.handlers

from paths import app_dir

LOG_PATH = app_dir() / "labdash.log"


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    try:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        # Schreibgeschuetztes Verzeichnis o.ae. -- App soll trotzdem starten.
        handler = logging.NullHandler()
    else:
        handler.setFormatter(formatter)
    root.addHandler(handler)

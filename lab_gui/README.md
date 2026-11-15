# lab_gui – die LabControl-Anwendung

PySide6-Anwendung: Hauptfenster, Testabläufe, Geräte-Worker, Prüfling-Dienste (UDS, XCP, canflash, Restbus),
REST-Freigabe und Kommandozeile. Die Gerätetreiber liegen in [`../device-driver/`](../device-driver/).

Architektur, Verzeichnisse, Prüfungen und Konventionen: [Doku „Für Entwickler“](../docs/de/entwickler.md).
Bedienung: [Dokumentation](../docs/de/index.md).

## Start

```
python main.py            # Fenster
python main.py run …      # ohne Fenster, siehe Doku „Automatisierung“
```

## Version

Die Versionsnummer steht zentral in `version.py` (`__version__`). Fenstertitel, `LabControl.spec` und der
Workflow `.github/workflows/build-exe.yml` lesen sie von dort. Bei jeder Änderung hochzählen und im
`CHANGELOG.md` eintragen.

## Als .exe bauen (Windows)

```
pip install -r ../requirements.txt
cd ..
pyinstaller LabControl.spec
```

Der Befehl läuft im Repo-Hauptordner (nicht in `lab_gui/`). Ergebnis: `dist/LabControl_v<version>.exe` (eine
Datei, kein Konsolenfenster, Splash beim Start). `LabControl.spec` bündelt Icons und Übersetzungen und blendet
ungenutzte PySide6-Module aus; der CI-Workflow baut mit derselben Datei nach jedem Push auf `master` und
veröffentlicht das Release „latest“. `build/` und `dist/` sind nicht versioniert.

# Labor-Steuerung GUI

PySide6-Oberfläche für die elektronische Last ([korad_kel102](../korad_kel102/))
und das Labornetzteil ([hcs34xx](../hcs34xx/)).

## Start

```
python main.py
```

## Version

Die Versionsnummer steht zentral in `lab_gui/version.py` (`__version__`) und
wird im Fenstertitel angezeigt. Bei jedem Release dort hochzählen und beim
.exe-Bau unten im `--name` mit übernehmen.

## Als .exe bauen (Windows)

```
pip install -r ../requirements.txt
cd ..
pyinstaller --name LaborSteuerung-0.1.1 --paths . --onefile --windowed lab_gui/main.py
```

Wichtig: Der Befehl muss aus `labor-dashboard/` (nicht aus `lab_gui/`) laufen, da
`--paths .` PyInstaller sagt, wo es die Pakete `korad_kel102` und `hcs34xx`
findet. Ergebnis liegt danach in `dist/LaborSteuerung-0.1.1.exe` (einzelne
Datei, kein Konsolenfenster). Gespeicherte Testabläufe landen automatisch
neben der .exe in `dist/testcases/` (nicht im flüchtigen
PyInstaller-Temp-Verzeichnis).

`build/`, `dist/` und `*.spec` sind in `.gitignore` – bei Bedarf neu bauen
statt die .exe zu versionieren.

## Aufbau

- **Dashboard** (immer sichtbar): aktuelle Spannung/Strom/Leistung der Last,
  Spannung/Strom/Modus des Netzteils. Wird alle 500 ms live aktualisiert.
- **Reiter „Control“**: Eingabemasken für die wichtigsten Funktionen
  (Last: Modus + Sollwert + Ausgang Ein/Aus; Netzteil: Spannung/Strom +
  Ausgang Ein/Aus-Workaround (siehe unten) + OVP/OCP + Presets P1–P3).
- **Reiter „Testcase“**: zeilenbasierter Editor für Testabläufe (Gerät,
  Aktion, Wert, Dauer, Aktiv je Zeile), inkl. Speichern/Laden als JSON
  (`testcases/`-Ordner) und Start/Stop zur sequenziellen Ausführung mit
  Wartezeit je Schritt. Während der Ausführung ist die Tabelle gesperrt,
  der aktuelle Schritt wird markiert und in der Statuszeile angezeigt.
  Das Wert-Feld passt Einheit/Min/Max automatisch an die gewählte Aktion an
  (z.B. Netzteil-Spannung nur 1–60V, siehe `hcs34xx/driver.py`: `MIN_VOLTAGE`).
- **Ausgang Ein/Aus-Workaround fürs Netzteil**: Das HCS-34xx hat kein
  Software-Ausgang-Ein/Aus (siehe `hcs34xx/README.md`). „Aus“ setzt den
  Strom auf 0A; „Ein“ übernimmt die Spannung und hebt den Strom auf
  mindestens 0,1A an, statt einen ggf. schon höher konfigurierten Wert zu
  überschreiben. Im Control-Tab nutzt „Ein“ die aktuell im Formular
  eingetragenen Sollwerte; im Testcase-Editor nutzt „Ausgang EIN
  (Workaround)“ den Wert der Zeile als Spannung und liest den zuletzt vom
  Gerät gespeicherten Stromsollwert.
- **Statusleiste**: USB-Verbindungsstatus beider Geräte (rot = getrennt,
  grün = verbunden). Verbindung wird automatisch alle 3 s neu versucht,
  falls ein Gerät (noch) nicht erreichbar ist.

## Architektur

Die gesamte Seriell-Kommunikation läuft in einem eigenen `QThread`
(`device_worker.py`), damit ein Verbindungsabbruch oder Timeout die GUI
nicht blockiert. Die GUI kommuniziert mit dem Worker ausschließlich über
Qt-Signale/Slots.

Die Testablauf-Ausführung (`testcase_runner.py`) läuft im GUI-Thread und
steuert die Schrittfolge per `QTimer` (nicht-blockierend), sendet die
eigentlichen Befehle aber per Signal an `DeviceWorker.execute_action()`
im Worker-Thread. Das Datenmodell (`testcase_model.py`, `TestStep`) ist
von der UI getrennt und JSON-serialisierbar.

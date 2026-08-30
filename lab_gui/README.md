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
pyinstaller --name LaborSteuerung-0.2.0 --paths . --add-data "lab_gui/icons;lab_gui/icons" --onefile --windowed lab_gui/main.py
```

Wichtig: Der Befehl muss aus `labor-dashboard/` (nicht aus `lab_gui/`) laufen, da
`--paths .` PyInstaller sagt, wo es die Pakete `korad_kel102` und `hcs34xx`
findet. `--add-data "lab_gui/icons;lab_gui/icons"` bündelt die Spinbox-Pfeil-
Icons (theme.py) mit ins .exe -- ohne das fehlen sie im Onefile-Build (kein
Absturz, nur optisch). Ergebnis liegt danach in `dist/LaborSteuerung-0.2.0.exe`
(einzelne Datei, kein Konsolenfenster). Gespeicherte Testabläufe landen
automatisch neben der .exe in `dist/testcases/` (nicht im flüchtigen
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
  - **Ablaufsteuerung**: über das „+“-Menü lassen sich neben Aktionsschritten
    auch Zählschleifen („Schleife n×“, z.B. für Lade-/Entlade-Zyklen bei
    Akku-Tests), While-Schleifen, If/Else-Verzweigungen und Laufvariablen
    (Variable setzen/erhöhen) einfügen, beliebig verschachtelbar. Block-Start
    und `Ende` werden als Paar eingefügt; der Editor zeigt die Verschachtelung
    als Einrückung und sperrt den Start-Button bei unausbalancierter Struktur
    (siehe `testcase_model.validate_structure`).
  - **Bedingungen** (While/If, `condition_dialog.py`) vergleichen einen
    Live-Messwert (Spannung/Strom/Leistung, Gerät automatisch oder gezielt),
    die verstrichene Zeit (seit Blockstart/Teststart) oder eine Laufvariable
    gegen einen Wert. Der Runner cached dafür die zuletzt empfangenen
    Messwerte der Geräte und lässt eine Bedingung bei fehlender/veralteter
    Messung bewusst fehlschlagen, statt mit einem stehengebliebenen Wert
    weiterzurechnen. While-Schleifen haben eine einstellbare
    Endlosschleifen-Bremse ("Max. Durchläufe").
  - **Pass/Fail-Grenzwerte** (Spalte „Prüfung“, `check_dialog.py`): jeder
    Aktionsschritt kann optional einen erwarteten Bereich [Min, Max] einer
    Messgröße (Spannung/Strom/Leistung des Schritt-Geräts) bekommen. Nach
    Ablauf der Wartezeit (bzw. nach dem Signalende eines Arbiträrsignals)
    bewertet der Runner die **erste danach eintreffende** Messung — nicht den
    bis zu 500 ms alten Cache-Stand — und färbt die Zeile dauerhaft grün/rot
    (in Schleifen „sticky“: einmal rot bleibt rot); der Messwert steht als
    Tooltip an der Prüfzelle. Pro Schritt wählbar bricht eine Verletzung den
    Lauf ab (wie ein Gerätefehler) oder der Test läuft durch und die
    Statuszeile meldet am Ende „BESTANDEN“/„NICHT bestanden“ mit Zähler.
    Bleibt die Messung aus (Gerät tot/getrennt), schlägt der Schritt fehl.
  - Testablauf-Dateien liegen seit v0.4.0 im Format v2 vor (JSON-Objekt mit
    `version`-Feld statt eines nackten Arrays); ältere v1-Dateien werden
    weiterhin geladen, v2-Dateien lassen sich aber nicht mit älteren
    Programmversionen öffnen.
- **Ausgang Ein/Aus-Workaround fürs Netzteil**: Das HCS-34xx hat kein
  Software-Ausgang-Ein/Aus (siehe `hcs34xx/README.md`). „Aus“ setzt den
  Strom auf 0A; „Ein“ übernimmt die Spannung und hebt den Strom auf
  mindestens 0,1A an, statt einen ggf. schon höher konfigurierten Wert zu
  überschreiben. Im Control-Tab nutzt „Ein“ die aktuell im Formular
  eingetragenen Sollwerte; im Testcase-Editor nutzt „Ausgang EIN
  (Workaround)“ den Wert der Zeile als Spannung und liest den zuletzt vom
  Gerät gespeicherten Stromsollwert.
- **Reiter „Verlauf“**: fortlaufende Oszilloskop-Ansicht aller Geräte-
  Messwerte in mehreren, per Button hinzufügbaren Diagrammen (gemeinsame
  Y-Achse je Einheit, eigenes QPainter-Rendering). Oberhalb der Diagramme
  sitzt die Aufzeichnung: Start/Stop eines Messwert-Logs über alle bekannten
  Geräte (Zeitstempel, Gerät, Kanal, Wert), unabhängig vom zeitfenster-
  gedeckelten Ringpuffer der Diagramme selbst. Export als CSV (Long-Format)
  oder MF4 (ASAM MDF4, ein Signal je Gerät+Kanal, benötigt das Paket
  `asammdf`), auch bei laufender Aufnahme möglich. Der „Anzeige
  zurücksetzen“-Button der Diagramm-Steuerung leert nur die Ringpuffer der
  Live-Ansicht — unabhängig vom separaten Aufzeichnung-Reset.
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

Für Bedingungen (While/If) hängt sich `TestRunner` zusätzlich direkt an
`DeviceWorker.load_measurement`/`psu_measurement` (dieselben Signale wie
Timeline-Tab und Recorder) und pflegt einen kleinen Cache der zuletzt
empfangenen Messwerte je Gerät. Jeder Rücksprung am Ende einer
Schleifeniteration läuft über einen 0ms-`QTimer` statt direkt weiterzuspringen,
damit die Event-Loop zwischen den Iterationen atmen kann — sonst kämen bei
einer eng getakteten While-Schleife weder GUI-Updates noch die für die
nächste Bedingungsauswertung nötigen Messwert-Signale an.

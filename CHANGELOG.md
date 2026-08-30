# Changelog

Alle nennenswerten Änderungen an der Labor-Steuerungs-App. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/), Versionierung nach
Semantic Versioning (`lab_gui/version.py`).

## [0.5.0]

### Hinzugefügt
- **Pass/Fail-Grenzwerte pro Testschritt**: Jeder Aktionsschritt kann optional
  einen erwarteten Wertebereich für eine Messgröße (Spannung/Strom/Leistung
  des Schritt-Geräts) bekommen, z.B. „Spannung muss 11,8–12,2 V sein". Neue
  Spalte „Prüfung" im Testcase-Editor mit Kurzzusammenfassung („U: 11.8–12.2 V")
  und Dialog (`lab_gui/check_dialog.py`, analog zum Bedingungs-Dialog; sperrt
  OK bei Minimum > Maximum). Der Runner bewertet **die erste Messung nach
  Ablauf der Wartezeit** des Schritts (bzw. nach dem Signalende eines
  Arbiträrsignal-Schritts) — bewusst nicht den bis zu 500 ms alten
  Cache-Stand, der bei kurzer Wartezeit noch von vor dem Sollwert stammen
  könnte. Bleibt die Messung aus (Gerät tot/getrennt), schlägt der Schritt
  fehl (fail-fast wie bei Bedingungen).
- Ergebnisanzeige: bestandene Schritte werden dauerhaft grün, fehlgeschlagene
  rot markiert (in Schleifen „sticky": einmal rot bleibt rot); der Messwert
  steht als Tooltip an der Prüfzelle. Die Statuszeile zeigt am Ende
  „Fertig – BESTANDEN (n Prüfungen)" bzw. „Fertig – NICHT bestanden (k/n
  Prüfungen fehlgeschlagen)"; die Farben bleiben zur Inspektion stehen, bis
  der nächste Lauf startet. Pro Schritt wählbar: „Bei Verletzung abbrechen"
  stoppt den Lauf sofort wie ein Gerätefehler (Quittieren über Stop),
  ansonsten läuft der Test durch und sammelt alle Ergebnisse.
- Deaktivierte Schritte werden weiterhin übersprungen und zählen nicht als
  Prüfung; die Prüfungszähler zählen jede Ausführung (ein Prüfschritt in
  einer 10er-Schleife = 10 Prüfungen).

### Geändert
- Version auf 0.5.0 angehoben. Testablauf-Dateiformat bleibt v2; Dateien ohne
  Prüfungen sind unverändert kompatibel, die neuen `check_*`-Felder werden
  beim Laden älterer Dateien mit Defaults aufgefüllt.

## [0.4.0]

### Hinzugefügt
- **Ablaufsteuerung im Testcase-Editor**: Schleifen, If/Else-Verzweigungen,
  While-Schleifen und einfache Laufvariablen, beliebig verschachtelbar —
  z.B. „wiederhole Schritte 3–7 zehnmal“ für Lade-/Entlade-Zyklen bei
  Akku-Tests, oder „entlade solange Spannung > 3,0 V“. Neuer Zeilentyp-Dropdown
  über das „+“-Menü im Testcase-Tab (Aktionsschritt/Schleife/Solange/Wenn/
  Sonst/Ende/Variable setzen/Variable erhöhen); Block-Start und -Ende werden
  als Paar eingefügt, verschachtelte Bloecke werden im Editor eingerückt
  dargestellt. Der Start-Button wird gesperrt, solange die Blockstruktur
  nicht ausbalanciert ist (fehlendes „Ende“, „Sonst“ ohne „Wenn“, …).
- **Bedingungen** (`lab_gui/condition_dialog.py`, neuer Dialog analog zu
  `signal_dialog.py`) können sich auf einen Live-Messwert (Spannung/Strom/
  Leistung eines Geräts, automatisch oder gezielt ausgewählt), die
  verstrichene Zeit (seit Blockstart oder seit Teststart) oder eine
  Laufvariable beziehen. Der Testrunner (`testcase_runner.py`) cached dafür
  neu die zuletzt empfangenen Messwerte (`DeviceWorker.load_measurement`/
  `psu_measurement`) und lässt den Ablauf bei einer veralteten oder fehlenden
  Messung bewusst fehlschlagen (fail-fast), statt mit einem stehengebliebenen
  Wert weiterzurechnen. While-Schleifen haben eine konfigurierbare
  Endlosschleifen-Bremse („Max. Durchläufe“, Default 1000).
- Neues Testablauf-Dateiformat v2 (`{"format": "labor-testcase", "version": 2,
  "steps": [...]}` statt eines nackten Arrays) für die zusätzlichen Felder;
  alte v1-Dateien werden weiterhin geladen. **v2-Dateien lassen sich nicht
  mit älteren Programmversionen öffnen** (klare Fehlermeldung statt Absturz).
- **Simulationsmodus simuliert jetzt auch eine elektronische Last**
  (`korad_kel102/mock.py`, `MockKoradKEL102`, analog zur bestehenden
  simulierten PSU) — damit lässt sich der Haupt-Anwendungsfall der neuen
  Bedingungen („entlade solange Spannung > 3,0 V“) ohne angeschlossene
  Hardware durchspielen.

### Geändert
- Version auf 0.4.0 angehoben.

## [0.3.0]

### Hinzugefügt
- **Neuer „Aufzeichnung“-Tab**: Start/Stop-Messwert-Logging für alle bekannten
  Geräte (Zeitstempel, Geräte-ID, Kanal, Wert), unabhängig vom Timeline-Tab
  und ohne dessen 30-Minuten-Ringpuffer-Deckelung. Neues Modul
  `lab_gui/recording.py` (`Recorder`) hängt sich dafür — analog zum
  Timeline-Tab — direkt an `DeviceWorker.load_measurement`/`psu_measurement`,
  sammelt aber nur, während eine Aufnahme aktiv ist.
- **Export als CSV und MF4** (`lab_gui/recording_export.py`): CSV im
  Long-Format (eine Zeile je Messwert), MF4 (ASAM MDF4, über die neue
  Abhängigkeit `asammdf`) mit einem Signal je Gerät+Kanal und eigenem
  Zeitvektor — beides ohne Resampling, auch bei unregelmäßig eintreffenden
  Messwerten (z.B. nach einem kurzen Verbindungsabbruch) verlustfrei. Export
  ist auch bei laufender Aufnahme möglich (liefert den Zwischenstand). Fehlt
  `asammdf`, bleibt der CSV-Export unabhängig davon nutzbar; der MF4-Export
  meldet den fehlenden Import als normale Fehlermeldung statt abzustürzen.

### Geändert
- Version auf 0.3.0 angehoben.

## [0.2.1]

### Hinzugefügt
- **Mehrsprachigkeit** (Deutsch/Englisch): JSON-basiertes i18n-System
  (`lab_gui/i18n.py`, `lab_gui/translations/{de,en}.json`) mit dem deutschen
  Originaltext als Schlüssel, damit noch nicht übersetzte Stellen automatisch
  auf Deutsch zurückfallen statt zu fehlen. Sprachwahl im Settings-Tab, wirkt
  sofort ohne Neustart; Control-, Dashboard-, Testcase-Tab und Signal-Dialog
  sind umgestellt.
- **Neuer „Timeline“-Tab**: fortlaufende Oszilloskop-Ansicht aller
  Geräte-Messwerte in Ringpuffern, mehrere per Button hinzufügbare Diagramme
  mit gemeinsamer Y-Achse je Einheit, eigenes QPainter-Rendering (keine
  externe Plot-Bibliothek nötig). Layout wird lokal gespeichert
  (`lab_gui/timeline_layout.json`, nicht versioniert).
- **OVP/OCP-Schwellenüberwachung**: Das Netzteil meldet seine aktuellen
  Schutzschwellen aktiv (neues `DeviceWorker`-Signal `psu_limits`);
  Control- und Testcase-Tab warnen jetzt, wenn ein eingegebener Sollwert die
  Schwelle überschreiten würde, statt dass das Gerät ihn kommentarlos
  ignoriert.
- **Zeilen-Hervorhebung im Testcase-Editor**: Die aktuell ausgewählte Zeile
  wird farblich markiert (Theme-Palette, Priorität
  Fehler > aktiver Schritt > Auswahl).

### Geändert
- Geräte-Panel im Control-Tab nutzt jetzt ein Flow-Layout (`flow_layout.py`)
  und bricht bei schmalem Fenster automatisch um, statt abgeschnitten zu
  werden.
- CI-Workflow bündelt `translations/` jetzt mit in die .exe.
- Version auf 0.2.1 angehoben.

### Behoben
- Beide Gerätetreiber (`hcs34xx`, `korad_kel102`) fingen ein ungültig
  gewordenes Serial-Port-Handle (z.B. nach Windows-Standby) nicht ab — die
  rohe `SerialException`/`OSError` lief an `device_worker.py` vorbei, das nur
  auf `PowerSupplyError`/`LoadError` reagiert, wodurch ein totes Gerät
  fälschlich als verbunden angezeigt blieb.
- `hcs34xx`: Ein von der aktuellen OVP/OCP-Schwelle abgelehnter Sollwert
  wurde wie ein Verbindungsabbruch behandelt statt als abgelehnter Wert
  erkannt zu werden.
- Testcase-Editor, Zeilenauswahl: Die „#“-Spalte war nicht anklickbar (fehlendes
  `ItemIsSelectable`-Flag); Verschieben/Entfernen einer Zeile ließ die
  farbliche Markierung teils an der falschen Zeile hängen, weil Qt während
  `removeRow()`/`insertRow()` zwischenzeitlich eine eigene, unpassende
  Auswahl feuert; das Öffnen des Geräte-/Aktion-Dropdowns einer markierten
  Zeile färbte fälschlich das komplette Popup-Menü statt nur die
  Tabellenzeile ein.

## [0.2.0]

### Hinzugefügt
- **Farb-Themes**: zwei durchgängige Paletten ("Modern Light" als Default,
  "Amber Industrial" als Dark Mode), umschaltbar per Checkbox im
  Settings-Tab und persistiert über Neustarts. Betrifft Hintergründe,
  Buttons, Tabs, Tabelle, Eingabefelder, Statuslabels sowie die
  Oszilloskop-Vorschau im Signal-Dialog. Theme-Wechsel wirkt sofort, ohne
  Neustart.
- **Material-Design-Icons** (qtawesome) für die Buttons in Testcase- und
  Control-Tab sowie den Umbenennen-Button im Dashboard; Icon-Farbe folgt
  automatisch dem aktiven Theme inkl. Hover-/Disabled-Zustand
  (`lab_gui/icons.py`).
- **Ausgang-Status farblich hervorgehoben**: Der EIN/AUS-Button der
  elektronischen Last färbt sich nach dem tatsächlichen, per Hardware-Abfrage
  (`get_input()`) ermittelten Zustand. Beim Netzteil (HCS-34xx, das laut
  Treiber-Doku keinen echten Ausgangsstatus liefert) zeigt die Färbung
  stattdessen den zuletzt hier geklickten Zustand.
- Simulationsmodus (virtuelles Labornetzteil `hcs34xx/mock.py`) für GUI-Tests
  ohne angeschlossene Hardware, umschaltbar im Settings-Tab.
- GitHub-Actions-Workflow zum automatischen Bauen der Windows-.exe bei jedem
  Push auf `master`.

### Geändert
- Geräte-Panel im Control-Tab richtet sich jetzt an seiner tatsächlichen
  Inhaltsbreite aus (linksbündig), statt sich über die volle Tab-Breite zu
  strecken.
- Version auf 0.2.0 angehoben.

### Technisch
- Neue Module: `lab_gui/theme.py` (Paletten + QSS), `lab_gui/icons.py`
  (IconButton), `lab_gui/settings.py`/`lab_gui/settings_tab.py`
  (persistierte App-Einstellungen: Simulationsmodus, Dark Mode).
- `DeviceWorker` sendet ein neues Signal `load_input_state` (device_id, on).

# Changelog

Alle nennenswerten Änderungen an der Labor-Steuerungs-App. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/), Versionierung nach
Semantic Versioning (`lab_gui/version.py`).

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

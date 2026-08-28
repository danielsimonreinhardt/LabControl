# Changelog

Alle nennenswerten Änderungen an der Labor-Steuerungs-App. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/), Versionierung nach
Semantic Versioning (`lab_gui/version.py`).

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

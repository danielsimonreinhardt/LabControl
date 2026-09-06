# Neuen Geräte-Treiber anlegen

Checkliste für die Integration eines neuen Gerätetyps in LabControl, abgeleitet
aus dem tatsächlichen Aufbau der bestehenden Treiber
([hcs34xx](hcs34xx/), [korad_kel102](korad_kel102/), [can_bus](can_bus/),
[microhil](microhil/)). Als Vorlage eignet sich für ein "einfaches" USB/
Seriell-Gerät mit Live-Messwerten am besten `hcs34xx` (Netzteil) oder
`korad_kel102` (Last); `can_bus`/`microhil` zeigen die Variante ohne
automatische USB-Discovery bzw. mit mehreren Kanälen pro Gerät.

## 1. Treiber-Paket anlegen

Neuer Ordner `<geraetename>/` auf gleicher Ebene wie `hcs34xx/`, mit:

- `__init__.py` — leer
- `driver.py` — echte Hardware-Anbindung
- `mock.py` — simulierter Ersatz für die GUI ohne Hardware
- `README.md` — Kurzbeschreibung + Eigenheiten (siehe Abschnitt 4)

## 2. `driver.py`: echte Hardware-Anbindung

- **Eigene Exception-Basisklasse** `class <X>Error(RuntimeError)`. Falls das
  Gerät Sollwerte kommentarlos ablehnen kann (ohne dass die Verbindung tot
  ist), dafür eine Subklasse anlegen (Vorbild: `PowerSupplyValueError` in
  `hcs34xx/driver.py`) — `device_worker._guard_*` muss "Wert abgelehnt" und
  "Verbindung tot" unterscheiden können, sonst wird bei jedem abgelehnten
  Sollwert fälschlich die Verbindung getrennt.
- **Low-Level-Kommunikation** in einer privaten Methode (z.B. `_query`)
  bündeln, die JEDEN rohen Bibliotheksfehler (`serial.SerialException`,
  `OSError`, Timeout, …) abfängt und in die eigene `<X>Error` übersetzt.
  Ungefangen sieht `device_worker.py` (fängt nur die eigene Error-Klasse) das
  Gerät fälschlich weiter als verbunden an.
- **Discovery** als Classmethods: `discover()` liefert Port-Strings,
  `discover_ports()` (optional) die vollen `ListPortInfo`-Objekte inkl.
  Seriennummer — nötig, damit `device_worker._resolve_device_ids()` mehrere
  baugleiche Geräte stabil auseinanderhalten kann. `open_first()` als
  Komfort-Methode für Standalone-Nutzung/CLI-Tests.
  - Hat das Gerät keine erkennbare VID/PID bzw. keine sichere
    Autodiscovery (z.B. CAN-Interfaces), stattdessen eine explizite
    Konfiguration vorsehen — siehe Abschnitt 9.
- **`close()`** sowie `__enter__`/`__exit__` für Context-Manager-Nutzung.
- Öffentliche API als einfache, **synchrone, blockierende** `get_*`/`set_*`-
  Methoden. Threading/Nicht-Blockieren übernimmt ausschließlich
  `device_worker.py` (eigener `QThread`) — der Treiber selbst bleibt dumm.
- Jede Eigenheit des Protokolls (Timing, Skalierung, ignorierte Werte,
  fehlende Kommandos) direkt als Kommentar an der betroffenen Stelle
  begründen, nicht nur im README. Beispiel: `hcs34xx/driver.py` `MIN_VOLTAGE`
  — ohne die Prüfung würde ein abgelehnter Wert in einen Timeout laufen und
  fälschlich als Verbindungsabbruch gewertet.

## 3. `mock.py`: simulierte Variante

- Bildet **dieselbe öffentliche Schnittstelle** wie `driver.py` nach (keine
  gemeinsame Basisklasse nötig — `device_worker.py` nutzt die Treiberklassen
  nur als Type-Hints, tatsächlich zählt Duck-Typing).
- Hält Zustand rein im Speicher, keine echte I/O, `close()` ist ein No-Op.
- Wird für den globalen Simulationsmodus gebraucht (Einstellungen-Tab) sowie
  für GUI-Entwicklung/-Tests ohne angeschlossene Hardware.

## 4. `README.md` je Treiber-Paket

Kurzbeschreibung, Verwendungsbeispiel, und zwingend ein Abschnitt
"Bekannte Eigenheiten / Einschränkungen" (Vorlage: `hcs34xx/README.md`) —
jede im Code kommentierte Eigenheit gehört hier zusätzlich in
nutzerverständlicher Form rein.

## 5. Einbindung in `lab_gui/device_worker.py`

1. Treiber + Mock importieren (Fehlerklassen mit).
2. Neues Dict `self._<kind>s: dict[str, <DriverClass>] = {}` in `__init__`.
3. Qt-Signale ergänzen, `device_id` immer als erstes Argument:
   `<kind>_connected(str, bool)`, plus passende Messwert-/Zustands-Signale
   (siehe `psu_measurement`, `hil_digital_state` als Vorbilder).
4. Simulationsmodus: `SIM_<KIND>_ID`-Konstante, `_add_mock_<kind>()` /
   `_remove_mock_<kind>()`, verdrahtet in `start()` und
   `set_simulation_mode()`.
5. `_reconnect_<kind>s()`: Kandidaten per `_resolve_device_ids()` (oder
   eigener Logik, siehe CAN/HIL) ermitteln und **zusätzlich zum reinen
   Portöffnen mit einer echten Abfrage verifizieren** (Handshake), bevor das
   Gerät als verbunden gilt — ein Port kann existieren, ohne dass ein
   antwortendes Gerät dahinterhängt.
6. `_poll()`: pro Zyklus Live-Werte abfragen; bei `<X>Error` Gerät schließen,
   aus dem Dict entfernen, `<kind>_connected(id, False)` und
   `device_removed("<kind>", id)` emittieren — exakt das Muster aus dem
   Load-/PSU-Block dort.
7. `_guard_<kind>()`: zentraler try/except-Wrapper für alle Steuerbefehle,
   der "Wert abgelehnt" (Verbindung bleibt offen) von "Verbindung tot"
   (schließen + entfernen + Signal) trennt (Vorbild: `_guard_psu`).
8. Steuer-Slots `@Slot(...) def set_<kind>_xxx(...)` implementieren, die über
   `_guard_<kind>` laufen.
9. Hat das Gerät einen sicherheitsrelevanten Ausgang (Spannung/Strom/Relais),
   in `all_outputs_off()` einen `_kill_<kind>()` nach Vorbild von
   `_kill_load`/`_kill_psu` ergänzen (Retry-Logik, darf die anderen Geräte
   nicht blockieren, kein `except` darf die Schleife verlassen).

## 6. `lab_gui/device_registry.py`

Eintrag im `KIND_DISPLAY`-Dict ergänzen (Anzeigename der Geräteart für die
automatische Kachel-Benennung, z.B. "Bank A"/"Bank B").

## 7. Testablauf-Integration

- `lab_gui/testcase_model.py`: neue Geräteart in `DEVICE_ACTIONS` (mit den
  verfügbaren Action-Codes analog zu `LOAD_ACTIONS`/`PSU_ACTIONS`),
  `DEVICE_KIND_LABELS` und ggf. `MEASUREMENT_DEVICE_KINDS` (falls die
  Geräteart in Bedingungen/Pass-Fail-Prüfungen per Messwert referenzierbar
  sein soll) eintragen.
- `DeviceWorker._dispatch_action()` in `device_worker.py` um einen
  `if kind == "<kind>":`-Zweig erweitern, der die Action-Codes auf
  Treiber-Methoden abbildet (siehe die `"load"`/`"psu"`-Zweige als Vorlage).
  `execute_action()` ruft das automatisch auf und meldet das Ergebnis über
  `action_completed`.

## 8. GUI: Dashboard + Control-Tab

- `lab_gui/dashboard.py`: neue Kachel-Darstellung für die Geräteart ergänzen
  (Vorbild: `_DevicePanel`).
- `lab_gui/control_tab.py`: neue `<X>ControlGroup(QGroupBox)`-Klasse analog zu
  `LoadControlGroup`/`PsuControlGroup`/`CanControlGroup`, verbunden mit den
  neuen Signalen/Slots aus `device_worker.py`.

## 9. Optional: persistente Konfiguration statt Autodiscovery

Hat das Gerät keine sichere USB-VID/PID-Autodiscovery (wie bei CAN-
Interfaces), braucht es stattdessen eine vom Nutzer gepflegte Konfiguration.
Vorlage: `settings.py`/`settings_tab.py` (`can_configs`) plus
`DeviceWorker.set_<kind>_configs()` nach Vorbild von `set_can_configs()`.

## 10. Dokumentation & Test

- Root-`README.md`, Abschnitt "Supported Devices", ergänzen.
- **`CHANGELOG.md` vor jedem Push aktualisieren** (Projekt-Konvention).
- Erst gegen `mock.py`/Simulationsmodus testen, dann **zwingend gegen echte
  Hardware verifizieren** — Codereview allein deckt Timing-/Protokoll-
  Eigenheiten nicht auf (siehe `hcs34xx/driver.py`: `MIN_VOLTAGE`,
  `_probe_alive` — beides erst an echter Hardware gefunden). Typische
  Fallstricke, auf die dabei zu achten ist:
  - Timeout durch abgelehnten Wert wird fälschlich als Verbindungsabbruch
    gewertet (Abhilfe: Value-Error-Subklasse, siehe Abschnitt 2).
  - Falsche Baudrate/Zahlenformat-Annahme aus der Anleitung ungeprüft
    übernommen.
  - Fehlendes Software-Aus für einen Ausgang (wie beim HCS-34xx) — ggf.
    Workaround dokumentieren statt stillschweigend keine Funktion anzubieten.

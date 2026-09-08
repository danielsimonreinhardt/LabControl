# PicoScope 2204A/2205A – USB-Oszilloskop

Treiber-Bibliothek zur Ansteuerung eines PicoScope 2204A/2205A über die
`ps2000`-API von Pico Technology (per `picosdk`-Python-Paket, ctypes-Wrapper
um `ps2000.dll`). `PicoScope2000.open_first()` verbindet automatisch.

In LabControl als Dashboard-Kachel (Status frei/belegt + Start-Button für
die PicoScope-7-App, siehe `lab_gui/picoscope_panel.py`) UND als
Testablauf-Aktionen (`PICO_VMAX`/`PICO_VMIN`/`PICO_VPP`/`PICO_VRMS`, siehe
unten) eingebunden -- bewusst KEIN Control-Tab-Abschnitt, keine
Block-Erfassung/Kurvenanzeige im GUI. Grund: LabControl ist eine
übergeordnete Laborsteuerung, kein Ersatz für die PicoScope-7-App -- für ein
so komplexes Gerät wie ein Oszilloskop (Kanäle, Trigger, Kurvenanzeige) macht
es wenig Sinn, deren Funktionsumfang nachzubauen. Stattdessen liefert
LabControl gezielt einzelne abgeleitete Kennwerte für Pass/Fail-Prüfungen
im Testablauf.

Getestet gegen echte Hardware: PicoScope 2204A, Serial `GP816/090` –
Verbindung, `get_info()`, `capture_block()` (Kanal A, 2V-Bereich, 1000
Samples, Timebase 8 → 2560 ns Sample-Intervall) und `usb_present()` (auch
während die PicoScope-7-App das Gerät exklusiv offen hielt) funktionieren.

## Verwendung

```python
from picoscope2000.driver import PicoScope2000

with PicoScope2000.open_first() as scope:
    print(scope.get_info())  # UnitInfo(variant='2204A', serial='GP816/090')
    capture = scope.capture_block(channel="A", voltage_range="2V", num_samples=1000)
    print(capture.millivolts[:10])
    print(capture.time_ns[-1])
```

## Bekannte Eigenheiten / Einschränkungen

- **"A" im Modellnamen ist irreführend.** 2204A/2205A nutzen trotz des
  Namens die ÄLTERE `ps2000`-API, nicht `ps2000a` – laut Pico-Support ist
  `ps2000a` erst für spätere Modelle gedacht. `ps2000aOpenUnit()` liefert bei
  diesen Geräten `PICO_NOT_FOUND` (Status 3), obwohl das Gerät angeschlossen
  ist – gegen echte Hardware verifiziert, hat mich beim ersten Versuch in die
  Irre geführt.
- **DLL liegt nicht auf dem PATH.** Ohne separates PicoSDK-Setup liegt
  `ps2000.dll` nur im Installationsordner der PicoScope-7-Software (z.B.
  `C:\Program Files\Pico Technology\PicoScope 7 T&M Stable\`). `picosdk`
  sucht die DLL beim Modulimport ausschließlich über
  `ctypes.util.find_library()` (= PATH) – `driver.py` sucht deshalb selbst in
  bekannten Installationspfaden und stellt das Verzeichnis dem
  Prozess-`PATH` voran, bevor `picosdk.ps2000` importiert wird
  (`_ensure_dll_on_path()`). Ist PicoScope 7 an einem anderen Ort installiert
  oder liegt ein separates PicoSDK vor, `_INSTALL_DIR_CANDIDATES` in
  `driver.py` erweitern (wird auch für `launch_app()`s Suche nach
  `PicoScope.exe` verwendet).
- **DLL-Import ist defensiv, nicht hart.** Ist weder PicoScope 7 noch
  PicoSDK installiert, würde ein normaler Modulimport mit
  `CannotFindPicoSDKError` crashen -- da `device_worker.py` dieses Modul
  global importiert, würde das die GESAMTE LabControl-App beim Start
  lahmlegen, nicht nur die PicoScope-Kachel. Der Import steht deshalb in
  einem `try/except OSError` (`CannotFindPicoSDKError` ist eine
  `OSError`-Unterklasse, siehe `picosdk.errors`); `open_first()`/
  `capture_block()` werfen stattdessen `PicoScope2000Error`, wenn die DLL
  fehlt (`_require_dll()`). `usb_present()` und `launch_app()` funktionieren
  unabhängig davon (reine Windows-SetupAPI- bzw. Prozessstart-Funktionen,
  keine ps2000.dll nötig).
- **`usb_present()` unterscheidet "belegt" von "nicht angeschlossen".**
  `ps2000_open_unit()` belegt das Gerät exklusiv -- ist es z.B. gerade in
  der PicoScope-7-App geöffnet, schlägt ein zweiter `open_unit()`-Aufruf
  fehl, und zwar mit demselben Status (0) wie bei komplett fehlendem Gerät
  (gegen echte Hardware verifiziert). `usb_present()` fragt stattdessen per
  Windows-SetupAPI (`SetupDiGetClassDevs`/`SetupDiEnumDeviceInfo`, VID
  `0x0CE9`, PID `0x1007`) die physische USB-Präsenz ab, unabhängig davon,
  wer das Gerät gerade hält -- Grundlage für die "frei"/"belegt"-Anzeige der
  Dashboard-Kachel (siehe `device_worker._reconnect_picoscope()`). Kein
  neuer Dependency nötig, nur `ctypes`.
- **Keine Geräte-Enumeration.** Die `ps2000`-API kennt (anders als `ps2000a`)
  keine `enumerate_units()`-Funktion. `ps2000_open_unit()` öffnet schlicht
  "das nächste freie Gerät" – bei mehreren angeschlossenen 2000er-Scopes ist
  keine gezielte Auswahl per Seriennummer möglich. `discover()` muss daher
  testweise verbinden und wieder trennen, um überhaupt an die Seriennummer zu
  kommen (anders als bei `hcs34xx`/`korad_kel102`, wo VID/PID-Listing ohne
  Verbindungsaufbau reicht).
- **Kein echter Trigger in `capture_block()`.** Aktuell wird ein sehr kurzes
  Auto-Trigger-Timeout (1 ms) statt eines echten Trigger-Levels verwendet –
  die Erfassung läuft damit effektiv freilaufend. Für eine spätere
  Trigger-basierte Erfassung (z.B. für Pass/Fail-Kriterien im
  Testablauf-System) müsste `ps2000_set_trigger()` mit echtem Threshold
  parametrisierbar gemacht werden.
- **`common.py` statt gemeinsamer Basisklasse.** `driver.py` und `mock.py`
  teilen sich `PicoScope2000Error`/`UnitInfo`/`BlockCapture`/
  `VOLTAGE_RANGE_CODES` über `common.py`, nicht über einen Import von
  `mock.py` aus `driver.py` – sonst würde bereits das Importieren von
  `mock.py` (Simulationsmodus!) die DLL-Suche aus `driver.py` auslösen und
  auf Systemen ohne installierte PicoScope-Software fehlschlagen.
- **Andere Geräteklasse als PSU/Last -- kein dauerhaft offenes Handle, UND
  kein periodisches Öffnen mehr.** `device_worker._reconnect_picoscope()`
  folgt NICHT dem Last/Netzteil/microHIL-Muster aus
  `NEUEN_TREIBER_ANLEGEN.md` (Treiber-Objekt im Dict halten, per `_poll()`
  befragen). Ursprünglich wurde bei jedem Tick des EIGENEN, langsamen
  `PICOSCOPE_RECONNECT_INTERVAL_MS`-Timers (30s) neu verbunden, `get_info()`
  abgefragt und sofort wieder getrennt -- an echter Hardware löste das bei
  JEDEM Tick ein hörbares Relaisklicken im Gerät aus (Nutzerfeedback), auch
  wenn sich am Status nichts geändert hatte. `_reconnect_picoscope()` prüft
  seither nur noch die reine USB-Präsenz (`usb_present()`, ohne das Gerät zu
  öffnen); das tatsächliche Öffnen (`_probe_picoscope()`, liefert
  variant/serial + den anfänglichen Frei/Belegt-Status) läuft nur noch
  EINMAL beim Erkennen eines (Wieder-)Anschlusses sowie einmalig am Ende
  eines Testlaufs mit PicoScope-Beteiligung (`close_picoscope_sessions()`),
  nicht mehr periodisch. Ein dauerhaft offenes Handle bliebe weiterhin
  ausgeschlossen -- es würde verhindern, dass die PicoScope-7-App
  (Start-Button) das Gerät je selbst öffnen könnte, da `ps2000_open_unit()`
  exklusiv ist. Trade-off: belegt ein externes Programm (PicoScope 7) das
  Gerät, NACHDEM die Kachel es bereits als "frei" gemeldet hat, bleibt die
  Anzeige bis zum nächsten Ab-/Anstecken oder einer echten
  Testablauf-Aktion optimistisch auf dem alten Stand -- ausdrücklich
  gewünschter Trade-off gegen das Relaisklicken.
  Ein einzelner PicoScope-Öffnen/Schließen-Zyklus dauert real ~4,5s --
  BLOCKIEREND, da alles im selben DeviceWorker-Thread läuft.
- **Testablauf-Integration (PICO_VMAX/PICO_VMIN/PICO_VPP/PICO_VRMS) --
  ohne Control-Tab-Abschnitt.** `measure()` liefert alle vier Kennwerte aus
  EINER Blockerfassung (eine zusätzliche Erfassung pro Kennwert wäre wegen
  der ~4,5s Verbinden/Trennen unverhältnismäßig teuer). Kanal (A=1/B=2)
  nutzt denselben generischen Kanal-Slot wie microHIL
  (`testcase_model.TestStep.hil_channel`, siehe dessen Docstring); der
  Spannungsbereich wird -- da `execute_action` kein eigenes Range-Feld hat
  -- als `VOLTAGE_RANGE_CODES`-Zahlencode im sonst bei Lese-Aktionen
  unbenutzten `value`-Feld transportiert (siehe `TestStep.value`-Docstring,
  `device_worker._execute_picoscope_action`). Bewusst KEIN Control-Tab-
  Abschnitt: `control_tab.on_device_known()` bricht für `kind ==
  "picoscope"` früh ab, bevor der generische `else`-Zweig sonst fälschlich
  eine bedeutungslose `CanControlGroup`-Sektion dafür anlegen würde.
- **Testlauf haelt die Verbindung offen (statt pro Aktion neu zu
  verbinden).** `main_window._on_run_requested()` ermittelt vor Laufstart,
  ob Schritte `kind == "picoscope"` referenzieren (ueber
  `_resolve_step_device_ids()`, dieselbe Auflösung wie fuer den Safety-
  Watchdog) und oeffnet dafuer genau einmal eine Session
  (`device_worker.open_picoscope_session`), die bis Laufende (fertig/
  gestoppt/fehlgeschlagen) offen bleibt (`close_picoscope_sessions`).
  `_execute_picoscope_action` nutzt eine bestehende Session direkt fuer
  `measure()` -- ohne erneutes Oeffnen/Schliessen. Gemessener Effekt (3
  Aktionen, echte Hardware): 13,8s (alt, pro Aktion oeffnen+schliessen) vs.
  4,6s (neu, eine Session) -- der Vorteil waechst mit der Schrittzahl, da
  nur EIN Verbinden/Trennen pro Lauf anfaellt statt pro Schritt. Ein Lauf
  ohne PicoScope-Schritte oeffnet gar nichts (blockiert also auch nicht
  unnoetig die PicoScope-7-App). Scheitert das Session-Oeffnen selbst
  (z.B. verschachtelte Reconnect-Probe, siehe naechster Punkt), bleibt
  einfach keine Session bestehen -- `_execute_picoscope_action` faellt dann
  automatisch auf den alten Pro-Aktion-Pfad (inkl. dessen Retry-Mechanismus)
  zurueck, kein zusaetzlicher Fehlerfall noetig. **Wichtig:** PicoScope-
  device_ids werden bewusst NICHT an `safety.begin_run_supervision()`
  uebergeben (Filter in `_on_run_requested()`) -- anders als PSU/Last/HIL
  hat das PicoScope keinen steuerbaren Ausgang und liefert waehrend eines
  Laufs bewusst keine periodischen Messwerte (kein Poll-Zyklus); ohne den
  Ausschluss brach der Watchdog jeden Lauf mit PICO_*-Schritt nach
  `STALE_TIMEOUT_S` faelschlich als "veraltet" ab (an echter Hardware
  gefunden).
- **Testablauf-Aktionen können mit dem eigenen Reconnect-Tick kollidieren --
  Auto-Retry in `testcase_runner.py`.** An echter Hardware reproduziert:
  `ps2000_open_unit()` (blockierend, ~3,5s) pumpt offenbar intern
  Windows-Messages, wodurch eine per Qt-Queued-Connection zugestellte
  `execute_action`-Ausführung VERSCHACHTELT in einen noch laufenden
  Reconnect-Aufruf hineinlaufen kann -- auf demselben Thread, per Log
  bestätigt. `set_test_running()` (pausiert neue Reconnect-Ticks während
  eines Testlaufs) reicht allein NICHT: ein knapp vor Testlaufstart bereits
  gestarteter Tick kann trotzdem noch in-flight sein. Ein simples
  "Flag setzen und warten" INNERHALB der verschachtelten Ausführung
  funktioniert dabei strukturell nicht (selbst 20s Wartezeit halfen nicht
  -- der äußere Aufruf kann erst zurückkehren, wenn der innere das tut, der
  aber auf den äußeren wartet). Die tatsächliche Lösung:
  `_execute_picoscope_action` erkennt die Verschachtelung
  (`_picoscope_busy`) und gibt sofort mit der Sentinel-Meldung
  `PICOSCOPE_RETRY_MESSAGE` auf, OHNE zu warten;
  `testcase_runner._retry_pico_action()` versucht denselben Schritt nach
  `PICOSCOPE_RETRY_DELAY_S` (6s) automatisch erneut -- als NEUER, nicht
  verschachtelter Aufruf, zu dem Zeitpunkt ist der äußere Aufruf längst
  zurückgekehrt. Begrenzt auf `PICOSCOPE_MAX_RETRIES` (3) Versuche.

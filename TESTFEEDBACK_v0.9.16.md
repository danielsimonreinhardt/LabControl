# Test-Feedback v0.9.16 (lokaler Build, Picoscope/microHIL/CAN-VN1610)

Gesammeltes Feedback aus dem Hardwaretest. Status je Punkt markiert --
**Umgesetzt** heisst code-seitig erledigt und neu gebaut
(`dist\LabControl_v0.9.16.exe`), aber noch NICHT gegen echte Hardware
nachgetestet.

## PicoScope

1. **Öffnen-Button auf der Dashboard-Kachel zu wenig sichtbar.** Anders als
   die übrigen Kacheln (reine Statusanzeige) erwartet die PicoScope-Kachel
   aktive Bedienung (Start-Button für PicoScope 7) — das muss optisch klar
   erkennbar sein, aktuell fällt der Button zu wenig auf.

   **Umgesetzt:** Button ist jetzt ein `IconButton` mit Icon, permanent
   akzentfarben (statt nur bei Hover) -- siehe `picoscope_panel.py`.

2. **30s-Reconnect-Tick löst am Gerät ein Relaisklicken aus.**
   `device_worker._reconnect_picoscope()` verbindet/trennt aktuell alle 30s
   für die Frei/Belegt-Anzeige — dabei schaltet im PicoScope selbst ein
   Relais, hörbar/spürbar bei jedem Tick. Das soll so nicht passieren.
   Nutzerseitiger Vorschlag: reine USB-Präsenzerkennung (`usb_present()`,
   Windows-SetupAPI, ohne `ps2000_open_unit()`) plus optimistische
   Verbunden-Anzeige, statt für die Statuskachel tatsächlich zu öffnen.
   Alternativ prüfen, ob sich die Geräte-Initialisierung für eine reine
   Verbunden-Abfrage aussetzen/abkürzen lässt (ohne die Relais-Schaltung
   auszulösen, die offenbar Teil von `ps2000_open_unit()` ist).

   **Umgesetzt (dein Vorschlag):** `_reconnect_picoscope()` prüft jetzt nur
   noch die reine USB-Präsenz (`usb_present()`, kein Öffnen). Das
   tatsächliche Öffnen (`_probe_picoscope()`, liefert variant/serial +
   Frei/Belegt) läuft nur noch einmalig bei Erkennen eines (Wieder-)
   Anschlusses sowie einmalig am Ende eines Testlaufs mit PicoScope-
   Beteiligung -- nicht mehr alle 30s. Trade-off: belegt eine externe App
   das Gerät NACH der letzten Probe, bleibt die Anzeige bis zum nächsten
   Ab-/Anstecken oder Testlauf optimistisch stehen (wie von dir
   vorgeschlagen).

3. **Bug: Testablauf startet bereits, bevor die PicoScope-Session steht.**
   `main_window._on_run_requested()` soll vor Laufstart einmalig
   `device_worker.open_picoscope_session()` öffnen, wenn Schritte
   PicoScope-Aktionen enthalten (siehe `picoscope2000/README.md`) — der
   Testablauf läuft aber offenbar schon los, bevor diese Initialisierung
   abgeschlossen ist (Race Condition beim Laufstart). Session-Aufbau muss
   den Laufstart blockieren/abwarten, nicht nur "vorher angestoßen" werden.

   **Umgesetzt:** Session-Öffnen läuft jetzt über
   `QMetaObject.invokeMethod(..., BlockingQueuedConnection)` (dasselbe
   Muster wie beim sauberen Abschalten in `closeEvent()`) statt über ein
   einfaches Signal-`emit()` -- blockiert den GUI-Thread bewusst, bis die
   Session steht, bevor der erste Testschritt startet.

## microHIL

1. **Bug: Testschritt "Digitalausgang setzen" bricht mit "Messwert
   veraltet" ab.** Ein Testablauf-Schritt, der am microHIL einen
   Digitalausgang setzt (HIL_OUT_ON/OFF), löst den Safety-Watchdog
   fälschlich aus (analog zum bereits gelösten PicoScope-Fall, siehe
   `safety.begin_run_supervision()`/`STALE_TIMEOUT_S` — vermutlich
   liefert dieser Aktionstyp keinen/keinen rechtzeitigen periodischen
   Messwert, den der Watchdog als "frisch" akzeptiert). Noch nicht
   analysiert, welche microHIL-Aktionen konkret betroffen sind.

   **Umgesetzt (Ursache gefunden):** `main_window._wire_safety()` verband
   `hil_digital_state`/etc. nie mit dem Watchdog -- JEDES supervisierte
   HIL-Gerät musste also nach spätestens `STALE_TIMEOUT_S` (2s) als
   "veraltet" abbrechen, unabhängig von der konkreten Aktion, sobald ein
   Schritt länger als 2s dauerte. Neuer Heartbeat-Slot
   `SafetyMonitor.on_hil_digital_state()` (analog zu `on_can_stats`),
   verbunden mit `device_worker.hil_digital_state` (1x/Sekunde, siehe
   `HIL_POLL_INTERVAL_MS`).

2. **Feature-Wunsch: While/If-Bausteine sollen microHIL direkt als
   Messquelle anbieten.** Aktuell vergleicht `condition_dialog.py`
   Bedingungen nur gegen Spannung/Strom/Leistung von Last/Netzteil (Gerät
   automatisch oder gezielt) bzw. gegen eine per `store_var` manuell
   gefüllte Variable. Gewünscht: microHIL-Kanäle (Digital-/Analogeingang)
   sollen dort genauso direkt als Bedingungsquelle wählbar sein wie
   Last/Netzteil — ohne den Umweg über einen vorgeschalteten
   HIL_*_READ-Schritt mit "In Variable speichern".

   **Noch offen** -- groessere Änderung an `condition_dialog.py`/
   `testcase_runner.py`, als naechstes geplant.

3. **Beobachtung (optional, keine feste Anforderung): Dashboard-/
   Control-Kachel zeigt keine Board-ID/Seriennummer.** War in der
   Testliste als erwartet aufgeführt, laut Nutzer aber nie explizit als
   Feature gefordert — bei Bedarf nachträglich ergänzen, sonst nur zur
   Kenntnis.

   **Zurückgestellt** -- keine Anforderung, nicht umgesetzt.

4. **Lücke: Control-Kachel bietet kein Setzen eines PWM-Kanals.**
   Bestätigt im Code (`lab_gui/control_tab.py`, `HilControlGroup`):
   Relais, Digitalausgänge und Analogausgang (AOUT) sind dort setzbar,
   ein PWM-Bedienelement (`set_pwm()`, siehe `microhil/driver.py`) fehlt
   komplett — nur ein Kommentar zur PWM/OUT-Verriegelung erwähnt PWM
   überhaupt. Zum Testen von Punkt "hil-pwm" aus der Testliste musste
   PWM bislang über einen Testablauf-Schritt statt über die Control-Kachel
   gesetzt werden.

   **Umgesetzt:** PWM1-4 als Sollwertfeld + Übernehmen-Button (0-1000 ‰),
   analog zu den AOUT-Feldern, in `HilControlGroup` ergänzt (inkl. Preset-
   Speicherung/-Wiederherstellung).

5. **Bug: Digitaleingänge zeigen nichts an.** Weder Dashboard- noch
   Control-Kachel zeigen einen erkennbaren Zustand für die
   Digitaleingänge, obwohl reale Signale angelegt wurden. Ansatzpunkt für
   die Analyse: `microhil_panel.MicroHilPanel.update_inputs()` (nimmt
   `list[bool]`) und deren Aufrufer in `device_worker.py`/
   `set_hil_digital_state()` — prüfen, ob der gelesene Zustand überhaupt
   bis zur Anzeige durchgereicht wird oder nur das Widget selbst leer
   bleibt.

   **Untersucht, kein Code-Bug gefunden:** Anzeigepfad
   (`device_worker._poll_hil` → `hil_digital_state` → `dashboard.
   update_hil_digital` → `MicroHilPanel.update_inputs`) ist exakt symmetrisch
   zum funktionierenden Digitalausgang-Pfad, Firmware (`protocol.c:
   cmd_in_query`) und Protokoll (`docs/protocol.md`) stimmen mit dem
   Treiber (`microhil/driver.py: get_inputs()`) überein. Vermutlich
   Hardware-/Verdrahtungsfrage (z.B. Eingänge ohne Referenz/Pull
   floatend) statt Software-Bug -- bräuchte zur weiteren Eingrenzung einen
   direkten Seriell-Terminal-Test von `IN?`/`IN? <n>` gegen die Firmware,
   während ein reales Signal anliegt.

6. **Analogausgänge (AOUT) anscheinend nicht richtig kalibriert.**
   Gesetzter Sollwert und real gemessene Ausgangsspannung weichen
   voneinander ab. Noch nicht geklärt, ob das ein LabControl-/Treiber-Bug
   (`microhil/driver.py: set_analog_output()`) oder ein Hardware-/
   Firmware-Kalibrierungsthema ist (vgl. Muster
   `KNOWN_HARDWARE_DEFECTS`/microHIL-Repo `docs/hardware-notes.md` für
   board-spezifische Defekte) — vor Umsetzung zuerst eingrenzen, auf
   welcher Seite die Abweichung liegt.

   **Erledigt (Firmware-seitig behoben, Treiber nachgezogen):** microHIL-
   Firmware hat AIN/AOUT/CURR am 2026-09-08 real kalibriert (siehe
   `docs/calibration.md` im microHIL-Repo, gegen Multimeter/Amperemeter
   verifiziert). `microhil/driver.py` in LabControl entsprechend
   nachgezogen: `AOUT_MAX_MV` 3300→12210 (kalibrierter statt roher
   DAC-Bereich), `get_current_sense_mv()` → `get_current_ma()` (liefert
   jetzt echte mA statt roher Sense-Spannung), neue
   `get_analog_input_raw()`/`set_analog_output_raw()`/
   `get_current_sense_raw_mv()` für die unkalibrierten Rohwerte
   (`AINRAW?`/`AOUTRAW`/`CURRRAW?`), plus `set_current_limit()`/`ILIM` ist
   jetzt firmwareseitig aktiv (vorher `ERR UNKNOWN`) inkl. neuem
   `get_current_limit()`/`get_pwr12_fault()` (`ILIM?`/`PWR12FLT?`). GUI-
   seitig (Dashboard/Control-Tab-Beschriftung "mA") war die Anzeige schon
   vorher korrekt beschriftet, der durchgereichte Wert jetzt auch
   tatsächlich mA. **Bitte mit echter Hardware nachtesten** (Sollwert
   setzen, gegen Multimeter/Amperemeter vergleichen).

   **Untersucht:** `set_analog_output()` schickt den mV-Wert unveraendert
   an die Firmware, die laut `docs/protocol.md`/`protocol.c` intern über
   feste 2-Punkt-Kalibrierkonstanten (`cal_invert()`) auf den DAC-Rohwert
   umrechnet -- vermutlich Firmware-/Kalibrierthema im microHIL-Repo, nicht
   im LabControl-Treiber. Bräuchte konkrete Messpunkte (gesetzter Sollwert
   vs. per Multimeter gemessener Ist-Wert, mehrere Punkte über den Bereich)
   für eine gezielte Kalibrierkorrektur.

7. **Testablauf-Editor: überflüssige Wert-Spalte bei kanalbasierten
   microHIL-Lese-/Schaltaktionen.** Bei `HIL_IN_READ`, `HIL_AIN_READ`,
   `HIL_OUT_ON` und `HIL_OUT_OFF` bietet die Spalte "Wert" zusätzlich zur
   ohnehin schon gewählten Kanalnummer eine weitere, hier bedeutungslose
   Werteingabe an (die Aktionen kennen keinen eigenen Sollwert -- lesen nur
   den Kanal bzw. schalten ihn ein/aus). Sollte für genau diese vier
   Aktionen ausgeblendet/deaktiviert werden, siehe Spaltensteuerung in
   `lab_gui/testcase_tab.py` und `TestStep.value`-Docstring in
   `testcase_model.py`.

   **Umgesetzt:** Wert-Spinbox wird für diese vier Aktionen (plus
   `HIL_RELAY_ON`/`HIL_RELAY_OFF`, gleiche Logik) jetzt komplett
   ausgeblendet statt nur gegraut.

8. **Feature-Wunsch: einstellbare PWM-Frequenz.** Aktuell nur Duty-Cycle
   (`PWM <ch> <permille>`) einstellbar, keine Frequenz. **Cross-Repo-Thema,
   nicht in LabControl allein lösbar:** im microHIL-Firmware-Repo geprüft
   (`firmware/microHIL_fw/Core/Src/main.c`/`protocol.c`) -- PWM1-4 laufen
   alle auf EINEM gemeinsamen Timer (TIM3, fest `Prescaler=0`/`ARR=65535`),
   das Protokoll (`docs/protocol.md`) kennt gar kein Frequenz-Kommando.
   Eine Frequenz waere damit zwangsweise GLOBAL fuer alle 4 PWM-Kanaele
   gemeinsam, nicht je Kanal einzeln, es sei denn die Timer-Zuordnung wird
   in der Firmware geaendert. Voraussetzung: erst firmwareseitig ein neues
   Kommando (z.B. `PWMFREQ <hz>`, inkl. Neuberechnung von Prescaler/ARR und
   Umskalierung der laufenden Duty-Cycle-Compare-Werte) ergaenzen, danach
   `microhil/driver.py` (neue Methode) + `lab_gui/control_tab.py`
   (Frequenz-Feld, vermutlich EIN gemeinsames statt 4 einzelne) +
   Testablauf-Editor entsprechend erweitern.

## CAN-Bus

1. **Bug: "Keine Kanäle gefunden" trotz angeschlossenem/konfiguriertem
   VN1610.** Settings-Tab meldet keine Kanäle für Interface "vector",
   obwohl das Gerät verbunden und in Vector Hardware Config sichtbar war.
   `CanBus.discover_configs()` (`can_bus/driver.py`) verlässt sich dafür
   komplett auf `can.detect_available_configs(interfaces=["vector"])` aus
   `python-can` — dessen Vector-Erkennung ist bekanntermaßen nicht
   durchgängig zuverlässig (anders als z.B. bei PCAN). Vor einem Fix
   prüfen, ob `python-can` hierfür überhaupt eine funktionierende
   Vector-Erkennung bietet oder ob die Kanalliste stattdessen z.B. direkt
   aus der Vector-XL-API/Hardware-Config gelesen werden müsste.

   **Umgesetzt (Ursache gefunden):** `python-can`s Vector-Backend sucht
   `vxlapi64.dll` über `ctypes.util.find_library()`, das unter Windows
   bekanntermassen nicht zuverlässig denselben Suchpfad wie
   `ctypes.WinDLL()` abklappert -- selbst bei korrekt installierter Vector
   XL Driver Library (Standardpfad `C:\Windows\System32\vxlapi64.dll`)
   liefert `find_library()` dabei None, wodurch der komplette Interface-Typ
   "vector" leer bleibt (nur eine geloggte Warnung, kein sichtbarer
   Fehler) -- dasselbe Muster wie der bereits bekannte PicoScope-DLL-Fund.
   Neue `can_bus/driver.py::_ensure_vector_dll_findable()` stellt das
   System32-Verzeichnis (+ optionalen SDK-Ordner) explizit vor den
   ersten Ladeversuch des Vector-Backends.

   **Weiterhin offen (Fix hat nicht gegriffen):** An echter Hardware
   erneut getestet, Meldung besteht weiter. Nachrecherchiert:
   `ctypes.util.find_library()` durchsucht unter Windows AUSSCHLIESSLICH
   die Verzeichnisse aus der `PATH`-Umgebungsvariable (kein Fallback auf
   die echte Windows-DLL-Suchreihenfolge/System32-Automatik) -- System32
   steht aber normalerweise ohnehin in PATH, `find_library` hätte die DLL
   dort also schon OHNE unseren Fix gefunden. Das deutet darauf hin, dass
   `vxlapi64.dll` auf diesem Rechner GAR NICHT in System32 liegt, sondern
   z.B. nur in einem Vector-Installationsordner unter "Program Files"
   (abhängig davon, ob nur "Vector Driver Setup" oder zusätzlich die
   eigenständige "XL Driver Library"/SDK installiert wurde) -- unsere
   geratenen Kandidatenpfade in `_VECTOR_DLL_CANDIDATES` treffen den
   echten Ort dann nicht. **Bräuchte:** den tatsächlichen Pfad von
   `vxlapi64.dll` auf dem Testrechner (z.B. Windows-Suche oder
   `where /r C:\ vxlapi64.dll` in einer Eingabeaufforderung), dann kann
   der Kandidatenpfad in `can_bus/driver.py` gezielt korrigiert werden.

   **Theorie widerlegt:** Suche ergab u.a. `C:\Windows\System32\vxlapi64.dll`
   -- System32 steht aber schon standardmaessig in PATH, `find_library()`
   haette die DLL also bereits OHNE unseren Fix gefunden. Der DLL-Fund-Fix
   in `can_bus/driver.py` bleibt drin (harmlos, aber offenbar nicht die
   Ursache). Wahrscheinlicher jetzt: `xldriver`/die DLL laedt korrekt,
   aber `_detect_available_configs()` (python-can) filtert Kanaele ohne
   gesetztes `XL_BUS_ACTIVE_CAP_CAN`-Bit heraus -- moeglich, dass der
   VN1610-Kanal in Vector Hardware Config zwar als Hardware sichtbar ist,
   aber keiner "Application"/keinem Netzwerk mit CAN-Bustyp zugewiesen
   wurde (VN1610 ist ein Standalone-Geraet, braucht dafuer ggf. eine
   explizite Zuweisung, nicht nur die Hardware-Erkennung).
   **Naechster Schritt:** direkter Diagnose-Snippet statt weiter zu raten
   -- folgendes im Python-Interpreter der App-Umgebung ausfuehren und
   Ausgabe mitteilen:
   ```python
   import ctypes.util
   print("DLL:", ctypes.util.find_library("vxlapi64"))
   import can
   print("configs:", can.detect_available_configs(interfaces=["vector"]))
   from can.interfaces.vector.canlib import get_channel_configs
   for c in get_channel_configs():
       print(c.name, c.channel_bus_capabilities, c.connected_bus_type)
   ```
   Das zeigt, ob die DLL laedt, ob ueberhaupt Kanaele gemeldet werden, und
   welchen Bustyp/welche Capabilities der VN1610-Kanal konkret meldet.

   **Direkt an der angeschlossenen VN1610 nachgemessen (Diagnose-Snippet +
   temporäres Logging im gebauten .exe):** Sowohl per einfachem `python`
   als auch aus `LabControl_v0.9.16.exe` heraus (mehrere Wiederholungen,
   mit UND ohne manuelles Vorladen der DLL) lieferte
   `can.detect_available_configs(interfaces=["vector"])` konsistent alle 4
   Kanäle (VN1610 Channel 1+2, 2x Virtual) -- `find_library("vxlapi64")`
   fand die DLL zuverlässig in `C:\Windows\System32`, `xldriver` lud
   sauber. Der ursprünglich gemeldete "keine Kanäle gefunden"-Fall liess
   sich NICHT reproduzieren. **Schlussfolgerung: vermutlich kein
   deterministischer Code-Bug**, sondern ein einmaliges/zeitliches Problem
   (z.B. Vector-Treiber-Enumeration kurz nach Systemstart/Anstecken der
   VN1610 noch nicht abgeschlossen). `_ensure_vector_dll_findable()` bleibt
   als harmloses Sicherheitsnetz im Code, gilt aber NICHT als bestätigter
   Fix. **Bitte nochmal testen** -- falls die Meldung erneut auftritt, wäre
   der genaue Zeitpunkt hilfreich (direkt nach Windows-Start? kurz nach
   Anstecken der VN1610? nach längerem Leerlauf?), um die eigentliche
   Ursache einzugrenzen.

   **Erneut untersucht, dabei einen ANDEREN, echten Bug gefunden und
   behoben (2026-09-08, an angeschlossener VN1610 verifiziert):** Die
   Discovery selbst liess sich weiterhin nicht zum Versagen bringen
   (`discover_configs()` lieferte reproduzierbar alle 4 Kanäle -- auch
   während bereits ein Bus geöffnet war, was als Erklärung geprüft und
   damit ausgeschlossen wurde). Stattdessen steckte der Fehler im
   VERBINDUNGS-Pfad: `python-can`s `VectorBus` defaultet `app_name` auf
   `"CANalyzer"`, was `can_bus/driver.py` nie überschrieb. `channel=0`
   bedeutete dadurch nicht "Kanal 0 der Hardware", sondern "der Kanal, den
   die Vector Hardware Config der Anwendung *CANalyzer* auf Position 0
   zugewiesen hat" (`_find_global_channel_idx` → `xlGetApplConfig`). Ohne
   eingerichtete CANalyzer-Anwendung schlägt damit jede Verbindung fehl
   (im `DeviceWorker` nur eine Log-Warnung -- das Interface taucht in der
   GUI nie auf), bei anderem Mapping verbindet sich LabControl mit der
   FALSCHEN Hardware ("verbunden", aber nie ein Frame). Zusätzlich meldete
   die Discovery den Hardware-Kanal (0/1 je Gerät), der Verbindungsaufbau
   erwartete aber einen globalen Index über alle Vector-Geräte -- die
   Auswahl bot deshalb viermal nur "0"/"1" an, ununterscheidbar, und die
   virtuellen Kanäle waren gar nicht erreichbar. Fix: immer
   `app_name=None` + Bindung über die Seriennummer, Kanäle als Token
   `"<seriennummer>:<hw_kanal>"`, Auswahl mit sprechendem Namen. Verifiziert
   an echter VN1610: alle 4 Kanäle verbinden, Frame-Sende-/Empfangstest
   (CAN1→CAN2 und virtuell) über `CanBus` und `DeviceWorker` erfolgreich.

   **URSACHE der Meldung selbst gefunden (2026-09-08): Backends fehlten in
   der .exe.** Entscheidender Hinweis war, dass die Meldung nur in
   `dist\LabControl_v0.9.16.exe` auftritt, nie aus dem Quellcode heraus.
   `python-can` lädt seine Interface-Backends über Modulnamen als String
   (`can.interfaces.BACKENDS` → `importlib.import_module`), was PyInstallers
   statische Analyse nicht sieht; einen Hook dafür liefert weder python-can
   noch pyinstaller-hooks-contrib, und `LabControl.spec` hatte
   `hiddenimports=[]`. Direkt an der gebauten .exe nachgewiesen:
   `grep -a -c "can.interfaces.vector"` → **0 Treffer** (ebenso `pcan`,
   `vxlapi`), während `can.interface` und `can_bus.driver` enthalten sind --
   das Paket `can.interfaces` war also dabei, aber kein einziges Backend.
   `detect_available_configs()` fängt den ImportError pro Interface-Typ ab
   und liefert wortlos `[]` → exakt die Meldung im Screenshot. Fix:
   `LabControl.spec` setzt `hiddenimports` jetzt aus
   `can_bus.driver.INTERFACE_LIST` ab. Zusätzlich unterscheidet
   `CanBus.backend_problem()` jetzt "Backend nicht ladbar" von "kein Gerät
   angeschlossen", wird geloggt und in der Meldung als "Grund: ..."
   angezeigt -- genau diese Ununterscheidbarkeit hatte die Suche mehrfach in
   die falsche Richtung geschickt (vermeintlich sporadisch, vermeintlich ein
   DLL-Fundproblem).

## Testablauf-Runner (allgemein)

1. **Vermuteter Bug: Aktion mit Dauer 0 scheint nicht ausgeführt zu werden.**
   Aufgefallen bei microHIL-Digitalausgang ausschalten (`HIL_OUT_OFF`) mit
   Dauer-Spalte = 0. **Code-seitig geprüft, kein Dispatch-Bug gefunden:**
   `TestRunner._advance()` (`testcase_runner.py`) sendet `execute_action`
   für einen Aktionsschritt IMMER, unabhängig von `step.duration` -- die
   Dauer gattert nur den nachfolgenden Wartetimer (`_wait_timer`, korrekt
   `setSingleShot(True)`, kein Wiederhol-Bug), nicht den Befehl selbst.
   Wahrscheinlichste Erklärung: rein optisch -- bei Dauer 0 läuft der Schritt
   so schnell durch, dass das grüne "läuft gerade"-Blinken der Zeile
   (`testcase_tab._start_blink`, 400ms-Takt) kaum/nicht sichtbar aufblitzt,
   bevor schon der nächste Schritt markiert wird, wodurch es so aussieht,
   als wäre nichts passiert, obwohl der Befehl an die Hardware ging.
   **Bräuchte zur Bestätigung:** Hat sich der reale Ausgang (Multimeter/LED)
   bei Dauer 0 tatsächlich NICHT geändert, oder hat nur die GUI-Zeile nicht
   sichtbar aufgeblinkt? Je nach Antwort entweder ein Mindest-Anzeigedauer-
   Fix in `testcase_tab.py` oder weitere Tiefenanalyse nötig.

## GUI / Theme

1. **Bug: Fehlermeldung im Light-Theme unleserlich (weiße Schrift auf
   weißem Hintergrund).** Aufgefallen bei der obigen "Messwert
   veraltet"-Meldung, vermutlich aber ein allgemeines Theme-Problem der
   Fehler-/Statusanzeige, nicht microHIL-spezifisch — beim Umsetzen prüfen,
   ob weitere Fehlermeldungen/Dialoge im Light-Theme denselben Kontrast-Bug
   haben.

   **Umgesetzt (Ursache gefunden):** Das Safety-Banner-Widget (zeigt u.a.
   diese Meldung) hatte kein `WA_StyledBackground`-Attribut gesetzt -- ein
   einfaches `QWidget` malt seinen per QSS gesetzten `background-color`
   ohne dieses Attribut NICHT (bekanntes Qt-Verhalten, dasselbe Muster gab
   es schon in `testcase_tab.py`). Der Banner blieb dadurch transparent auf
   dem normalen (im Light-Theme hellen) Fensterhintergrund, der
   hartkodiert weisse Text darauf unleserlich. Jetzt in `main_window.py`
   ergänzt.

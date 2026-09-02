# Changelog

Alle nennenswerten Änderungen an der Labor-Steuerungs-App. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/), Versionierung nach
Semantic Versioning (`lab_gui/version.py`).

## [Unreleased]

### Hinzugefügt
- **Baustein-Kopfzeile mit eigener Unternummerierung und Zusammenfassung**:
  Die Kopfzeile eines per "Baustein einfügen" hinzugefügten Bausteins zählt
  in der Spalte "#" jetzt normal in der Hauptsequenz mit, während die dazu
  gehörenden Zeilen eine eigene Unternummerierung bekommen (z.B. "4.1",
  "4.2" unter Kopfzeile "4"). Die Zeilen eines Bausteins sind dabei als
  Ganzes (alle Spalten gleichmäßig, nicht nur ein Texteinzug in "Gerät")
  leicht nach rechts verschoben, unabhängig von der Verschachtelungstiefe
  in Schleifen/Wenn-Blöcken (`testcase_tab.py::_renumber_rows`,
  `BLOCK_MEMBER_INDENT_PX`). Die Kopfzeile selbst bleibt dabei UNVERSCHOBEN
  und zeigt dauerhaft "Baustein"/Bausteinname/Schrittzahl an -- auch im
  aufgeklappten Zustand, statt wieder die rohen Geräte-/Aktion-/Wert-Felder
  ihres ersten Schritts freizugeben (`_sync_header_overlay`). Die Spalte
  "Dauer" der Kopfzeile zeigt statt der Dauer des ersten Baustein-Schritts
  die aufsummierte Gesamtdauer aller Baustein-Schritte an und ist nicht
  mehr editierbar; die Spalte "Prüfung" der Kopfzeile lässt sich ebenfalls
  nicht mehr bearbeiten und färbt sich stattdessen nach dem Gesamtergebnis
  aller im Baustein angelegten Prüfungen ein (grün nur, wenn alle bestanden
  haben, sonst rot) -- auch dies unabhängig vom Ein-/Ausklapp-Zustand (neue
  Klasse `_ReadonlyCellOverlay`, `_sync_duration_overlay`/
  `_sync_check_overlay`/`_update_block_check_aggregate` in
  `testcase_tab.py`).
- **Eigenes Increment-Verhalten der Pfeil-Buttons an Sollwert-Eingabefeldern**:
  Ein einfacher Klick auf Hoch/Runter an den Sollwert-Spinboxen im
  Control-Tab (Last: Sollwert; Netzteil: Spannung/Strom/OVP/OCP) ändert den
  Wert jetzt um 0,1, ein gehaltener Klick um 1,0 pro Schritt alle 0,2s,
  statt wie zuvor Qts eingebautes Klick-/Halte-Verhalten zu verwenden
  (siehe [FEATURES.md](FEATURES.md) Punkt 3). Neue Klasse
  `lab_gui/step_spinbox.py::SteppedDoubleSpinBox` (ersetzt `QDoubleSpinBox`
  in den betroffenen Feldern in `lab_gui/control_tab.py`), wertet die
  Maus-Events auf den Pfeil-Subcontrols selbst aus statt Qts internes
  Auto-Repeat zu nutzen; Tastatur-Pfeiltasten und Mausrad bleiben
  unverändert (dort gilt weiterhin `singleStep`).
- **Testablauf-Schritt „Warten“**: Neuer Kontrollfluss-Schritttyp im
  Testablauf-Editor (Menü „Zeile hinzufügen“ → „Warten“), der den
  Testablauf-Fortschritt beim Ausführen einfach um die angegebene
  Zeitspanne pausiert, ohne eine Geräteaktion auszulösen -- z.B. um vor
  einer Prüfung gezielt auf ein langsames Einschwingen zu warten. Zeile
  besteht nur aus einem Dauer-Feld (Sekunden) und
  Aktiv-Checkbox; Fortschrittsanzeige/Report zeigen „Warten (X s)“ wie bei
  den anderen Kontrollfluss-Schritten (`testcase_model.py`:
  `CONTROL_STEP_TYPES`/`CONTROL_STEP_LABELS`; `testcase_runner.py`:
  `_advance()`; `testcase_tab.py`; `run_report.py`; `block_dialog.py`).
- **Benutzerhandbuch / Hilfe**: Neuer Button „Hilfe“ im Einstellungen-Tab
  öffnet ein vollstaendiges Benutzerhandbuch, das Dashboard, alle vier
  Reiter (Steuerung/Testablauf/Verlauf/Einstellungen), den
  Sicherheits-Watchdog und die Statuszeile beschreibt (siehe
  [FEATURES.md](FEATURES.md) Punkt 3). Neues Modul
  `lab_gui/help_dialog.py` (`HelpDialog`) zeigt den Text ueber
  `QTextBrowser.setMarkdown()` direkt in der App an, statt einen
  externen Browser/PDF-Reader zu oeffnen -- funktioniert dadurch
  identisch im Dev-Betrieb und in der `.exe`. Der Anleitungstext liegt
  als mitgelieferte Markdown-Datei je Sprache unter neuem `lab_gui/help/`
  (`manual_de.md`/`manual_en.md`), gebuendelt nach demselben
  MEIPASS-Muster wie `translations/`/`icons/` (`LabControl.spec`:
  `datas`).
- **Wiederverwendbare Testcase-Bausteine**: Ein zusammenhaengender
  Zeilenbereich des Testablauf-Editors (z.B. ein Entladeprofil) laesst sich
  jetzt unter einem Namen als eigene Datei ablegen und in beliebiger Stelle
  desselben oder eines anderen Testablaufs wieder einfuegen, statt haeufige
  Schrittfolgen jedes Mal neu aufzubauen (siehe [FEATURES.md](FEATURES.md)
  Punkt 3). Neue Toolbar-Buttons „Baustein speichern…“/„Baustein
  einfügen…“ neben den bestehenden Laden/Speichern-Buttons
  (`lab_gui/testcase_tab.py`, `_save_block`/`_insert_block_from_file`);
  Zeilenbereich + Name werden im neuen `block_dialog.SaveBlockDialog`
  gewaehlt, das den Bereich per `testcase_model.validate_structure()` gegen
  strukturelle Unwucht prueft (z.B. eine Schleife ohne ihr „Ende“ im Bereich)
  und den OK-Button dafuer sperrt. Eigenes Dateiformat
  `testcase_model.save_block`/`load_block` (`"labor-testcase-block"`,
  zusaetzlich zum Schritte-Array mit Namen versehen), abgelegt unter dem
  neuen Verzeichnis `blocks/` neben `testcases/`, damit ein Baustein nicht
  versehentlich ueber „Testablauf laden…“ als vollstaendiger Ablauf geoeffnet
  werden kann. Ein eingefuegter Baustein (ab zwei Zeilen) erscheint im Editor
  standardmaessig eingeklappt auf nur seine erste Zeile reduziert, mit
  Klapp-Pfeil-Icon in der Zeilennummer-Spalte zum Auf-/Zuklappen; im
  aufgeklappten Zustand sind die uebrigen Baustein-Zeilen wie ein
  Schleifen-/Wenn-Rumpf zusaetzlich eingerueckt. Rein editorseitige
  Gruppierung (neue Klasse `testcase_tab._BlockGroup`) ohne Einfluss auf
  Testschritt-Daten, Speichern oder Ausfuehrung -- verborgene Zeilen bleiben
  ganz normal Teil von `steps()`; waehrend eines Testlaufs klappt eine
  eingeklappte Gruppe automatisch auf, sobald ihr laufender Schritt erreicht
  wird (`_reveal_row`), damit der Fortschritt sichtbar bleibt.
- **Platzhalter-Kachel „Kein Gerät verbunden"**: Dashboard und Control-Tab
  zeigen jetzt eine graue Hinweis-Kachel, solange kein einziges Gerät (weder
  Last noch Netzteil) verbunden ist -- neues Modul `lab_gui/no_device_tile.py`
  (`NoDeviceTile`), eingebunden in `DashboardWidget`/`ControlTab`
  (`_update_empty_tile`, aufgerufen bei jedem Online-/Offline-Wechsel).
  Hintergrundfarbe (`#9e9e9e`) ist bewusst fest verdrahtet statt aus
  `theme.Palette` -- bleibt dadurch in beiden Themes und unabhängig von
  individuellen Panel-Farben (`panel_color.py`) immer gleich grau.
- **Dashboard-Panel bleibt nach dem Trennen stehen (ausgegraut)**: Ein
  einmal bekanntes Geräte-Panel im Dashboard verschwindet beim Trennen nicht
  mehr, sondern wird fest grau eingefärbt (dieselben Farben wie die
  „Kein Gerät verbunden"-Kachel, siehe oben) und bekommt oben rechts ein
  „Verbindung getrennt"-Icon (`mdi.lan-disconnect`) --
  `_DevicePanel.set_online` (`dashboard.py`), Position per `resizeEvent`
  nachgeführt. Die zuletzt bekannte Kachel bleibt damit als Erinnerung
  sichtbar, statt spurlos zu verschwinden. Die Platzhalter-Kachel
  „Kein Gerät verbunden" richtet sich dadurch jetzt danach, ob JEMALS ein
  Gerät bekannt wurde, nicht mehr nach dessen Online-Status. Im Control-Tab
  bleibt das bisherige Verhalten (Sektion verschwindet beim Trennen)
  unverändert.
- **Button „Gerätezuordnung löschen" im Einstellungen-Tab**: Setzt alle
  gespeicherten Geräte-Namen (`device_registry.DeviceRegistry.reset_all`),
  Sicherheits-Grenzwerte und Panel-Farben (`settings.Settings.
  reset_device_settings`) auf die Standardwerte zurück, mit
  Rückfrage (`settings_tab.py`, `QMessageBox.question`, analog zu
  `timeline_tab._on_recording_clear_clicked`). Wirkt sofort live, kein
  Neustart nötig, und unterscheidet zwei Fälle je zuvor bekanntem Gerät
  (`main_window._on_reset_devices_requested`): ein AKTUELL VERBUNDENES Gerät
  bekommt nur einen frischen Standardnamen (über `DeviceRegistry.
  on_device_added`, wie beim allerersten Verbinden — darüber laufen
  Dashboard/Control-Tab/Testablauf/Verlauf/Statusleiste/Einstellungen-Tab
  automatisch über die bestehende `device_known`-Verkabelung mit), ein NICHT
  verbundenes Gerät (z.B. eine ausgegraute Dashboard-Kachel) wird dagegen
  komplett vergessen (`main_window._forget_device`, neue
  `forget_device()`-Methode in `dashboard.py`/`control_tab.py`/
  `settings_tab.py`/`testcase_tab.py` sowie Aufräumen der Statuszeile) —
  ohne diese Unterscheidung blieb ein getrenntes Gerät trotz Klick auf den
  Button überall sichtbar stehen, nur umbenannt statt vergessen.
- **Last-Modus im Dashboard-Panel**: Das Dashboard-Panel der elektronischen
  Last zeigt jetzt zusätzlich zu Spannung/Strom/Leistung den aktuell aktiven
  Betriebsmodus an (CC/CV/CR/CW/SHORT), analog zur bestehenden CC/CV-Anzeige
  beim Netzteil. Neues `DeviceWorker`-Signal `load_function_state`
  (`device_worker.py`, fragt `KoradKEL102.get_function()` pro Poll-Zyklus
  sowie sofort nach dem Verbinden ab) → `DashboardWidget.set_load_mode`
  (`dashboard.py`), inkl. Mapping der SET-Codes (`CURR/VOLT/RES/POW`) und der
  von echter Hardware zurückgelieferten Kurzform (`CC/CV/CR/CW`) auf dieselbe
  Anzeige (`LOAD_MODE_SHORT`).
- **Individuelle Panel-Hintergrundfarben (Dashboard/Control)**: Jedes
  Geräte-Panel kann über einen neuen Paletten-Button im Panel-Header eine von
  7 Akzentfarben bekommen (Blau/Türkis/Grün/Orange/Violett/Pink/Grau, je
  Theme eigens abgestimmt) oder „Kein (Standard)". Die Farbe gilt pro
  Geräte-ID und wird in Dashboard und Control-Tab synchron angezeigt, damit
  ein Gerät überall an derselben Farbe erkennbar bleibt. Global
  ein-/ausschaltbar im Einstellungen-Tab (Standard: aus); die Auswahl bleibt
  beim Ausschalten gespeichert. Neues Modul `lab_gui/panel_color.py`
  (`PanelColorButton`, `apply_panel_tint`), Farbwerte in
  `theme.Palette.panel_tints`.
- **Testablauf: Plus- und Menü-Klick am „Zeile hinzufügen"-Button getrennt**:
  Ein Klick auf das Plus-Icon fügt jetzt direkt eine neue Aktionsschritt-Zeile
  ein, ohne das Dropdown-Menü zu öffnen; ein Klick auf den Menü-Pfeil öffnet
  wie bisher das Auswahlmenü (Schleife/Solange/Wenn/…). Neue Klasse
  `SplitIconButton` (`lab_gui/icons.py`, `QToolButton` mit
  `MenuButtonPopup`-Modus statt `IconButton`/`QPushButton`, das Icon- und
  Menü-Klick nicht unterscheiden kann) — nur für diesen Button verwendet
  (`lab_gui/testcase_tab.py`).
- **Aufnahme-Start/Stopp zu einem Button vereint**: Statt getrennter
  „Aufnahme starten"/„Aufnahme stoppen"-Buttons gibt es im Verlauf-Reiter
  jetzt einen einzigen Button — statisch rot, solange keine Aufnahme läuft,
  blinkt rot während der Aufnahme (`lab_gui/timeline_tab.py`). Dafür bekam
  `IconButton` eine `set_color_override()`-Methode für eine vom Theme
  unabhängige Icon-Farbe (`lab_gui/icons.py`).
- **Aufnahme zeichnet nur noch Diagramm-zugeordnete Signale auf**: Bisher
  zeichnete `Recorder` Messwerte aller bekannten Geräte auf, unabhängig
  davon, ob sie einem Diagramm im Verlauf-Reiter zugeordnet sind. Jetzt
  werden nur Signale aufgezeichnet, die aktuell mindestens einem Diagramm
  zugeordnet sind (`Recorder.set_active_signals`,
  `TimelineTab.active_signals_changed`/`active_signal_keys`). **Achtung**:
  Solange kein Signal einem Diagramm zugeordnet ist (Startzustand mit leerem
  Diagramm), zeichnet eine gestartete Aufnahme zunächst nichts auf.
- **Desktop-Benachrichtigung bei Lauf-Ende/Fehler**: Über
  `QSystemTrayIcon.showMessage` (nur wenn ein System-Tray verfügbar ist —
  auf dem Kiosk-Pi ohne Taskleiste bleibt die Funktion ein stilles No-Op),
  ein-/ausschaltbar im Einstellungen-Tab (Standard: an). Praktisch bei
  langen unbeaufsichtigten Läufen (z.B. Akku-Zyklen über Nacht).
- **Arbiträrsignal-Generator: Dreieck- und Sägezahn-Signalform, einstellbarer
  Tastgrad beim Rechtecksignal**: Zusätzlich zu Sinus/Rechteck stehen jetzt
  Dreieck- und Sägezahn-Kurven zur Verfügung; beim Rechtecksignal lässt sich
  der Tastgrad (Anteil High-Phase) statt des bisher fest verdrahteten 50/50
  einstellen (`TestStep.arb_duty`, `lab_gui/testcase_model.py::arb_value`,
  neues Tastgrad-Feld in `lab_gui/signal_dialog.py`, nur bei Rechteck
  sichtbar).
- **Verlauf-Diagramme: vertikale Gitterlinien + feste Y-Achsen-Skalierung**:
  Diagramme zeigen jetzt zusätzlich zu horizontalen auch vertikale
  Gitterlinien. Die Y-Achsen-Skalierung lässt sich je Diagramm per neuem
  Button im Diagramm-Header von automatisch (bisheriges Verhalten) auf feste,
  selbst gesetzte Wertebereiche umschalten (`lab_gui/timeline_tab.py`,
  `_ScopeChart.set_y_mode`/`set_fixed_range`, neuer `_YAxisDialog`) — gilt
  bewusst nur für die laufende Session, keine Persistenz über
  `timeline_layout.json`.
- Version auf 0.8.0 angehoben.
- **Bootloader-Splash beim .exe-Start**: Die Onefile-.exe entpackt sich bei
  jedem Start komplett in ein Temp-Verzeichnis, bevor Python überhaupt läuft
  — bisher ohne jede Rückmeldung, fühlte sich wie ein Hänger an. Ein
  PyInstaller-`Splash` (`LabControl.spec`, Bild aus
  `tools/generate_splash.py`) erscheint jetzt bereits während dieser Phase
  und wird von `lab_gui/main.py` (`pyi_splash.close()`) geschlossen, sobald
  das Hauptfenster steht. Im Dev-Betrieb ohne Onefile-Build ein No-Op.
- **.exe-Größe von 258 MB auf ~92 MB reduziert**: Der PySide6-PyInstaller-Hook
  bündelte bisher ungenutzte Qt-Submodule mit — allein `Qt6WebEngineCore.dll`
  war 205 MB groß, dazu QtQml/QtQuick/Qt3D/QtCharts/QtLocation/virtuelle
  Tastatur, obwohl nichts davon im Programm verwendet wird (reines
  QtWidgets-Programm). Jetzt explizit über `excludes` in
  `LabControl.spec` ausgeschlossen — das reduziert nebenbei auch die
  Onefile-Entpackzeit bei jedem Start spürbar. `pandas`/`numexpr` bewusst
  NICHT ausgeschlossen: `asammdf` importiert `pandas` fest auf Modulebene
  (`asammdf/blocks/mdf_v4.py`), ein Ausschluss hätte den MF4-Export
  gebrochen.
- **GitHub-Actions-Build nutzt jetzt dieselbe `.spec`-Datei wie der lokale
  Build**: Der Workflow (`.github/workflows/build-exe.yml`) rief PyInstaller
  bisher direkt per CLI-Flags auf, komplett unabhängig von den lokalen
  `.spec`-Dateien — Splash und die Qt-Submodul-`excludes` (siehe oben)
  hätten sich dadurch NICHT auf die automatisch veröffentlichten Releases
  ausgewirkt. Neue, versionsdynamische `LabControl.spec` (liest die
  Versionsnummer zur Baubauzeit aus `lab_gui/version.py`, statt sie wie
  bisher im Dateinamen und `name=` hart zu verdrahten) ersetzt sowohl die
  alten, pro Version manuell angelegten `.spec`-Dateien als auch die
  CLI-Flags im Workflow (`pyinstaller LabControl.spec`). Einzige `.spec`-
  Datei, die nicht mehr über `*.spec` in `.gitignore` ausgeschlossen ist
  (die CI braucht sie beim Checkout).
- **Pfeil-Icons an Zahlenfeldern besser erkennbar**: Die Auf/Ab-Pfeile an
  Eingabefeldern (z.B. Sollwerte) waren mit 10×10px auf dem Touch-Kiosk kaum
  als Pfeil erkennbar und nicht themefähig (feste PNG-Datei, keine Bindung an
  die Textfarbe der aktiven Palette). Werden jetzt aus `qtawesome` erzeugt
  (`mdi.chevron-up`/`mdi.chevron-down`, wie alle anderen Icons im Programm)
  statt als statische Datei gepflegt — dadurch größer (14×14px) und
  automatisch farblich zur aktiven Palette passend (`lab_gui/theme.py`).
- **Software-Presets: 5 feste, geräteübergreifende Preset-Plätze**: Ersetzt
  die frühere, rein geräteseitige PSU-Preset-Funktion (P1/P2/P3 auf dem
  HCS-34xx selbst) durch eine neue Preset-Leiste ganz oben im Control-Tab
  (`lab_gui/presets.py`: `PresetStore`, `lab_gui/control_tab.py`:
  `PresetBar`). 5 Plätze, lokal als JSON gespeichert; jeder Platz speichert
  je aktuell sichtbarem Gerät (Last und/oder Netzteil, auch mehrere
  gleichzeitig) Sollwerte UND Schaltstatus (Last-Eingang/PSU-Ausgang)
  gemeinsam. Jeder Platz ist ein größerer (`lab_gui/control_tab.py`:
  `_PresetSlotButton`), dezent hervorgehobener Haupt-Button (Preset laden)
  mit zwei kleinen Sub-Buttons Speichern (übernimmt den
  aktuellen Zustand aller sichtbaren Geräte-Panels) und Umbenennen, die als
  Ecken-Buttons oben rechts bzw. unten rechts optisch zum Haupt-Button
  gehören statt in einer eigenen Reihe daneben zu stehen. „Laden" schreibt
  sofort auf die Hardware (inkl. Ein-/Ausschalten) statt nur die
  Eingabefelder vorzubelegen — ein gespeicherter Schaltstatus lässt sich
  nicht sinnvoll nur anzeigen, ohne ihn auch anzuwenden. Es gibt dafür keine
  separate Preset-Zeile mehr in den einzelnen Geräte-Panels (Last/Netzteil).
  Die Hervorhebung nutzt bewusst denselben abgetönten Farbton wie die
  individuellen Geräte-Panel-Farben (`Palette.panel_tints`, fix "blue") statt
  des vollen Theme-Akzenttons — letzterer wirkte als Dauerfläche zu grell.
- Version auf 0.9.0 angehoben.
- Version auf 0.9.3 angehoben (bündelt u.a. mehrere an echter Hardware
  verifizierte Fixes, siehe [BUGS_GESCHLOSSEN.md](BUGS_GESCHLOSSEN.md)).

### Geändert
- **Simulationsmodus nur noch im Dev-Betrieb verfügbar**: In der von
  PyInstaller gebauten Release-`.exe` ist die Option im Einstellungen-Tab
  jetzt komplett ausgeblendet, `Settings.simulation_mode` liefert dort
  zusätzlich hart `False` (auch falls eine ältere `settings.json` noch
  `true` enthält) — verhindert, dass ein Release-Build versehentlich mit
  simulierten statt echten Geräten läuft (siehe [FEATURES.md](FEATURES.md)
  Punkt 4). Unterscheidung über `sys.frozen`, neue Konstante
  `paths.IS_FROZEN`, analog zum bereits bestehenden Muster in
  `paths.app_dir()`.
- **Einstellungen-Tab: thematisch gruppiert + Buttons nicht mehr volle
  Breite**: Dünne Trennlinien (neue Helfer `settings_tab._separator()`)
  gliedern den Tab jetzt in Simulationsmodus / Darstellung (Dark Mode,
  Benachrichtigungen, Panel-Farben, Sprache) / Hilfe / Geräteverwaltung
  („Gerätezuordnung löschen") / Sicherheit (Grenzwerte). Die Trennlinie vor
  dem Simulationsmodus wird in der Release-`.exe` zusammen mit der
  (ohnehin ausgeblendeten) Option selbst versteckt, sonst stünde dort eine
  Linie ohne Inhalt darüber. „Hilfe"- und „Gerätezuordnung löschen"-Button
  sind jetzt (neuer Helfer `settings_tab._button_row()`, analog zur
  bestehenden `language_row`) nur noch so breit wie ihr Text statt über die
  volle Tab-Breite gestreckt — `QPushButton` hat standardmäßig eine
  horizontale `Minimum`-SizePolicy (kann wachsen), die ihn in einem
  `QVBoxLayout` ohne einschränkenden Wrapper auf die volle verfügbare
  Breite zieht.
- **„Panel-Farbe wählen…"-Button im Control-Tab nur noch bei aktivierten
  individuellen Panel-Farben sichtbar**: Vorher stand der Button in jeder
  Geräte-Sektion, auch wenn die Option „Individuelle Panel-Hintergrundfarben"
  im Einstellungen-Tab ausgeschaltet war — eine Auswahl anzubieten, die
  gerade gar nicht wirkt, war irreführend. Neue Methode
  `LoadControlGroup.set_colors_enabled`/`PsuControlGroup.set_colors_enabled`
  (`lab_gui/control_tab.py`), aufgerufen von `ControlTab.
  set_panel_colors_enabled` (bestehender globaler An/Aus-Schalter) und für
  neu hinzukommende Geräte direkt in `ControlTab.on_device_known`.

### Entfernt
- **Testablauf-Aktion „Preset P1/P2/P3 abrufen"**: Griff auf die
  geräteseitigen HCS-34xx-Presets zu (`hcs34xx/driver.py: recall_memory`);
  im Control-Tab gab es dafür schon länger keinen Button mehr, nur noch diese
  Aktion im Testablauf-Editor. Durch die neuen Software-Presets (siehe oben)
  ersetzt — die sind bewusst nicht im Testablauf-Editor nutzbar, nur im
  Control-Tab. Bestehende Testablauf-Dateien mit dieser Aktion lassen sich
  weiterhin öffnen (unbekannter Aktionscode fällt beim Bearbeiten der Zeile
  auf die erste verfügbare Aktion zurück, kein Absturz).

### Behoben
- **[BUGS.md #16, tatsächliche Root Cause -- vorheriger Fix wirkungslos]
  Control-Tab: „Kein Gerät verbunden"-Kachel blieb nach „Geräte-Zuordnung
  löschen" gleichzeitig mit weiterhin verbundenen Geräten sichtbar**: Der
  vorherige Fix (`_update_empty_tile()` zusätzlich im Relabel-Pfad
  aufrufen) traf nicht die eigentliche Ursache und blieb laut
  Nutzer-Rückmeldung im Live-Test wirkungslos. Tatsächliche Ursache:
  `_update_empty_tile()` prüfte `section.isVisible()` — das hängt in Qt
  aber von der GESAMTEN Vorfahrenkette ab, nicht nur vom eigenen
  Sichtbarkeits-Flag. Der „Geräte-Zuordnung löschen"-Button sitzt im
  Einstellungen-Tab; während er geklickt wird, ist der Control-Tab
  zwangsläufig NICHT die aktive Seite des `QTabWidget` und daher selbst
  unsichtbar — dadurch lieferte JEDE Geräte-Sektion `isVisible() == False`,
  unabhängig vom tatsächlichen Verbindungsstatus. Die Platzhalter-Kachel
  wurde so fälschlich sichtbar geschaltet und blieb es auch nach dem
  Zurückwechseln auf den Control-Tab, während die eigentlich verbundenen
  Sektionen (ihr eigenes Sichtbarkeits-Flag war ja unverändert) ebenfalls
  wieder erschienen. Per gezieltem Offscreen-Repro (ControlTab als Seite
  eines `QTabWidget` mit einer zweiten, aktiven Seite) reproduziert und
  verifiziert. Fix: `_update_empty_tile()` nutzt jetzt `isHidden()` (das
  eigene, explizit gesetzte hide()/show()-Flag einer Sektion, unabhängig
  vom Sichtbarkeitszustand der Vorfahren) statt `isVisible()`
  (`lab_gui/control_tab.py`).
- **[BUGS.md #20] Testablauf-Editor: „Zeile hinzufügen" fügte bei markierter
  Baustein-Kopfzeile in den Baustein ein statt darunter**:
  `_insert_at_selection()` berechnete die Einfügeposition immer als
  `selected_row + 1`, ohne zu prüfen, ob die markierte Zeile die Kopfzeile
  eines Bausteins ist — landete dadurch strikt innerhalb des Bausteins
  statt danach. Ist die markierte Zeile eine Baustein-Kopfzeile, wird jetzt
  hinter den gesamten Baustein eingefügt (`group.start + group.count`);
  betrifft denselben Mechanismus auch bei „Baustein einfügen…"
  (`lab_gui/testcase_tab.py`).
- **[BUGS.md #22] Testablauf-Editor: „Zeile entfernen" bei
  Baustein-Kopfzeile löschte nicht den ganzen Baustein, keine
  „modifiziert"-Kennzeichnung**: Drei zusammenhängende Punkte rund um
  eingefügte Bausteine. a)/b) „Zeile entfernen" bei markierter (im
  eingeklappten Zustand ohnehin einzig markierbarer) Baustein-Kopfzeile
  löscht jetzt den kompletten Bereich statt nur der Kopfzeile — im
  aufgeklappten Zustand bleibt das Entfernen einzelner Mitgliederzeilen
  unverändert möglich. c) Neues `_BlockGroup.modified`-Flag, gesetzt von
  `_on_row_inserted`/`_on_row_removed` bei einer Zeilenzahl-Änderung
  INNERHALB eines bestehenden Bausteins (nicht beim Auflösen/kompletten
  Löschen) — die Kopfzeilen-Zusammenfassung zeigt bei gesetztem Flag
  zusätzlich zum Namen „(modifiziert)" an (`lab_gui/testcase_tab.py`).
- **[BUGS.md #21] Eigenes Increment-Verhalten der Pfeil-Buttons nur im
  Control-Tab umgesetzt, nicht app-übergreifend**: `SteppedDoubleSpinBox`
  (siehe Eintrag oben) war bisher nur an den Sollwert-Feldern in
  `control_tab.py` im Einsatz, alle übrigen Zahlen-Eingabefelder der App
  nutzten weiterhin Qts eingebautes Klick-/Halte-Verhalten. Gemeinsame
  Maus-Event-Logik nach `_SteppedSpinMixin` ausgelagert; neue
  `SteppedSpinBox`-Klasse für Ganzzahl-Felder (Default 1/Klick, 10/Schritt
  beim Halten, pro Feld über `small_step`/`large_step` anpassbar, z.B.
  50/200 für das ms-Intervall im Arbiträrsignal-Dialog) ergänzt
  `SteppedDoubleSpinBox`. Jetzt überall eingesetzt: Sicherheits-Grenzwerte
  (`settings_tab.py`), Wert-/Dauer-Felder und Durchlaufzahl bei Schleifen
  (`testcase_tab.py`), Bedingungs-Werte und max. Iterationen
  (`condition_dialog.py`), Prüfung Min/Max (`check_dialog.py`),
  Arbiträrsignal-Parameter (`signal_dialog.py`), Y-Achsen-Grenzwerte
  (`timeline_tab.py`), Zeilenbereich von/bis (`block_dialog.py`)
  (`lab_gui/step_spinbox.py` und die genannten Module).
- **Mitgelieferte Sprachdateien/Benutzerhandbuch in der `.exe` unerreichbar
  (Sprachumschaltung fiel lautlos auf Deutsch zurück, Hilfe-Dialog zeigte
  eine leere Fläche)**: `LabControl.spec` bündelte `lab_gui/translations`
  und `lab_gui/help` mit demselben `lab_gui/`-Präfix als Ziel-Verzeichnis
  im Onefile-Build, wodurch sie zur Laufzeit unter
  `<Extraktionsordner>/lab_gui/translations/…` bzw. `…/lab_gui/help/…`
  landeten. `i18n._TRANSLATIONS_DIR`/`help_dialog._HELP_DIR` suchten dort
  aber ohne dieses Präfix (`<Extraktionsordner>/translations/…` bzw.
  `…/help/…`) — ein Nachweis über den Inhalt eines echten
  Extraktions-Temp-Ordners bestätigte die Abweichung. Die Ziel-Pfade in
  `LabControl.spec: datas` sind jetzt flach (`translations`, `help`
  statt `lab_gui/translations`, `lab_gui/help`), passend zum bereits
  vorhandenen Code; per Neu-Build verifiziert (Archiv-Inspektion zeigt
  `translations\de.json`/`help\manual_de.md` an der erwarteten Stelle).
  Betraf vermutlich jede bisher gebaute `.exe` seit Einführung von
  `LabControl.spec` (v0.9.0) — nicht neu durch das Benutzerhandbuch-Feature
  in dieser Session, nur dadurch aufgefallen.
- **[Sicherheitskritisch] Setzen von Strom/Spannung schaltete den
  PSU-Ausgang real ein, obwohl der Schalter auf „Aus" stand** (an echter
  HCS-34xx-Hardware reproduziert): Die Spannung/Strom-„Setzen"-Buttons im
  Control-Tab griffen unabhängig vom Ausgang-Schalter direkt durch und hoben
  damit den emulierten „Aus"-Zustand (Strom=0A) unbemerkt auf. Die Buttons
  sind jetzt gesperrt, solange der Ausgang nicht aktiv über „EIN"
  eingeschaltet wurde (`lab_gui/control_tab.py`). Das HCS-34xx hat laut
  eigener Treiber-Doku kein echtes Ausgang-AUS-Kommando — dieser Fix schließt
  nur den GUI-Pfad, nicht automatisierte Testablauf-Aktionen, die
  PSU_VOLT/PSU_CURR unabhängig von PSU_OUT_ON/OFF verwenden.
- **Dashboard-Panels ohne erkennbare Trennung beim ersten Start**:
  Nebenwirkung des Bug-8-Fixes (Container-Hintergrund jetzt `pal.surface`,
  identisch zur Panel-Fläche) — der sehr helle Standard-Rahmen reichte allein
  nicht mehr als Trennung. Panels haben jetzt immer einen deutlich
  sichtbaren Rahmen (`pal.text_muted`), unabhängig von einer individuellen
  Farbe (`lab_gui/dashboard.py`).
- **Panel-Farbwahl und Umbenennen-Button jetzt nur noch im Control-Tab**:
  Beide Bedienelemente aus dem reinen Anzeige-Dashboard entfernt; der
  Umbenennen-Button existierte im Control-Tab bisher gar nicht und wurde dort
  neu ergänzt (`lab_gui/dashboard.py`, `lab_gui/control_tab.py`,
  `lab_gui/main_window.py`).
- **Individuelle Panel-Farben im Dark Mode kaum sichtbar**: Die Tönungswerte
  lagen mit einer Leuchtdichte von ~34-45 fast auf demselben Niveau wie die
  Panel-Fläche selbst (~34) — praktisch unsichtbar. Neue Werte liegen bei
  ~65-83, deutlich abgesetzt (`lab_gui/theme.py`).
- **Panel-Farben werden jetzt automatisch vergeben**: Beim Aktivieren der
  Option „Individuelle Panel-Hintergrundfarben" im Einstellungen-Tab bekommt
  jedes bereits bekannte Gerät automatisch eine unterschiedliche Farbe, statt
  dass jedes Panel manuell eingefärbt werden muss; bereits gesetzte Farben
  bleiben beim Aus-/Wiedereinschalten erhalten (`lab_gui/main_window.py`).
- **Einstellungen-Tab: Sicherheits-Grenzwerte-Panels unnötig über die volle
  Breite gestreckt**: `QFormLayout`s Default-Wachstumspolicy ließ die
  Feld-Spalte auf volle Breite wachsen; Panels sind jetzt kompakt, nur so
  breit wie der Inhalt braucht (`lab_gui/settings_tab.py`).
- **Individuelle Panel-Farbe färbte auch Buttons/Eingabefelder im Panel ein**
  (an echter Hardware reproduziert, erster Fix-Versuch am echten Gerät noch
  wirkungslos): Root Cause war eine selektorlose `background-color: X;`-
  Eigenschaft auf der GroupBox bzw. den Sollwert-Zeilen-Wrappern
  (`control_tab._row`) — sobald im selben Instanz-Stylesheet zusätzlich
  Selektor-Regeln (die neuen Button-/Eingabefeld-Regeln) folgen, wird eine
  führende selektorlose Eigenschaft von Qt nicht mehr zuverlässig nur auf
  das eine Widget beschränkt und schlägt weiter auf Kind-Widgets durch. Die
  Button-/Eingabefeld-Regeln wurden in eine geteilte Funktion
  `theme.form_control_qss()` ausgelagert; die eigene Hintergrundfarbe wird
  jetzt mit explizitem Typ-Selektor geschrieben (`QGroupBox { ... }` /
  `QWidget { ... }`), wodurch normale QSS-Spezifität dafür sorgt, dass die
  spezifischere Button-/Eingabefeld-Regel zuverlässig gewinnt
  (`lab_gui/theme.py`, `lab_gui/panel_color.py`, `lab_gui/control_tab.py`).
- **Zeilen-Beschriftungen (Sollwert/Spannung/Modus/Ausgang usw.) zeigen jetzt
  die individuelle Panel-Farbe**: Anders als Buttons/Eingabefelder (siehe
  vorheriger Punkt) sollen reine Text-Beschriftungen sich optisch in die
  getönte Panel-Fläche einfügen statt sich davon abzuheben. Die von
  `QFormLayout` automatisch erzeugten Zeilen-Labels hatten bisher kein
  eigenes Stylesheet und zeigten dadurch die allgemeine Seiten-
  Hintergrundfarbe statt der Panel-Tönung. Neue Hilfsfunktion
  `control_tab._detint_label()` setzt „background: transparent;" auf jedes
  Zeilen-Label, damit die Panel-Fläche dahinter durchscheint
  (`lab_gui/control_tab.py`).
- **Testablauf-Reiter: "Zeile hinzufügen"-Button sah anders aus als übrige
  Buttons**: `SplitIconButton` (`icons.py`) ist als einziger Button im
  Programm ein `QToolButton` statt `QPushButton` (für Qt's
  `MenuButtonPopup`-Modus) — dem globalen Stylesheet fehlte dafür jede
  Regel, wodurch der Button komplett auf natives Styling zurückfiel (andere
  Farbe, native statt theme-eigene Hover-Hervorhebung). Neue
  `QToolButton`-Regeln (identisch zu `QPushButton`) plus
  `QToolButton::menu-button` (Trennlinie zur Menü-Pfeil-Klickzone) in
  `theme.form_control_qss()` ergänzt.
- **"Zeile hinzufügen"-Button: "+"-Icon nicht zentriert, Dropdown-Pfeil zu
  groß**: Nachbesserung zum vorherigen Punkt. Qt zentriert das Icon eines
  `QToolButton` standardmäßig über die gesamte Button-Breite inkl. der
  reservierten Menü-Pfeil-Zone statt nur über die eigentliche
  Icon-Klickfläche — zusätzliches `padding-right` gleicht das aus. Der
  native (grobe) Pfeil wurde durch dasselbe schlanke qtawesome-Chevron wie
  bei den Spinbox-Pfeilen ersetzt (`QToolButton::menu-arrow` — das
  zuständige Subcontrol bei `MenuButtonPopup`, weder `down-arrow` noch
  `menu-indicator` griffen hier) (`lab_gui/theme.py`).
- **Getrenntes Gerät nach App-Neustart spurlos aus dem Dashboard
  verschwunden**: Der "einmal bekannt"-Status eines Geräts existierte bisher
  nur im laufenden Prozess (`DashboardWidget._panels`) — nach einem Neustart
  fehlte die ausgegraute Erinnerungs-Kachel für ein aktuell nicht
  verbundenes, aber früher schon einmal gesehenes Gerät komplett, obwohl sein
  Name weiterhin in `device_labels.json` stand. Neue Methode
  `DeviceRegistry.known_devices()` plus `main_window._replay_known_devices()`
  (aufgerufen einmalig beim Start, nach allen `_wire_*()`-Verkabelungen):
  spielt für jedes gespeicherte Gerät `device_known` erneut ein und markiert
  es als offline (`_on_load_connected`/`_on_psu_connected` mit
  `online=False`) — dieselben Pfade wie bei einer echten
  Verbindungsänderung, daher ohne Sonderfall in Dashboard/Control-Tab/
  Statusleiste. Ein tatsächliches (Wieder-)Verbinden überschreibt diesen
  Startzustand danach ganz normal.
- **Ausgegraute Dashboard-Kachel im Dark Mode kaum lesbar** (Screenshot-Bug):
  Zwei Ursachen. (1) Die feste Titel-Textfarbe (`OFFLINE_TEXT`, dunkel) war
  für die native `QGroupBox::title`-Zeile gedacht, aber gegen das
  Amber-Industrial-Theme nicht kontrastreich genug geplant — jetzt ohne
  eigene Farbregel, fällt zurück auf die globale
  `QGroupBox::title { color: pal.text }`-Regel (dieselbe, die die normalen
  Online-Panels bereits problemlos nutzen). (2) Das „Verbindung
  getrennt"-Icon oben rechts (`_offline_icon`) war als direktes Kind-Widget
  der `QGroupBox` OHNE `theme.no_own_background()` angelegt und erbte
  dadurch die globale `QWidget { background-color: pal.bg }`-Regel — im Dark
  Mode ein praktisch schwarzes Quadrat, das das Icon-Pixmap komplett
  verdeckte. Jetzt mit `no_own_background()` transparent (`lab_gui/
  dashboard.py`).
- **„Verbindung getrennt"-Icon trotz Fix oben weiterhin kaum erkennbar**:
  Ursache war diesmal nicht Sichtbarkeit, sondern die Symbolwahl selbst --
  `mdi.lan-disconnect` (zwei Geräte-Rechtecke + Verbindungslinie + X) ist bei
  18px zu detailreich, um noch als Silhouette erkennbar zu sein, unabhängig
  vom Theme. Ersetzt durch `mdi.close-network-outline` (ein Geräte-Symbol
  mit deutlichem X, klare Silhouette) und auf 22px vergrößert (`lab_gui/
  dashboard.py`).
- **[BUGS.md #17] Testablauf-Aktion „Netzteil: Ausgang EIN" (`PSU_OUT_ON`)
  änderte ungewollt die Spannung**: Der Schritt hatte ein aktives
  Spannungs-Wertfeld und überschrieb beim Ausführen immer den zuletzt per
  `PSU_VOLT` gesetzten Sollwert. `PSU_OUT_ON` ist jetzt eine reine
  Schaltaktion (analog `PSU_OUT_OFF`) — lässt die Spannung unangetastet und
  hebt nur den Strom auf mind. 0,1A an (`lab_gui/testcase_model.py`:
  `VALUELESS_ACTIONS`/`ACTION_VALUE_RANGE`; `lab_gui/device_worker.py`:
  `_dispatch_action`).
- **[BUGS.md #16] Control-Tab: „Kein Gerät verbunden"-Kachel blieb nach
  „Geräte-Zuordnung löschen" sichtbar, obwohl andere Geräte weiter verbunden
  waren**: `ControlTab.on_device_known` prüfte die Sichtbarkeit der
  Platzhalter-Kachel bisher nur beim Neuanlegen einer Sektion, nicht im
  Relabel-Pfad (bereits existierende Sektion eines weiterhin verbundenen
  Geräts) — genau dieser Pfad läuft aber beim Reset-Button für jedes
  weiterhin verbundene Gerät. `_update_empty_tile()` wird jetzt auch dort
  aufgerufen (`lab_gui/control_tab.py`).
- **[BUGS.md #15, Nachbesserung zu 10e] Neu angeschlossenes Gerät bekam bei
  bereits aktiver Panel-Farben-Option keine automatische Farbe**: 10e deckte
  nur das Einschalten der Option für bereits bekannte Geräte ab. Die
  Vergabe-Logik ist jetzt in `_assign_free_panel_colors()` ausgelagert und
  wird zusätzlich in `_on_device_known_panel_color` für ein einzelnes neu
  hinzukommendes Gerät angestoßen, wenn die Option schon aktiv ist
  (`lab_gui/main_window.py`).
- **[BUGS.md #18] Testablauf-Editor: eingeklappter Baustein zeigte die
  echte erste Schritt-Zeile statt einer Zusammenfassung**: Die Kopfzeile
  eines eingeklappten Bausteins zeigte bisher die echten Geräte-/Aktions-/
  Wert-Widgets des ersten Schritts. Neue `_BlockHeaderOverlay`-Klasse blendet
  im eingeklappten Zustand „Baustein"/Bausteinname/Schrittzahl als rein
  visuelle Fläche über den (weiterhin unveränderten, allein
  datenführenden) Zellen-Widgets ein — `steps()`/`_row_to_step` lesen
  dadurch unverändert die echten Werte, unabhängig vom Ein-/Ausklapp-Zustand
  (`lab_gui/testcase_tab.py`).

## [0.6.2]

### Hinzugefügt
- **Nachlauf-Report**: Nach einem Testlauf (auch nach Stop oder Fehlerabbruch)
  erzeugt der neue „Report"-Button im Testablauf-Reiter einen selbst-
  enthaltenen HTML-Report (`lab_gui/reports/`, per Browser geöffnet) mit
  Gesamtverdikt, einer Ergebnistabelle der Pass/Fail-Prüfungen je Schritt,
  einem chronologischen Zeitverlauf des Laufs und Messwert-Diagrammen
  (Spannung/Strom/Leistung über die Laufzeit, QPainter-gerendert und als
  PNG eingebettet — keine neue Abhängigkeit). Optionaler PDF-Export über
  denselben Button (Qt-Bordmittel: `QTextDocument` + `QPdfWriter`). Neue
  Module `lab_gui/run_record.py`, `lab_gui/report_chart.py`,
  `lab_gui/run_report.py`. Der Testablauf-Editor merkt sich außerdem die
  zuletzt geladene/gespeicherte Datei; ihr Name erscheint im Report (sonst
  „Unbenannt").
- **Kompakte Dashboard-Ansicht**: umschaltbar über einen Icon-Button unten
  rechts im Dashboard-Bereich, Zustand wird in `settings.json` persistiert.
  Im Kompaktmodus zeigt jedes Geräte-Panel eine einzige Zeile mit den
  Messwerten, beschriftet nur durch Icons (Blitz = Spannung, DC-Symbol =
  Strom, Tacho = Leistung, Pfeile = CC/CV-Modus; voller Name als Tooltip) —
  der Gerätename steht bereits im Rahmentitel des Panels und braucht daher
  keine eigene Zeile. Insgesamt gut die Hälfte weniger vertikale Höhe, mehr
  Platz für die Tabs darunter (`lab_gui/dashboard.py`,
  `Settings.dashboard_compact`).

### Geändert
- **Gerätename als Rahmentitel**: Alle Geräte-Panels (Dashboard und
  Steuerung-Tab) zeigen den Namen jetzt als natives QGroupBox-„title“ auf dem
  oberen Rahmen, genau wie der „Dashboard“-Titel selbst — statt als eigenes
  QLabel im Panel-Inneren. Folgt damit automatisch Theme-Wechseln (Farbe,
  Fettdruck), ohne eigene Stylesheet-Pflege (`lab_gui/dashboard.py`,
  `lab_gui/control_tab.py`).
- **Dashboard-Panels kompakter**: Umbenennen-Button und Geräteart-Icon
  (Stecker-Symbol fürs Netzteil, Widerstand-Symbol für die Last — ersetzt die
  bisherige Textzeile „Labornetzteil“/„Elektronische Last“, voller Name
  weiterhin als Tooltip) haben keine eigene Zeile mehr, sondern sitzen direkt
  in der ersten bzw. letzten Werte-Zeile (oben/unten rechts) — spart zwei
  Zeilen Höhe pro Panel. Die feste Panel-Breite (bisher 220px) wird jetzt
  dynamisch aus dem tatsächlichen Inhalt aller Panels berechnet und
  angeglichen (`DashboardWidget._relayout_panels`, analog zu
  `ControlTab._equalize_sections`) statt fest verdrahtet zu sein — sonst
  wären längere Werte (z.B. die dritte Nachkommastelle bei der Last) oder
  längere Übersetzungen abgeschnitten worden (`lab_gui/dashboard.py`).
- **Control-Panels vereinheitlicht**: alle Geräte-Panels im Steuerung-Tab
  sind jetzt gleich groß (Höhe und Breite des größten Panels; erscheint die
  OVP/OCP-Warnung, darf das Netzteil-Panel weiterhin wachsen). Die
  EIN/AUS-Schalter für den Ausgang sitzen in allen Panels ganz unten, und
  bei der elektronischen Last steht der Übernehmen-Button jetzt direkt
  rechts neben dem Sollwert-Feld statt in einer eigenen Zeile
  (`lab_gui/control_tab.py`).
- **Aufnahme-Bereich im Verlauf-Tab kompakter und nach unten verschoben**:
  statt vier Zeilen (Status, Hinweistext, zwei Button-Reihen) jetzt eine
  einzige Zeile — Start/Stop/Zurücksetzen als reine Material-Design-Icon-
  Buttons (voller Name im Tooltip), die Export-Buttons mit Kurzlabel
  „CSV…“/„MF4…“ und die Statusanzeige rechts daneben. Der erklärende
  Hinweistext ist in den Tooltip der GroupBox gewandert. Sitzt jetzt unter
  statt über den Diagrammen, aber bewusst außerhalb von deren QScrollArea —
  bleibt dadurch immer sichtbar am unteren Tab-Rand, auch wenn mehrere
  Diagramme Scrollen nötig machen (`lab_gui/timeline_tab.py`).
- Version auf 0.6.2 angehoben.

### Behoben
- **Dashboard-Panels sprangen sichtbar hin und her**: Die dynamische
  Breitenangleichung hob bei jedem Messwert-Update erst die Fixierung aller
  Panels auf und fixierte danach nur bei abweichender *Geometrie* neu —
  Zurücksetzen und Neu-Fixieren wechselten sich über die selbst ausgelösten
  LayoutRequests endlos ab, die Panels pendelten zwischen natürlicher und
  angeglichener Breite. Zusätzlich schwankt die natürliche Inhaltsbreite mit
  jedem Messwert um einige Pixel (Ziffern sind in der Proportionalschrift
  unterschiedlich breit), was die Panels permanent zittern ließ. Die Breite
  wird jetzt als Ratsche geführt (wächst nur auf die breiteste je gesehene
  Anforderung; in der Kompaktansicht je Panel, in der Normalansicht
  gemeinsam) und nur neu gesetzt, wenn die gesetzte Beschränkung tatsächlich
  abweicht; zurückgesetzt wird sie nur beim Ansichtswechsel
  (`lab_gui/dashboard.py`: `_relayout_panels`).
- Icon-only-Buttons mit angehängtem Menü (z.B. der Report-Button) quetschten
  das Icon sichtbar an den linken Rand, weil die knappe Standardbreite den
  von Qt zusätzlich gezeichneten Dropdown-Pfeil nicht einrechnete — solche
  Buttons werden jetzt automatisch breiter (`lab_gui/icons.py`).
- Bei hohen Fenstern verteilte das Hauptlayout überschüssige Höhe je zur
  Hälfte auf Dashboard und Tabs — das Dashboard wurde dadurch weit über
  seinen Inhalt hinaus gestreckt (besonders auffällig in der Kompaktansicht,
  wo die Panel-Zeile dann mittig in einem leeren Rahmen schwebte). Das
  Dashboard ist jetzt vertikal fixiert und die Tabs bekommen den gesamten
  Überschuss (`lab_gui/dashboard.py`, `lab_gui/main_window.py`).
- Die feste Höhe der Dashboard-ScrollArea wird jetzt bei jedem LayoutRequest
  des Panel-Containers nachgezogen. Bisher blieb sie veraltet, wenn der
  Panel-Inhalt nach der Berechnung noch wuchs (z.B. erste Messwerte nach dem
  Verbinden) — die Panels konnten dadurch unten leicht abgeschnitten werden.
- **[Sicherheitskritisch] Netzteil startete mit eingeschaltetem Ausgang**: Das
  HCS-34xx-Protokoll kennt kein echtes Ausgang-AUS-Kommando (nur Emulation
  über Stromsollwert 0 A) — beim (Wieder-)Verbinden wurde bislang nie aktiv
  auf 0 A gesetzt, sodass ein von einer früheren Sitzung oder manuell
  eingeschalteter Ausgang real eingeschaltet blieb, während die GUI
  standardmäßig „Aus" anzeigte (realer Hardware-Zustand wich von der Anzeige
  ab). Jetzt wird beim Verbindungsaufbau explizit auf 0 A gesetzt, bevor das
  Gerät als verbunden gilt (`lab_gui/device_worker.py`).
- **[Sicherheitskritisch] Sicherheits-Grenzwerte sind jetzt geräte-individuell
  statt geräteartweit global**: Der Software-Watchdog (`lab_gui/safety.py`)
  prüfte Last-/Netzteil-Grenzwerte bisher gemeinsam für alle Geräte einer Art
  — bei zwei baugleichen Netzteilen ließen sich also keine unterschiedlichen
  Schwellen setzen. Grenzwerte sind jetzt pro Geräte-ID konfigurierbar; der
  Einstellungen-Tab erzeugt dafür dynamisch eine eigene Grenzwert-Sektion je
  bekanntem Gerät, analog zu den Steuerung-Panels (`lab_gui/settings.py`,
  `lab_gui/settings_tab.py`). **Achtung**: Das Speicherformat für
  `safety_limits` in `settings.json` hat sich geändert (Schlüssel jetzt
  Geräte-ID statt Geräteart) — zuvor gespeicherte globale Grenzwerte werden
  beim Update nicht automatisch übernommen und müssen neu gesetzt werden.
- **Dark Mode: EIN-Buttons im Control-Panel jetzt grün**: `success` ist im
  Amber-Industrial-Theme bewusst amber (Theme-Akzent) statt grün — dadurch
  war der aktive Ausgang-Zustand im Dark Mode farblich kaum von anderen
  Buttons zu unterscheiden. Die EIN/AUS-Anzeige nutzt jetzt `check_pass`
  (immer grün/rot in beiden Themes, wie schon bei den Pass/Fail-Ergebnissen)
  statt `success` (`lab_gui/control_tab.py`).
- **Last-Panel flackerte beim Übernehmen im Control-Tab**: Der
  Sollwert-Übernehmen-Button rief die Worker-Setter bislang direkt als
  Python-Methode auf statt über eine Qt-Signal/Slot-Verbindung — das umging
  Qt's Thread-Routing und führte die blockierende Serial-I/O synchron im
  GUI-Thread statt im Worker-Thread aus (kurzes Einfrieren/Neuzeichnen).
  Jetzt korrekt über eine neue `DeviceWorker.set_load_setpoint`-Slot-Methode
  verbunden, wie bereits beim Netzteil-Pfad (`lab_gui/main_window.py`,
  `lab_gui/device_worker.py`).
- **Verlaufs-Diagramme aktualisierten nur mit ~2 Hz**: `_ScopeChart.
  paintEvent` berechnet das angezeigte Zeitfenster bei jedem Aufruf live über
  die aktuelle Uhrzeit — bei nur 2 Hz Repaint-Rate wirkte das Scrollen
  sichtbar ruckelig. Repaint-Intervall von 500 ms auf 33 ms (~30 Hz)
  reduziert (`lab_gui/timeline_tab.py`). Schnelleres Neuzeichnen allein
  brachte aber nichts, solange die zugrundeliegenden Messwerte selbst nur
  alle 500 ms (2 Hz) neu abgefragt wurden — `device_worker.POLL_INTERVAL_MS`
  daher zusätzlich auf 100 ms (10 Hz) gesenkt (5×), bewusst konservativ statt
  auf die vollen 30 Hz: pro Zyklus werden alle Geräte sequentiell abgefragt
  (eine Last macht bereits 4 Kommandos pro Zyklus), das HCS-34xx hängt an
  einem CP210x-USB-UART-Wandler (9600 Baud, oft ~16 ms Windows-VCP-
  Latenz-Timer pro Leseaufruf) — ein zu aggressiver Wert könnte ein Gerät mit
  Kommandos überfordern und Timeouts als (fälschliche) Verbindungsabbrüche
  auslösen (`lab_gui/device_worker.py`). **Noch nicht an echter Hardware
  verifiziert** (aktuell kein Gerät angeschlossen) — bei häufigeren
  „getrennt"-Log-Einträgen nach dem Update den Wert wieder erhöhen.
- **Testschritt-Tabelle: Spaltenbreiten skalierten nicht sinnvoll**: Beim
  Skalieren des Hauptfensters wuchs bisher nur die „Aktion"-Spalte (einzige
  Stretch-Spalte) übermäßig. „#", „Dauer" und „Aktiv" sind jetzt fest
  (`Fixed`), alle übrigen Spalten (inkl. „Aktion") teilen sich den
  verbleibenden Platz gleichmäßig (`Stretch`) (`lab_gui/testcase_tab.py`).
- **Control-Tab: Außenabstand zum Fensterrand ergänzt**: Die Geräte-Panels
  stießen links/oben direkt an den Fensterrand. `FlowLayout` (eigene
  Layout-Implementierung, `lab_gui/flow_layout.py`) berücksichtigte
  `contentsMargins()` nur in `minimumSize()` (der Container forderte dadurch
  zwar mehr Platz an), nicht aber in `setGeometry()`/`_do_layout()` — Panels
  wurden weiterhin relativ zum vollen, nicht margin-reduzierten Rect
  platziert, ein zunächst versuchter Fix allein in `control_tab.py` (Margin
  setzen) blieb daher wirkungslos. `setGeometry()`/`heightForWidth()` ziehen
  die Margins jetzt korrekt ab (`rect.marginsRemoved(...)`), Panels haben
  denselben Außenabstand wie den Innenabstand zueinander
  (`lab_gui/flow_layout.py`, `lab_gui/control_tab.py`).
- **Grauer Rand/graue Flecken um verschachtelte Panels — betraf mehr als nur
  das Dashboard**: Ursprünglich am Dashboard bemerkt (ScrollArea/Container
  zeigten den allgemeinen Seiten-Hintergrund `pal.bg` statt der Fläche der
  umschließenden GroupBox `pal.surface`, sichtbar als grauer Rand z.B. um das
  „Last 150W"-Panel), aber derselbe Effekt trat unabhängig davon an
  mehreren weiteren Stellen auf: reine Layout-Wrapper-`QWidget`s (Zeilen im
  Control-Tab-Formular, die Diagramm-Legende im Verlauf-Tab, der
  Status-Container in der Statusleiste) sowie `QLabel`s, die direkt (ohne
  Wrapper) im Layout einer `QGroupBox` hängen (Untertitel, Grenzwert-Warnung,
  Diagramm-Titel) malten ebenfalls opak den Seiten- statt den
  GroupBox-/Statusleisten-Hintergrund — sichtbar als graue Streifen/Flecken
  z.B. hinter jeder Eingabezeile im Steuerung-Tab oder unter dem
  Diagramm-Titel im Verlauf-Tab. Neue Hilfsfunktion `theme.no_own_background()`
  (Instanz-Stylesheet statt einer riskanten globalen `QGroupBox QWidget`-Regel,
  die wegen höherer Selektor-Spezifität versehentlich auch Buttons/Eingabefelder
  überschreiben würde) an allen gefundenen Stellen angewendet
  (`lab_gui/theme.py`, `lab_gui/dashboard.py`, `lab_gui/control_tab.py`,
  `lab_gui/timeline_tab.py`, `lab_gui/main_window.py`). In Light- und
  Dark-Theme verifiziert.
- **Diagramm-Hintergrund war immer schwarz**: `plot_bg`/`plot_grid` waren in
  beiden Paletten fest auf ein dunkles Oszilloskop-Schwarz gesetzt. Im
  Light-Theme jetzt hell (passend zur restlichen UI); im
  Amber-Industrial-Theme bewusst unverändert dunkel (`lab_gui/theme.py`).
- **Netzteil-Schalter im Control-Tab blieben nach „Alle Aus" auf EIN
  stehen**: Anders als bei der Last (Zustand kommt per echter
  Hardware-Rückfrage aus dem Poll-Zyklus) ist der EIN/AUS-Schalter eines
  Netzteil-Panels rein lokaler GUI-Zustand (das HCS-34xx-Protokoll kennt
  keine Abfrage des tatsächlichen Ausgangszustands). Schaltete der Worker den
  Ausgang selbst ab — „Alle Aus"-Button, Safety-Watchdog-Trip, Fenster
  schließen —, erfuhr das Panel davon nichts und zeigte weiter „EIN", obwohl
  der Ausgang real bereits aus war. Neues `DeviceWorker`-Signal
  `psu_output_state` informiert das Control-Tab-Panel jetzt explizit, sowohl
  beim Abschalten (`all_outputs_off`/`_kill_psu`) als auch beim
  (Wieder-)Verbinden (`lab_gui/device_worker.py`, `lab_gui/control_tab.py`,
  `lab_gui/main_window.py`).

## [0.6.1]

### Geändert
- **Aufzeichnung in den Verlauf-Tab verschoben**: der bisher eigenständige
  „Aufzeichnung"-Reiter (v0.3.0) ist entfallen; Start/Stop/Zurücksetzen sowie
  CSV-/MF4-Export sitzen jetzt oben im „Verlauf"-Tab, direkt über den
  Diagrammen, da beide Funktionen dieselben Messwerte betreffen. Der
  bisherige „Aufzeichnung zurücksetzen"-Button der Diagramm-Steuerung (löscht
  nur die Ringpuffer der Live-Ansicht) heißt jetzt „Anzeige zurücksetzen“, um
  ihn klar vom neuen, separaten Aufzeichnung-Reset zu unterscheiden — beide
  wirken unabhängig voneinander.
- Version auf 0.6.1 angehoben.

## [0.6.0]

### Hinzugefügt
- **Software-Watchdog / Sicherheitsabbruch**: schrittunabhängige, geräteartweite
  Grenzwerte (max. Spannung/Strom/Leistung je für Lasten und Netzteile,
  neuer Reiter-Abschnitt „Globale Grenzwerte" in den Einstellungen). Wird ein
  aktivierter Grenzwert überschritten, schalten **alle** Ausgänge sofort ab
  (Last: Ausgang AUS, Netzteil: Strom auf 0 A) — unabhängig davon, ob und
  welcher Testschritt gerade läuft (`lab_gui/safety.py`,
  `DeviceWorker.all_outputs_off`). Der Auslöser ist latchend: ein rotes
  Banner mit Grund bleibt stehen, bis er über „Quittieren" bestätigt wird;
  bis dahin ist ein Testlauf-Start gesperrt. Ein Statusleisten-Indikator
  zeigt „Sicherheit: AUS/AKTIV/AUSGELÖST".
- Während eines laufenden Testablaufs überwacht der Watchdog zusätzlich die
  beteiligten Geräte auf Verbindungsabbruch/veraltete Messwerte (2 s
  Toleranz, wie `testcase_runner.MEASUREMENT_STALE_S`) und löst ebenfalls
  aus — das deckt insbesondere unbeaufsichtigte Übernacht-Läufe ab, bei
  denen ein Gerät die Verbindung verliert.
- **Safe-Stop**: Stop-Button, ein fehlgeschlagener Testschritt und das
  Schließen des Fensters schalten jetzt ebenfalls alle Ausgänge ab (bisher
  blieb der zuletzt gesetzte Sollwert unbegrenzt aktiv). Zusätzlich ein
  manueller „ALLE AUS"-Panic-Button im Dashboard-Header.
- **Datei-Logging** (`lab_gui/app_logging.py`, `labdash.log` neben der .exe/
  dem Skript, rotierend): Verbindungs-Ereignisse, Sicherheitsabbrüche und
  Abschaltversuche sind damit auch bei der `--windowed`-.exe im Nachhinein
  nachvollziehbar.

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
  steht als Tooltip an der Prüfzelle. Bestanden ist auch im Dark-Theme grün
  (neuer Palettenwert `check_pass`, da `success` dort bewusst amber ist). Die Statuszeile zeigt am Ende
  „Fertig – BESTANDEN (n Prüfungen)" bzw. „Fertig – NICHT bestanden (k/n
  Prüfungen fehlgeschlagen)"; die Farben bleiben zur Inspektion stehen, bis
  der nächste Lauf startet. Pro Schritt wählbar: „Bei Verletzung abbrechen"
  stoppt den Lauf sofort wie ein Gerätefehler (Quittieren über Stop),
  ansonsten läuft der Test durch und sammelt alle Ergebnisse.
- Deaktivierte Schritte werden weiterhin übersprungen und zählen nicht als
  Prüfung; die Prüfungszähler zählen jede Ausführung (ein Prüfschritt in
  einer 10er-Schleife = 10 Prüfungen).
- An echter Hardware verifiziert: HCS-Netzteil (U-/I-Prüfungen, Fail ohne
  Abbruch, Abbruchpfad inkl. Safe-Stop-Auslösung, automatische
  Geräteauflösung) und Korad-Last (U-/I-/P-Prüfungen ohne Quelle an den
  Klemmen, Fail-/Abbruchpfad, Eingang-aus-Wiederherstellung). Hinweis aus
  der Netzteil-Verifikation: Die Dauer-Spalte muss die reale Einschwingzeit
  abdecken — ohne Last fällt die Ausgangsspannung nach einem niedrigeren
  Sollwert nur langsam (Bleeder), siehe `lab_gui/README.md`.

### Geändert
- Version auf 0.5.0 angehoben. Testablauf-Dateiformat bleibt v2; Dateien ohne
  Prüfungen sind unverändert kompatibel, die neuen `check_*`-Felder werden
  beim Laden älterer Dateien mit Defaults aufgefüllt.

### Behoben
- Gesperrte Eingabefelder (`setEnabled(False)`) sahen app-weit exakt wie
  aktive aus, weil das globale Stylesheet keine `:disabled`-Regeln hatte und
  damit die native Ausgrau-Darstellung von Qt überschrieb — aufgefallen im
  „Prüfung definieren"-Dialog, dessen Felder bei inaktiver Prüfung gesperrt
  sind. Jetzt werden Eingabefelder und Checkboxen in beiden Themes sichtbar
  ausgegraut (betrifft z.B. auch das Wert-Feld bei Aktionen ohne Zahlenwert).

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

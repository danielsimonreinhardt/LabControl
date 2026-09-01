# Changelog

Alle nennenswerten Änderungen an der Labor-Steuerungs-App. Format angelehnt an
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/), Versionierung nach
Semantic Versioning (`lab_gui/version.py`).

## [Unreleased]

### Hinzugefügt
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
- Version auf 0.7.0 angehoben.

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

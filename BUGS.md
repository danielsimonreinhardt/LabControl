# Bug-Liste (gefunden beim Testen von LabControl v0.6.1)

Sortiert nach empfohlener Bearbeitungsreihenfolge (sicherheitskritisch zuerst,
kosmetisch zuletzt). Jeder Eintrag kann unabhängig in einer eigenen Session
bearbeitet werden.

## 1. [SICHERHEITSKRITISCH] Netzteile beim Start bereits eingeschaltet

> **Status: Gefixt, verifiziert an echter Hardware (v0.9.0, 2026-09-01).**
> Beim (Wieder-)Verbinden setzt `device_worker.py` jetzt aktiv den Strom auf
> 0 A, bevor das Gerät als verbunden gilt.

Die Netzteile (hcs34xx) sind direkt nach dem App-Start bereits eingeschaltet,
obwohl der Ein/Aus-Schalter in der GUI auf "Aus" steht.

**Vermutete Ursache:** Der Startzustand der GUI liest nicht den echten
Hardware-Status des Geräts aus, und/oder es wird beim Start kein
Ausschalt-Befehl an das Gerät gesendet, sodass ein zuvor eingeschalteter
Ausgang eingeschaltet bleibt, während die GUI einen Default-Zustand ("Aus")
anzeigt, der nicht mit der Hardware synchronisiert ist.

**Risiko:** Realer Hardware-Zustand weicht von der Anzeige ab — sicherheitsrelevant,
da ein Nutzer von einem ausgeschalteten Ausgang ausgeht, obwohl Spannung/Strom anliegt.

**Zu prüfen:** Initialisierungs-Code des DeviceWorker/PSU-Treibers beim
Verbindungsaufbau — wird der tatsächliche Output-Status abgefragt und in der
GUI korrekt widergespiegelt, oder wird per Default "Aus" angezeigt ohne Abgleich?


### 1b. [SICHERHEITSKRITISCH, NEU – Hardware-Test v0.8.0] Setzen von Strom/Spannung schaltet Ausgang real ein, obwohl Schalter auf AUS steht

> **Status: Gefixt (GUI-Sperre), verifiziert an echter Hardware (v0.9.0,
> 2026-09-01).** Root Cause: Die Spannung/Strom-„Setzen"-Buttons im
> Control-Tab riefen `set_voltage`/`set_current` immer direkt auf, unabhängig
> vom Ausgang-Schalter — dadurch ließ sich der emulierte „Aus"-Zustand
> (Strom=0A) unbemerkt aufheben. Die Buttons sind jetzt gesperrt, solange der
> Ausgang nicht aktiv über „EIN" eingeschaltet ist (das Sollwertfeld bleibt
> editierbar, nur das Anwenden ist gesperrt) — `control_tab.py:
> PsuControlGroup._update_output_buttons`. **Wichtige Einschränkung (weiter
> zu beachten):** Das HCS-34xx hat laut eigener Treiber-Dokumentation *kein*
> echtes Ausgang-AUS-Kommando — dieser Fix schließt nur den GUI-Pfad (Klick
> auf „Setzen"), nicht z.B. automatisierte Testablauf-Schritte (PSU_VOLT/
> PSU_CURR-Aktionen), die weiterhin direkt am Ausgang wirken können, wenn ein
> Testablauf sie unabhängig von PSU_OUT_ON/OFF verwendet.

Reproduziert am echten HCS-34xx: Wenn bei ausgeschaltetem Ausgang (Schalter
in der GUI auf "Aus") ein Strom- und Spannungswert gesetzt wird, liegt danach
tatsächlich Spannung am Ausgang an — unabhängig vom Schalterzustand.

**Unterschied zu Bug 1 oben:** Bug 1 betraf den Zustand direkt nach dem
(Wieder-)Verbinden. Dieser Fall tritt während des laufenden Betriebs auf,
ausgelöst durch das Setzen von Sollwerten bei ausgeschaltetem Ausgang.

**Vermutete Ursache:** Der Treiber/DeviceWorker sendet vermutlich beim Setzen
von Strom/Spannung einen Befehl an das Gerät (z.B. `VSET`/`ISET`), der auf
manchen PSUs implizit den Ausgang aktiviert, oder die Reihenfolge der
gesendeten Befehle (Sollwert setzen vor/statt Output-Status prüfen) sorgt
dafür, dass der GUI-Zustand "Aus" nicht mehr mit dem Geräte-Zustand
übereinstimmt.

**Risiko:** Wie Bug 1 — realer Hardware-Zustand weicht von der Anzeige ab,
mit unmittelbarem Spannungsausgang trotz vermeintlich sicherem "Aus"-Zustand.

**Zu prüfen:** Code-Pfad für das Setzen von Sollwerten (`set_voltage`/
`set_current` bzw. äquivalent im `hcs34xx`-Treiber und `device_worker.py`) —
wird dabei versehentlich auch der Output aktiviert, oder synchronisiert die
GUI den Schalter-Zustand nicht mit einer Output-Aktivierung, die das Gerät
selbstständig beim Empfang neuer Sollwerte vornimmt?


## 2. [SICHERHEITSKRITISCH] Sicherheits-Grenzwerte sind global statt geräte-individuell

> **Status: Gefixt, funktional in Simulation verifiziert — kein Hardware-Test
> nötig.** Grenzwerte sind jetzt pro Geräte-ID gespeichert/geprüft
> (`safety.py`, `settings.py`, dynamische Sektion je Gerät in
> `settings_tab.py`); rein software-/persistenzseitige Logik, per
> Offscreen-Smoke-Test (Grenzwert setzen → korrekt pro Geräte-ID in
> `settings.json` gelandet) bestätigt. Einziger Wermutstropfen: Das
> Speicherformat hat sich geändert (Schlüssel jetzt Geräte-ID statt
> Geräteart) — nach dem Update kurz den Einstellungen-Tab öffnen und prüfen,
> dass zuvor gesetzte globale Grenzwerte ggf. neu gesetzt werden müssen.

Der Software-Watchdog (`lab_gui/safety.py`, `SafetyMonitor`) verwendet aktuell
globale Spannungs-/Strom-/Leistungsgrenzwerte, die für alle Geräte gleich sind.

**Gewünscht:** Jedes Gerät (PSU, Last) soll eigene, individuell konfigurierbare
Sicherheitsgrenzwerte bekommen können, statt einer gemeinsamen globalen
Einstellung.

**Kontext:** Der Watchdog selbst ist bereits vorhanden und funktionsfähig
(latching trip, `all_outputs_off()` bei Grenzwertüberschreitung) — es geht nur
um die fehlende Geräte-Individualität der Grenzwert-Konfiguration.


## 3. Dark Mode: "Ein"-Buttons im Control-Panel nicht grün

> **Status: Sicher gefixt.** Rein visuelle Ein-Zeilen-Änderung (Buttons nutzen
> jetzt `pal.check_pass` statt `pal.success`, siehe `control_tab.py`), per
> Screenshot-Rendering in Light- und Dark-Theme bestätigt.

Im Dark Mode sind die Buttons zum Einschalten eines Ausgangs im Control-Panel
nicht grün eingefärbt (im Light-Theme funktioniert die Farbcodierung).

**Risiko:** Farbcodierung für Gerätezustand ist eine wichtige visuelle
Sicherheitsinformation (auf einen Blick erkennbar, ob ein Ausgang aktiv ist) —
sollte in beiden Themes konsistent grün sein.

**Zu prüfen:** Stylesheet/Theme-Definition für Buttons — vermutlich wird die
grüne Akzentfarbe nur im Light-Palette-QSS gesetzt und im Dark-Palette-QSS
überschrieben oder fehlt dort.


## 4. Last-Panel verschwindet kurz beim Übernehmen im Control-Tab

> **Status: Gefixt, verifiziert an echter Hardware (v0.9.0, 2026-09-01).**
> Der Übernehmen-Button rief den Worker bislang per direktem
> Python-Methodenaufruf statt über eine Qt-Queued-Connection auf und
> blockierte damit den GUI-Thread mit Serial-I/O (`main_window.py`/
> `device_worker.py`, neue `set_load_setpoint`-Slot-Methode).

Wenn im Control-Tab bei der elektronischen Last ein Wert eingestellt und auf
"Übernehmen" geklickt wird, verschwindet das Panel kurz (Flackern/Re-Render).

**Vermutete Ursache:** UI-Refresh/Re-Render-Problem im Panel-Update-Handler
nach dem Anwenden eines neuen Werts — evtl. wird das Widget kurzzeitig neu
aufgebaut statt nur die Werte zu aktualisieren.


## 5. Verlaufs-Diagramme aktualisieren zu langsam (~2Hz)

> **Status: Gefixt, verifiziert an echter Hardware (v0.9.0, 2026-09-01).**
> Zwei Anteile: (1) Repaint-Rate der Diagramme 500ms→33ms (~30Hz,
> `timeline_tab.REPAINT_INTERVAL_MS`). (2) Poll-Intervall der Geräteabfrage
> selbst 500ms→100ms (~10Hz, `device_worker.POLL_INTERVAL_MS`) — bewusst
> konservativ gewählt (nicht die vollen 30Hz), um ein Gerät (insb. das
> HCS-34xx am 9600-Baud-CP210x-Wandler) nicht mit Kommandos zu überfordern.
> An echten Geräten getestet, keine gehäuften „getrennt"-Log-Einträge
> beobachtet.

Im Verlaufs-Tab werden die Diagramme mit geschätzt nur ~2Hz aktualisiert.
Für eine flüssige Darstellung werden mindestens 15Hz, besser 30Hz benötigt.

**Zu prüfen:** Timer-Intervall für den Plot-Refresh in der Timeline/History-
Tab-Implementierung.


## 6. Testschritt-Tabelle: Spaltenbreiten skalieren nicht sinnvoll

> **Status: Sicher gefixt.** „#"/Dauer/Aktiv sind jetzt `Fixed`, alle übrigen
> Spalten (inkl. „Aktion") `Stretch` (`testcase_tab.FIXED_COLUMNS`) —
> deterministisches Qt-Layout-Verhalten, per pixelgenauer Geometrie-Prüfung
> verifiziert (Stretch-Spalten exakt gleich breit, Fixed-Spalten unverändert
> bei Fenster-Resize).

Beim Skalieren des Hauptfensters passt sich die Spaltenaufteilung der
Testschritt-Tabelle nicht sinnvoll an — die Spalte "Aktion" wird dabei sehr breit.

**Gewünschtes Verhalten:**
- Spalte "#" (Zeilennummer): immer feste Breite
- Spalte "Dauer": immer feste Breite
- Spalte "Aktiv": immer feste Breite
- Alle übrigen Spalten (inkl. "Aktion") teilen sich den verbleibenden Platz
  gleichmäßig (dynamische, gewichtete Verteilung statt fixer/auto Breiten)

**Zu prüfen:** QTableWidget/QTableView Spaltenresize-Modi (`setSectionResizeMode`)
in der Testcase-Tab-Implementierung — vermutlich müssen feste Spalten auf
`Fixed` und die übrigen auf `Stretch` mit gleichem Stretch-Faktor gesetzt werden.


## 7. Control-Tab: Geräte-Panels ohne Außenabstand zu Fensterrand

> **Status: Sicher gefixt.** Der eigentliche Fehler lag in `flow_layout.py`
> selbst — `setGeometry()`/`heightForWidth()` ignorierten `contentsMargins()`
> komplett bei der Positionierung (nur `minimumSize()` rechnete sie ein). Ein
> erster Versuch nur in `control_tab.py` (Margin setzen) blieb deshalb
> wirkungslos. Jetzt per `rect.marginsRemoved(...)` korrekt behoben und per
> pixelgenauer Geometrie-Prüfung verifiziert (Panel exakt am erwarteten
> Versatz von 12px).

Im Control-Tab stoßen die Geräte-Panels links und oben direkt an den
Fensterrand an, ohne Abstand — anders als der Abstand, der zwischen den
einzelnen Geräte-Panels besteht.

**Gewünscht:** Gleicher Außenabstand (Margin) wie der Innenabstand (Spacing)
zwischen den Panels.


## 8. Verschachtelte Panel-Hintergründe uneinheitlich

> **Status: Gefixt, verifiziert an echter Hardware/im echten Betrieb (v0.9.0,
> 2026-09-01).** Root Cause: reine Layout-Wrapper-`QWidget`s bzw. `QLabel`s,
> die direkt (ohne Wrapper) im Layout einer `QGroupBox` hängen, malten opak
> den allgemeinen Seitenhintergrund statt die GroupBox-/Statusleisten-Fläche
> durchscheinen zu lassen (neue Hilfsfunktion `theme.no_own_background()`).
> Betraf Dashboard, Control-Tab (Formularzeilen, Untertitel,
> Grenzwert-Warnung), Verlauf-Tab (Diagramm-Titel/-Legende) und die
> Statusleiste. War zuvor zweimal unvollständig gefixt (erst nur Dashboard,
> dann weitere Stellen entdeckt) — nach erneutem Rundgang jetzt bestätigt.

Es scheint, dass ein GUI-Element die theme-abhängige Hintergrundfarbe korrekt
enthält, aber ein umschließendes/einbettendes Element eine leicht andere Farbe
zeigt (im Light-Theme: grau). Sichtbar z.B. beim "Last 150W"-Panel — es
entsteht ein sichtbarer grauer Rand um das eigentliche Panel.

**Vermutete Ursache:** Der äußere Container hat kein eigenes Stylesheet/
Hintergrund-Property gesetzt und zeigt daher die Qt-Default-Palette-Farbe
statt der Theme-Hintergrundfarbe des inneren Widgets.

**Screenshot:** Wurde vom Nutzer bereitgestellt (nicht in diesem Repo abgelegt) —
bei Bedarf erneut anfordern, zeigt das "Last 150W"-Panel mit grauem Rand im
Light-Theme.


## 9. Diagramm-Hintergrund immer schwarz

> **Status: Sicher gefixt.** Reine Palettenwert-Änderung (`plot_bg`/
> `plot_grid` im Light-Theme, `theme.py`), per Screenshot-Rendering
> verifiziert (heller statt schwarzer Hintergrund); Dark-Theme bewusst
> unverändert (Oszilloskop-Look).

Der Hintergrund der Verlaufs-Diagramme ist fest auf Schwarz gesetzt,
unabhängig vom aktuell gewählten Farb-Theme (Light/Dark).

**Gewünscht:** Diagramm-Hintergrund soll sich analog zum gewählten
Farb-Theme anpassen (z.B. hell im Light-Theme, dunkel im Dark-Theme).

**Hinweis:** Bugs 6-9 sind alle Layout-/Styling-Themen und lassen sich
vermutlich gemeinsam angehen, da man ohnehin im selben QSS-/Stylesheet-Bereich
unterwegs ist.


## 10. Individuelle Panel-Hintergrundfarben (siehe [FEATURES.md](FEATURES.md) Punkt 2, bereits umgesetzt) — mehrere Nachbesserungen nötig

Gemeldet beim Hardware-Test von v0.8.0. Betrifft `lab_gui/panel_color.py`
(`PanelColorButton`, `apply_panel_tint`) und die zugehörige UI in
Dashboard-/Control-Tab.

> **Gesamtstatus: a-e gefixt und verifiziert (Screenshot/Smoke-Test,
> Light+Dark), f gefixt und an echter Hardware bestätigt, g (Nachbesserung zu
> f, Beschriftungen sollen die Panel-Farbe zeigen) gefixt, Hardware-Test
> steht noch aus.** Details je Punkt unten.

**a) Dashboard-Panels beim ersten Start ohne erkennbare Trennung**

> **Status: Sicher gefixt.** War eine unbeabsichtigte Nebenwirkung des
> eigenen Bug-8-Fixes (Dashboard-Container auf `pal.surface` gesetzt, damit
> ging der Farbkontrast verloren, der Panel-Grenzen vorher erkennbar machte —
> der sehr helle `pal.border`-Rahmen allein reichte nicht). Panels haben
> jetzt immer einen sichtbaren 1px-Rahmen in `pal.text_muted` (deutlich
> präsenter, siehe `dashboard._DevicePanel._apply_style`), unabhängig von
> einer individuellen Farbe. Per Screenshot verifiziert.
Beim allerersten Start (noch keine individuellen Farben vergeben) sind alle
Dashboard-Panels komplett in der normalen Hintergrundfarbe gehalten und ohne
sichtbare Trennung zueinander. Ein Rahmen (Border) um jedes Panel wäre besser
als reine Flächenfarbe als Trennung zu verlassen.

**b) Farbpalette-Auswahl soll im Dashboard-Panel komplett entfallen**
Die Farbauswahl (`PanelColorButton`) soll im Dashboard-Tab nicht mehr
angezeigt werden — im Control-Tab reicht sie aus. (Beide Tabs zeigen
vermutlich aktuell denselben Panel-Header inkl. Farbwahl-Button.)

> **Status: Sicher gefixt.** Farbwahl-Button aus `dashboard._DevicePanel`
> entfernt; Dashboard zeigt die Farbe weiterhin an (`set_panel_color` bleibt
> verdrahtet), kann sie aber nicht mehr selbst auswählen. Per Smoke-Test
> verifiziert (`hasattr(panel, "_color_button")` ist jetzt `False`).

**c) Umbenennen-Button vom Dashboard- ins Control-Panel verschieben**
Der Button zum Umbenennen eines Geräts soll ebenfalls nicht mehr im
Dashboard-Panel erscheinen, sondern nur noch im Control-Panel (analog zu b).

> **Status: Sicher gefixt.** Umbenennen-Button war im Control-Tab bisher gar
> nicht vorhanden — jetzt dort ergänzt (`LoadControlGroup`/`PsuControlGroup`,
> neues `rename_requested`-Signal) und aus dem Dashboard-Panel entfernt. Die
> komplette Signalkette Control-Tab → MainWindow → `DeviceRegistry.rename`
> per Smoke-Test end-to-end verifiziert (inkl. tatsächlich ausgelöstem
> `label_changed`).

**d) Dark Mode: Farben setzen sich zu wenig vom Hintergrund ab**
Im Light-Theme sind die individuellen Panel-Farben gut erkennbar, im
Dark-Theme aber zu blass/zu wenig Kontrast zum normalen Hintergrund.
Gewünscht: alle Farben im Dark Mode kräftiger/gesättigter machen (vermutlich
`theme.Palette.panel_tints` braucht getrennte, kräftigere Werte für Dark statt
nur einer abgeschwächten Variante der Light-Werte).

> **Status: Sicher gefixt.** Die alten Dark-Werte lagen mit Leuchtdichte
> ~34-45 fast auf demselben Niveau wie `surface` (~34) — praktisch
> unsichtbar. Neue Werte liegen bei ~65-83 (deutlich abgesetzt, aber noch
> kein Neon-Ton). Per Screenshot verifiziert, klar erkennbarer Farbunterschied.

**e) Automatische Farbvergabe beim Aktivieren der Option**
Wenn die Option "individuelle Panel-Farben" im Einstellungen-Tab aktiviert
wird, soll automatisch für jedes vorhandene Gerät eine (unterschiedliche)
Farbe vergeben werden, statt dass der Nutzer jedes Panel manuell einfärben
muss.

> **Status: Sicher gefixt.** Neuer Handler in `main_window.py`
> (`_on_panel_colors_enabled_changed`) vergibt beim Einschalten automatisch
> unterschiedliche Farben aus `PANEL_COLOR_ORDER` an alle bekannten Geräte
> ohne bereits gespeicherte Farbe; bereits gesetzte Farben bleiben beim
> Aus-/Wiedereinschalten erhalten. Per Smoke-Test verifiziert (zwei simulierte
> Geräte bekamen unterschiedliche Farben).

**f) Buttons/Eingabefelder sollen bei der Theme-Hintergrundfarbe bleiben**
Die individuelle Panel-Farbe soll nur die Panel-Fläche selbst betreffen.
Die Hintergrundfarbe von Buttons und Eingabefeldern innerhalb des Panels soll
weiterhin die normale Theme-Hintergrundfarbe sein, nicht mit der individuellen
Panel-Farbe eingefärbt werden.

> **Status: Gefixt (zweiter Anlauf), verifiziert an echter Hardware
> (2026-09-01).** Ein erster Fix-Versuch (reine `background-color: X;`-Eigenschaft
> ohne Selektor auf der GroupBox, kombiniert mit `theme.form_control_qss()`
> im selben Stylesheet-String) wurde am echten Gerät getestet und blieb
> wirkungslos — Root Cause dafür: eine selektorlose Eigenschaft ist nur
> zuverlässig auf genau das eine Widget beschränkt, wenn sie das EINZIGE ist,
> was im Stylesheet-String steht; sobald zusätzlich Selektor-Regeln (hier die
> `QPushButton`/`QLineEdit`-Regeln aus `form_control_qss()`) im selben String
> folgen, wird sie von Qt nicht mehr zuverlässig korrekt geparst und schlägt
> trotz der eigentlich spezifischeren `QPushButton`-Regel weiterhin auf
> Kind-Widgets durch (per minimalem Repro-Test isoliert bestätigt: rot
> statt der beabsichtigten blauen Button-Füllung). Zusätzlich betraf das
> nicht nur `panel_color.apply_panel_tint()` selbst, sondern auch
> `control_tab._row()`, das für Sollwert-/Spannungs-/Strom-/OVP-/OCP-Zeilen
> einen Wrapper-Container mit ebenfalls selektorloser
> "background: transparent"-Eigenschaft verwendet (`theme.no_own_background`,
> siehe Bug 8) — dieser Wrapper liegt näher am Button/Spinbox als die
> GroupBox und hätte die Panel-Farbe daher so oder so weiter durchgereicht.
> **Fix (korrigiert):** Beide Stellen schreiben die selektorlose Eigenschaft
> jetzt als expliziten Typ-Selektor (`QGroupBox { background-color: ...; }`
> bzw. `QWidget { background: transparent; }`), gefolgt von den
> spezifischeren `form_control_qss()`-Regeln — normale QSS-Spezifität
> innerhalb eines Stylesheets sorgt dann zuverlässig dafür, dass die
> spezifischere `QPushButton`/`QLineEdit`-Regel gewinnt
> (`lab_gui/panel_color.py`, `lab_gui/control_tab.py::_row`/
> `_row_stylesheet`, dort auch beim Theme-Wechsel neu angewendet, um
> veraltete Farben nach Light/Dark-Wechsel zu vermeiden). Per gezieltem
> Pixel-Sampling (nicht nur visuellem Screenshot-Vergleich) an
> `LoadControlGroup` UND `PsuControlGroup` bestätigt: Sollwert-/Spannungs-/
> Strom-/OVP-/OCP-Felder und alle zugehörigen Buttons (inkl. EIN/AUS) zeigen
> exakt die normale Theme-Farbe, nicht die Panel-Tönung; die Panel-Fläche
> selbst bleibt weiterhin sichtbar getönt. Die Inkrement-/Dekrement-Pfeile
> waren nie betroffen (reine Subcontrols des Spinbox-Styles, keine echten
> Kind-Widgets).

**g) Reine Beschriftungen (Zeilen-Labels wie "Spannung:"/"Sollwert:"/
"Modus:"/"Ausgang:") sollen DOCH die individuelle Panel-Farbe zeigen**
Nachbesserung zu f): f) hatte absichtlich NUR Buttons/Eingabefelder von der
Panel-Tönung ausgenommen — die von `QFormLayout` automatisch erzeugten
Zeilen-Beschriftungen (reiner Text, keine Eingabe-/Klick-Funktion) sollen
aber, anders als ursprünglich angenommen, die individuelle Panel-Farbe
zeigen und sich damit optisch in die getönte Fläche einfügen, statt sich
farblich davon abzuheben.

> **Status: Sicher gefixt, per Screenshot verifiziert (Light-Theme, beide
> Geräte-Panels, 2026-09-01).** Die Zeilen-Labels hatten bislang gar kein
> eigenes Stylesheet und zeigten dadurch (als direkte QGroupBox-Kinder ohne
> Selektor-Regel dafür in `apply_panel_tint`) die allgemeine
> Seiten-Hintergrundfarbe statt der Panel-Tönung. Neue Hilfsfunktion
> `control_tab._detint_label()` setzt auf jedem von `QFormLayout`
> automatisch erzeugten Zeilen-Label (`form.labelForField(...)`) ein reines
> `background: transparent;` (bewusst OHNE weitere Selektor-Regeln im
> selben String -- anders als bei f) ist das hier unproblematisch, siehe
> Docstring), wodurch die Panel-Fläche dahinter durchscheint. Betrifft
> Modus/Sollwert/Ausgang (`LoadControlGroup`) sowie Spannung/Strom/OVP/OCP/
> Ausgang (`PsuControlGroup`). Farbunabhängige Eigenschaft, daher kein
> Theme-Wechsel-Refresh nötig. Bitte am echten Gerät gegentesten
> (Control-Panel mit zugewiesener Farbe, Light UND Dark).


## 11. Einstellungs-Tab: Sicherheits-Grenzwerte-Panels unnötig über volle Breite gestreckt

> **Status: Sicher gefixt.** Zwei Ursachen zusammen: `QFormLayout`s
> Default-Policy (`AllNonFixedFieldsGrow`) ließ die Feld-Spalte auf volle
> Breite wachsen (jetzt `FieldsStayAtSizeHint`), und die Sektionen hingen
> ohne Stretch direkt in der QVBoxLayout (jetzt in einer Zeile mit
> `addStretch()`). Per Screenshot verifiziert — Panels sind jetzt kompakt,
> nur so breit wie der Inhalt braucht.

Gemeldet beim Hardware-Test von v0.8.0. Die Panels für die (seit Bug 2
geräte-individuellen) Sicherheits-Grenzwerte im Einstellungen-Tab
(`settings_tab.py`) sind über die gesamte verfügbare Breite gestreckt.

**Gewünscht:** Panels kompakter darstellen — nur so breit wie für den Inhalt
nötig, statt die volle Fensterbreite auszufüllen.


## 12. Testablauf-Reiter: "Zeile hinzufügen"-Button sieht anders aus als übrige Buttons

> **Status: Sicher gefixt.** Root Cause: `SplitIconButton` (`icons.py`, seit
> der Zweiteilung von Icon-/Menü-Pfeil-Klick, siehe CHANGELOG „Testablauf:
> Plus- und Menü-Klick... getrennt") ist als einziger Button im Programm ein
> `QToolButton` statt `QPushButton` (fuer Qt's `MenuButtonPopup`-Modus). Das
> globale Stylesheet (`theme.stylesheet()`) hatte nie eine `QToolButton`-Regel
> definiert, wodurch der Button komplett auf natives Styling zurueckfiel —
> andere Hintergrund-/Rahmenfarbe UND native (system-blaue) statt
> theme-eigene Hover-Hervorhebung. Neue `QToolButton`-Regeln (Normal/Hover/
> Pressed/Disabled, identisch zu `QPushButton`) plus `QToolButton::menu-button`
> (Trennlinie zur Menü-Pfeil-Klickzone) in `theme.form_control_qss()`
> ergänzt. Per Screenshot in Light UND Dark, inkl. simuliertem Hover-Zustand,
> verifiziert — Button fügt sich jetzt optisch nahtlos neben die übrigen
> Buttons ein.

Gemeldet vom Nutzer. Der Button zum Hinzufügen einer Zeile im
Testablauf-Reiter (seit der Aufteilung in Icon-Klick/Menü-Pfeil-Klick, siehe
CHANGELOG) zeigt eine andere Hervorhebungsfarbe beim Hover als die übrigen
Buttons und sieht auch sonst optisch anders aus (Rahmen/Hintergrund).

**Zu prüfen:** `SplitIconButton` (`lab_gui/icons.py`) ist ein `QToolButton`
statt `QPushButton` — vermutlich fehlt dafür eine eigene Regel im globalen
Stylesheet (`theme.stylesheet()`), sodass der Button auf natives Styling
zurückfällt.

**Zu prüfen:** Layout der Grenzwert-Sektionen in `settings_tab.py` — vermutlich
ein `QHBoxLayout`/`QVBoxLayout` mit Stretch-Faktor oder fehlendem
`setSizePolicy`/`addStretch`, das die Panels horizontal aufbläht.


## 13. Splash-Screen zeigt veraltete Versionsnummer

> **Status: Sicher gefixt, inkl. Root Cause.** `lab_gui/icons/splash.png` war
> ein statisches, in Git getracktes Bild, das nur durch manuelles Ausführen
> von `tools/generate_splash.py` (liest `lab_gui/version.py`) neu erzeugt
> wurde — beim v0.9.0→v0.9.3-Bump (Commit `99f0c7d`) wurde das vergessen,
> wodurch die v0.9.3-.exe noch das v0.9.0-Bild zeigte. `LabControl.spec`
> importiert `tools/generate_splash.py` jetzt und ruft dessen `main()` vor
> jedem Build automatisch auf, bevor `Splash(...)` das PNG einliest — das
> Bild kann dadurch nicht mehr von `version.py` abweichen, unabhängig davon,
> ob jemand das Skript manuell ausführt. Das Skript bleibt zusätzlich direkt
> ausführbar (z.B. um das Bild-Design isoliert zu prüfen). Per Neu-Build
> verifiziert: Splash zeigt die aktuelle Versionsnummer.

Gemeldet vom Nutzer nach dem v0.9.3-Build: Splash-Screen beim Start zeigte
"v0.9.0", obwohl `lab_gui/version.py` bereits auf "0.9.3" stand.
Ausführen zu verlassen.

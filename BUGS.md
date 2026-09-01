# Bug-Liste (gefunden beim Testen von LabControl v0.6.1)

Sortiert nach empfohlener Bearbeitungsreihenfolge (sicherheitskritisch zuerst,
kosmetisch zuletzt). Jeder Eintrag kann unabhängig in einer eigenen Session
bearbeitet werden.

## 1. [SICHERHEITSKRITISCH] Netzteile beim Start bereits eingeschaltet

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


## 2. [SICHERHEITSKRITISCH] Sicherheits-Grenzwerte sind global statt geräte-individuell

Der Software-Watchdog (`lab_gui/safety.py`, `SafetyMonitor`) verwendet aktuell
globale Spannungs-/Strom-/Leistungsgrenzwerte, die für alle Geräte gleich sind.

**Gewünscht:** Jedes Gerät (PSU, Last) soll eigene, individuell konfigurierbare
Sicherheitsgrenzwerte bekommen können, statt einer gemeinsamen globalen
Einstellung.

**Kontext:** Der Watchdog selbst ist bereits vorhanden und funktionsfähig
(latching trip, `all_outputs_off()` bei Grenzwertüberschreitung) — es geht nur
um die fehlende Geräte-Individualität der Grenzwert-Konfiguration.


## 3. Dark Mode: "Ein"-Buttons im Control-Panel nicht grün

Im Dark Mode sind die Buttons zum Einschalten eines Ausgangs im Control-Panel
nicht grün eingefärbt (im Light-Theme funktioniert die Farbcodierung).

**Risiko:** Farbcodierung für Gerätezustand ist eine wichtige visuelle
Sicherheitsinformation (auf einen Blick erkennbar, ob ein Ausgang aktiv ist) —
sollte in beiden Themes konsistent grün sein.

**Zu prüfen:** Stylesheet/Theme-Definition für Buttons — vermutlich wird die
grüne Akzentfarbe nur im Light-Palette-QSS gesetzt und im Dark-Palette-QSS
überschrieben oder fehlt dort.


## 4. Last-Panel verschwindet kurz beim Übernehmen im Control-Tab

Wenn im Control-Tab bei der elektronischen Last ein Wert eingestellt und auf
"Übernehmen" geklickt wird, verschwindet das Panel kurz (Flackern/Re-Render).

**Vermutete Ursache:** UI-Refresh/Re-Render-Problem im Panel-Update-Handler
nach dem Anwenden eines neuen Werts — evtl. wird das Widget kurzzeitig neu
aufgebaut statt nur die Werte zu aktualisieren.


## 5. Verlaufs-Diagramme aktualisieren zu langsam (~2Hz)

Im Verlaufs-Tab werden die Diagramme mit geschätzt nur ~2Hz aktualisiert.
Für eine flüssige Darstellung werden mindestens 15Hz, besser 30Hz benötigt.

**Zu prüfen:** Timer-Intervall für den Plot-Refresh in der Timeline/History-
Tab-Implementierung.


## 6. Testschritt-Tabelle: Spaltenbreiten skalieren nicht sinnvoll

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

Im Control-Tab stoßen die Geräte-Panels links und oben direkt an den
Fensterrand an, ohne Abstand — anders als der Abstand, der zwischen den
einzelnen Geräte-Panels besteht.

**Gewünscht:** Gleicher Außenabstand (Margin) wie der Innenabstand (Spacing)
zwischen den Panels.


## 8. Verschachtelte Panel-Hintergründe uneinheitlich

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

Der Hintergrund der Verlaufs-Diagramme ist fest auf Schwarz gesetzt,
unabhängig vom aktuell gewählten Farb-Theme (Light/Dark).

**Gewünscht:** Diagramm-Hintergrund soll sich analog zum gewählten
Farb-Theme anpassen (z.B. hell im Light-Theme, dunkel im Dark-Theme).

**Hinweis:** Bugs 6-9 sind alle Layout-/Styling-Themen und lassen sich
vermutlich gemeinsam angehen, da man ohnehin im selben QSS-/Stylesheet-Bereich
unterwegs ist.

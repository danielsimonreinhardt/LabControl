# LabControl – Benutzerhandbuch

LabControl steuert eine elektronische Last (Korad KEL102) und ein
Labornetzteil (HCS-34xx) über USB. Dieses Handbuch beschreibt alle
Bereiche der Oberfläche: das Dashboard, die drei Reiter „Steuerung“,
„Testablauf“, „Verlauf“ und „Einstellungen“, sowie die Statuszeile.

Diese Seite lässt sich jederzeit über den Button **Hilfe** im Reiter
„Einstellungen“ erneut öffnen.

---

## 1. Dashboard (immer sichtbar)

Das Dashboard sitzt oben im Fenster, oberhalb der Reiter, und zeigt für
jedes bekannte Gerät eine eigene Kachel mit den aktuellen Messwerten
(Last: Spannung/Strom/Leistung/Modus; Netzteil: Spannung/Strom/Modus).
Die Anzeige aktualisiert sich automatisch etwa alle 500 ms.

- **Kein Gerät verbunden**: Solange kein einziges Gerät erkannt wurde,
  erscheint eine graue Platzhalter-Kachel.
- **Getrenntes Gerät**: Ein einmal bekanntes Gerät verschwindet beim
  Trennen nicht aus dem Dashboard, sondern wird grau dargestellt und
  bekommt ein „Stecker getrennt“-Symbol – so bleibt sichtbar, welche
  Geräte grundsätzlich zur Session gehören.
- **Ansicht umschalten**: Der Pfeil-Button unten rechts im
  Dashboard-Rahmen wechselt zwischen normaler und kompakter Ansicht
  (kleinere Kacheln, für viele gleichzeitig sichtbare Geräte).
- **Panel-Farben**: Ist die Option in den Einstellungen aktiviert (siehe
  Abschnitt 5), lässt sich jedes Geräte-Panel individuell einfärben, um
  z.B. „Last 1“ und „Last 2“ auf einen Blick zu unterscheiden. Die Farbe
  wird im Reiter „Steuerung“ gewählt und im Dashboard automatisch
  übernommen.

---

## 2. Reiter „Steuerung“

Für jedes verbundene Gerät erscheint hier eine eigene Bedien-Sektion.
Sind zwei baugleiche Geräte angeschlossen (z.B. zwei Netzteile), werden
sie unabhängig voneinander gesteuert.

### Preset-Leiste

Ganz oben befinden sich 5 feste, geräteübergreifende Preset-Plätze. Ein
Preset speichert für alle aktuell sichtbaren Geräte-Panels gemeinsam die
Sollwerte und den Schaltzustand:

- Großer Button in der Mitte: **Preset laden** (schreibt sofort auf die
  Hardware).
- Kleines Symbol oben rechts am Platz: **Preset speichern** (überschreibt
  den aktuellen Inhalt des Platzes mit dem aktuellen Zustand aller
  Panels).
- Kleines Symbol unten rechts: **Preset umbenennen**.

### Elektronische Last (KEL102)

- **Modus**: Konstantstrom (CC), Konstantspannung (CV),
  Konstantwiderstand (CR), Konstantleistung (CW) oder Kurzschluss
  (SHORT).
- **Sollwert**: Zahlenwert für den gewählten Modus, mit dem Häkchen-Button
  übernehmen. Bei „Kurzschluss“ ist kein Sollwert nötig.
- **Ausgang EIN/AUS**: Schaltet den Lasteingang. Der zuletzt aktive
  Zustand wird farbig hervorgehoben (grün = ein, rot = aus). Direkt nach
  dem Verbinden ist der Zustand unbekannt, bis die erste
  Hardware-Rückmeldung eintrifft – dann sind beide Buttons neutral.
- Über das Stift-Symbol oben in der Sektion lässt sich das Gerät
  **umbenennen**, über das Farbkreis-Symbol daneben die **Panel-Farbe**
  wählen.

### Labornetzteil (HCS-34xx)

- **Spannung** und **Strom**: Sollwerte, jeweils mit eigenem
  Häkchen-Button übernehmen.
- **OVP/OCP**: Überspannungs-/Überstrom-Schutzschwelle des Geräts. Ist
  der Spannungs- oder Stromsollwert höher als die zugehörige
  OVP/OCP-Schwelle, erscheint eine Warnung – das Gerät lehnt einen
  solchen Wert sonst kommentarlos ab.
- **Ausgang-Workaround**: Das HCS-34xx kennt kein echtes
  Software-Ausgang-Aus. „AUS“ setzt intern den Strom auf 0 A; „EIN“
  übernimmt die eingetragene Spannung und hebt den Strom auf mindestens
  0,1 A an, ohne einen bereits höher eingestellten Stromwert zu
  überschreiben. **Solange „AUS“ aktiv ist, sind die Übernehmen-Buttons
  für Spannung/Strom gesperrt** – das verhindert, dass ein Sollwert-Klick
  den emulierten Aus-Zustand unbemerkt wieder aufhebt. Erst ein Klick auf
  „EIN“ schaltet die Sollwert-Buttons wieder frei.

---

## 3. Reiter „Testablauf“

Zeilenbasierter Editor für automatisierte Testabläufe. Jede Zeile ist
entweder ein **Aktionsschritt** (Gerät + Aktion + Wert + Wartezeit) oder
ein **Ablaufsteuerungs-Element** (Schleife, Bedingung, Variable).

### Werkzeugleiste

- **„+“-Button**: öffnet über den Pfeil daneben ein Menü mit allen
  einfügbaren Zeilentypen (siehe unten). Ein Klick auf den Button selbst
  fügt direkt einen neuen Aktionsschritt ein.
- **„–“-Button**: entfernt die markierte Zeile.
- **Pfeil hoch/runter**: verschiebt die markierte Zeile.
- **Puzzleteil mit Plus** („Baustein speichern…“): speichert einen
  markierten, zusammenhängenden Zeilenbereich als wiederverwendbaren
  **Baustein** in einer eigenen Datei (Ordner `blocks/`). Der Dialog
  prüft dabei, dass der gewählte Bereich strukturell in sich
  abgeschlossen ist (z.B. eine Schleife inklusive ihres „Ende“).
- **Puzzleteil** („Baustein einfügen…“): fügt einen zuvor gespeicherten
  Baustein an der aktuellen Position ein – praktisch für wiederkehrende
  Abschnitte wie ein Lade-/Entladeprofil.
- **Ordner-Symbol** / **Diskette-Symbol**: Testablauf laden/speichern
  (JSON-Datei im Ordner `testcases/`).

### Eine Zeile bearbeiten (Aktionsschritt)

- **Gerät**: konkretes Gerät oder „automatisch“ (zur Laufzeit wird das
  einzige verbundene Gerät dieser Art verwendet – sind mehrere passende
  Geräte verbunden, bricht der Schritt mit einer Fehlermeldung ab).
- **Aktion**: abhängig vom Gerät, z.B. Sollwert setzen, Ausgang ein/aus,
  Modus wählen. Die Wert-Spalte passt Einheit sowie Minimum/Maximum
  automatisch an die gewählte Aktion an.
- **Arbiträrsignal**: Bei Aktionen, die ein sich veränderndes Signal statt
  eines festen Werts erlauben, erscheint statt des Zahlenfelds ein
  Button „Signal definieren…“. Im folgenden Dialog lässt sich zwischen
  Sinus, Rechteck (mit einstellbarem Tastgrad), Dreieck und Sägezahn
  wählen, dazu Amplitude, Offset, Frequenz und Update-Intervall – eine
  Live-Vorschau zeigt den Signalverlauf.
- **Wert / Dauer (s)**: Sollwert bzw. Wartezeit nach dem Schritt, bevor
  der nächste beginnt. Die Wartezeit muss die physikalische
  Einschwingzeit des Geräts abdecken (siehe Hinweis unten).
- **Prüfung**: optionale Pass/Fail-Grenzwerte (siehe unten).
- **Aktiv**: Häkchen zum vorübergehenden Deaktivieren einer Zeile, ohne
  sie zu löschen.

### Ablaufsteuerung

Über das „+“-Menü lassen sich zusätzlich einfügen:

- **Schleife (n×) … Ende**: wiederholt den Rumpf eine feste Anzahl mal
  (z.B. für Lade-/Entladezyklen bei Akku-Tests).
- **Solange … Ende**: wiederholt den Rumpf, solange eine Bedingung
  zutrifft (siehe „Bedingungen“ unten). Eine einstellbare Obergrenze
  „Max. Durchläufe“ verhindert eine Endlosschleife, falls die Bedingung
  nie falsch wird (0 = unbegrenzt, mit Vorsicht verwenden).
- **Wenn … Ende** (mit optionalem **Sonst**): führt den Rumpf nur aus,
  wenn eine Bedingung zutrifft.
- **Variable setzen / Variable erhöhen**: legt eine Laufvariable an bzw.
  ändert ihren Wert – nützlich als Zähler oder um Bedingungen später im
  Ablauf abzufragen.

Block-Start und „Ende“ werden immer als Paar eingefügt. Die Tabelle zeigt
die Verschachtelung als Einrückung; ist die Struktur unausgeglichen (z.B.
eine Schleife ohne „Ende“), bleibt der Start-Button gesperrt, bis die
Struktur korrigiert ist.

### Bedingungen (Solange/Wenn)

Über den Button mit dem Fragezeichen-Raute-Symbol in der Bedingungs-Zeile
öffnet sich ein Dialog mit drei Quellen:

- **Messwert**: vergleicht Spannung/Strom/Leistung eines konkreten oder
  automatisch gewählten Geräts gegen einen Wert.
- **Zeit**: vergleicht die seit Block- oder Testablaufstart verstrichene
  Zeit gegen einen Wert.
- **Variable**: vergleicht eine zuvor per „Variable setzen“ angelegte
  Laufvariable gegen einen Wert.

Fehlt bei „Messwert“ eine aktuelle Messung (Gerät gerade getrennt oder
Daten veraltet), gilt die Bedingung als nicht erfüllt, statt mit einem
stehengebliebenen Wert weiterzurechnen.

### Pass/Fail-Prüfungen

Über den Haken-Kreis-Button in der Spalte „Prüfung“ lässt sich pro
Aktionsschritt ein erwarteter Bereich [Min, Max] für Spannung, Strom oder
Leistung des Schritt-Geräts festlegen. Nach Ablauf der Wartezeit (bzw.
nach Ende eines Arbiträrsignals) bewertet der Testablauf die erste danach
eintreffende Messung:

- Die Zeile wird dauerhaft **grün** (bestanden) oder **rot** (nicht
  bestanden) eingefärbt; der tatsächliche Messwert steht als Tooltip
  über der Prüfzelle.
- In Schleifen ist eine Verletzung „sticky“: Ist die Zeile einmal rot,
  bleibt sie es, auch wenn spätere Durchläufe bestehen.
- Pro Prüfung wählbar: entweder bricht eine Verletzung den gesamten Lauf
  sofort ab (wie ein Geräte-Fehler), oder der Test läuft weiter und die
  Statuszeile meldet am Ende „BESTANDEN“/„NICHT bestanden“ mit einem
  Zähler aller Prüfungen.
- Bleibt die erwartete Messung ganz aus (Gerät getrennt), schlägt der
  Schritt fehl.

**Wichtiger Hinweis zur Wartezeit:** Am realen HCS-34xx fällt die
Ausgangsspannung ohne angeschlossene Last nach einem niedrigeren
Sollwert nur langsam über den internen Bleeder-Widerstand ab (in einer
Messung: 2 Sekunden nach „5 V setzen“ noch 8,7 V). Für Abwärtssprünge
ohne Last daher großzügige Wartezeiten wählen, sonst schlägt eine
Prüfung fälschlich fehl.

### Start, Stop, Report

- **Start**: beginnt die sequenzielle Ausführung aller aktiven Zeilen.
  Während der Ausführung ist die Tabelle gesperrt, der aktuelle Schritt
  markiert und in der Statuszeile sichtbar (inkl. aktueller
  Schleifen-/Solange-Durchlauf, falls zutreffend).
- **Stop**: bricht den Lauf ab und schaltet sofort alle Ausgänge ab
  (Sicherheitsabschaltung).
- **Report-Button** (nach jedem Lauf aktiv): öffnet einen Nachlauf-Report
  als HTML-Seite im Browser oder exportiert ihn als PDF. Der Report
  enthält den Ablauf, alle Prüfergebnisse und ein Diagramm der
  aufgezeichneten Messwerte.
- Bei aktivierter Desktop-Benachrichtigung (siehe Einstellungen) meldet
  eine System-Benachrichtigung Lauf-Ende bzw. -Fehler, auch wenn das
  Fenster gerade nicht im Vordergrund ist.

---

## 4. Reiter „Verlauf“

Fortlaufende Oszilloskop-Ansicht aller Geräte-Messwerte in einem oder
mehreren Diagrammen.

- **Diagramm hinzufügen**: legt ein neues, leeres Diagramm an.
- Pro Diagramm: **Signal hinzufügen** wählt, welche Geräte-Messgrößen
  angezeigt werden (gemeinsame Y-Achse je Einheit); über **Y-Achse
  einstellen…** lässt sich zwischen automatischer und fester
  Skalierung (links/rechts getrennt) wechseln; **Diagramm umbenennen**
  und **Diagramm entfernen** stehen ebenfalls zur Verfügung.
- **Zeitfenster**: bestimmt, wie viele Sekunden Verlauf sichtbar sind.
- **Pause/Fortsetzen**: friert die Live-Anzeige ein, ohne die
  Aufzeichnung zu stoppen.
- **Anzeige zurücksetzen**: leert nur die sichtbaren Diagramm-Puffer,
  unabhängig von einer laufenden Aufzeichnung.

### Aufzeichnung

Oberhalb der Diagramme sitzt die Aufzeichnung eines Messwert-Logs über
alle bekannten Geräte (Zeitstempel, Gerät, Kanal, Wert) – unabhängig vom
zeitfenster-begrenzten Ringpuffer der Live-Diagramme.

- Start/Stopp über einen gemeinsamen Button (rot blinkend = aktiv).
- Aufgezeichnet werden nur die Signale, die aktuell mindestens einem
  Diagramm zugeordnet sind.
- **Zurücksetzen**: löscht die bisherige Aufzeichnung.
- **Als CSV exportieren…**: Long-Format, eine Zeile je Messwert.
- **Als MF4 exportieren…**: ASAM-MDF4-Datei (ein Signal je Gerät+Kanal),
  benötigt das zusätzliche Python-Paket `asammdf`. Beide Exporte sind
  auch bei laufender Aufzeichnung möglich.

---

## 5. Reiter „Einstellungen“

- **Simulationsmodus**: blendet ein virtuelles Netzteil und eine
  virtuelle Last ein, um die Oberfläche ohne angeschlossene Hardware zu
  testen. In der fertig gebauten `.exe` ist diese Option aus
  Sicherheitsgründen nicht vorhanden.
- **Dark Mode**: wechselt zwischen dem hellen „Modern Light“- und dem
  dunklen „Amber Industrial“-Farbschema, ohne Neustart.
- **Desktop-Benachrichtigung bei Lauf-Ende/Fehler**: siehe Abschnitt 3.
- **Individuelle Panel-Hintergrundfarben**: schaltet die Panel-Farbwahl
  im Reiter „Steuerung“ global ein/aus (siehe Abschnitt 1/2). Beim
  erstmaligen Aktivieren bekommt jedes bereits bekannte Gerät automatisch
  eine eigene Farbe zugewiesen.
- **Sprache**: wechselt die Oberflächensprache sofort, ohne Neustart.
- **Gerätezuordnung löschen**: setzt gespeicherte Geräte-Namen,
  Sicherheits-Grenzwerte und Panel-Farben aller Geräte auf die
  Standardwerte zurück (mit Rückfrage, nicht rückgängig zu machen).
- **Sicherheits-Grenzwerte (Watchdog)**: siehe Abschnitt 6.
- **Hilfe**: öffnet dieses Benutzerhandbuch.

---

## 6. Sicherheit: Software-Watchdog

Für jedes bekannte Gerät lässt sich im Einstellungen-Tab eine eigene
Sektion mit Grenzwerten (max. Spannung/Strom, bei der Last zusätzlich
max. Leistung) aktivieren. Überschreitet eine laufende Messung einen
aktivierten Grenzwert **dieses konkreten Geräts**, schaltet die Software
sofort **alle** Ausgänge ab – unabhängig davon, ob gerade ein Testablauf
läuft.

- Ein ausgelöster Alarm ist „latchend“: Er bleibt bestehen, bis er über
  den **Quittieren**-Button im roten Banner am oberen Fensterrand
  bestätigt wird – so bleibt ein unbeaufsichtigter Lauf nicht unbemerkt
  auf „ausgelöst“ stehen.
- Der Sicherheitsstatus in der Statuszeile zeigt **AUS** (keine
  Grenzwerte aktiv), **AKTIV** (überwacht, alles im grünen Bereich) oder
  **AUSGELÖST** (Grenzwert überschritten, Ausgänge abgeschaltet).
- Während eines Testlaufs überwacht der Watchdog zusätzlich die
  Verbindung der beteiligten Geräte – bricht die Verbindung ab oder
  liefert ein Gerät zu lange keine aktuelle Messung, schaltet ebenfalls
  sofort ab.
- Der **„ALLE AUS“-Button** ganz rechts in der Statuszeile schaltet
  jederzeit manuell und sofort alle Ausgänge ab, unabhängig vom
  Watchdog-Status.

---

## 7. Statuszeile

Am unteren Fensterrand zeigt je ein Label pro bekanntem Gerät dessen
Verbindungsstatus (grün = verbunden, rot = getrennt). Die Verbindung
wird automatisch alle 3 Sekunden neu versucht, solange ein Gerät nicht
erreichbar ist. Daneben stehen der Sicherheitsstatus (siehe Abschnitt 6)
und ganz rechts der „ALLE AUS“-Button.

---

## 8. Testablauf-Dateien, Bausteine und Presets im Dateisystem

- Testabläufe: Ordner `testcases/` neben der `.exe` bzw. neben
  `lab_gui/`.
- Wiederverwendbare Bausteine: Ordner `blocks/`.
- Nachlauf-Reports (HTML/PDF): eigener Reports-Ordner, beim Export über
  den Dialog wählbar.
- Presets, Geräte-Namen, Panel-Farben und Sicherheits-Grenzwerte werden
  automatisch zwischen Programmstarts gespeichert und müssen nicht
  manuell exportiert werden.

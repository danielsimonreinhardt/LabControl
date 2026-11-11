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
- **Abdocken**: Der Button links daneben öffnet das Dashboard in einem
  eigenen Fenster, z. B. für einen zweiten Bildschirm; das Hauptfenster
  zeigt dann nur noch die Reiter. Schließen des Fensters (oder derselbe
  Button) holt das Dashboard zurück. Position und Größe des Fensters merkt
  sich die App, ebenso ob es beim Beenden abgedockt war.
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

**Anordnen**: Eine Sektion lässt sich an ihrer Titelzeile greifen und in
jede Zelle des Rasters ziehen, auch in eine Lücke neben einer großen
Sektion. Schon beim Ziehen sieht man, wo sie landet. Liegt dort bereits
eine Sektion, weicht nur diese auf den nächsten freien Platz aus; alle
anderen bleiben stehen. Die Anordnung bleibt gespeichert, Escape bricht ab.

### Preset-Leiste

Ganz oben, im eigenen Kasten „Presets“, befinden sich 5 feste, geräteübergreifende Preset-Plätze. Ein
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
- **Ausgang EIN/AUS**: Der Ausgang wird wirklich geschaltet (Befehl
  `SOUT`, der Zustand wird mit `GOUT` zurückgelesen). „AUS“ lässt Spannung und
  Strom unverändert; „EIN“ übernimmt die eingetragene Spannung und die
  Strombegrenzung und schaltet dann ein. Spannung und Strom lassen sich auch
  bei „AUS“ vorgeben (erst einstellen, dann einschalten). Ohne Last sinkt die
  Ausgangsspannung nach „AUS“ langsam (Entladen, etwa 10 s). Wird der Ausgang
  von außen geschaltet (z. B. über den Fernsteueranschluss auf der Rückseite
  oder ein anderes Programm; an der Front gibt es keine Ausgangstaste), folgt
  der Schalter in der Kachel nach etwa einer Sekunde. Beim Verbinden übernimmt
  LabControl den Zustand des Geräts (Ausgang, Spannung, Strom) und ändert nichts.
  „Alle aus“ schaltet den Ausgang aus.
- **Handbedienung**: Das Gerät hat keine Ausgangstaste, und jeder Befehl sperrt
  seine Regler für etwa 5 s (LED „Rear control“, sie blinkt bei laufender
  Abfrage im Takt von 5 Hz). Gleichzeitig von Hand und per LabControl zu
  bedienen geht deshalb nicht. Mit dem Schalter **Handbedienung** in der Kachel
  gibt LabControl das Gerät frei: Spannung auf das Minimum (1 V; 0 V nimmt das
  Gerät nicht an), Strom 0 A, **Ausgang EIN**, Regler entsperrt. Danach spricht
  LabControl das Gerät nicht mehr an (keine Abfrage, die LED bleibt aus), die
  Felder der Kachel sind gesperrt, Befehle aus Fernsteuerung und Testablauf
  werden mit „Netzteil in Handbedienung“ abgelehnt, und „Alle aus“ lässt das
  Gerät unberührt. Das Dashboard zeigt das Gerät als nicht verfügbar, die
  Statusleiste „Handbedienung“. Beim Zurückschalten übernimmt LabControl die am
  Gerät eingestellten Werte in die Felder. **Beim Beenden von LabControl wird
  jedes Netzteil genauso freigegeben** (1 V, 0 A, Ausgang EIN), damit es
  danach von Hand benutzbar ist.

### Funktionsgenerator (JDS2915 / JDS66xx)

Zwei gleichartige Kanalkästen (Kanal 1 und 2) nebeneinander:

- **Form, Frequenz, Amplitude, Offset, Tastverhältnis**: jeweils mit
  eigenem Häkchen-Button übernehmen. Die Amplitude ist Spitze-Spitze im
  Leerlauf (0–20 V), der Offset liegt bei ±9,99 V.
- **Ausgang EIN/AUS** je Kanal. Anders als bei der Last kennt der
  Generator eine Rückmeldung aller Einstellungen: Die Schalter zeigen
  immer den vom Gerät gemeldeten Zustand, und ein Eingriff am Gerät
  selbst (Drehknopf, Tasten) erscheint nach spätestens zwei Sekunden in
  den Feldern. Eine laufende Eingabe wird dabei nicht überschrieben,
  solange sich der Gerätewert nicht ändert.
- **Phase** (Kanal 2 relativ zu Kanal 1) gibt es nur einmal.
- **ALLE AUS** schaltet beide Ausgänge ab; Form, Frequenz usw. bleiben
  gespeichert.
- Im **Testablauf** stehen Aktionen für Frequenz, Amplitude, Offset,
  Tastverhältnis, Phase, die Formen Sinus/Rechteck/Puls/Dreieck/DC und
  Ausgang EIN/AUS zur Verfügung; der Kanal wird in der Zeile gewählt.
- Das Gerät wird über den CH340-USB-Seriell-Wandler erkannt. Weil dieser
  Wandler auch in fremden Geräten steckt, wird per Abfrage geprüft, ob
  tatsächlich ein Funktionsgenerator antwortet. Ein eingeschaltetes Gerät
  wird daher bis zu 30 Sekunden nach dem Einschalten erkannt.

### Oszilloskop (PicoScope 2204A/2205A)

Kanäle, Trigger und Kurven stellt LabControl bewusst nicht ein – dafür gibt
es die PicoScope-7-App bzw. Erfassungen über den MCP-Server. Die Sektion
zeigt:

- **Status** (frei, belegt, Testlauf, über MCP verbunden) sowie Typ und
  Seriennummer.
- **Letzte Erfassung** über Netzwerk/MCP mit dem Knopf **„Kurve“** (zeigt
  sie als Bild).
- **„PicoScope 7 öffnen“** und – solange LabControl das Gerät für
  Erfassungen hält – **„Trennen“**.
- Wie bei den anderen Geräten **umbenennen** (Stift) und **Panel-Farbe**
  (Farbkreis); beides gilt auch für die Dashboard-Kachel.

### Oszilloskop im Netzwerk (UNI-T MSO1000HD, z. B. MSO1154HD-S)

Dieselbe Sektion wie beim PicoScope, nur ohne Start-Knopf – das Gerät wird am
eigenen Bildschirm bedient. Es wird nicht per USB gefunden, sondern über seine
IP-Adresse:

1. Am Oszilloskop eine **feste IP-Adresse** einstellen (oder im Router
   reservieren). Die Adresse ist zugleich seine Kennung in LabControl – Name,
   Farbe und Freigaben hängen daran.
2. **Einstellungen → Geräte → „Oszilloskope im Netzwerk (SCPI)“**: Adresse
   eintragen, mehrere durch Komma getrennt.
3. LabControl prüft alle 10 Sekunden, ob das Gerät antwortet; dann erscheint
   die Sektion.

Nach einer Erfassung über den MCP-Server bleibt das Oszilloskop gestoppt und
zeigt die Erfassung an. *Bisher nur mit dem Simulator getestet.*

### Batterie-Simulator (14 Zellen, über CAN)

Der Simulator hat keinen eigenen USB-Anschluss, er hängt an einem
CAN-Interface, das in LabControl schon eingerichtet ist (Vector, PCAN oder
SLCAN). Einrichten:

1. **Einstellungen → CAN-Interfaces**: das Interface eintragen, an dem der
   Simulator hängt (500 kbit/s), und in der Spalte **„Gerät am Bus“**
   „Batterie-Simulator“ wählen. Das Interface kann gleichzeitig für andere
   Teilnehmer (z. B. das BCU-Board) genutzt werden.
2. Sobald der Simulator antwortet, erscheint seine Kachel. Bleibt seine
   Statusbotschaft eine Sekunde aus, gilt er als getrennt.

Bedienung in der Sektion:

- **Fernsteuerung EIN** übernimmt das Gerät (Zustand „Fernsteuerung“), der
  Ausgang bleibt dabei aus. Solange die Fernsteuerung aus ist, sendet
  LabControl nichts – das Gerät lässt sich dann normal am Bedienfeld nutzen.
- **Sollwerte** je Zelle oder für **alle Zellen** setzen, Zellen mit dem
  Haken **ein**- oder **aus**schalten. Eine ausgeschaltete Zelle ist am
  Ausgang gebrückt (0 V), der Stapel bleibt geschlossen.
- **Ausgang EIN** legt die eingeschalteten Zellen an. **ALLE AUS** sperrt den
  Ausgang, die Fernsteuerung bleibt dabei bestehen.
- **Grenzwert je Zelle** (Voreinstellung 4,30 V, bis 5,0 V): höhere Sollwerte
  werden abgelehnt – auch aus Testablauf und Fernsteuerung.
- **Istwert** je Zelle und die **Stapelspannung** meldet das Gerät selbst.
- **Neue Firmware (BattSimFW):** Die Zeile „Zustand“ zeigt zusätzlich den **Grund** eines FAIL (z. B. „Timeout 0x130“,
  „Überlast 48 V“, „Strommessung ungültig“, „Überspannung Zelle“), ob der **Regler** arbeitet, den Strom der 48-V-Versorgung
  und die Firmware-Version; die Kachel im Dashboard zeigt FAIL-Grund und Regler. Eine Zelle mit einem Sollwert unter etwa
  0,7 V zeigt hinter dem Istwert „(OL)“ (Open Load), siehe unten – das ist kein Fehler.
  Bei der alten Firmware bleiben diese Angaben leer.
- **Kanalstatus „OL“ (Open Load):** Bei der neuen Firmware zeigt eine Zelle „(OL)“ hinter dem Istwert (Kanalstatus 4; Netzwerk/MCP:
  `cellN_status` = „OL“), wenn ihr Sollwert größer als 0 V, aber kleiner als der **Pegelboden** der Endstufe ist (Parameter
  `OutputFloor_V`, Vorgabe 0,7 V). Das heißt:
  - **Ohne Last** geht die Endstufe nicht unter etwa 0,7 V. Ein Sollwert darunter ist nicht erreichbar, der Ausgang bleibt bei etwa
    0,7 V.
  - **Mit Last** (gemessen mit 100 Ω und 1 kΩ) folgt die Spannung dem Sollwert bis herab zu etwa 0,3 V.
  - Der Kanal bleibt **freigegeben**. Es ist **kein Fehler und kein FAIL**, nur ein Hinweis.
  - In diesem Bereich **arbeitet der Spannungsregler nicht** (der Sollwert ist ohne Last nicht erreichbar). Die Überspannungserkennung
    der Firmware vergleicht dann gegen den Boden plus 0,2 V, nicht gegen den Sollwert.
  - Die Firmware kennt den Strom der einzelnen Kanäle nicht. Der Hinweis „OL“ erscheint deshalb **auch mit angeschlossenem Prüfling**,
    obwohl die Spannung dann meist stimmt; ob wirklich keine Last hängt, zeigt er nicht.
- **Zustand „FAIL“**: Fällt die Verbindung zu LabControl aus (Programm
  beendet sich unerwartet, Kabel ab), schaltet der Simulator nach 4,5–6
  Sekunden ab. **„FAIL zurücksetzen“** holt ihn zurück; den Ausgang danach
  wieder einschalten. Beim normalen Beenden gibt LabControl die
  Fernsteuerung vorher geordnet ab.
- Im **Testablauf** gibt es Aktionen für Fernsteuerung, Ausgang,
  Zellspannung (einzeln/alle), Zelle ein/aus und zum Lesen von Zell- und
  Stapelspannung (mit Pass/Fail-Prüfung). Die Zelle wird in der Zeile
  gewählt.

*Bisher nur gegen einen nachgebildeten Simulator getestet, die Prüfung am
Gerät steht aus.*

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
- **Touch-Bedienung**: für kleine Touch-Displays wie am Raspberry Pi
  (1024 × 600). Größere Schrift und Bedienelemente, die Reiter als große
  Schaltflächen links, die Übersicht (Dashboard) als eigener Reiter statt
  eines festen Streifens, und oben eine Leiste mit dem Gerätezustand (zum
  Wischen) und **ALLE AUS**. Der Knopf daneben schaltet Vollbild ein und
  aus; am Raspberry Pi startet LabControl im Vollbild. Unter Linux gibt es
  dort zusätzlich einen Ein/Aus-Knopf: Er fährt den Rechner nach einer
  Rückfrage herunter oder startet ihn neu, LabControl gibt vorher die
  Netzteile frei. Zahlenfelder in den Kacheln haben große −/+-Tasten links
  und rechts; ein Tipp auf den Wert öffnet ein Zahlenfeld mit Bereich und
  Einheit („Setzen“ übernimmt und sendet wie der ✓-Knopf, ein Wert außerhalb
  des Bereichs lässt sich nicht setzen). Für die CAN-ID und die Nutzdaten
  gibt es dasselbe Feld mit den Tasten A–F. Der Reiter „Testablauf“ zeigt
  statt des Editors eine Ausführen-Ansicht: links die gespeicherten Abläufe
  (vom Netzlaufwerk, Ordner `testcases/`, sonst lokal), rechts die Schritte,
  Fortschritt, Status und große Knöpfe Start und Stop. Bearbeitet und
  gespeichert wird am PC; ausgeführt wird genauso wie im Editor (gleiche
  Prüfungen, ein Schrittfehler wird mit Stop quittiert). Listen, Tabellen
  und Seiten lassen sich mit dem Finger wischen; Kacheln verschiebt man nur
  im Modus **Anordnen** (Knopf in der Leiste oben, „Fertig“ beendet ihn).
  Knöpfe, die am PC nur ein Symbol mit Tooltip zeigen (Verlauf, CAN), tragen
  im Touch-Profil einen kurzen Text. Die Übersicht bricht an der
  Bildschirmbreite um und wird nach unten länger, statt waagerecht zu
  scrollen. Der Batterie-Simulator zeigt seine Zellen als 2 × 7-Raster
  (1–7 links, 8–14 rechts); ein Tipp auf einen Zellsollwert öffnet das
  Zahlenfeld, ausgeschaltete Zellen stehen grau. Im Verlauf wählt man das
  Zeitfenster über Tasten (30 s … 30 min), Signale für ein Diagramm aus
  einer Liste zum Wischen. Dialoge sind nie größer als der Bildschirm,
  Datei-Dialoge nutzen ihn ganz, die Kurve einer Erfassung hat einen Knopf
  „Schließen“. Die Leiste oben zeigt je Gerät einen kurzen Chip (✓ verbunden,
  ✕ getrennt, Ⓗ Handbedienung); ein Tipp darauf listet alle Geräte mit
  vollem Namen. Solange das Häkchen nie gesetzt wurde, schaltet LabControl das
  Profil auf Bildschirmen bis 1024 × 600 von selbst ein. Das Abdocken des
  Dashboards gibt es im Touch-Profil nicht.
- **Desktop-Benachrichtigung bei Lauf-Ende/Fehler**: siehe Abschnitt 3.
- **Individuelle Panel-Hintergrundfarben**: schaltet die Panel-Farbwahl
  im Reiter „Steuerung“ global ein/aus (siehe Abschnitt 1/2). Beim
  erstmaligen Aktivieren bekommt jedes bereits bekannte Gerät automatisch
  eine eigene Farbe zugewiesen.
- **Sprache**: wechselt die Oberflächensprache sofort, ohne Neustart.
- **Netzlaufwerk (Log und Daten)**: Ist ein Ordner eingetragen (z. B.
  `\\nas\labor` am PC oder `/mnt/labor` am Raspberry Pi), legt LabControl
  dort alles ab, was im Betrieb entsteht, damit man es später auswerten kann:

  | Ort auf dem Netzlaufwerk | Inhalt |
  |---|---|
  | `<Rechnername>/labdash.log`, `share_access.log` | Protokoll und Zugriffsprotokoll |
  | `<Rechnername>/captures/` | Oszilloskop-Erfassungen (CSV) |
  | `<Rechnername>/reports/` | Ablauf-Berichte (HTML, PDF) |
  | `<Rechnername>/recordings/` | Verlaufs-Aufzeichnungen (CSV, MF4) |
  | `testcases/`, `blocks/` | Testabläufe und Bausteine, gemeinsam für alle Rechner |

  Erfassungen und Berichte entstehen zuerst lokal und werden etwa eine
  Minute später verschoben; ein hängendes Netzlaufwerk hält die Bedienung
  so nie auf. Ist das Netzlaufwerk nicht erreichbar, bleibt alles lokal
  und wird nachgeschoben, sobald es wieder da ist (auch ältere Dateien aus
  der Zeit vor der Einstellung). Lokal bleiben dabei höchstens 200 MB
  liegen, darüber werden die ältesten noch nicht verschobenen Dateien
  gelöscht (steht im Protokoll). Das Protokoll schreibt lokal in
  `labdash.log` weiter und versucht es jede Minute erneut. Die
  Speichern-Dialoge und die Ordner für Testabläufe und Bausteine zeigen aufs
  Netzlaufwerk, wenn es erreichbar ist. Unter dem Feld steht, wohin gerade
  geschrieben wird und wie viele Dateien warten. Am Raspberry Pi gilt ein
  Ordner auf der SD-Karte nicht als Netzlaufwerk (auch kein leerer, nicht
  eingehängter Mountpoint). Die Umgebungsvariable `LABCONTROL_NETWORK_DIR`
  hat Vorrang vor diesem Feld. Ohne Eintrag bleibt alles wie bisher lokal.
- **Nicht verbundene Geräte löschen**: vergisst nur die Geräte, die gerade
  nicht verbunden sind (ausgegraute Kacheln), samt Namen,
  Sicherheits-Grenzwerten, Panel-Farben und Netzwerk-Freigaben. Verbundene
  Geräte bleiben unverändert. Wird ein vergessenes Gerät später wieder
  angeschlossen, erscheint es mit einem neuen Standardnamen.
- **Alle Geräte löschen**: setzt gespeicherte Geräte-Namen,
  Sicherheits-Grenzwerte, Panel-Farben und Netzwerk-Freigaben aller
  Geräte auf die Standardwerte zurück; verbundene Geräte bekommen neue
  Standardnamen, nicht verbundene verschwinden.
  Beide Knöpfe fragen vorher nach und lassen sich nicht rückgängig machen.
- **Sicherheits-Grenzwerte (Watchdog)**: siehe Abschnitt 6.
- **Netzwerk-Freigabe und Fernsteuerung**: siehe unten.
- **Hilfe**: öffnet dieses Benutzerhandbuch.

### Unterreiter „Netzwerk“: Kacheln im lokalen Netz bereitstellen

Damit lassen sich **einzelne Dashboard-Kacheln** für andere Geräte im
lokalen Netzwerk sichtbar machen — etwa für ein kleines ESP32-Display im
Labor oder für einen Browser auf dem Handy. Zum **Anzeigen** genügt die
Freigabe „Lesen“; zum **Fernsteuern** braucht es zusätzlich „Steuern“ und
den Hauptschalter (siehe „Fernsteuerung“ weiter unten).

Im Auslieferungszustand ist alles aus: der Server läuft nicht, es ist
keine Kachel freigegeben, und ein Zugangstoken wird verlangt.

Einrichtung in vier Schritten:

1. **Freigabe im lokalen Netzwerk aktivieren** anhaken.
2. Bei **Erreichbar für** „Alle Geräte im lokalen Netzwerk“ wählen (die
   Voreinstellung „Nur diesen PC“ ist zum Ausprobieren gedacht — ein
   ESP32 erreicht die App damit nicht). Beim ersten Mal fragt die
   Windows-Firewall nach einer Freigabe; diese muss erteilt werden.
3. In der Geräte-Tabelle unten bei jeder gewünschten Kachel **Lesen**
   anhaken. Ohne Haken ist eine Kachel von außen unsichtbar, auch wenn
   ihre Geräte-ID bekannt ist.
4. Die angezeigte **Adresse** in den ESP32-Sketch oder in den Browser
   kopieren.

Nützliche Adressen (`ADRESSE` ist die im Feld angezeigte Basisadresse):

| Adresse | Ergebnis |
| --- | --- |
| `ADRESSE/` | Übersicht aller freigegebenen Kacheln mit fertigen Links |
| `ADRESSE/display?tile=psu:0001` | kompakte Anzeigeseite, aktualisiert sich selbst |
| `ADRESSE/display?tile=psu:0001&refresh=2` | dasselbe, alle 2 Sekunden |
| `ADRESSE/display?tile=psu:0001&fmt=text` | schlichte `name=wert`-Zeilen |
| `ADRESSE/api/v1/tiles` | Liste der freigegebenen Kacheln als JSON |
| `ADRESSE/api/v1/tiles/psu:0001` | aktuelle Messwerte einer Kachel als JSON |

Die Anzeigeseite braucht kein JavaScript und ist rund ein Kilobyte groß —
sie ist bewusst so gebaut, dass ein ESP32 sie ohne weiteres darstellen
kann. Steht ein Wert oder ist das Gerät getrennt, wird er rot und
entsprechend beschriftet angezeigt. Läuft gerade ein Testablauf oder hat
der Watchdog ausgelöst, steht das ebenfalls auf der Seite.

**Token.** Er wird beim Einschalten der Freigabe automatisch erzeugt. Solange
„Lesezugriff ohne Token erlauben“ nicht angehakt ist,
muss jede Anfrage den Token mitbringen — entweder als Kopfzeile
(`Authorization: Bearer …`) oder einfach angehängt an die Adresse
(`…/api/v1/tiles?token=…`), was für einfache Displays der bequemere Weg
ist. „Neu erzeugen“ macht den bisherigen Token sofort ungültig und ist
damit der Weg, einem Gerät den Zugriff wieder zu entziehen.

**Sicherheitshinweis.** Die Verbindung ist **unverschlüsselt**, und der
Token wird im Klartext übertragen und im Klartext in `settings.json`
gespeichert. Er ist eine Zugangskennung fürs lokale Netz, **kein
Passwort**. Diese Funktion ist für ein vertrauenswürdiges Heim- oder
Labornetz gedacht: Der Port darf **niemals** per Portfreigabe oder UPnP
aus dem Internet erreichbar gemacht werden.

**Zugriffe protokollieren** schreibt jede Anfrage in eine eigene Datei
`share_access.log` neben dem Programm, bei eingestelltem Netzlaufwerk dort
unter `<Rechnername>/share_access.log`. Bewusst getrennt von `labdash.log`,
damit häufiges Abfragen die eigentliche Protokolldatei nicht überschreibt.
Standardmäßig aus.

#### Fernsteuerung

Über dieselbe Schnittstelle lassen sich Geräte auch **steuern**: Sollwerte
setzen, Ausgänge und Relais schalten, alles abschalten. Gedacht ist das für
einen KI-Assistenten (siehe MCP-Server unten) oder eine eigene App. Weil
dabei reale Hardware bewegt wird, müssen **alle** folgenden Bedingungen
erfüllt sein:

1. **Token**: Schreibende Anfragen verlangen den Token immer — auch wenn
   Lesen tokenfrei erlaubt ist — und nur als Kopfzeile
   (`Authorization: Bearer …`), nie in der Adresse.
2. **Steuern** ist beim Gerät in der Tabelle angehakt (setzt „Lesen“ voraus).
   Möglich ist das für Last, Netzteil, microHIL, Funktionsgenerator und
   Batterie-Simulator; CAN und
   Oszilloskop lassen sich nicht fernsteuern (zum Oszilloskop siehe „Messen“ unten).
3. Der Hauptschalter **„Fernsteuerung aktiv“** ist eingeschaltet (nicht nötig für
   Zugriffe vom selben Rechner, siehe „Ausnahme für diesen PC“). Er ist nach
   jedem Programmstart aus und schaltet sich nach dem eingestellten Zeitlimit
   (Standard 60 Minuten) von selbst wieder aus. Solange er an ist, steht in
   der Statuszeile ein orangefarbener Hinweis mit der Restzeit. Beim Ablauf
   bleiben die Geräte in ihrem Zustand — sie werden nicht abgeschaltet.
4. Es läuft **kein Testablauf** und die **Sicherheitsabschaltung** hat nicht
   ausgelöst. Sonst wird jeder Steuerbefehl abgewiesen (Lesen bleibt möglich).
5. Aktion, Wert und Kanal sind gültig — die Wertebereiche entsprechen denen im
   Testeditor — und der Sollwert liegt **nicht über einem aktiven
   Sicherheits-Grenzwert** dieses Geräts (Abschnitt 6).

**Ausnahme für diesen PC.** Programme auf demselben Rechner wie LabControl —
etwa der MCP-Server — brauchen den Hauptschalter nicht, solange „Zugriffe von
diesem PC brauchen den Hauptschalter nicht“ angehakt ist (Voreinstellung). Es gibt
dann kein Zeitfenster; Token, „Steuern“ beim Gerät, Wertebereiche, Grenzwerte und
die Sperren bei Testlauf und Sicherheitsabschaltung gelten weiter. Herausnehmen
des Hakens stellt das Zeitfenster auch für lokale Zugriffe wieder her. „Lokal“
wird an der Verbindung erkannt (Loopback oder die eigene Adresse des Rechners),
nicht an Angaben des Aufrufers.

**Notaus.** Die Aktion „Alle Ausgänge aus“ ist die einzige Ausnahme: sie
verlangt nur den Token und geht immer — auch bei ausgeschaltetem Hauptschalter,
laufendem Testablauf und nach einer Sicherheitsabschaltung. Sie wirkt wie der
„ALLE AUS“-Button.

Zusätzlich sind höchstens zwei Befehle gleichzeitig unterwegs, und schnelle
Befehlsfolgen werden gedrosselt, damit ein Stau den Notaus nicht verzögern kann.
Jeder Steuerbefehl **und jede Abweisung** steht mit der Adresse des Aufrufers im
Protokoll `labdash.log`.

Am Netzteil HCS-34xx schalten `PSU_OUT_ON`/`PSU_OUT_OFF` den Ausgang wirklich;
Spannung und Strombegrenzung bleiben dabei unverändert. `PSU_CURR` setzt nur die
Strombegrenzung und schaltet nichts. Der EIN/AUS-Schalter im Reiter „Steuerung“
folgt einer Fernsteuer-Aktion; die Sollwertfelder dort bleiben unverändert, die
tatsächlichen Werte zeigt das Dashboard.

Befehle sind `POST`-Anfragen mit JSON-Rumpf, z.B. (`TOKEN` und `ADRESSE`
einsetzen; die erlaubten Aktionen je Gerät nennt `ADRESSE/api/v1/tiles/<ID>`):

```
curl -X POST -H "Authorization: Bearer TOKEN" ^
     -d "{\"action\":\"PSU_VOLT\",\"value\":5.0}" ^
     ADRESSE/api/v1/tiles/psu:0001/actions
curl -X POST -H "Authorization: Bearer TOKEN" ADRESSE/api/v1/all-off
```

Die Antwort meldet, ob das Gerät den Befehl bestätigt hat (`"ok":true`) oder
warum er abgewiesen wurde (`"error":"…"`).

**MCP-Server für KI-Assistenten.** Im Ordner `labcontrol_mcp` liegt ein kleiner
Server, der diese Schnittstelle als Werkzeuge für Claude Code & Co. bereitstellt
(Status lesen, Geräte auflisten, Werte lesen, Aktion ausführen, Notaus, dazu
mit dem Oszilloskop messen). Einrichtung und Sicherheitsmodell stehen in
`labcontrol_mcp/README.md`. Er läuft als eigener Prozess und kann nicht mehr, als
die Schnittstelle oben erlaubt — insbesondere kann er den Hauptschalter nicht
selbst einschalten.

**Messen mit dem Oszilloskop über das Netzwerk.** Mit dem Haken **„Messen“** beim
Oszilloskop darf ein Assistent über den MCP-Server Erfassungen einstellen und
auswerten (Kanäle, Bereich, Zeitbasis, Trigger; Kennwerte wie Frequenz,
Tastgrad, Anstiegszeit). Weil dabei nichts geschaltet wird, braucht „Messen“ den
Hauptschalter nicht, wohl aber den Token und „Lesen“. Solange eine Erfassung
läuft oder kürzlich lief, hält LabControl das Oszilloskop geöffnet — die Kachel
zeigt dann **„Verbunden (MCP)“**. 60 Sekunden nach der letzten Erfassung wird
von selbst getrennt; **„Trennen“** auf der Kachel oder **„PicoScope 7 öffnen“**
geben es sofort frei. Nach einer Erfassung zeigt die Kachel deren Uhrzeit und
Kennwerte; **„Kurve“** öffnet sie als Bild — so sieht man, was der Assistent
gemessen hat. Ein Testablauf mit Oszilloskop-Schritten hat Vorrang: beim
Start wird eine Verbindung für Erfassungen getrennt, und bis zum Laufende werden
keine neuen angenommen. Jede Erfassung liegt zusätzlich als CSV-Datei im Ordner
`captures` neben dem Programm.

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
- Fernsteuer-Befehle über das Netzwerk (Abschnitt 5) werden abgewiesen,
  solange der Alarm ausgelöst ist oder ein Testablauf läuft, und ein
  Sollwert über einem aktiven Grenzwert wird gar nicht erst angelegt.
  „Alle Ausgänge aus“ bleibt auch dann möglich.

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

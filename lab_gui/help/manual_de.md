# LabControl – Benutzerhandbuch

LabControl steuert eine elektronische Last (Korad KEL102) und ein
Labornetzteil (HCS-34xx) über USB. Dieses Handbuch beschreibt alle
Bereiche der Oberfläche: das Dashboard, die Seiten „Steuerung“,
„Testablauf“, „Verlauf“ und „Einstellungen“ (Navigation links) und die
Leiste oben mit dem Gerätezustand und „ALLE AUS“.

Diese Seite lässt sich jederzeit über den Knopf mit dem Fragezeichen (**Hilfe**)
in den „Einstellungen“ erneut öffnen.

---

## 1. Dashboard (immer sichtbar)

Das Dashboard sitzt oben im Fenster, über jeder Seite, und zeigt für
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
  (kleinere Kacheln, für viele gleichzeitig sichtbare Geräte). Ohne
  gespeicherte Wahl startet LabControl kompakt; die Wahl wird gespeichert.
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
- **Letzte Erfassung** über Netzwerk/MCP mit dem Knopf **Kurve** (Diagramm-Symbol,
  zeigt sie als Bild).
- **„PicoScope 7 öffnen“** (nur am PC, nicht im Touch-Profil) und – solange
  LabControl das Gerät für Erfassungen hält – **Trennen** (Symbol mit getrenntem
  Netzwerk).
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

### Prüfling (UDS)

Ist in den Einstellungen ein Prüfling eingetragen (Abschnitt 5), erscheint
er als eigene Kachel. Oben stehen Session (mit „entsperrt“) und das Ergebnis
der letzten Bedienung. Darunter: Session wählen und wechseln, **Entsperren**
(Schloss), eine DID wählen und **lesen** (Werte mit Einheiten), **Fehlerspeicher
lesen** (Code, Status, Text) und **löschen**, **Reset**. Löschen und Reset
fragen nach. Während ein Testablauf läuft, ist die Kachel gesperrt; die
Übersicht zeigt Session, Zahl der DTCs und die letzte Bedienung.

### J-Link (Flashen und Reset)

Ein SEGGER J-Link am PC lässt sich als Gerät einbinden: in den Einstellungen
unter **Geräte → J-Link** „J-Link verwenden“ anhaken und den **Zielbaustein**
(z. B. `S32K144`), die Schnittstelle (SWD/JTAG) und den Takt eintragen; bei
mehreren J-Links die Seriennummer. Gebraucht wird die SEGGER J-Link Software
(J-Link Commander), LabControl sucht sie selbst.

Die Kachel zeigt Ziel, Zielspannung, die letzte Bedienung mit Ergebnis und die
zuletzt geflashte Datei. Darunter fünf Symbol-Knöpfe (Name im Tooltip):
**Prüfen** (Stecker) verbindet sich mit dem Ziel und liest Zielspannung und Kern
(der Kern läuft dabei weiter), **Reset** (Kreispfeil), **Anhalten** (Pause),
**Starten** (Abspielen) und **Flashen** (Download-Pfeil: Datei wählen, danach
Reset und Start). Der Dateidialog beginnt bei der zuletzt geflashten Datei und
zeigt HEX, SREC/S19/MOT und ELF/AXF/OUT; `.bin` wird abgelehnt, weil sie keine
Adresse enthält. Im Testablauf gibt es dieselben Bedienungen; beim Flashen wählt man die
Datei (HEX, SREC oder ELF) in der Wert-Spalte. Ein fehlgeschlagener Schritt
bricht den Ablauf ab.

LabControl öffnet den J-Link nur für die Dauer einer Bedienung und fragt ihn
nicht im Hintergrund ab – der Debugger in der IDE bleibt nutzbar, solange gerade
nichts läuft. Bei der **EDU-Lizenz** zeigt SEGGER einmal je 24 Stunden einen
Bestätigungsdialog; bis er weggeklickt ist, wartet die Bedienung (Zeitlimit
30 s, Flashen 180 s) und meldet danach diesen Grund. Jede Bedienung steht im
Log, beim Flashen mit Dateiname und Prüfsumme (SHA-256).

---

## 3. Reiter „Testablauf“

Zeilenbasierter Editor für automatisierte Testabläufe. Jede Zeile ist
entweder ein **Aktionsschritt** (Gerät + Aktion + Wert + Wartezeit) oder
ein **Ablaufsteuerungs-Element** (Schleife, Bedingung, Variable).

Solange noch kein Ablauf geladen und nichts eingetragen ist, steht über
der Tabelle der Hinweis **„Noch kein Testablauf“** mit dem Ordner, in dem
die Abläufe liegen. Er verschwindet, sobald ein Ablauf geladen ist oder man
einen Schritt bearbeitet. Rechts in der Werkzeugleiste stehen **Neuer Ablauf**
(Blatt mit Plus: leert die Tabelle bis auf eine Startzeile und löst die
Verbindung zur Datei, mit Rückfrage, wenn schon etwas geladen oder eingetragen
ist), **Laden** und **Speichern**.

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
  Abschnitte wie ein Lade-/Entladeprofil oder einen Fehler am microHIL
  („Kabelbruch setzen“ / „Kabelbruch aufheben“ aus HIL-Schritten).
  Der Baustein ist **verknüpft**: Die Kopfzeile zeigt „Baustein (verknüpft)“,
  seine Zeilen sind gesperrt, und der Ablauf verweist auf die Baustein-Datei.
  Beim Laden und vor jedem Start übernimmt er deren aktuellen Inhalt –
  ändert man den Baustein (neu speichern unter demselben Namen), gilt das in
  allen Abläufen. Die Ablauf-Datei enthält die Schritte zusätzlich als Kopie;
  fehlt die Baustein-Datei (z. B. am anderen Rechner), läuft die Kopie und die
  Statuszeile nennt den Baustein.
- **Kette durchgestrichen** („Verknüpfung zum Baustein lösen“): macht aus dem
  markierten verknüpften Baustein eine bearbeitbare Kopie. Fügt man in einen
  verknüpften Baustein eine Zeile ein oder entfernt eine, löst sich die
  Verknüpfung ebenfalls.
- **Ordner-Symbol** / **Diskette-Symbol**: Testablauf laden/speichern
  (JSON-Datei im Ordner `testcases/`). Wurde die geöffnete Datei seit dem
  Öffnen anderswo geändert (z. B. am zweiten Rechner im gemeinsamen Ordner),
  fragt das Speichern nach: **„Überschreiben“**, **„Als Kopie speichern“**
  (legt „Name (Kopie).json“ an) oder **„Abbrechen“**.
- **Regler-Symbol** („Eigenschaften des Ablaufs…“): Beschreibung und
  Anforderungs-IDs des ganzen Ablaufs (z. B. `SSR-12, SSR-13`) sowie
  **„Netzteil und Last gleichzeitig (Quelle/Senke)“** – hebt für diesen Ablauf
  die Verriegelung Netzteil/Last auf (Abschnitt 6), etwa um eine Batterie
  nachzubilden. Der Report vermerkt das.

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

### UDS-Schritte (Prüfling)

Mit dem Gerät **„Prüfling (UDS)“** spricht ein Ablauf über UDS mit dem in
den Einstellungen eingetragenen Steuergerät (Abschnitt 5): Session wechseln,
Entsperren, DID lesen/schreiben, Fehlerspeicher lesen/löschen, Routine,
Reset, rohe Anfrage. **„UDS-Parameter…“** wählt DID, Element, Session oder
Routine aus dem ODX. Jeder Schritt hat eine **Erwartung**: positive Antwort
oder eine bestimmte negative Antwort (NRC, z. B. 0x33 für „gesperrt“).
„Fehlerspeicher lesen“ kann DTCs verlangen, verbieten oder einen leeren
Speicher. Eine Abweichung gilt als verletzte Prüfung und bricht den Lauf ab.
„DID lesen“ liefert den Wert des gewählten Elements für eine Prüfung mit
Bereich und für eine Variable (Bedingungen in „Wenn“/„Solange“).

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

- **Start** (Abspielen-Symbol): beginnt die sequenzielle Ausführung aller aktiven Zeilen.
  Während der Ausführung ist die Tabelle gesperrt, der aktuelle Schritt
  markiert und in der Statuszeile sichtbar (inkl. aktueller
  Schleifen-/Solange-Durchlauf, falls zutreffend).
- **Stop** (Quadrat): bricht den Lauf ab und schaltet sofort alle Ausgänge ab
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
  (1024 × 600). Größere Schrift und Bedienelemente, größere Schaltflächen in
  der Navigation links, die Übersicht (Dashboard) als eigene Seite statt
  eines festen Streifens, der Gerätezustand oben zum Wischen und der Knopf
  zum **Anordnen** (Verschiebe-Kreuz). Den Vollbild-Knopf oben gibt es auch am PC; am Raspberry Pi
  startet LabControl im Vollbild. Unter Linux gibt es
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
  im Modus **Anordnen** (Knopf mit dem Verschiebe-Kreuz in der Leiste oben; während des
  Anordnens zeigt er einen Haken, ein Tipp darauf beendet den Modus).
  Knöpfe, die am PC nur ein Symbol mit Tooltip zeigen (Verlauf, CAN), tragen
  im Touch-Profil einen kurzen Text. Die Übersicht bricht an der
  Bildschirmbreite um und wird nach unten länger, statt waagerecht zu
  scrollen; sie ist dort immer kompakt (etwas größer, ohne Umschalter).
  Der Batterie-Simulator zeigt seine Zellen als 2 × 7-Raster
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
- **Anforderungsregister**: Dateien mit Anforderungen, mehrere mit `;`
  getrennt – StrictDoc (`.sdoc`, z. B. `ssr.sdoc` der neXtBSW) oder eine
  Tabelle als CSV/Markdown (Spalten ID und Titel). Im Prüfungsdialog
  vervollständigt das Feld „Anforderungen“ daraus; der Report zeigt die
  Titel und je Anforderung das Urteil: bestanden nur, wenn mindestens eine
  Prüfung lief und alle bestanden; ohne gelaufene Prüfung „nicht geprüft“.
  `python lab_gui/main.py coverage --csv abdeckung.csv` fasst alle Läufe
  zusammen (je Anforderung das Urteil des jüngsten Laufs).
- **Prüfling (UDS)** (Reiter „Geräte“): das Steuergerät, mit dem LabControl
  über UDS spricht. **ODX/PDX** ist der Export der neXtBSW
  (`python -m tools.diaggen pdx <projekt> -o <datei>.pdx`); daraus kommen
  CAN-IDs, DIDs mit Einheiten, Fehlertexte und Routinen. **CAN-Interface**
  „automatisch“ nimmt das einzige verbundene. **Schlüssel** ist der Name der
  Datei `<gemeinsamer Ordner>/keys/<Name>.key` mit dem Master-Schlüssel
  (64 Hex-Zeichen) für das Entsperren. Im Simulationsmodus spricht LabControl
  ohne Eintrag mit einem nachgebildeten Steuergerät am simulierten Bus.
- **Messmittel** (Reiter „Geräte“): je Gerät Typ, Seriennummer und Firmware,
  wie das Gerät sie beim Verbinden meldet (das HCS-Netzteil kennt keine
  Firmware-Abfrage), dazu Inventarnummer, Kalibrierdatum und Fälligkeit zum
  Eintragen (Datum als JJJJ-MM-TT). Gespeichert wird in `messmittel.json` im
  gemeinsamen Ordner, PC und Pi sehen denselben Stand. Der Report eines
  Laufs listet die verwendeten Messmittel und warnt, wenn eine Kalibrierung
  abgelaufen ist; der Lauf gilt trotzdem als bestanden.
- **Sicherheits-Grenzwerte (Watchdog)**: siehe Abschnitt 6.
- **Netzwerk-Freigabe und Fernsteuerung**: siehe unten.
- **Hilfe** (Fragezeichen): öffnet dieses Benutzerhandbuch.

### Unterreiter „Netzwerk“: Kacheln im lokalen Netz bereitstellen

Damit lassen sich **einzelne Dashboard-Kacheln** für andere Geräte im
lokalen Netzwerk sichtbar machen — etwa für ein kleines ESP32-Display im
Labor oder für einen Browser auf dem Handy. Zum **Anzeigen** genügt die
Freigabe „Lesen“; zum **Fernsteuern** braucht es zusätzlich „Steuern“, und
der Aufrufer muss steuern dürfen (siehe „Fernsteuerung“ weiter unten).

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
3. Der Aufrufer **darf steuern**. Das sind genau drei:
   - der gekoppelte Verbund-Partner (siehe „Verbund“),
   - Programme am selben Rechner, solange „Claude (MCP) darf steuern“ angehakt ist,
   - **gekoppelte Programme** mit ihrem eigenen Schlüssel (siehe unten).

   Wer nur den allgemeinen Token hat (etwa ein ESP32-Display), darf lesen, aber
   nicht steuern. Den früheren Hauptschalter mit Zeitfenster gibt es seit 0.57.0
   nicht mehr.
4. Es läuft **kein Testablauf** und die **Sicherheitsabschaltung** hat nicht
   ausgelöst. Sonst wird jeder Steuerbefehl abgewiesen (Lesen bleibt möglich).
5. Aktion, Wert und Kanal sind gültig — die Wertebereiche entsprechen denen im
   Testeditor — und der Sollwert liegt **nicht über einem aktiven
   Sicherheits-Grenzwert** dieses Geräts (Abschnitt 6).

**Claude (MCP) darf steuern.** Programme auf demselben Rechner wie LabControl —
etwa der MCP-Server — dürfen steuern, solange **„Claude (MCP)
darf steuern“** angehakt ist (Voreinstellung, direkt unter „Freigabe im lokalen
Netzwerk aktivieren“). Token, „Steuern“ beim
Gerät, Wertebereiche, Grenzwerte und die Sperren bei Testlauf und
Sicherheitsabschaltung gelten weiter. Ohne Haken darf Claude nur lesen. Der Chip **„MCP“** in der Leiste oben zeigt den Stand:
grün = Claude darf steuern, grau = nur lesen. „Lokal“ wird an der Verbindung
erkannt (Loopback oder die eigene Adresse des Rechners), nicht an Angaben des
Aufrufers.

**Programme koppeln.** Ein Skript oder ein MCP-Server auf einem anderen Rechner
koppelt sich wie der Verbund-Partner. Das geht mit
`python tools/labcontrol_pair.py ADRESSE "Name des Programms"`:
1. Das Skript zeigt einen 6-stelligen Code.
2. LabControl fragt „Programm „Name“ (Adresse) möchte koppeln und steuern“ mit
   demselben Code.
3. Bei gleichem Code tippt man **„Code gleich – koppeln“**.

Danach bekommt das Programm einmal seinen **eigenen Schlüssel**. Den benutzt es wie
den Token, aber nur als Kopfzeile `Authorization: Bearer …`. LabControl
speichert davon nur einen Fingerabdruck. Mit dem Schlüssel darf das Programm
lesen und steuern; die Freigabeliste („Lesen“, „Steuern“) und alle Sperren
gelten weiter. Jeder Befehl steht mit dem Programmnamen im Protokoll.

Unter **Einstellungen → Netzwerk → „Gekoppelte Programme“** stehen alle Programme
mit Adresse und Datum. Der Papierkorb hebt eine Kopplung auf, der Schlüssel gilt
dann sofort nicht mehr.

**Notaus.** Die Aktion „Alle Ausgänge aus“ ist die einzige Ausnahme: sie
verlangt nur den Token und geht immer — auch für Aufrufer, die nicht steuern dürfen,
bei laufendem Testablauf und nach einer Sicherheitsabschaltung. Sie wirkt wie der
„ALLE AUS“-Button.

Zusätzlich sind höchstens zwei Befehle gleichzeitig unterwegs, und schnelle
Befehlsfolgen werden gedrosselt, damit ein Stau den Notaus nicht verzögern kann.
Jeder Steuerbefehl **und jede Abweisung** steht mit der Adresse des Aufrufers im
Protokoll `labdash.log`.

Auch jede Bedienung am Gerät selbst steht dort, eine Zeile je Bedienung an
Netzteil, Last, Funktionsgenerator, microHIL und Batterie-Simulator: Gerät,
Aktion, Wert, Kanal, Herkunft (`Kachel`, `Preset` oder `Testablauf`) und
Ergebnis, z. B. `Netzteil psu:…: PSU_VOLT (wert=12.0, kanal=0) per Kachel -> ok`.
Bei `FEHLER` steht der Grund in eckigen Klammern dahinter. Reine Leseaktionen
eines Testablaufs (Eingänge, Zellspannungen) erscheinen nicht.

Am Netzteil HCS-34xx schalten `PSU_OUT_ON`/`PSU_OUT_OFF` den Ausgang wirklich;
Spannung und Strombegrenzung bleiben dabei unverändert. `PSU_CURR` setzt nur die
Strombegrenzung und schaltet nichts. Der EIN/AUS-Schalter und die Sollwertfelder
im Reiter „Steuerung“ folgen einer Fernsteuer-Aktion (seit 0.56.0 auch die
Sollwerte), ein Feld, in dem gerade jemand tippt, ausgenommen.

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
die Schnittstelle oben erlaubt — insbesondere kann er sich das Steuern nicht
selbst erlauben. Läuft er auf einem anderen Rechner, koppelt man ihn als Programm
(siehe oben) und trägt dessen Schlüssel als Token ein.

**Messen mit dem Oszilloskop über das Netzwerk.** Mit dem Haken **„Messen“** beim
Oszilloskop darf ein Assistent über den MCP-Server Erfassungen einstellen und
auswerten (Kanäle, Bereich, Zeitbasis, Trigger; Kennwerte wie Frequenz,
Tastgrad, Anstiegszeit). Weil dabei nichts geschaltet wird, braucht „Messen“ kein
Steuerrecht, wohl aber den Token und „Lesen“. Solange eine Erfassung
läuft oder kürzlich lief, hält LabControl das Oszilloskop geöffnet — die Kachel
zeigt dann **„Verbunden (MCP)“**. 60 Sekunden nach der letzten Erfassung wird
von selbst getrennt; **„Trennen“** auf der Kachel oder **„PicoScope 7 öffnen“**
geben es sofort frei. Nach einer Erfassung zeigt die Kachel deren Uhrzeit und
Kennwerte; **„Kurve“** öffnet sie als Bild — so sieht man, was der Assistent
gemessen hat. Ein Testablauf mit Oszilloskop-Schritten hat Vorrang: beim
Start wird eine Verbindung für Erfassungen getrennt, und bis zum Laufende werden
keine neuen angenommen. Jede Erfassung liegt zusätzlich als CSV-Datei im Ordner
`captures` neben dem Programm.

**Verbund mit einem zweiten LabControl (z. B. Raspberry Pi und PC).** Ganz unten
im Unterreiter „Netzwerk“ trägt man unter **„Verbund“** die Partner-Adresse
(z. B. `192.168.178.181:8420`) und den Partner-Token ein und hakt **„Verbund
aktivieren“** an. Auf beiden Rechnern muss die Freigabe für **alle Geräte
im lokalen Netzwerk** laufen. Der Verbund ist von beiden Rechnern gleichwertig
bedienbar: Der gekoppelte Partner sieht und bedient **alle** Geräte des anderen
Rechners, unabhängig von dessen Freigabeliste. Die Freigabeliste gilt weiter für
alle anderen Zugriffe und für Claude (MCP): Claude bekommt Partnergeräte nur,
wenn sie dort zum Lesen bzw. Steuern freigegeben sind. Die Geräte des
Partners erscheinen in **Übersicht und Steuerung** mit denselben Kacheln
wie eigene Geräte und dem Namen, den sie am Partner haben; einziges Merkmal ist
das Netzwerk-Symbol hinter dem Kacheltitel (Tooltip: Rechnername). Was über den
Verbund nicht geht (Handbedienung, OVP/OCP und Speicherplätze des Netzteils,
PWM und 12-V-Ausgänge des microHIL, der Grenzwert des Batterie-Simulators,
CAN-Frames senden), fehlt in der Partnerkachel; der CAN-Bus des Partners steht
nur in der Übersicht. **Beide Rechner zeigen dasselbe:** Die Felder einer Kachel
(Sollspannung, Strombegrenzung, Last-Modus und -Sollwert, Funktionsgenerator,
Zellwerte) folgen dem Gerät, egal an welchem Rechner, über Claude oder im
Testablauf bedient wurde; ein Feld, in dem gerade jemand tippt, bleibt
unangetastet. Name und Kachelfarbe gehören dem Rechner, an dem das Gerät steckt.
Umbenennen oder Umfärben in einer Partnerkachel ändert sie dort, und beide Rechner
zeigen das Ergebnis. Die Anordnung der Kacheln hat jeder Rechner für sich. Der
gekoppelte Partner darf dort immer steuern:
Die Kopplung, bei der an beiden Rechnern derselbe Code bestätigt wird, ist die
Freigabe. Erkannt wird er an der Verbund-Kennung **und** an der eingetragenen
Partner-Adresse. Bedienen geht, solange am Partner weder ein Testablauf läuft
noch eine Sicherheitsabschaltung anliegt; sonst ist die Kachel gesperrt (ausgegraut) und
der Verbund-Chip oben färbt sich gelb, sein Tooltip nennt den Grund. Lehnt der
Partner eine Bedienung ab, erscheint der Grund kurz an der Kachel. Jedes Gerät
gehört dem Rechner, an dem es steckt: Grenzwerte, Handbedienung und
Sicherheitsabschaltung prüft immer dieser.

**Koppeln statt Token abtippen.** Am einfachsten trägt man nur die Adresse des
anderen Rechners ein und drückt **„Koppeln…“**. Beide Rechner zeigen dann
denselben sechsstelligen Code; am anderen Rechner erscheint „Kopplung
anfragen“ – stimmt der Code, dort **„Koppeln“** tippen. Danach tauschen beide
ihre Token aus, tragen sich gegenseitig als Partner ein und schalten den
Verbund ein; am Pi muss man so nichts abtippen. Die Anfrage gilt 60 Sekunden.
„Ablehnen“ oder „Abbrechen“ verwerfen sie, es bleibt alles, wie es war.
Voraussetzung: Am angefragten Rechner läuft die Freigabe für alle Geräte im
lokalen Netzwerk. Damit der Partner auch den fragenden Rechner erreicht (ALLE AUS
in beide Richtungen), braucht dieser ebenfalls die Freigabe im lokalen Netzwerk –
sonst erinnert ein Hinweis daran. Welche Geräte gelesen und gesteuert werden
dürfen, legt weiterhin jeder Rechner in seiner Freigabeliste fest.

**ALLE AUS wirkt im Verbund auf beide Rechner**: der Knopf, ALLE AUS über MCP
oder das Netzwerk und eine Sicherheitsabschaltung schalten auch beim Partner
alles ab. Stop oder Fehler eines Testablaufs betreffen nur den eigenen Rechner.
Die Leiste oben zeigt den Verbund als Chip „⇄ Pi“ (verbunden) bzw. „✕ Pi“
(getrennt). Beim Beenden der App wird am Partner nichts abgeschaltet.
ALLE AUS bricht außerdem einen laufenden Testablauf ab, damit dessen nächster
Schritt die Ausgänge nicht wieder einschaltet.

**Gemeinsame Testabläufe.** Im Testablauf stehen die Geräte des Partners in der
Geräteauswahl („Pi · Netzteil 1 (Netzteil)“) neben den eigenen; Bedingungen und
Prüfungen können ihre Messwerte nutzen (Netzteil, Last, microHIL; etwa im
Sekundentakt). Der Rechner, an dem der Ablauf gestartet wird, führt ihn aus
(Leitstand). Beim Start **belegt** er die Partnergeräte, die der Ablauf bedient:
Am Partner zeigen deren Kacheln „belegt durch <Rechner> · <Ablauf>“ und sind
gesperrt (Handbedienung, ALLE AUS und Sicherheitsabschaltung bleiben), und
Bedienungen von anderer Stelle weist er ab. Der Leitstand meldet sich jede
Sekunde; **bleibt das 3 Sekunden aus** (Netz weg, Absturz), schaltet der Partner
die belegten Geräte ab, und der Leitstand bricht den Ablauf ab. Nach dem Ende
des Ablaufs ist die Belegung aufgehoben. Pi und PC deshalb per Kabel verbinden,
nicht über WLAN. Über den Verbund gehen die Bedienungen aus der Partnerkachel
(Sollwerte, Ausgänge, Kanäle), nicht aber Arbiträrsignale, Leseschritte und
CAN-Botschaften – solche Abläufe starten mit einer Meldung gar nicht erst.
Gespeichert wird mit Rechnernamen („@raspberrypi/psu:0001“), so meint dieselbe
Datei an beiden Rechnern dieselben Geräte.

**Aufzeichnung im Verbund.** Der Leitstand zeichnet die Werte der Partnergeräte
(Netzteil, Last, microHIL) mit auf: Sie stehen im Verlauf zur Auswahl, landen in
Aufzeichnung (CSV/MF4) und Bericht, etwa im Sekundentakt mit dem Zeitstempel
beim Eintreffen. Die Uhren beider Rechner sollen über NTP laufen (Pi:
systemd-timesyncd, PC: Windows-Zeitdienst); weichen sie um mehr als eine Sekunde
ab, zeigt der Verbund-Chip „⚠“ und einen Hinweis. Für schnelle Vorgänge ist das
Oszilloskop zuständig.

**Claude (MCP) im Verbund.** Der MCP-Server bleibt auf den PC eingestellt. Die
Freigabe des PCs zeigt Claude zusätzlich die Partnergeräte als
`@raspberrypi/psu:0001` und reicht Befehle daran an den Partner weiter. Dafür
muss am PC „Claude (MCP) darf steuern“ angehakt sein; am Partner braucht es
nichts weiter. Vor dem ersten Befehl belegt der PC das Gerät beim
Partner („belegt durch PC · Claude (MCP)“) und meldet sich jede Sekunde; fehlt
das 3 Sekunden, schaltet der Partner das Gerät ab. Die Belegung endet nach
10 Minuten ohne Befehl von Claude, bei ALLE AUS, einer Sicherheitsabschaltung,
dem Start eines Testablaufs oder wenn der Verbund umgestellt wird; abgeschaltet
wird dabei nichts. Durchgereicht wird nur an Aufrufer auf demselben Rechner.

**Batterie-Simulator: nur ein Sender.** Hängen Pi und PC am selben CAN-Bus, darf
nur einer den Batterie-Simulator steuern (Botschaft 0x130, im Normalfall der
Pi). LabControl hört 0x130 mit: Sendet schon ein anderer Rechner, wird
„Fernsteuerung EIN“ abgelehnt, und die Kachel zeigt „anderer Rechner sendet
0x130“. 0x130 von Hand oder per CAN-Schritt zu senden ist gesperrt, solange ein
Batterie-Simulator am Bus hängt oder der Verbund-Partner einen führt.

**LAN-Oszilloskop.** Ein Oszilloskop im Netzwerk gehört einem Rechner. Ist
dieselbe Adresse an beiden eingetragen, weisen Verbund-Chip und Einstellungen
darauf hin – dann an einem Rechner austragen.

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
- Der Sicherheitsstatus in der Leiste oben (Chip mit Schild-Symbol) zeigt **AUS** (keine
  Grenzwerte aktiv), **AKTIV** (überwacht, alles im grünen Bereich) oder
  **AUSGELÖST** (Grenzwert überschritten, Ausgänge abgeschaltet).
- Während eines Testlaufs überwacht der Watchdog zusätzlich die
  Verbindung der beteiligten Geräte – bricht die Verbindung ab oder
  liefert ein Gerät zu lange keine aktuelle Messung, schaltet ebenfalls
  sofort ab.
- Der **„ALLE AUS“-Button** ganz rechts in der Leiste oben schaltet
  jederzeit manuell und sofort alle Ausgänge ab, unabhängig vom
  Watchdog-Status.
- Fernsteuer-Befehle über das Netzwerk (Abschnitt 5) werden abgewiesen,
  solange der Alarm ausgelöst ist oder ein Testablauf läuft, und ein
  Sollwert über einem aktiven Grenzwert wird gar nicht erst angelegt.
  „Alle Ausgänge aus“ bleibt auch dann möglich.
- **Verriegelung Netzteil/Last** (Einstellungen → Sicherheit): Paare aus
  Netzteil und Last, die an derselben Stelle angeschlossen sind. Im
  Testablauf wird „Ausgang EIN“ abgelehnt, solange das andere Gerät des
  Paares eingeschaltet ist oder sein Zustand unbekannt ist; der Lauf endet
  dann mit Fehler und ALLE AUS. Ein Ablauf kann das über „Eigenschaften des
  Ablaufs…“ aufheben (Quelle/Senke). Kachel, Speicherplätze und Netzwerk
  sind nicht verriegelt. Es zählen nur Geräte dieses Rechners.

---

## 7. Leiste oben

In der Leiste am oberen Fensterrand steht zuerst der Sicherheitsstatus als
Chip mit Schild-Symbol (siehe Abschnitt 6), dann der Verbund-Chip und der
Chip „MCP“ (Abschnitt 5), danach je ein Chip pro bekanntem Gerät
mit Symbol und Kurzname: ✓ verbunden (grün), ✕ getrennt (rot),
Ⓗ Handbedienung (orange), z. B. „✓ NT1“. Der volle Name steht im
Tooltip; ein Klick auf die Chips öffnet „Gerätezustand“ mit allen
Geräten. Die Verbindung wird automatisch alle 3 Sekunden neu versucht,
solange ein Gerät nicht erreichbar ist. Rechts folgen der Vollbild-Knopf und
der „ALLE AUS“-Button. Meldungen der App erscheinen kurz als Hinweis am
unteren Fensterrand.

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

---

## 9. Kommandozeile (ohne Fenster)

Für CI und Nachtläufe führt LabControl einen Testablauf ohne Fenster aus:

```
python lab_gui/main.py run ablauf.json --report bericht.html --junit ergebnis.xml --json run-result.json
LabControl.exe run ablauf.json --junit ergebnis.xml
python lab_gui/main.py devices
```

- `--var name=wert` belegt Laufvariablen vor (mehrfach möglich),
  `--simulation` nimmt simulierte Geräte, `--wait-devices S` wartet bis zu
  S Sekunden auf die Geräte des Ablaufs (Standard 15), `--timeout S` bricht
  den Lauf nach S Sekunden ab.
- **Exit-Code:** 0 bestanden, 1 nicht bestanden, 2 Abbruch, Fehler oder
  Sicherheitsabschaltung, 3 Konfiguration oder Geräte fehlen, 130
  unterbrochen (Strg+C).
- Am Ende schaltet LabControl immer ALLE AUS und gibt die Netzteile wie beim
  Schließen des Fensters mit 1 V / 0 A zur Handbedienung frei.
- Läuft LabControl schon (z. B. der Kiosk am Pi), startet die Kommandozeile
  den Lauf über die laufende Instanz (sonst würden zwei Programme dieselben
  Geräte öffnen). Dafür müssen dort die Netzwerk-Freigabe und „Claude (MCP)
  darf steuern“ an sein und der Editor darf keine ungespeicherten Änderungen
  haben; der Ablauf erscheint dann im Editor der Instanz.
- Andere Programme und Claude starten Abläufe über die Netzwerk-Schnittstelle
  (`POST /api/v1/runs` mit dem Namen eines Ablaufs im gemeinsamen Ordner,
  Stand unter `/api/v1/runs/<id>`, Ergebnisse unter `…/result`, `…/junit`,
  `…/report`; MCP: `run_testcase`, `get_run`, `stop_run`). Jeder Lauf, auch
  aus dem Fenster, legt `run_<id>.json/.xml/.html` im Berichtsordner ab.
- Nachtlauf am Pi: `tools/pi/nightly_pi.sh` (siehe `tools/pi/README.md`).
- Noch nicht ohne Fenster: Geräte eines Verbund-Partners und J-Link-Schritte.
- Die `.exe` ist ein Fensterprogramm: sie schreibt keine Textausgabe in die
  Konsole, und `cmd` wartet nur mit `start /wait LabControl.exe run …` auf
  das Ende. Das Ergebnis steht im Exit-Code und in den Dateien.

# Rohde & Schwarz RTB2000 (RTB2002, RTB2004) – Oszilloskop über SCPI

> **Stand: am echten RTB2002 (Firmware 01.210, ohne Bandbreiten-Option) über
> LAN geprüft**, auch über die Netzwerk-Schnittstelle (Weg des MCP-Servers).
> `tools/check_rs_rtb.py --hardware --address <IP> --signal-ch1 rechteck:10000:2.5`
> ist dort grün. Grundlage ist das Benutzerhandbuch (R&S RTB2000 User Manual
> 1333.1611.02 ─ 04, Kapitel 15 „Remote Control Commands“; Seiten unten =
> gedruckte Seitenzahlen). Was am Gerät anders war als im Handbuch, steht unter
> „Am Gerät gefunden“; noch offen ist nur, was als **HARDWARE-PRÜFUNG** markiert ist.

Treiber für die R&S-Reihe RTB2000 (RTB2002 mit 2, RTB2004 mit 4 Kanälen, 10 bit,
bis 2,5 GS/s, 10 Msample je Kanal) über SCPI. Er hängt unter derselben Geräteart
wie das UNI-T MSO1000HD (`scpiscope`): dieselbe Adressliste in den Einstellungen,
dieselbe Kachel (Status, Name, Farbe, letzte Erfassung, „Kurve“, „Trennen“, ohne
Start-Knopf) und Erfassungen für Netzwerk/MCP über `scope_api`
(`scope_api/rs_rtb_adapter.py`). Welcher Treiber zu einer Adresse gehört,
entscheidet die Antwort auf `*IDN?` (`scope_api/scpi.py`). Testablauf-Aktionen
gibt es für SCPI-Oszilloskope noch nicht.

## Verbindung

```
LabControl ──pyvisa──> pyvisa-py (@py) ──VXI-11 über LAN──> RTB2002
```

- **LAN (empfohlen):** reines Python-Backend `pyvisa-py`, **kein NI-VISA nötig**,
  läuft auch in der `.exe`. Eingetragen wird die bloße IP (wird zu
  `TCPIP0::<IP>::inst0::INSTR`, VXI-11, Handbuch S. 243). Erkennung und
  Erfassungen laufen aber über den **SCPI-Socket** `TCPIP0::<IP>::5025::SOCKET`
  (Port 5025, S. 156), VXI-11 nur, wenn der Socket nicht aufgeht -- siehe „Am
  Gerät gefunden“.
- **USB:** nur mit installiertem VISA (NI-VISA oder R&S VISA, Backend `@ivi`),
  am Gerät USB-Modus „USB TMC“ (S. 157), Ressource `USB0::0x0AAD::…::INSTR`.

Einrichten:

1. Am Gerät **Setup → Interface → Ethernet** wählen, unter „Parameter“ eine **feste
   IP** vergeben (oder im Router eine DHCP-Reservierung) -- die Adresse ist
   zugleich die Kennung in LabControl (`scpiscope:<IP>`); Name, Farbe und
   Freigaben hängen daran.
2. LabControl → Einstellungen → Geräte → „Oszilloskope im Netzwerk (SCPI)“: IP
   eintragen (mehrere durch Komma, auch gemischt mit UNI-T-Geräten).
3. LabControl prüft alle 10 s (`*IDN?`, 2 s Timeout, in eigenem Thread), ob das
   Gerät antwortet. Meldet es sich als `Rohde&Schwarz,RTB2…`, erscheint die Kachel.
4. Für MCP: Einstellungen → Netzwerk → Häkchen „Messen“ beim Oszilloskop.

Im Simulationsmodus gibt es zusätzlich `scpiscope:SIM-RTB` (simuliertes RTB2002).

## Dateien

| Datei | Inhalt |
|---|---|
| `driver.py` | `RTB2000` (SCPI-Befehle, Datenblöcke `#<n><Länge>`, Kopf der Daten, Fehlerwarteschlange), `VisaTransport`, `parse_model()` (Kanäle, Bandbreite aus `*OPT?`) |
| `mock.py` | `SimulatedRTB2000`: das *Gerät* auf SCPI-Ebene nachgebildet -- der echte Treiber- und Adaptercode läuft im Simulationsmodus mit |

Adressen → Geräte-ID und der Wächter gegen gleichzeitigen Zugriff (Erkennung vs.
Erfassung) kommen vom UNI-T-Paket (`unit_mso1000hd/driver.py`,
`unit_mso1000hd/guard.py`) -- sie sind herstellerneutral und gelten für alle
`scpiscope`-Geräte.

## Verwendung

```python
from rs_rtb2000.driver import RTB2000

with RTB2000.open("TCPIP0::192.168.1.60::inst0::INSTR") as dev:
    print(dev.idn())                      # IdnInfo(vendor='Rohde&Schwarz', model='RTB2002', ...)
    dev.set_channel(1, display=True, scale_v=0.5)
    dev.set_timebase(1e-3, 0.0)
    dev.set_acquisition(100_000)
    dev.set_edge_trigger(1, 1.0, rising=True)
    dev.single()
    dev.wait_finished(timeout_s=5)
    volts, head = dev.read_waveform_volts(1)   # head.xstart, head.xinc, head.points
```

Über `scope_api` (das nutzt LabControl):

```python
from scope_api.scpi import open_scope
scope = open_scope("TCPIP0::192.168.1.60::inst0::INSTR")   # wählt per *IDN? den Adapter; "SIM-RTB" = Simulator
capture = scope.acquire(request)
```

## Ablauf einer Erfassung (Adapter)

Jede Erfassung stellt alles neu ein (zustandslos, wie beim PicoScope):

1. `STOP`, `*CLS`.
2. Kanäle: angefragte an, übrige aus. Volt/Teilung = kleinste 1-2-5-Stufe, die
   `range_v` mit ±5 Teilungen abdeckt; Tastkopf am Gerät setzen
   (`PROBe<n>:SETup:ATTenuation:MANual`); Offset.
3. Zeitbasis: Referenz in der Mitte, s/Teilung = kleinste 1-2-5-Stufe, bei der
   12 Teilungen (`TIMebase:DIVisions?`) die Dauer abdecken; Aufzeichnungslänge
   10k…10M nach gewünschter Auflösung, aber nie so lang, dass das Gerät auf
   2,5 GS/s geht; `TIMebase:POSition` so, dass der Pre-Trigger-Anteil stimmt.
4. **Zurücklesen**, was das Gerät eingestellt hat (`CHANnel:RANGe?`, `OFFSet?`,
   `TIMebase:SCALe?`) -- gemeldet wird das Tatsächliche, Abweichungen als Warnung.
5. Flankentrigger (`TRIGger:A:MODE NORMal`, Filter und Holdoff aus), `*OPC?`,
   `SINGle`, `ACQuire:STATe?` pollen bis `COMP`. `auto`: nach `timeout_s`
   `*TRG`; `none`: sofort; `single`: nach `timeout_s` `STOP` und Fehler
   `trigger_timeout`.
6. Daten je Kanal als **REAL,32 in Volt** (`FORMat REAL,32`, `FORMat:BORDer LSBF`,
   `CHANnel:DATA:POINts DMAX`), Zeitachse aus `CHANnel<n>:DATA:HEADer?`.

Das Gerät bleibt danach gestoppt und zeigt die Erfassung. Binär statt ASCII: in
Volt ohne Umrechnung, 4 Byte je Punkt. Gemessen über den Socket: ~1,4 MB/s, eine
Erfassung mit 375 000 Punkten dauert insgesamt ~2 s (davon ~0,4 s, bis das Gerät
die Einstellungen übernommen hat); 10 Mio. Punkte (40 MB) also rund 30 s je Kanal.

## Bekannte Eigenheiten / Einschränkungen

Aus dem Handbuch, im Code an der jeweiligen Stelle kommentiert:

- **Offset hat das umgekehrte Vorzeichen wie beim UNI-T:** `CHANnel<n>:OFFSet`
  wird vom Signal *abgezogen* (S. 254), die Bildschirmmitte liegt bei +Offset.
  Der Adapter setzt `offset_v` deshalb unverändert.
- **Kopplung heißt `DCLimit`/`ACLimit`** (1 MΩ), nicht `DC`/`AC` (S. 254). Einen
  50-Ω-Eingang hat das RTB2000 nicht.
- **`CHANnel:DATA?` liest immer die ganze Kurve** in einem Block, es gibt keine
  Teilbereiche wie beim UNI-T. Darum höchstens 10 Mio. Samples je Kanal; die
  20 Msample im Interleaved-Modus liest der Adapter nicht.
- **Abtastrate höchstens 1,25 GS/s** (siehe unten, 2,5 GS/s liefert falsche
  Daten) -- die tatsächliche Rate folgt aus dem Kopf der Daten.
- **Bandbreite:** Grundgerät 70 MHz, per Lizenz RTB-B22x (RTB2002) bzw. RTB-B24x
  (RTB2004) 100/200/300 MHz. Erkannt über `*OPT?`, sobald das Gerät für eine
  Erfassung verbunden ist; vorher meldet LabControl 70 MHz.
- **Nur Flanken-Trigger** an CH1…CH4; Breiten-, Video-, Muster- und
  Bus-Trigger, Logikkanäle (Option B1), Mathe- und Referenzkurven sowie der
  eingebaute Generator (Option B6) werden nicht angesprochen.
- **Jede Erfassung überschreibt die Einstellungen am Gerät** (Kanäle,
  Zeitbasis, Trigger, Aufzeichnungslänge, Erfassungsart „Refresh“ mit
  Dezimierung „Sample“). Wer am Gerät weiterarbeitet, stellt danach neu ein.

### Am Gerät gefunden (RTB2002, Firmware 01.210)

- **10 vertikale Teilungen** (`RANGe` = 10 · `SCALe`), nicht 8, wie die Grenzen
  auf S. 253 nahelegen. Der Adapter rechnet mit ±5 Teilungen.
- **12 horizontale Teilungen** (`TIMebase:DIVisions?` = 12; das Fenster laut Kopf
  der Daten ist 12 · s/div -- `TIMebase:ACQTime` „10 divisions“ stimmt nicht).
- **Vorzeichen bestätigt:** `TIMebase:POSition` = Zeit der Bildschirmmitte relativ
  zum Trigger (Pre-Trigger 20 % kommt genau so heraus), `CHANnel:OFFSet` =
  Bildschirmmitte.
- **Werte werden bei 99,9 % des Vollausschlags begrenzt**, knapp *innerhalb* des
  Bereichs (Offset 2,5 V, ±1 V: Minimum 1,50098 V). Übersteuerung meldet der
  Adapter darum schon ab 99,5 %.
- **Triggerfilter bleiben vom Bedienen an:** `TRIGger:A:EDGE:FILTer:HFReject` war
  eingeschaltet, der Trigger kam dadurch 2,0 µs nach der Flanke. Der Treiber
  schaltet HFReject, NREJect und Holdoff jetzt bei jeder Erfassung aus; danach
  liegt die Flanke auf ±2 ns am Trigger-Index.
- **`*OPC?` vor `SINGle` ist Pflicht:** Die Einstellungen brauchen 0,2…0,5 s.
  Ohne Synchronisation ging `SINGle` gelegentlich verloren (Zustand blieb `STOP`),
  und `CHANnel:DATA?` antwortete danach gar nicht mehr.
- **`ACQuire:STATe?`** antwortet in Kurzform: nach `STOP` mit `STOP`, nach
  `SINGle` erst noch `STOP`, dann `COMP`. Fertig ist eine Erfassung erst bei `COMP`.
- **2,5 GS/s (interleaved, ein Kanal) liefert unbrauchbare Daten:** Ein
  0…2,5-V-Rechteck kam als −1,8…−1,4 V an, in REAL wie in ASCII. Das Gerät wählt
  die Rate aus Aufzeichnungslänge / Fenster (10k bei 6 µs → 1,25 GS/s, 100k bei
  6 µs → 2,5 GS/s); der Adapter begrenzt die Länge so, dass es bei 1,25 GS/s
  bleibt. Möglicherweise behebt das ein Firmware-Update (02.202 liegt vor) --
  nicht geprüft.
- **VXI-11 ist langsam und anfällig:** Mit pyvisa-py nur ~0,25 MB/s; mit mehr als
  20 kB je Lesevorgang lief es in den Timeout, und nach einigen Timeouts nahm das
  Gerät keine VXI-11-Verbindungen mehr an („error creating link: 3“, im Lauf
  des Tests nicht wieder erholt). Der Socket blieb erreichbar und schafft ~1,4 MB/s --
  darum laufen Erkennung (`scope_api/scpi.py`) und Daten über Port 5025.
- Nach einem Datenblock folgt ein LF; `SYSTem:ERRor?` liefert `0,"No error"`.
- `*OPT?` liefert ohne Lizenzen nur `0`. `*IDN?`:
  `Rohde&Schwarz,RTB2002,1333.1005k02/000000,01.210` -- das Gerät meldet als
  Seriennummer `000000`; die Kennung in LabControl ist ohnehin die IP.
- `CHANnel<n>:ZOFFset?` antwortet nicht (Timeout); der Treiber benutzt es nicht.

**HARDWARE-PRÜFUNG** -- noch nicht am Gerät nachgemessen:

- `CHANnel<n>:SCALe` bezieht sich nach `PROBe<n>:…:MANual` auf die Tastkopfspitze
  (bisher nur 1:1 geprüft).
- Erfassungen mit beiden Kanälen und mit Trigger an CH2 (bisher nur CH1 mit
  Signal geprüft).
- Format der `*OPT?`-Antwort mit installierter Bandbreiten-Option (gesucht wird
  nach `B22x`/`B24x`).

## Simulator

`mock.SimulatedRTB2000` versteht die Teilmenge der SCPI-Befehle, die `driver.py`
benutzt, und verhält sich wie das gemessene RTB2002 (12 × 10 Teilungen,
Begrenzung bei 99,9 %, 2,5 GS/s nur bei langer Aufzeichnung mit einem Kanal). Signale (`SimulatedRTB2000.signals`, für Prüfskripte austauschbar):

| Kanal | Signal |
|---|---|
| CH1 | 1-kHz-Rechteck 0 … 3,3 V |
| CH2 | 1-kHz-Sinus ±1 V |

`hang_after_run = True` simuliert ein Gerät, das nach `SINGle` nie fertig wird.

## Prüfen

```
python tools/check_rs_rtb.py                                   # Simulator
python tools/check_rs_rtb.py --hardware --address 192.168.1.60 # + echtes Gerät
python tools/check_rs_rtb.py --hardware --address 192.168.1.60 --signal-ch1 rechteck:1000:2.5
```

Mit `--signal-ch1` (z. B. Tastkopf-Abgleichausgang an CH1 -- am geprüften Gerät
ein 10-kHz-Rechteck 0…2,5 V, also `rechteck:10000:2.5`) prüft das Skript
Frequenz, Pegel, Lage des Triggers (Vorzeichen `TIMebase:POSition`, Flanke am
Trigger-Index) und den Offset (Vorzeichen `CHANnel:OFFSet`). Dazu gibt es
Optionen, Teilungen, `ACQuire:STATe?`-Antwort und RANGe/SCALe aus.

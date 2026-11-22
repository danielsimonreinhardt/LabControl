# UNI-T MSO1000HD (MSO1154HD-S) – Oszilloskop über SCPI

> **Stand 1.0.2: am MSO1154HD-S (Firmware V1.01.0081) über LAN geprüft.** Das
> Gerät weicht an mehreren Stellen vom Programmierhandbuch ab, siehe
> „Am Gerät geprüft“ unten. `tools/check_unit_mso.py --hardware --address <IP>`
> prüft Erreichbarkeit und eine freie Erfassung.

Treiber für die UNI-T-Reihe MSO1000HD (MSO1102HD … MSO1254HD, 2 oder 4 Kanäle,
12 bit, bis 2,5 GS/s) über SCPI. Eingebunden wie das PicoScope 2204A:
Dashboard- und Control-Kachel (Status, Name, Farbe, letzte Erfassung, „Kurve“,
„Trennen“ -- ohne Start-Knopf, das Gerät hat seine Bedienung am eigenen
Bildschirm) und als Oszilloskop für Netzwerk/MCP über `scope_api`
(`scope_api/unit_mso_adapter.py`). Testablauf-Aktionen (wie `PICO_VPP`) gibt es
für dieses Gerät noch nicht.

## Verbindung

```
LabControl ──pyvisa──> pyvisa-py (@py) ──VXI-11 über LAN──> MSO1154HD-S
```

- **LAN (empfohlen):** reines Python-Backend `pyvisa-py`, **kein NI-VISA nötig**,
  läuft auch in der `.exe`. Ressource `TCPIP0::<IP>::inst0::INSTR` (Handbuch S. 324).
- **USB:** nur mit installiertem NI-VISA (Backend `@ivi`), Ressource
  `USB0::…::INSTR`. pyvisa-py kann USBTMC unter Windows nur mit libusb-Treiber.

Einrichten:

1. Am Gerät eine **feste IP** vergeben (oder im Router eine DHCP-Reservierung) --
   die Adresse ist zugleich die Kennung in LabControl (`scpiscope:<IP>`); Name,
   Farbe und Freigaben hängen daran.
2. LabControl → Einstellungen → Geräte → „Oszilloskope im Netzwerk (SCPI)“: IP
   eintragen (mehrere durch Komma).
3. LabControl prüft alle 10 s (`*IDN?`, 2 s Timeout, in eigenem Thread), ob das
   Gerät antwortet und sich als UNI-T meldet. Dann erscheint die Kachel.
4. Für MCP: Einstellungen → Netzwerk → Häkchen „Messen“ beim Oszilloskop.

Im Simulationsmodus gibt es zusätzlich `scpiscope:SIM` (Simulator, siehe unten).

## Dateien

| Datei | Inhalt |
|---|---|
| `driver.py` | `MSO1000HD` (SCPI-Befehle, Datenblöcke `#9…`, Fehlerwarteschlange), `VisaTransport`, `probe()`, Adressen → Geräte-ID |
| `mock.py` | `SimulatedInstrument`: das *Gerät* auf SCPI-Ebene nachgebildet -- der echte Treiber- und Adaptercode läuft im Simulationsmodus mit |
| `guard.py` | je Gerät ein Wächter: Erkennung (DeviceWorker) und Erfassungen (ScopeService) fallen sich nicht ins Wort |

## Verwendung

```python
from unit_mso1000hd.driver import MSO1000HD

with MSO1000HD.open("TCPIP0::192.168.1.50::inst0::INSTR") as dev:
    print(dev.idn())                      # IdnInfo(vendor='UNI-T Technologies', model='MSO1154HD-S', ...)
    dev.set_channel(1, display=True, scale_v=1.0)
    dev.set_timebase(1e-3, 0.0)
    dev.set_edge_trigger(1, 1.5, rising=True, sweep="SINGle")
    dev.run()
    dev.wait_status({"STOP"}, timeout_s=5)
    volts, pre = dev.read_waveform_volts(1)
```

Über `scope_api` (das nutzt LabControl):

```python
from scope_api.unit_mso_adapter import open_unit_mso
scope = open_unit_mso("TCPIP0::192.168.1.50::inst0::INSTR")   # oder "SIM"
capture = scope.acquire(request)
```

## Ablauf einer Erfassung (Adapter)

Jede Erfassung stellt alles neu ein (zustandslos, wie beim PicoScope):

1. `STOP`, Fehlerwarteschlange leeren.
2. Kanäle: angefragte an, übrige aus. Volt/Teilung = kleinste 1-2-5-Stufe, die
   `range_v` mit ±4 Teilungen abdeckt; Tastkopf am Gerät setzen; Offset.
3. Zeitbasis: s/Teilung = kleinste 1-2-5-Stufe ≥ Dauer/8 (der Speicher deckt
   8 Teilungen ab, siehe unten); Speichertiefe
   25K/250K/500K/5M nach gewünschter Auflösung; Fenster per `TIMebase:OFFSet`
   so verschoben, dass der Pre-Trigger-Anteil stimmt.
4. **Zurücklesen**, was das Gerät eingestellt hat (Bereich, Zeitbasis) -- gemeldet
   wird das Tatsächliche, Abweichungen als Warnung.
5. Flankentrigger, `TRIGger:SWEep SINGle`, `RUN`, Status pollen bis `STOP`.
   `auto`: nach `timeout_s` `TRIGger:FORCe`; `none`: sofort; `single`: nach
   `timeout_s` Fehler `trigger_timeout`.
6. 0,6 s warten (direkt nach `STOP` liefert das Gerät noch unfertige Daten).
7. Daten je Kanal als **ASCII in Volt** (`WAV:MODE RAW`, `WAV:FORM ASCII`,
   `WAV:POIN 1000000`), in Stücken zu 250 000 Punkten (`WAV:STAR`/`WAV:STOP`).
   t = 0 aus `XORigin` der Präambel.

Das Gerät bleibt danach gestoppt und zeigt die Erfassung. ASCII statt WORD,
weil die WORD-Umrechnung (YINC/YREF) am Gerät nicht bei jeder Skala stimmte;
kostet Zeit (≈ 16 Byte je Punkt): 250 000 Punkte ≈ 1,6 s je Kanal, eine
Erfassung mit einem Kanal dauert 3–8 s, mit vier Kanälen ≈ 18 s.

## Am Gerät geprüft (1.0.2)

Gemessen am MSO1154HD-S mit dem Kalibrierausgang (1-kHz-Rechteck 0…3 V) an CH1:

| Punkt | Verhalten am Gerät | Folge im Treiber |
|---|---|---|
| VXI-11-Lesen | meldet `maxRecvSize` 1024; ist eine Antwort größer als die Leseanfrage, kommt ein I/O-Fehler statt eines Teilstücks | Leseanfrage 64 MB (`VXI11_READ_SIZE`), jede Antwort am Stück |
| Unbekannter Befehl | I/O-Fehler auf VXI-11-Ebene, danach `-113` in `SYST:ERR?` (z. B. `ACQ:SRAT?`, `WAV:MODE MAX`, `WAV:FORM BYTE`) | nur geprüfte Befehle |
| `WAV:PRE?` | Textzeile, kein `#9…`-Block | beides wird angenommen |
| `WAV:DATA?` | `#9<Länge><Werte>` und dahinter `,\r\n` | Rest wird verworfen |
| `WAV:MODE RAW` | liefert nur so viele Punkte wie mit `WAV:POIN` eingestellt (sonst 1); höchstens 1 000 000, auch bei Tiefe 5M | vor dem Lesen `WAV:POIN 1000000` |
| `WAV:STAR`/`WAV:STOP` | STARt ab 0, STOP exklusiv; STARt rückt nach jedem `DATA?` weiter | beide vor jedem Stück neu |
| Daten direkt nach `STOP` | zuerst Füllwerte (Rohwert 0x0404), nach 0,5 s vollständig | 0,6 s Wartezeit |
| Speicherfenster | 8 Teilungen um die Bildschirmmitte (1 ms/div: −4…+4 ms), bei schnellen Zeitbasen länger | Zeitbasis, Punkte und Vorlauf mit 8 Teilungen; t = 0 aus `XORigin` (geprüft bei 0/10/20/25/50 % Vorlauf) |
| Statusfolge nach `RUN` | (`RESET` nach geänderten Einstellungen, bis 0,7 s) → `ARMED` → `READY` → `TRIGED` → `STOP` | „scharf“ erst ab `READY` |
| `CHAN:OFFS` | Bildschirmmitte = −Offset | wie angenommen |
| `TRIG:EDGE:LEV` mit Tastkopf 10X | geschriebener Wert wird durch den Tastkopffaktor geteilt zurückgemeldet | nicht weiter geprüft (kein 10:1-Tastkopf am Messpunkt) |
| `WAV:MODE NORMal` | Bildschirmdaten mit falschem XINCrement (Flanken im halben Abstand) | nicht benutzt |
| `WAV:FORM WORD` | 8× schneller, Umrechnung passte bei 10 V/div, nicht bei 1 V/div | nicht benutzt |

Weiter angenommen (nicht geprüft): Kopplung AC wirkt (Mittelwert ≈ 0 gemessen), Bandbreitenbegrenzung,
2-Kanal-Modelle (MSO1xx2).

Erkennung: Modell `MSO1<BB><K>` → Bandbreite BB·10 MHz, K Kanäle.

## Simulator

`mock.SimulatedInstrument` versteht die Teilmenge der SCPI-Befehle, die
`driver.py` benutzt, und antwortet in der Form des Handbuchs. Signale
(`SimulatedInstrument.signals`, für Prüfskripte austauschbar):

| Kanal | Signal |
|---|---|
| CH1 | 1-kHz-Rechteck 0 … 3,3 V |
| CH2 | 1-kHz-Sinus ±1 V |
| CH3 | 12 V mit 50 mVss Welligkeit bei 100 kHz |
| CH4 | 0 V |

`hang_after_run = True` simuliert ein Gerät, das nach `RUN` nie fertig wird.

## Prüfen

```
python tools/check_unit_mso.py                                   # Simulator
python tools/check_unit_mso.py --hardware --address 192.168.1.50 # + echtes Gerät
python tools/check_unit_mso.py --hardware --address 192.168.1.50 --signal-ch1 rechteck:1000:3
```

Mit `--signal-ch1` (z. B. Kalibrierausgang des Geräts oder Funktionsgenerator an
CH1) prüft das Skript Frequenz, Pegel, Lage des Triggers (Vorzeichen
`TIMebase:OFFSet`) und den Offset (Vorzeichen `CHANnel:OFFSet`).

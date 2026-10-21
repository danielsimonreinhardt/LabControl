# UNI-T MSO1000HD (MSO1154HD-S) – Oszilloskop über SCPI

> **Stand 0.21.0: nur gegen den Simulator geprüft.** Das Gerät war beim
> Schreiben noch nicht da. Alles, was unten als **HARDWARE-PRÜFUNG** markiert
> ist, beruht auf dem Programmierhandbuch (UNI-T MSO1000HD Programming Manual
> V1.0) und ist am echten Gerät zu bestätigen -- `tools/check_unit_mso.py
> --hardware --address <IP>` prüft die wichtigsten Punkte.

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
3. Zeitbasis: s/Teilung = kleinste 1-2-5-Stufe ≥ Dauer/10; Speichertiefe
   25K/250K/500K/5M nach gewünschter Auflösung; Fenster per `TIMebase:OFFSet`
   so verschoben, dass der Pre-Trigger-Anteil stimmt.
4. **Zurücklesen**, was das Gerät eingestellt hat (Bereich, Zeitbasis) -- gemeldet
   wird das Tatsächliche, Abweichungen als Warnung.
5. Flankentrigger, `TRIGger:SWEep SINGle`, `RUN`, Status pollen bis `STOP`.
   `auto`: nach `timeout_s` `TRIGger:FORCe`; `none`: sofort; `single`: nach
   `timeout_s` Fehler `trigger_timeout`.
6. Daten je Kanal als **ASCII in Volt** (`WAV:MODE RAW`, `WAV:FORM ASCII`), in
   Stücken zu 25 000 Punkten (`WAV:STAR`/`WAV:STOP`).

Das Gerät bleibt danach gestoppt und zeigt die Erfassung. ASCII statt WORD,
weil die Skalierung der Binärdaten (YINC/YOR/YREF) im Handbuch nicht eindeutig
ist; kostet Zeit (≈ 15 Byte je Punkt), bei 250 000 Punkten über 100-Mbit-LAN
grob 1–2 s je Kanal (**HARDWARE-PRÜFUNG**).

## Eigenheiten und Annahmen

**HARDWARE-PRÜFUNG** -- am echten Gerät zu bestätigen:

- Vorzeichen von `CHANnel<n>:OFFSet`: angenommen Bildschirmmitte = −Offset
  (positiver Offset schiebt die Kurve nach oben). Der Adapter setzt
  `OFFSet = −offset_v`.
- `TIMebase:OFFSet`: angenommen ist das Fenster um den Offset zentriert und
  t = 0 der Trigger. Pre-Trigger 20 % → Offset = Dauer · 0,3.
- `CHANnel<n>:SCALe` bezieht sich auf die Tastkopfspitze (nach `PROBe`).
- Im RAW-Modus enthält der Speicher genau das Bildschirmfenster (10 Teilungen).
- Statusfolge nach `RUN` bei `SWEep SINGle`: `WAIT`/`ARMED`/`READY` → `STOP`.
  Direkt nach `RUN` kann noch das `STOP` der vorigen Erfassung kommen
  (Wartezeit 0,3 s im Adapter).
- `SYSTem:ERRor?` liefert `<code>,"<text>"`, `0,"No error"` wenn leer.
- Vertikal 8 Teilungen, 1 mV/div bis 10 V/div am Eingang (1:1).
- `*IDN?`: `UNI-T Technologies,MSO1154HD-S,<Seriennummer>,<Firmware>`.

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

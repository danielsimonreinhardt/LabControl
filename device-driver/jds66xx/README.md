# JDS66xx / JDS2915 – Funktionsgenerator per USB

Treiber für die zweikanaligen DDS-Funktionsgeneratoren von Joy-IT/JUNTEK
(**JDS2915**, 15 MHz; dasselbe Protokoll sprechen JDS6600 u. a.). Grundlage ist
das Joy-IT-Dokument „JT-JDS6600-Communication-protocol“ (Stand 2024-04-23).

Verbindung über den USB-Seriell-Wandler (CH340) mit 115200 Baud, 8N1. Telegramme
`:w23=25786,0.` (schreiben) bzw. `:r23=.` (lesen), jeweils mit CR/LF; jeder
Schreibbefehl wird mit `:ok` quittiert.

## Verwendung

```python
from jds66xx.driver import JDS66xx, WAVE_SQUARE

with JDS66xx.open_first() as gen:
    print(gen.identify())               # Modell/Seriennummer
    gen.set_waveform(1, WAVE_SQUARE)
    gen.set_frequency(1, 1000.0)        # Hz
    gen.set_amplitude(1, 3.3)           # V, Spitze-Spitze (Leerlauf)
    gen.set_offset(1, 1.65)             # V
    gen.set_duty(1, 25.0)               # %
    gen.set_output(1, True)
    print(gen.get_state())
    gen.set_outputs(False, False)
```

Alle Werte sind in SI-Einheiten; die Skalierung des Geräts (0,01 Hz, mV, 0,01 V
mit Nullpunkt bei 1000, 0,1 %, 0,1 °) steckt vollständig in `driver.py`.

## Bekannte Eigenheiten / Einschränkungen

- **Die CH340-VID/PID (`1A86:7523`) ist nicht eindeutig.** Viele Boards tragen
  denselben Wandler. Ein Port gilt deshalb erst nach einem Handshake
  (`probe()`: Register 21 lesen, Antwort `:r21=<Zahl>.`) als Funktionsgenerator.
  Ein Board mit Konsole oder Echo besteht ihn nicht. Der Handshake schickt aber
  eine Zeile an das fremde Gerät; `device_worker` wiederholt ihn für einen
  durchgefallenen Port nur alle 30 s (`FG_REPROBE_INTERVAL_S`).
- Ein ausgeschalteter Generator hat seinen COM-Port trotzdem (USB-Wandler) und
  fällt beim ersten Handshake durch; nach dem Einschalten dauert die Erkennung
  daher bis zu 30 s.
- Das Register 20 (Ausgänge) kennt nur das **Paar** `:w20=<K1>,<K2>.`. Ein
  einzelner Kanal wird per Lesen-Ändern-Schreiben geschaltet.
- **Frequenz** wird immer mit Multiplikator 0 und 0,01 Hz Auflösung geschrieben
  (feinere Werte werden gerundet). Die Multiplikatoren 3/4 (mHz/µHz) werden beim
  Lesen ausgewertet, aber nicht geschrieben (laut Protokoll nur bis 80 kHz bzw.
  80 Hz, an Hardware nicht verifiziert).
- **Frequenz** bis 15 MHz wurde am JDS2915 für Sinus, Rechteck, Puls und Dreieck
  angenommen und exakt zurückgelesen (keine Klemmung je Wellenform beobachtet).
- **Amplitude ist frequenzabhängig begrenzt:** bis 5 MHz wurden 20 V angenommen,
  bei 15 MHz klemmt das Gerät **still auf 10 V** (Sinus und Rechteck). Der Treiber
  prüft nur die absolute Grenze (20 V); wer die Wirkung braucht, liest die
  Amplitude nach dem Setzen zurück (der Worker meldet den zurückgelesenen Wert).
- **Amplitude** ist Spitze-Spitze im Leerlauf (0…20 V), **Offset** ±9,99 V,
  **Phase** gibt es nur einmal (Kanal 2 relativ zu Kanal 1).
- Das Protokoll kennt keine Betriebsart „Ausgang aus, Signal an“ – „AUS“ ist die
  Abschaltung des Ausgangs; alle Signalparameter bleiben gespeichert.
- **DC: Ausgang ≈ Offset + Amplitude/2** (gemessen am JDS2915 am 17.11.2026, CH1
  ohne Last, PicoScope 10:1):

  | Amplitude | Offset | gemessen |
  |---|---|---|
  | 0 V | 0 V | 0,01 V |
  | 1 V | 0 V | 0,47 V |
  | 2,5 V | 0 V | 1,21 V |
  | 5 V | 0 V | 2,41 V |
  | 2,5 V | 1 V | 2,29 V |
  | 2,5 V | 2,5 V | 3,73 V |
  | **0 V** | 2,5 V | **0,22 V** |
  | 0,01 V | 2,5 V | 2,61 V |
  | 0,1 V | 2,5 V | 2,57 V |

  **Sonderfall Amplitude genau 0:** der Offset kommt nur zu etwa 1/10 an. Deshalb
  setzt LabControl in DC die Amplitude auf **0,01 V** (`DC_AMPLITUDE_V`, kleinster
  Wert ≠ 0) und gibt die Spannung als **Offset** vor (`set_waveform_checked`,
  `set_offset_checked`); es bleibt ein Rest von Amplitude/2 = **5 mV**
  (`DC_RESIDUAL_V`) über dem Offset. Die gemessenen 2,61 V bei 2,5 V Offset liegen
  etwa 0,1 V darüber – Offset-Fehler des Geräts ohne Abgleich, nicht korrigiert.
  `set_amplitude_checked` lehnt eine Amplitude in DC ab (`FunctionGeneratorValueError`).
- **Tastgrad wirkt nur bei Puls:** Rechteck 1 kHz mit 10 % und 99 % ergab jeweils
  50 %, Puls 99 % → 98,9 %, 10 % → 9,97 %. Das Gerät nimmt den Wert in jeder Form
  an (`:ok`), er wirkt nur bei Puls. `set_duty_checked` lehnt ihn bei jeder anderen
  Form ab, statt die Form still umzuschalten. `get_duty`/`set_duty` selbst bleiben
  roh (Register).
- Die `*_checked`-Funktionen sind Modulfunktionen (wie `read_field`) und gelten für
  Treiber und Mock gleich; `device_worker` nutzt sie für Kachel, Testablauf und
  Fernsteuerung. Der Mock bildet das gemessene Verhalten nach
  (`output_dc_voltage`, `effective_duty`).
- Werte außerhalb des Bereichs wirft der Treiber als `FunctionGeneratorValueError`
  **vor** dem Senden (Verbindung bleibt), Timeouts als `FunctionGeneratorError`.

## Verifikation

`python tools/check_jds66xx.py` prüft Kodierung, Handshake gegen fremde Boards
und Mock ohne Hardware. Am echten Gerät (JDS2915, nichts an den Ausgängen
angeschlossen, 2026-09-25) verifiziert: Handshake, Lesen des Vollzustands (~47 ms),
Schreiben und Zurücklesen von Frequenz (0,01 Hz … 15 MHz), Amplitude, Offset
(±), Tastverhältnis, Phase, Wellenform und Ausgängen je Kanal, Ablehnung
ungültiger Werte ohne Verbindungsabbruch, Erkennung und Steuerung über den
`DeviceWorker`, ALLE AUS. **Nicht** am Ausgang gemessen (Oszilloskop): die
tatsächlich anliegende Signalform und Amplitude.

Am 17.11.2026 am Ausgang gemessen (CH1 ohne Last, PicoScope 10:1): DC-Spannung
abhängig von Amplitude und Offset und Tastgrad bei Rechteck/Puls (Tabellen oben).
Die Umsetzung (DC mit 0,01 V Amplitude, Tastgrad nur bei Puls) ist nur gegen den
Mock geprüft: `python tools/check_fg_dc_duty.py`.

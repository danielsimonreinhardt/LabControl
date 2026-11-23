# mhs5200a – Funktionsgenerator MHS-5200A

Zweikanalige DDS-Generatoren MHS-5206A/5212A/5220A/5225A. Stand: **am MHS-5225A geprüft** (23.11.2026), Ausgang mit
dem R&S RTB2002 (1 MΩ, ohne Abschluss) nachgemessen.

| Datei | Inhalt |
|---|---|
| `driver.py` | `MHS5200A` (serieller Port) und die Modulfunktionen wie `jds66xx.driver` (`read_field`, `readback_fields`, `set_*_checked`, `state_dict`, `outputs_off`) |
| `mock.py` | `FakeMhsPort` (Zeilen-Nachbildung des Geräts) und `MockMHS5200A` (echter Treiber am nachgebildeten Port) |

Prüfung: `python tools/check_mhs5200a.py` (Simulator), `... --hardware --port /dev/cu.usbserial-…` bzw.
`--port COM7` (am Gerät; stellt Werte ein, Ausgänge bleiben wie vorgefunden, danach wird der vorgefundene Zustand
wiederhergestellt).

## Protokoll

Grundlage: „Serial Protocol for MHS-5200A“ (Dokumentversion 3, 12.07.2018, von Nutzern nachgebaut). CH340,
57600 Baud, 8N1, RTS/CTS. Zeile `:<s|r><Kanal><Feld><Wert>` mit CR/LF; Schreiben quittiert das Gerät mit `ok`,
Lesen mit dem Echo und dem Wert (`:r1f` → `:r1f0000100000`).

| Feld | Bedeutung | Einheit am Gerät | in LabControl |
|---|---|---|---|
| `c` (Kanal 0) | Kennung | `5225A5040000` | Modell, Frequenzgrenze |
| `w` | Form | 0 Sinus, 1 Rechteck, 2 Dreieck, 3/4 Rampe steigend/fallend, 100+n Arbiträr | Codes wie JDS2915 (0, 1, 3), Rampen 20/21, Arbiträr 101..116 |
| `f` | Frequenz | 0,01 Hz | Hz |
| `a`, `y` | Amplitude, Dämpfung | `y=1`: 0,01 V; `y=0` (−20 dB): 0,001 V | Vss, Dämpfung automatisch bis 2 V |
| `o` | Offset | % der halben Amplitude, 120 = 0 % | V |
| `d` | Tastverhältnis | 0,1 % | % |
| `b` | Ausgang | 0/1 | EIN/AUS |
| `p` | Phase | Grad | nicht benutzt (siehe unten) |

## Am Gerät gemessen (MHS-5225A)

- **Kennung:** `:r0c` → `5225A5040000`; `:r1c` und `:r2c` (laut Protokoll Firmware) kommen leer zurück.
- **Antwortzeit:** 10–30 ms je Befehl. Einmal brauchte ein Schreibbefehl nach einer langen Folge 0,65 s (nicht
  gezielt nachstellbar) – Timeout deshalb 1,5 s.
- **Begrenzung still:** Werte außerhalb quittiert das Gerät mit `ok` und begrenzt: Amplitude bei 2000, Frequenz bei
  25 MHz, Tastgrad bei 999, Offset bei 240, Phase modulo 360. Unsinn wie `:s1q5` quittiert es ebenfalls mit `ok`;
  ein unbekanntes Lesefeld liefert nur das Echo, eine Zeile ohne `:` gar nichts. Der Handshake prüft deshalb den
  Inhalt der Kennung.
- **Arbiträr:** Speicherplatz n wird als `100+n` geschrieben und als `10+n` gemeldet (das Protokoll nennt 32–47).
- **Amplitude:** ohne Dämpfung Wert/100 V, mit Dämpfung Wert/1000 V (1,0/2,0/5,0 Vss → 1,06/2,05/5,16 Vss
  gemessen, Spitze-Spitze inklusive Rauschen). Amplitude 0 gibt ein Störsignal (~0,6 Vss) aus – der Treiber
  verlangt mindestens 5 mV.
- **Offset:** Prozent der halben Amplitude (bei 4 Vss: +100 % → +1,93 V, −100 % → −1,84 V, ±120 % → +2,31/−2,24 V);
  etwa 5 % zu klein, Nullpunkt etwa +1 % der Amplitude. Mit Dämpfung gleich (Prozent der gedämpften Amplitude).
- **Tastverhältnis:** wirkt nur bei Rechteck (25,0 % → 25,1 %, 90,0 % → 90,0 %); bei Dreieck und Rampen nicht.
- **Phase:** gilt für beide Kanäle gemeinsam (`:s2p` ändert auch `:r1p`). Gemessener Versatz von Kanal 2 gegen
  Kanal 1 bei 1 kHz: p = 45/90/135/180/225/270/315° → 132/87/42/0/132/87/42°, also 180° − (p mod 180°); auch nach
  erneutem Schreiben der Frequenz. Ein Versatz zwischen 180° und 360° ist nicht einstellbar – `HAS_PHASE = False`.
- **Über den Treiber** (Sollwert → gemessen): 3 Vss/+0,5 V → 3,12 Vss/+0,50 V; 3 Vss/−1 V → 3,15 Vss/−0,95 V;
  1,2 Vss/+0,3 V (gedämpft) → 1,26 Vss/+0,31 V; 8 Vss/+2 V → 8,09 Vss/+1,92 V.

## Eigenheiten im Treiber

- **Amplitudenwechsel ohne Überhöhung:** Dämpfung EIN vor dem neuen Wert, AUS danach; ein kleinerer
  Offset-Prozentwert vor, ein größerer nach der Amplitude. So liegt zwischendurch nie mehr an als alter bzw. neuer
  Wert (geprüft im Simulator).
- **Offset bleibt in Volt:** `set_amplitude` schreibt den Prozentwert nach; passt der Offset nicht in ±120 % der
  neuen halben Amplitude, wird die Amplitude abgelehnt (`FunctionGeneratorValueError`). Auflösung 1 % der halben
  Amplitude (bei 10 Vss 50 mV).
- **Erkennung:** derselbe CH340 wie beim JDS2915 – der DeviceWorker fragt erst den JDS-Handshake (115200 Baud),
  dann diesen (57600). Die Geräte-ID `fg:mhs5200a-<Port>` (statt `fg:<Port>`) lässt den Ablauf-Editor das
  Treibermodul ohne Gerät erkennen.
- Nicht umgesetzt: Wobbeln, Zähler/Frequenzmesser, Speicherplätze, Laden von Arbiträrkurven.

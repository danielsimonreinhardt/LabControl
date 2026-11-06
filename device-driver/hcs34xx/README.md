# Manson/reichelt HCS-3400/3402/3404 USB – Labornetzteil

Treiber-Bibliothek zur Fernsteuerung des Labornetzteils per USB (virtueller
COM-Port über Silicon-Labs-CP210x-Wandler). `HCS34xx.open_first()` findet
den Port automatisch, unter Windows als `COMx`, unter Linux als
`/dev/ttyUSB0` o.ä.

Getestet gegen echte Hardware: HCS-3404 (Preset P3 = 55,0 V bestätigt das
Modell), Max 60,5 V / 9,0 A, Baudrate 9600 8N1.

## Verwendung

```python
from driver import HCS34xx

with HCS34xx.open_first() as psu:
    print(psu.get_max())          # (60.5, 9.0)
    psu.set_voltage(12.0)
    psu.set_current(1.5)
    print(psu.get_display())      # Display(voltage=..., current=..., constant_current=...)
```

## Bekannte Eigenheiten / Einschränkungen

- **Ausgang per Software schaltbar (undokumentiert).** Der Befehlssatz der
  Anleitung (Kap. 12.2) enthält keinen Befehl dafür, das Gerät kennt aber
  `SOUT0`/`SOUT1` (Ausgang EIN/AUS) und `GOUT` (Zustand, `0` = EIN, `1` = AUS).
  Quelle: [sigrok-Wiki](https://sigrok.org/wiki/Manson_HCS-3xxx_series),
  am 04.11.2026 am HCS-3404 (60 V) bestätigt: `SOUT1` schaltet ab (die Spannung
  sinkt ohne Last langsam, ~10 s bis unter 0,6 V), `SOUT0` schaltet in unter 2 s
  wieder ein, Sollwerte bleiben unverändert. `get_output()`/`set_output()`
  benutzen diese Befehle; `set_output()` liest den Zustand mit `GOUT` zurück und
  wiederholt bis zu 3-mal (wie die Sollwerte, siehe `_set_verified`).
  `set_voltage()`/`set_current()` setzen nur Sollwerte und schalten nichts.
  Frühere Fassungen emulierten „AUS“ über Strom 0 A.
- **Sperre der Regler („Rear control“).** Jeder Befehl sperrt die Regler für
  Spannung und Strom am Gerät für etwa 5 s (es hat keine weiteren Bedienelemente);
  bei Dauerabfrage ist es nie von Hand bedienbar. `ENDS` hebt die Sperre sofort
  auf (`release_keyboard()`). Nach jedem Befehl gesendet (so in 0.25.0/0.25.1)
  hält es die Regler zwar bedienbar, die LED „Rear control“ blinkt aber im Takt
  der Abfrage, und ohne Ausgangstaste am Gerät ist gleichzeitiges Bedienen von
  Hand und per Software ohnehin nicht sinnvoll. Befehl und `ENDS` in einem
  Schreibvorgang zu senden geht nicht (am Gerät geprüft: der zweite Befehl wird
  nicht verarbeitet). LabControl sendet deshalb `ENDS` nur einmal beim Wechsel
  in die Handbedienung und fragt das Gerät danach nicht mehr ab.
- **VID/PID nicht eindeutig.** `10C4:EA60` ist die generische Silicon-Labs-
  Werksvorgabe für CP210x und wird von vielen Geräten verwendet. Sind
  mehrere CP210x-Geräte angeschlossen, wirft `open_first()` einen Fehler –
  dann Port explizit an `HCS34xx(port=...)` übergeben.
- **Zwei Zahlenformate:** Die meisten Befehle (VOLT/CURR/GMAX/GETS/GOVP/
  GOCP/SOVP/SOCP/PROM/GETM) nutzen 3-stellige, zehnfach skalierte Felder
  (eine Nachkommastelle). `GETD` (Live-Anzeige) nutzt 4-stellige, hundertfach
  skalierte Felder (zwei Nachkommastellen) plus eine Statusziffer (0=CV,
  1=CC) – laut Anleitung modellabhängig unterschiedlich, hier gegen die
  reale Hardware verifiziert.
- Baudrate ist bei diesem CP210x-UART (anders als beim USB-CDC-Gerät der
  elektronischen Last) relevant – 9600 8N1 wurde gegen die reale Hardware
  bestätigt.
- **Minimale Ausgangsspannung 1,0 V.** Laut technischen Daten der Anleitung
  ist die Ausgangsspannung nur ab 1 V einstellbar. Gegen die reale Hardware
  verifiziert: `VOLT000`/`SOVP000` (0,0 V) werden vom Gerät kommentarlos
  ignoriert (keine Antwort, kein `OK`) – ohne Prüfung würde der Treiber in
  den Timeout laufen und das fälschlich als Verbindungsabbruch werten.
  `set_voltage()`/`set_ovp()` werfen daher unterhalb von 1,0 V sofort ein
  `PowerSupplyValueError` (Subklasse von `PowerSupplyError`), das explizit
  **kein** Verbindungsfehler ist – Aufrufer sollten es getrennt behandeln
  und die Verbindung dabei nicht schließen. `set_current()`/`set_ocp()`
  akzeptieren 0 problemlos.

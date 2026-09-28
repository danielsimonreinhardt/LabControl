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

- **Kein Software-Ausgang-Ein/Aus.** Der dokumentierte Befehlssatz (Kap. 12.2
  der Anleitung) enthält keinen Befehl zum Schalten des Ausgangs. Das geht
  laut Anleitung nur über den analogen Fernsteueranschluss (8-pol. Stecker,
  Pin 5 gegen Masse = aus) oder manuell am Gerät. `set_voltage()`/
  `set_current()` wirken direkt auf den laufenden Ausgang.
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

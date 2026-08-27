# Korad KEL102 – Steuerung per USB

Treiber-Bibliothek zur Fernsteuerung der elektronischen Last **Korad KEL102**
per USB (virtueller CDC-COM-Port). Windows und Linux werden unterstützt –
`KoradKEL102.open_first()` findet das Gerät automatisch über USB VID/PID
(`0416:5011`), unabhängig davon, ob es sich als `COM3` (Windows) oder
`/dev/ttyACM0` (Linux) meldet.

Getestet gegen echte Hardware: Firmware V2.20, SN 00010925.

## Verwendung

```python
from driver import KoradKEL102

with KoradKEL102.open_first() as load:
    print(load.identify())          # z.B. "KORAD-KEL102 V2.20 SN:00010925"
    load.set_function("CURR")       # CC-Modus
    load.set_current(1.0)           # Sollwert 1 A
    load.set_input(True)            # Last einschalten
    print(load.measure())           # Measurement(voltage=..., current=..., power=...)
    load.set_input(False)
```

## Bekannte Eigenheiten

- `get_function()` liefert nach dem Setzen mit `VOLT|CURR|RES|POW` die
  Anzeigenamen `CV|CC|CR|CW` zurück (weicht von der Beispiel-Doku ab, wurde
  gegen die reale Last verifiziert).
- Die per pyserial konfigurierte Baudrate hat auf USB keinen Effekt (reines
  USB-CDC), funktioniert aber bei jeder getesteten Rate identisch.

## Nächste Schritte

- GUI (PySide6) für Grundsteuerung: Modus, Sollwerte, Ein/Aus, Live-Anzeige.
- Optional später: Batterie-Testmodus, LIST-Sequenzen, Logging/Plot.

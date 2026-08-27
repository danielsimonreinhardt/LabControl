# Labor-Steuerung GUI

PySide6-Oberfläche für die elektronische Last ([korad_kel102](../korad_kel102/))
und das Labornetzteil ([hcs34xx](../hcs34xx/)).

## Start

```
python main.py
```

## Aufbau

- **Dashboard** (immer sichtbar): aktuelle Spannung/Strom/Leistung der Last,
  Spannung/Strom/Modus des Netzteils. Wird alle 500 ms live aktualisiert.
- **Reiter „Control“**: Eingabemasken für die wichtigsten Funktionen
  (Last: Modus + Sollwert + Ausgang Ein/Aus; Netzteil: Spannung/Strom/OVP/OCP
  + Presets P1–P3).
- **Reiter „Testcase“**: Platzhalter, Testablauf-Definition folgt später.
- **Statusleiste**: USB-Verbindungsstatus beider Geräte (rot = getrennt,
  grün = verbunden). Verbindung wird automatisch alle 3 s neu versucht,
  falls ein Gerät (noch) nicht erreichbar ist.

## Architektur

Die gesamte Seriell-Kommunikation läuft in einem eigenen `QThread`
(`device_worker.py`), damit ein Verbindungsabbruch oder Timeout die GUI
nicht blockiert. Die GUI kommuniziert mit dem Worker ausschließlich über
Qt-Signale/Slots.

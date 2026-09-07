# CAN-Bus – vendor-unabhängiger Treiber

Dünner Wrapper um [`python-can`](https://python-can.readthedocs.io/), der
nach außen nur `interface`/`channel`/`bitrate` kennt – welcher Hersteller
dahintersteckt, ist nur beim Verbindungsaufbau relevant, nirgends sonst im
Code. Aktuell unterstützt (siehe `INTERFACE_LIST` in `driver.py`):

- `"vector"` – Vector-Interfaces (z.B. CANcase XL). Benötigt die **Vector XL
  Driver Library** (separat vom Hersteller installieren, nicht Teil dieses
  Repos).
- `"pcan"` – PEAK-Interfaces (z.B. PCAN-USB). Benötigt **PCAN-Basic**
  (separat vom Hersteller installieren).

Beides ist Fremd-Software (Windows-Treiber/DLLs), die `python-can`
voraussetzt – ohne installierten Vendor-Treiber liefert `discover_configs()`
für den jeweiligen Interface-Typ einfach keine Kanäle (Fehler wird intern
abgefangen), es gibt keine Fehlermeldung beim Programmstart.

## Verwendung

```python
from can_bus.driver import CanBus

for cfg in CanBus.discover_configs():
    print(cfg)  # {"interface": "pcan", "channel": "PCAN_USBBUS1", ...}

with CanBus(interface="pcan", channel="PCAN_USBBUS1", bitrate=500_000) as bus:
    bus.send(arbitration_id=0x123, data=bytes([0x01, 0x02]))
    frame = bus.recv(timeout=1.0)
    if frame is not None:
        print(frame.arbitration_id, frame.data.hex())
```

## Bekannte Eigenheiten / Einschränkungen

- **Keine Hotplug-Autodiscovery wie bei hcs34xx/korad_kel102.** Ein
  CAN-Adapter lässt sich nicht wie ein USB-Seriell-Gerät einfach "anfragen,
  ob da wer ist" – ohne passende Bitrate stört ein Verbindungsversuch
  potenziell den laufenden Bus. `discover_configs()` listet zwar verfügbare
  Kanäle je Interface-Typ auf, die Bitrate muss aber vom Nutzer explizit
  konfiguriert werden (siehe Einstellungen-Tab in der GUI).
- **Breites Exception-Handling.** `python-can` wirft je nach Backend/
  Fehlerursache sehr unterschiedliche Exception-Typen (fehlende Vendor-DLL,
  falscher Kanalname, Bus-Fehler, ...). `driver.py` fängt das bewusst breit
  ab und bildet es auf `CanConnectionError`/`CanError` ab, analog zum
  `SerialException`-Fang in `hcs34xx/driver.py`.
- Kein CAN-FD (nur klassisches CAN, max. 8 Datenbytes).
- Keine DBC-Signaldecodierung – `recv()`/`send()` arbeiten auf
  Roh-Frames (Arbitration-ID + Bytes), siehe FEATURES.md Punkt 3.

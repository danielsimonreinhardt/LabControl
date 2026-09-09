# CAN-Bus – vendor-unabhängiger Treiber

Dünner Wrapper um [`python-can`](https://python-can.readthedocs.io/), der
nach außen nur `interface`/`channel`/`bitrate` kennt – welcher Hersteller
dahintersteckt, ist nur beim Verbindungsaufbau relevant, nirgends sonst im
Code. Aktuell unterstützt (siehe `INTERFACE_LIST` in `driver.py`):

- `"vector"` – Vector-Interfaces (z.B. CANcase XL, VN1610). Benötigt die
  **Vector XL Driver Library** (separat vom Hersteller installieren, nicht
  Teil dieses Repos). `driver.py::_ensure_vector_dll_findable()` ist ein
  billiges, rein defensives Sicherheitsnetz für den (an echter VN1610-
  Hardware nicht reproduzierbaren) Fall, dass `System32` nicht in PATH
  steht.

  **Kanäle werden als Token `"<seriennummer>:<hw_kanal>"` angegeben**
  (z.B. `"75816:0"`), nicht als nackte Kanalnummer. Grund, an echter
  VN1610 nachgemessen: `python-can`s `VectorBus` defaultet `app_name` auf
  `"CANalyzer"`, wodurch `channel=0` **nicht** "Kanal 0 der Hardware"
  bedeutet, sondern "der Kanal, den die Vector Hardware Config der
  Anwendung *CANalyzer* auf Position 0 zugewiesen hat". Ohne eingerichtete
  CANalyzer-Anwendung schlägt damit jede Verbindung fehl
  (`xlGetApplConfig failed`), mit falsch gemappter Anwendung verbindet sich
  LabControl klaglos mit der falschen Hardware (Status "verbunden", nie ein
  Frame). `_vector_bus_kwargs()` setzt deshalb immer `app_name=None` und
  bindet über die Seriennummer. Erschwerend meldet `python-can` beim
  *Erkennen* den Hardware-Kanal (0/1 je Gerät), erwartet beim *Verbinden*
  ohne Seriennummer aber einen globalen Index über alle Vector-Geräte --
  zwei Nummernkreise, die nur zufällig gleich sind, solange genau ein Gerät
  angeschlossen ist. Alt-Konfigurationen mit reiner Zahl bleiben lauffähig
  (als globaler Index), sollten aber im Settings-Tab einmal neu ausgewählt
  werden.
- `"pcan"` – PEAK-Interfaces (z.B. PCAN-USB). Benötigt **PCAN-Basic**
  (separat vom Hersteller installieren).

Beides ist Fremd-Software (Windows-Treiber/DLLs), die `python-can`
voraussetzt – ohne installierten Vendor-Treiber liefert `discover_configs()`
für den jeweiligen Interface-Typ einfach keine Kanäle (Fehler wird intern
abgefangen), es gibt keine Fehlermeldung beim Programmstart.

## Fallstrick beim .exe-Bau (war ein realer Bug)

`python-can` lädt seine Backends **nicht per normalem `import`**, sondern zur
Laufzeit über Modulnamen als String (`can.interfaces.BACKENDS` →
`importlib.import_module`, siehe `can/interface.py::_get_class_for_interface`).
PyInstallers statische Analyse sieht solche Importe nicht, und weder
`python-can` noch `pyinstaller-hooks-contrib` liefern dafür einen Hook.

Folge in `LabControl_v0.9.16.exe`: das Paket `can.interfaces` war enthalten,
aber **kein einziges Backend** – nachweisbar per `grep -a -c
"can.interfaces.vector"` direkt auf die .exe (0 Treffer, während
`can.interface` 1 Treffer hatte). Jeder CAN-Zugriff fiel dadurch lautlos aus:
`detect_available_configs()` fängt den ImportError pro Interface-Typ als
`CanInterfaceNotImplementedError` ab und liefert eine leere Liste, sichtbar
nur als **"Keine Kanäle gefunden"** im Settings-Tab. Aus dem Quellcode heraus
war der Fehler prinzipiell nicht reproduzierbar, weil dort alle Backends
normal importierbar sind – deshalb ging die Suche mehrfach in die falsche
Richtung (vermeintlich sporadisch, vermeintlich ein DLL-Fundproblem).

Behoben in `LabControl.spec` über `CAN_HIDDENIMPORTS`, abgeleitet aus
`INTERFACE_LIST` statt hart verdrahtet. **Wer `INTERFACE_LIST` erweitert,
muss nichts nachziehen – aber wer den .exe-Bau umbaut, muss die
hiddenimports behalten.** Gegenprobe nach jedem Build:

```bash
grep -a -c "can.interfaces.vector" dist/LabControl_v<version>.exe   # muss > 0 sein
```

`CanBus.backend_problem(interface)` unterscheidet seit v0.9.17 "Backend gar
nicht ladbar" von "kein Gerät angeschlossen"; `discover_configs(errors)`
füllt auf Wunsch ein `interface -> Grund`-Dict, das der Settings-Tab in der
Meldung anzeigt.

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
- `recv()`/`send()` selbst arbeiten weiterhin ausschließlich auf Roh-Frames
  (Arbitration-ID + Bytes) – DBC-Signaldecodierung ist bewusst NICHT Teil
  von `driver.py`, sondern ein eigenständiges, optionales Modul
  `can_bus/dbc.py` (nutzt `cantools`), das auf bereits empfangenen
  `CanFrame`-Objekten arbeitet und unabhängig vom Interface-Typ ist. Pro
  konfiguriertem Interface lässt sich im Einstellungen-Tab optional eine
  DBC-Datei hinterlegen (`settings.py::can_configs`, Schlüssel
  `"dbc_path"`); `device_worker.DeviceWorker` decodiert damit jeden
  empfangenen Frame zusätzlich zu den unverändert weiter gemeldeten
  Rohdaten (`can_signals_decoded`-Signal neben `can_frame_received`),
  sichtbar in `control_tab.CanControlGroup`. Siehe FEATURES.md Punkt 3.

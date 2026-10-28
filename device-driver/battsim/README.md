# Batterie-Simulator (battsim)

Treiber für den selbst gebauten Batterie-Simulator des Longboard-Projekts: 14 galvanisch getrennte Zellkanäle
(0–5 V, max. 0,5 A), zu einem Stapel kaskadiert, Master mit S32K146, fernsteuerbar über CAN (500 kbit/s).
Projektdateien: `C:\DATA\Hobby-Projekte\Longboard\03_TOOLS\Batterie-Simulator`, Analyse im Workspace
`BatterieSimulator` (`doc/can-verhalten.md`).

Der Simulator ist in LabControl ein **eigenes Gerät** (Geräteart `battsim`, ID `battsim:<Interface>`, z. B.
`battsim:vector:59177:1`), öffnet aber **keinen eigenen Bus**: er hängt als `CanNode`
([`can_bus/node.py`](../can_bus/node.py)) an einem in den Einstellungen konfigurierten CAN-Interface
(Spalte „Gerät am Bus“). Damit geht jedes Interface, das LabControl kennt (Vector, PCAN, SLCAN), und der Bus
kann mit dem Prüfling geteilt werden.

| Datei | Inhalt |
|---|---|
| `driver.py` | `BattSim` (CanNode): Sollzustand, zyklisches Senden, Auswertung der Rückmeldungen |
| `mock.py` | `SimulatedBattSim`: nachgebildetes **Gerät** am `MockCanBus` (nicht ein Ersatz-Treiber) |
| `BattSimV1.dbc` | Protokoll, Kopie aus `SOFTWARE/BattSimV1.dbc` des Simulator-Projekts |

## Verwendung (ohne GUI)

```python
from can_bus.driver import CanBus
from can_bus.node import CanScheduler
from battsim.driver import BattSim

bus = CanBus("vector", "59177:1", 500_000)
node = BattSim()
scheduler = CanScheduler(bus)          # sendet im eigenen Thread
scheduler.attach(node)
# Empfang: Frames aus bus.recv() an node.on_frame() geben (in der App macht das der DeviceWorker-Poll)
node.set_remote(True)
node.set_all_voltages(3.7)
node.set_all_enabled(True)
node.set_output(True)
print(node.get_state())
scheduler.stop(send_shutdown=True)     # Ausgang sperren, Fernsteuerung abgeben
```

Prüfskript: `python tools/check_battsim.py` (Nachbildung + DeviceWorker im Simulationsmodus),
`--hardware --interface vector --channel 59177:1` (nur lesen, Kanalbezeichnung `<Seriennummer>:<Hardware-Kanal>`, hier CANcaseXL Kanal 2),
zusätzlich `--remote --volts 3.7` (Folge am Gerät), mit `--hold 15` hält jede Stufe 15 s zum Ablesen am Multimeter.

## Protokoll in Kürze

| ID | Richtung | Zyklus | Inhalt |
|---|---|---|---|
| 0x100–0x105 | → Simulator | 1000 ms + bei Änderung | Sollwert (0,1 mV) und Freigabe je Zelle, 3 Zellen je Botschaft |
| 0x130 | → Simulator | 100 ms + bei Änderung | REMOTE (1/2), OUTPUT (1/2), MODE (1 Standby, 2 Ein) |
| 0x110–0x115 | ← Simulator | 100 ms | Istspannung (auf mV gerundet) und Status je Zelle |
| 0x120 | ← Simulator | 100 ms | Gerätezustand, Ausgangszustand |

Jede Botschaft trägt einen 4-Bit-Alive-Zähler, der Treiber erhöht ihn je Botschaft um 1 und zählt
1..14 (nach 14 folgt 1, 0 und 15 kommen nicht vor) – wie die alte Firmware beim Senden. Die neue
Firmware (BattSimFW ab Stufe B) wertet 0 und 15 als Alive-Fehler.

## Bekannte Eigenheiten / Einschränkungen

Aus dem Simulink-Modell der bestehenden Firmware gelesen und **am Gerät am 22.10.2026 geprüft (BS-1.8)**: Zellfreigabe,
Zellstatus, Ausfall von 0x130 (FAIL nach 5,16 s) und das Zurücksetzen sind bestätigt. Nicht geprüft: Freigabewert 1
als „ein“ (der Treiber sendet nur 0 und 2) und die Überlast. Details: Workspace BatterieSimulator, `doc/can-verhalten.md`.

- **Zellfreigabe**: die Firmware wandelt `CELLn_ENABLE` nach boolean. 0 = aus, **1 und 2 = ein** – die DBC nennt 1
  „disable“, das würde die Zelle also einschalten. Der Treiber sendet deshalb **0 für aus** und 2 für ein.
- **Zellstatus**: die Firmware meldet 1 = aus und **0 = ein** (DBC: 2 = ein). Gelesen wird 1 = aus, 0/2 = ein,
  3 = Sollwertfehler.
- **Ausfall**: fehlt 0x130 1,5–3 s, wird die Steuerbotschaft ungültig; im Zustand RUN bleiben die Ausgänge noch
  3 s an, dann FAIL (alle Zellen aus). Ausgänge sind also 4,5–6 s nach dem letzten Frame aus. „FAIL zurücksetzen“
  sendet MODE=1 und nach 0,5 s wieder MODE=2.
- **Überlast** (48-V-Strom über 1,28 A) wird in der Firmware gespeichert und lässt sich nur mit der Taste ALL ON/OFF
  am Gerät zurücksetzen, nicht per CAN. Danach hilft „FAIL zurücksetzen“ nicht, und der Wechsel in die Fernsteuerung
  wird verweigert (Firmware-Analyse F5, Workspace BatterieSimulator `doc/firmware-analyse.html`).
- **Fernsteuerung nur auf Anforderung**: ohne „Fernsteuerung EIN“ sendet der Treiber nichts. Ein zyklisches
  REMOTE=1 würde das Gerät sonst aus einer am Bedienfeld gewählten Fernsteuerung herausholen.
- **„Alles aus“** sperrt nur den Ausgang (OUTPUT=1) und sendet weiter – Abbruch des Sendens hieße nach 4,5–6 s FAIL.
- **Geordnetes Ende** (App schließt, Interface entfernt): dreimal 0x130 mit OUTPUT=1, REMOTE=1, dann Ende.
- **Grenzwert je Zelle**: Voreinstellung 4,30 V (Board-Entscheidung bs-grenzwert), einstellbar bis 5,0 V; Sollwerte
  darüber lehnt der Treiber mit `BattSimValueError` ab.
- **14 Kanäle**: die DBC sieht 16 vor, in der Firmware sind Kanal 15/16 auskommentiert; der Treiber bietet sie nicht an.
- **Dynamisches Zellmodell** (0x131/0x132, MODE=3) ist noch nicht umgesetzt (BS-1.9).
- Die Alive-Prüfung der Firmware ist wegen einer ODER-Verknüpfung wirkungslos; der Treiber hält den Zähler trotzdem
  korrekt, damit eine neue Firmware ihn prüfen kann.

## Am Gerät beobachtet (22.10.2026)

- **Istwerte sind nur auf etwa ±0,1 V genau.** Soll 3,7 V, Multimeter und Meldung je Zelle: Zelle 2 3,685/3,669 V,
  Zelle 5 3,700/3,712 V, Zelle 10 3,735/3,773 V, Zelle 13 3,740/3,813 V. Der Ausgang weicht je Kanal um −15 … +40 mV
  vom Sollwert ab (gesteuert, nicht geregelt), die Rückmeldung zusätzlich um −16 … +73 mV vom Multimeter (Zelle 13 mit
  festem Offset ≈ +70 mV, im Standby 0,064 V statt 0). Für Genauigkeit am Prüfling ein externes Multimeter verwenden.
- **Abgeschaltete Zellen zeigen 0,02–0,07 V** (Restwert der Messung), nicht 0.
- **Handbetrieb** (Taste Output Enable am Gerät) startet mit dem zuletzt gesetzten Sollwert, nicht mit 0 V: Nach einer
  Fernsteuer-Sitzung mit 3,7 V liegen alle Zellen sofort auf 3,7 V.
- **Reset und Einschalten:** Beim RESET an der Front der Supply-Platine (5-V-Regler aus) geht jede Zelle für ≈ 40 ms auf
  ≈ 5,4 V, beim Netz-Ein auf bis 2,3 V. **Mit Prüfling am Zellabgriff nicht drücken oder einschalten.**
- **Fernsteuerung über die Fronttaste** (Remote Control) ohne 0x130 bleibt ohne FAIL (nur aus RUN gibt es FAIL).
- Das Kachelfeld „Fernsteuerung“ zeigt die Anforderung des Treibers, nicht den Zustand am Gerät (Feld „Zustand“).
- Einmal blieb Zelle 5 nach „Alle Zellen ein“ aus (nicht reproduzierbar).

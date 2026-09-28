# scope_api – herstellerneutrale Oszilloskop-Anbindung

Grundlage dafür, dass ein KI-Assistent über den LabControl-MCP-Server mit
Oszilloskopen misst und auswertet. Die Schnittstelle ist so geschnitten, dass
ein zweites Oszilloskop (geplant: SCPI-Tischgerät per LAN) nur einen weiteren
Adapter braucht. Auswertung, Speicher, Netzwerk-Schnittstelle und
MCP-Werkzeuge bleiben dabei gleich.

Stand: **Phase 3** (seit 0.18.0): Erfassungen als Bild -- `lab_gui/scope_plot.py`
(QPainter, keine neue Abhängigkeit), Endpunkt `GET /api/v1/captures/{cid}/plot`,
MCP-Werkzeug `plot_capture` und die letzte Erfassung samt Knopf „Kurve“ auf der
Oszilloskop-Kachel. Davor **Phase 2** (0.16.0). Phase 1 (0.15.0): Schnittstelle, Auswertung,
Speicher, 2204A-Adapter, Mock. Phase 2: eigener Scope-Thread mit
Leerlauf-Trennung (`lab_gui/scope_service.py`), Endpunkte `/api/v1/scopes` und
`/api/v1/captures` (`lab_gui/share_api.py`), Häkchen „Messen“, Kachelzustand
„Verbunden (MCP)“ und die MCP-Werkzeuge (`labcontrol_mcp/server.py`). Die
Abstimmung um das exklusive PicoScope-Handle zwischen DeviceWorker und
Scope-Dienst regelt `device-driver/picoscope2000/guard.py`.
Plan und Entscheidungen vom 2026-09-28:
<https://claude.ai/artifact/PbSiAExdmfT1kjTkVpnStc>.

## Dateien

| Datei | Inhalt |
|---|---|
| `base.py` | Datentypen (`Capabilities`, `AcquireRequest`, `Capture`), `ScopeDriver`-Protokoll, `ScopeError` mit stabilen Codes |
| `analysis.py` | Kennwerte, Flanken, Hüllkurve, Verdichtung, kompakter Bericht (`report`) |
| `store.py` | Erfassungen mit `capture_id` im Speicher (Standard: die letzten 50) plus CSV-Datei |
| `pico2000_adapter.py` | PicoScope 2204A/2205A über `picoscope2000` (echtes Gerät oder Mock) |

## Verwendung

```python
from scope_api.base import AcquireRequest
from scope_api.pico2000_adapter import open_pico2000
from scope_api.store import CaptureStore
from scope_api import analysis

scope = open_pico2000(simulate=False)          # ~4,5 s, PicoScope 7 muss zu sein
store = CaptureStore(directory=Path("captures"))
request = AcquireRequest.from_dict({
    "channels": [{"name": "A", "range_v": 5}, {"name": "B", "range_v": 2}],
    "timing": {"duration_s": 0.01, "pre_trigger_pct": 20},
    "trigger": {"mode": "single", "source": "A", "level_v": 1.65, "edge": "rising", "timeout_s": 2},
})
capture = scope.acquire(request)
store.add(capture)
print(analysis.report(capture, ["vpp", "frequency", "duty", "rise_time"]))
scope.close()
```

## Grundsätze

- **Einheiten statt Gerätecodes.** Bereich in ±V an der Tastkopfspitze,
  Dauer bzw. Abtastintervall in s, Triggerpegel in V. Der Adapter wählt den
  nächsten passenden Gerätebereich und die passende Zeitbasis und meldet die
  **tatsächlich** eingestellten Werte in der `Capture` zurück.
- **Zustandslose Erfassung.** Jede Anfrage enthält die vollständige
  Einstellung. Auf einen Gerätezustand von vorher verlässt sich nichts, denn
  dazwischen kann PicoScope 7 oder ein Testablauf am Gerät gewesen sein.
- **Auswertung im Host.** Alle Kennwerte kommen aus `analysis.py`, nicht aus
  der Mess-Engine des Geräts. So liefern beide Scopes vergleichbare Zahlen,
  und eine gespeicherte Erfassung lässt sich neu auswerten.
- **Klein zurückgeben.** `report()` liefert Einstellungen, Kennwerte je Kanal,
  Warnungen und eine Hüllkurve mit höchstens 200 Min/Max-Paaren. Rohdaten gibt
  es nur als CSV oder als Ausschnitt (`CaptureStore.excerpt`).

## Trigger

| `mode` | Verhalten |
|---|---|
| `none` | freilaufend, `triggered = None` |
| `auto` | wartet bis `timeout_s` auf die Flanke, erfasst danach trotzdem. `triggered` wird am Signal selbst abgelesen (Kreuzung am Trigger-Index), weil die ps2000-API das nicht meldet |
| `single` | nur mit Trigger; ohne Flanke nach `timeout_s` Fehler `trigger_timeout` (höchstens 30 s) |

`pre_trigger_pct` legt fest, wie viel Prozent der Erfassung vor dem Trigger
liegen. `t = 0` ist der Trigger-Zeitpunkt.

## Kennwerte

`vmax`, `vmin`, `vpp`, `vmean`, `vrms`, `vbase`, `vtop`, `amplitude`,
`frequency`, `period`, `duty`, `rise_time`, `fall_time` (10–90 %),
`overshoot`, `undershoot`, `edge_count`. Kann ein Wert im Fenster nicht
bestimmt werden, ist er `None` und der Grund steht unter `unavailable`.

- **Pegel:** `vbase`/`vtop` sind die Mediane der Samples unter bzw. über der
  Mitte zwischen Min und Max. Das ist robust gegen Überschwingen und Rauschen.
- **Flanken:** erkannt an der 50-%-Schwelle, linear interpoliert. Die
  Hysterese beträgt 10 % des Hubs und wächst bei Rauschen auf bis zu 3 σ
  (Rauschen per MAD aus Nachbardifferenzen geschätzt), höchstens 40 % des Hubs.
- **Auflösung:** Anstiegs- und Abfallzeiten unter 2 Abtastintervallen gibt es
  nicht. Dann kommt `None` mit Hinweis, weil sonst nur das Intervall gemessen
  würde. Am 2204A ist bei 10 ns Schluss: eine JDS2915-Flanke (~22 ns) liegt
  genau an dieser Grenze.
- **Rauschschwelle:** liegt der Hub unter 2 % des Messbereichs (gut 2,5
  ADC-Stufen bei 8 Bit), werden keine Flanken ausgewertet. Grund: Am 2204A
  lieferten offene Eingänge sonst Fantasie-Frequenzen von 15 kHz und 620 kHz.
  Abhilfe ist ein kleinerer Bereich; die Meldung sagt das.

## CSV-Format

Drei Kopfzeilen mit `#` (Scope, Zeitpunkt, Abtastintervall, Trigger-Index,
Bereiche), dann `time_s,A_V,B_V`. Lesbar mit
`numpy.loadtxt(path, delimiter=",", comments="#", skiprows=4)` oder
`pandas.read_csv(path, comment="#")`. Die Datei bleibt liegen, auch wenn die
Erfassung aus dem Speicher verdrängt wird.

## Fehlercodes (`ScopeError.code`)

| Code | Bedeutung |
|---|---|
| `invalid_request` | Anfrage falsch geformt (fehlende/doppelte Angaben, Typen, Grenzen) |
| `setting_not_supported` | Gerät kann das nicht (Bereich, Intervall, Offset, Kanal); `details` nennt den erlaubten Wert |
| `level_out_of_range` | Triggerpegel außerhalb des Bereichs der Quelle |
| `duration_too_long` | Dauer passt nicht in den Speicher; `max_duration_s` nennt das Maximum |
| `trigger_timeout` | `single` ohne Trigger innerhalb von `timeout_s` |
| `device_error` | Gerät nicht erreichbar oder hat abgelehnt |
| `unknown_capture` | ID nicht (mehr) im Speicher |
| `unknown_measurement` | unbekannter Kennwert; `allowed` listet die bekannten |

## 2204A-Adapter: Eigenheiten

- Bereiche ±50 mV bis ±20 V (9 Stück), AC/DC, kein Offset, 8 Bit, 10 MHz.
- Zeitbasis: Intervall = 10 ns · 2^Timebase. Welche Timebase mit welcher
  Kanalzahl und Sample-Zahl geht, fragt der Adapter per `ps2000_get_timebase`
  beim Gerät ab. 10 ns gehen nur mit einem Kanal.
- Der Speicher (8 kS) wird von den aktiven Kanälen geteilt.
- Das Warten auf den Trigger ist begrenzt (`run_block_raw`, danach
  `ps2000_stop`). Anders als `capture_block()` gibt es keine Endlosschleife.
- Noch nicht: erweiterte Trigger (Pulsbreite, Fenster, Runt, Dropout),
  Streaming für lange Aufnahmen, Signalgenerator. Alles kommt in Phase 4.

## Prüfen

```
.venv\Scripts\python.exe tools\check_scope_api.py              # nur Mock + bekannte Signale
.venv\Scripts\python.exe tools\check_scope_api.py --hardware   # zusätzlich echtes 2204A
.venv\Scripts\python.exe tools\check_scope_api.py --hardware --signal-a rechteck:1000:3.3
```

`--signal-a` erwartet ein bekanntes Rechteck an Kanal A und prüft Trigger,
Frequenz und Pegel.

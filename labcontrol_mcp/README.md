# LabControl-MCP-Server

Gibt einem KI-Assistenten (z.B. Claude Code) Werkzeuge, um die freigegebenen
Messgeräte von LabControl **zu lesen und zu steuern** (Sollwerte setzen,
Ausgänge schalten, Notaus) und **mit Oszilloskopen zu messen**.

Der Server läuft als eigener Prozess **außerhalb** der LabControl-`.exe` und
übersetzt Werkzeugaufrufe in Anfragen an deren Netzwerk-Schnittstelle
(`lab_gui/share_api.py`). Alle Sicherheitsentscheidungen fallen in LabControl:
der Server kann nichts, was die Schnittstelle nicht ohnehin erlaubt.

## Werkzeuge

| Werkzeug | Zweck |
|---|---|
| `get_status` | Version, Sperrzustand, Watchdog, Fernsteuerung (`effective` = du darfst steuern, `program` = gekoppelt als) |
| `list_devices` | Freigegebene Geräte mit Messwerten, erlaubten Aktionen und Sperrgrund |
| `read_device` | Messwerte eines Geräts (zum Gegenprüfen nach dem Schreiben) |
| `control_device` | Eine Aktion ausführen (`PSU_VOLT`, `PSU_OUT_ON`, `CURR`, `HIL_RELAY_ON`, …) |
| `all_off` | Notaus: alle Ausgänge aller Geräte ab — geht immer |
| `list_scopes` | Oszilloskope mit Zustand (frei / verbunden / Testlauf) und ob Messen gerade geht |
| `get_scope_capabilities` | Kanäle, Bereiche, Abtastrate, Speicher, Triggerarten eines Oszilloskops |
| `acquire` | Eine Erfassung mit vollständiger Einstellung (V, s); liefert Kennwerte, Warnungen, Hüllkurve, CSV-Pfad |
| `get_capture` | Ausschnitt einer Erfassung (roh oder verdichtet) |
| `measure` | Kennwerte einer gespeicherten Erfassung neu berechnen, ohne neue Messung |
| `plot_capture` | Erfassung als Bild (PNG) ansehen, auch gezoomt oder für einzelne Kanäle |
| `release_scope` | Verbindung zum Oszilloskop sofort trennen (sonst nach 60 s von selbst) |

`control_device` bekommt die Antwort des Geräts abgewartet: `ok: true` heißt,
das Gerät hat den Befehl bestätigt (nicht nur „abgeschickt"). Fehler kommen als
Werkzeugfehler mit einem Hinweis, was zu tun ist.

## Einrichtung

**1. In LabControl** (Einstellungen → Netzwerk):

1. „Freigabe im lokalen Netzwerk aktivieren" anhaken. Der Token wird dabei
   automatisch erzeugt.
2. In der Geräte-Tabelle bei den gewünschten Geräten **Lesen** und **Steuern**
   anhaken, beim Oszilloskop **Lesen** und **Messen**. CAN und Oszilloskop sind
   nicht fernsteuerbar.
3. Den **Token** kopieren (Kopieren-Knopf neben dem Feld).

Läuft der MCP-Server auf demselben Rechner wie LabControl, darf er steuern, solange
**„Claude (MCP) darf steuern"** angehakt ist (Standard, siehe „Sicherheitsmodell").
Auf einem **anderen Rechner** koppelt man ihn einmal als Programm:

```
python tools\labcontrol_pair.py <ADRESSE>:8420 "MCP Laptop" --save mcp_schluessel.txt
```

Das Skript zeigt einen Code, LabControl fragt „Programm … möchte koppeln und
steuern“ mit demselben Code, dort „Code gleich – koppeln“ tippen. Den Schlüssel aus
der Datei als `LABCONTROL_TOKEN` eintragen (statt des allgemeinen Tokens).

**2. Umgebung für den MCP-Server** (einmalig, im Ordner `LabControl`):

```
python -m venv labcontrol_mcp\.venv
labcontrol_mcp\.venv\Scripts\python.exe -m pip install -r labcontrol_mcp\requirements.txt
```

**3. Server bei Claude Code anmelden** (PowerShell, Token einsetzen):

```
claude mcp add labcontrol --scope user `
  -e LABCONTROL_URL=http://127.0.0.1:8420 `
  -e LABCONTROL_TOKEN=<TOKEN> `
  -- C:\WORK\vscode_workspace\LabControl\labcontrol_mcp\.venv\Scripts\python.exe `
     C:\WORK\vscode_workspace\LabControl\labcontrol_mcp\server.py
```

Alternativ per `.mcp.json` (Vorlage: `.mcp.json.example`). Der Token steht dann
im Klartext in der Konfiguration — die Datei nicht einchecken (`labcontrol_mcp/.mcp.json`
ist in `.gitignore`).

Läuft LabControl auf einem anderen Rechner, statt `127.0.0.1` dessen Adresse
eintragen, in LabControl bei „Erreichbar für" das ganze Netzwerk wählen und den
MCP-Server wie oben als Programm koppeln.

**4. Prüfen** (steuert nichts):

```
labcontrol_mcp\.venv\Scripts\python.exe labcontrol_mcp\selftest.py
```

Der Selbsttest meldet, was noch fehlt (LabControl nicht gestartet, Token falsch,
keine Kachel freigegeben, kein Steuerrecht).

## Sicherheitsmodell

Damit ein Befehl ankommt, müssen **alle** Bedingungen erfüllt sein:

1. **Token** stimmt (nur als `Authorization`-Header, nie in der URL).
2. Das Gerät ist **freigegeben** („Lesen") und für **Steuern** angehakt.
3. Der Zugang **darf steuern** (seit LabControl 0.57.0, vorher Hauptschalter mit
   Zeitfenster):
   - **am selben Rechner**, solange in Einstellungen → Netzwerk **„Claude (MCP)
     darf steuern"** angehakt ist (Standard). Ohne Haken darf der Assistent nur
     lesen. Erkannt wird „lokal" an der Verbindung (Loopback oder die eigene
     Adresse des Rechners), nicht an Angaben des Aufrufers.
   - **von einem anderen Rechner** nur als **gekoppeltes Programm** mit eigenem
     Schlüssel (siehe Einrichtung). Die Kopplung bestätigt der Nutzer am
     Bildschirm von LabControl; unter „Gekoppelte Programme" hebt er sie wieder auf.

   **Nur der Nutzer kann das erlauben**, der Assistent hat dafür keinen Weg.
4. Es läuft **kein Testablauf** und die **Sicherheitsabschaltung** hat nicht
   ausgelöst (sonst HTTP 409). Lesen bleibt dann möglich.
5. Aktion, Wert und Kanal sind **gültig** (Wertebereiche wie im Testeditor) und
   der Sollwert liegt **nicht über einem aktiven Sicherheits-Grenzwert** des Geräts.

**Immer erlaubt** (mit Token): `all_off`. Auch ohne Steuerrecht,
laufendem Testablauf und nach einer Sicherheitsabschaltung.

**Nie fernsteuerbar:** CAN-Frames senden, Arbiträrsignale, Ausgänge des Oszilloskops
(Signalgenerator folgt später unter denselben Regeln wie „Steuern“).

### Oszilloskope (seit 0.16.0)

Erfassen braucht **Token**, **Lesen** und das eigene Häkchen **„Messen“**, aber
**kein Steuerrecht** — ein Oszilloskop schaltet nichts. Ein **Testablauf**,
der das Oszilloskop nutzt, hat Vorrang (`scope_in_test_run`). Solange nach einer
Erfassung weniger als 60 s vergangen sind, hält LabControl das Gerät geöffnet
(Kachel: „Verbunden (MCP)“); PicoScope 7 kann es dann nicht öffnen —
`release_scope`, „Trennen“ oder „PicoScope 7 öffnen“ auf der Kachel geben es frei.
Jede Erfassung steht mit Aufrufer im `labdash.log` und als CSV in `captures/`
neben der App. Einheiten, Kennwerte und Fehlercodes: `scope_api/README.md`.

Zusätzlich: höchstens 2 Befehle gleichzeitig unterwegs und eine
Schreibraten-Begrenzung (5/s), damit ein Stau von Befehlen den Notaus nicht
verzögert. Jede Aktion und jede Abweisung steht mit Adresse des Aufrufers im
`labdash.log` von LabControl.

**Netzteil HCS-34xx:** `PSU_OUT_ON`/`PSU_OUT_OFF` schalten den Ausgang wirklich
(seit 0.25.0, Sollwerte bleiben). `PSU_CURR` setzt nur die Strombegrenzung und
schaltet nichts. Ohne Last sinkt die Spannung nach „AUS“ langsam (etwa 10 s).
Steht das Netzteil in **Handbedienung** (Schalter in der Kachel), ist es für MCP
nicht verfügbar und jede Aktion wird mit „Netzteil in Handbedienung“ abgelehnt;
den Schalter bedient der Nutzer, nicht MCP.

## Fehlersuche

| Meldung | Ursache |
|---|---|
| `nicht erreichbar` | LabControl läuft nicht, Freigabe aus, Adresse/Port falsch, Firewall |
| `unauthorized` | Token in der MCP-Konfiguration stimmt nicht (nach „Neu erzeugen" anpassen) |
| `token_not_configured` | In LabControl ist noch kein Token erzeugt |
| `remote_control_inactive` | „Claude (MCP) darf steuern" aus, oder LabControl läuft auf einem anderen Rechner und der MCP-Server ist dort nicht als Programm gekoppelt — Nutzer muss das ändern |
| `control_not_permitted` | Bei diesem Gerät ist „Steuern" nicht angehakt |
| `locked` | Testablauf läuft oder Sicherheitsabschaltung — Steuern gesperrt |
| `exceeds_safety_limit` | Sollwert über dem aktiven Grenzwert des Geräts |
| `device_error` | Das Gerät hat abgelehnt oder ist nicht verbunden (Text steht in `message`) |
| `measure_not_permitted` | Beim Oszilloskop ist „Messen“ nicht angehakt |
| `scope_busy_external` | Oszilloskop von einem anderen Programm belegt (PicoScope 7?) |
| `scope_in_test_run` | Ein Testablauf nutzt das Oszilloskop |
| `trigger_timeout` | Kein Trigger in der Wartezeit (Pegel, Flanke, Quelle prüfen) |

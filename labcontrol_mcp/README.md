# LabControl-MCP-Server

Gibt einem KI-Assistenten (z.B. Claude Code) Werkzeuge, um die freigegebenen
Messgeräte von LabControl **zu lesen und zu steuern**: Sollwerte setzen,
Ausgänge schalten, Notaus.

Der Server läuft als eigener Prozess **außerhalb** der LabControl-`.exe` und
übersetzt Werkzeugaufrufe in Anfragen an deren Netzwerk-Schnittstelle
(`lab_gui/share_api.py`). Alle Sicherheitsentscheidungen fallen in LabControl:
der Server kann nichts, was die Schnittstelle nicht ohnehin erlaubt.

## Werkzeuge

| Werkzeug | Zweck |
|---|---|
| `get_status` | Version, Sperrzustand, Watchdog, Fernsteuerung (Hauptschalter mit Restzeit, `effective` = gilt für dich) |
| `list_devices` | Freigegebene Geräte mit Messwerten, erlaubten Aktionen und Sperrgrund |
| `read_device` | Messwerte eines Geräts (zum Gegenprüfen nach dem Schreiben) |
| `control_device` | Eine Aktion ausführen (`PSU_VOLT`, `PSU_OUT_ON`, `CURR`, `HIL_RELAY_ON`, …) |
| `all_off` | Notaus: alle Ausgänge aller Geräte ab — geht immer |

`control_device` bekommt die Antwort des Geräts abgewartet: `ok: true` heißt,
das Gerät hat den Befehl bestätigt (nicht nur „abgeschickt"). Fehler kommen als
Werkzeugfehler mit einem Hinweis, was zu tun ist.

## Einrichtung

**1. In LabControl** (Einstellungen → Netzwerk):

1. „Freigabe im lokalen Netzwerk aktivieren" anhaken. Der Token wird dabei
   automatisch erzeugt.
2. In der Geräte-Tabelle bei den gewünschten Geräten **Lesen** und **Steuern**
   anhaken. CAN und Oszilloskop sind nicht fernsteuerbar.
3. Den **Token** kopieren (Kopieren-Knopf neben dem Feld).

Der Hauptschalter **„Fernsteuerung aktiv"** braucht der MCP-Server nicht, solange er
auf demselben Rechner wie LabControl läuft und **„Zugriffe von diesem PC brauchen
den Hauptschalter nicht"** angehakt ist (Standard, siehe „Sicherheitsmodell").

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
eintragen und in LabControl bei „Erreichbar für" das ganze Netzwerk wählen.

**4. Prüfen** (steuert nichts):

```
labcontrol_mcp\.venv\Scripts\python.exe labcontrol_mcp\selftest.py
```

Der Selbsttest meldet, was noch fehlt (LabControl nicht gestartet, Token falsch,
keine Kachel freigegeben, Hauptschalter aus).

## Sicherheitsmodell

Damit ein Befehl ankommt, müssen **alle** Bedingungen erfüllt sein:

1. **Token** stimmt (nur als `Authorization`-Header, nie in der URL).
2. Das Gerät ist **freigegeben** („Lesen") und für **Steuern** angehakt.
3. Der Hauptschalter **„Fernsteuerung aktiv"** ist an. Er ist nach jedem
   App-Start aus und schaltet sich nach dem eingestellten Zeitlimit (Standard
   60 min) von selbst wieder aus — auch wenn LabControl sonst nichts mehr tut.
   **Nur der Nutzer kann ihn einschalten**, der Assistent hat dafür keinen Weg.
   Beim Ablauf bleiben die Geräte in ihrem Zustand, sie werden nicht abgeschaltet.

   **Ausnahme für diesen PC:** Zugriffe vom selben Rechner — also auch dieser
   MCP-Server, wenn er lokal läuft — brauchen den Hauptschalter nicht, solange in
   Einstellungen → Netzwerk **„Zugriffe von diesem PC brauchen den Hauptschalter
   nicht"** angehakt ist (Standard). Es gibt dann **kein Zeitfenster**: der
   Assistent kann jederzeit steuern, solange Token, „Steuern" und die Sperren
   (Punkte 1, 2, 4, 5) stimmen. Wer das nicht will, nimmt den Haken heraus; dann
   gilt auch für lokale Zugriffe wieder das Zeitfenster. Erkannt wird „lokal" an der
   Verbindung (Loopback oder die eigene Adresse des Rechners), nicht an Angaben des
   Aufrufers.
4. Es läuft **kein Testablauf** und die **Sicherheitsabschaltung** hat nicht
   ausgelöst (sonst HTTP 409). Lesen bleibt dann möglich.
5. Aktion, Wert und Kanal sind **gültig** (Wertebereiche wie im Testeditor) und
   der Sollwert liegt **nicht über einem aktiven Sicherheits-Grenzwert** des Geräts.

**Immer erlaubt** (mit Token): `all_off`. Auch bei ausgeschaltetem Hauptschalter,
laufendem Testablauf und nach einer Sicherheitsabschaltung.

**Nie fernsteuerbar:** CAN-Frames senden, PicoScope, Arbiträrsignale.

Zusätzlich: höchstens 2 Befehle gleichzeitig unterwegs und eine
Schreibraten-Begrenzung (5/s), damit ein Stau von Befehlen den Notaus nicht
verzögert. Jede Aktion und jede Abweisung steht mit Adresse des Aufrufers im
`labdash.log` von LabControl.

**Netzteil HCS-34xx:** „Ausgang AUS" ist nur Strom = 0 A. `PSU_CURR` mit einem
Wert über 0 A schaltet den Ausgang wieder ein.

## Fehlersuche

| Meldung | Ursache |
|---|---|
| `nicht erreichbar` | LabControl läuft nicht, Freigabe aus, Adresse/Port falsch, Firewall |
| `unauthorized` | Token in der MCP-Konfiguration stimmt nicht (nach „Neu erzeugen" anpassen) |
| `token_not_configured` | In LabControl ist noch kein Token erzeugt |
| `remote_control_inactive` | Hauptschalter aus oder abgelaufen und die Ausnahme „Zugriffe von diesem PC…" aus (oder LabControl läuft auf einem anderen Rechner) — Nutzer muss das ändern |
| `control_not_permitted` | Bei diesem Gerät ist „Steuern" nicht angehakt |
| `locked` | Testablauf läuft oder Sicherheitsabschaltung — Steuern gesperrt |
| `exceeds_safety_limit` | Sollwert über dem aktiven Grenzwert des Geräts |
| `device_error` | Das Gerät hat abgelehnt oder ist nicht verbunden (Text steht in `message`) |

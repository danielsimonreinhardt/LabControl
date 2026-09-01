# Feature-Ideen (gesammelt beim Testen von LabControl v0.6.1)

Keine feste Priorität — nach Bedarf auswählen. Jeder Eintrag kann unabhängig
in einer eigenen Session umgesetzt werden.

## 1. Hinzufügen-Button im Testablauf-Reiter: Plus vs. Menü-Pfeil unterscheiden

Der Hinzufügen-Button im Testablauf-Reiter soll erkennen, ob auf das
Plus-Symbol oder auf den Menü-Öffnen-Pfeil geklickt wird:
- Klick auf **Plus**: direkt eine neue Aktionsschritt-Zeile hinzufügen, ohne
  das Dropdown-Menü zu öffnen.
- Klick auf **Pfeil**: Verhalten wie bisher (Dropdown-Menü mit Auswahl öffnen).


## 2. Individuelle Panel-Hintergrundfarben (Dashboard + Control)

Die Geräte-Panels im Dashboard- und Control-Tab sollen jeweils eine
individuelle Hintergrundfarbe bekommen können, um sie optisch besser
unterscheiden zu können.

**Aktivierung:** Als Option im Einstellungs-Tab (an/aus schaltbar).


## 3. Aufnahme-Start/Stopp zu einem Button vereinen

Die getrennten "Aufnahme starten" und "Aufnahme stoppen" Buttons sollen zu
einem einzigen Button zusammengeführt werden:
- Keine Aufnahme läuft: Button statisch **rot** eingefärbt, mit passendem
  Icon (z.B. Record-Symbol).
- Aufnahme läuft: Button **blinkt rot**.


## 4. Aufnahme zeichnet nur aktivierte Diagramm-Signale auf

Aktuell zeichnet die Aufnahmefunktion (CSV-Export, `lab_gui/recording.py`)
vermutlich alle verfügbaren Signale auf. Gewünscht: nur die Signale
aufzeichnen, die aktuell in den Diagrammen aktiviert/sichtbar sind.


## 5. Statistik/Auswertung nach Testlauf

Nach einem Testcase-Lauf sollen aus den geloggten Daten berechnet werden:
- Min/Max/Mittelwert pro Signal
- Energie (Wh) bzw. Kapazität (Ah) aus kombinierten Last+PSU-Daten
  (z.B. für Akku-Kapazitätstests)

**Status:** Noch nicht umgesetzt (Stand: Codeprüfung 2026-09-01). Es gibt
aktuell keine Wh/Ah/Mittelwert-Berechnung im Repo — `report_chart.py`
berechnet nur Pixel-lokale Min/Max-Werte für die Chart-Darstellung, keine
Lauf-weiten Summary-Statistiken. Würde sich gut in den bereits vorhandenen
Nachlauf-Report (`lab_gui/run_report.py`) integrieren lassen.


## 6. Weitere Gerätetreiber

Zusätzliche Gerätetreiber über hcs34xx (PSU) und korad_kel102 (Last) hinaus,
z.B.:
- Ein einfaches USB-Multimeter (nur Messen, kein Steuern)
- Ein generischer SCPI/VISA-Treiber für Rigol/Siglent-Geräte

Passt zum "vendor-agnostic"-Anspruch aus der README.

**Status:** Noch nicht umgesetzt — aktuell existieren nur die zwei genannten
Treiber, kein `pyvisa` oder generischer SCPI-Layer.


## 7. Wiederverwendbare Testcase-Bausteine

Häufige Schrittfolgen (z.B. ein Entladeprofil) sollen als Vorlage/Baustein
gespeichert und in andere Testcases eingefügt werden können, statt sie jedes
Mal neu aufzubauen.

**Status:** Noch nicht umgesetzt. Es gibt aktuell nur PSU-Device-Presets
("Preset P1/P2/P3"), aber keinen Mechanismus für wiederverwendbare
Test-Schrittfolgen/Templates.


## 8. Desktop-Benachrichtigung bei Lauf-Ende/Fehler

Eine Desktop-Benachrichtigung (z.B. via System-Tray/Toast) beim Ende oder bei
einem Fehler eines Testlaufs — praktisch bei langen unbeaufsichtigten Läufen
(z.B. Akku-Zyklen über Nacht).

**Status:** Noch nicht umgesetzt — kein `QSystemTrayIcon` oder
Notification-Mechanismus im Code vorhanden.


## 9. Arbiträrsignal-Generator: neue Signalformen

Der Arbiträrsignal-Generator soll erweitert werden um:
- Dreieck-Signalform
- Sägezahn-Signalform
- Beim Rechtecksignal: einstellbarer Duty-Cycle (echtes PWM statt festem 50%)


## 10. [Toolchain/Build] Simulationsmodus nur im Dev-Build

Der Simulationsmodus soll nur im Entwicklungspfad verfügbar sein. Alles, was
released wird, soll die Option nicht mehr anbieten (in Release-Builds
komplett ausgeblendet/nicht anwählbar).

**Zu klären bei Umsetzung:** Wie wird Dev- vs. Release-Build unterschieden
(Build-Flag, Environment-Variable, PyInstaller-Spec-Unterscheidung)?

## 11. Diagramm-Anzeige Erweiterungen

Die Diagramm-Anzeige soll anstatt nur horizontale Rasterlinien auch vertikale Rasterlinien bekommen.
Die y-Achsenskalierung soll umschaltbar von automatisch auf feste WErtebereiche möglich sein

---

## Bereits umgesetzte Vorschläge (zur Info, keine offenen Punkte)

Beim Review früherer Feature-Vorschläge (Codeprüfung 2026-09-01) wurde
festgestellt, dass folgende bereits implementiert sind und daher NICHT mehr
in dieser Liste stehen:

- **Messwert-Logging / CSV-Export** — `lab_gui/recording.py` (`Recorder`)
  + `lab_gui/recording_export.py` (`export_csv`, zusätzlich auch `export_mf4`)
- **Pass/Fail-Grenzwerte pro Testschritt** — `TestStep.check_enabled/check_min/
  check_max` in `lab_gui/testcase_model.py`, ausgewertet in
  `testcase_runner.py::_finish_step`
- **Nachlauf-Report (HTML/PDF)** — `lab_gui/run_record.py` (`RunRecorder`/
  `RunRecord`) + `lab_gui/run_report.py` (`build_html`, `export_pdf`) mit
  Chart-Einbettung aus `lab_gui/report_chart.py`
- **Schleifen/Wiederholungen im Testcase** — `step_type` inkl. `loop`, `while`,
  `if`, `else`, `end` mit `loop_count` in `testcase_model.py` /
  `testcase_runner.py` (geht sogar über einfache Schleifen hinaus)
- **Software-Watchdog / Sicherheitsabbruch** — `lab_gui/safety.py`
  (`SafetyMonitor`), global (siehe [BUGS.md](BUGS.md) Punkt 2 — soll
  geräte-individuell werden)

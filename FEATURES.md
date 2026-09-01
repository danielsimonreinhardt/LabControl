# Feature-Ideen (gesammelt beim Testen von LabControl v0.6.1)

Keine feste Priorität — nach Bedarf auswählen. Jeder Eintrag kann unabhängig
in einer eigenen Session umgesetzt werden.

**Stand 2026-09-01:** Punkte 1, 2, 3, 4, 8, 9, 11 der ursprünglichen Liste
sind umgesetzt (siehe „Bereits umgesetzte Vorschläge" unten) und daher aus
der offenen Liste entfernt; die verbleibenden Punkte sind neu von 1-4
durchnummeriert.

## 1. Statistik/Auswertung nach Testlauf

Nach einem Testcase-Lauf sollen aus den geloggten Daten berechnet werden:
- Min/Max/Mittelwert pro Signal
- Energie (Wh) bzw. Kapazität (Ah) aus kombinierten Last+PSU-Daten
  (z.B. für Akku-Kapazitätstests)

**Status:** Noch nicht umgesetzt (Stand: Codeprüfung 2026-09-01). Es gibt
aktuell keine Wh/Ah/Mittelwert-Berechnung im Repo — `report_chart.py`
berechnet nur Pixel-lokale Min/Max-Werte für die Chart-Darstellung, keine
Lauf-weiten Summary-Statistiken. Würde sich gut in den bereits vorhandenen
Nachlauf-Report (`lab_gui/run_report.py`) integrieren lassen.


## 2. Weitere Gerätetreiber

Zusätzliche Gerätetreiber über hcs34xx (PSU) und korad_kel102 (Last) hinaus,
z.B.:
- Ein einfaches USB-Multimeter (nur Messen, kein Steuern)
- Ein generischer SCPI/VISA-Treiber für Rigol/Siglent-Geräte

Passt zum "vendor-agnostic"-Anspruch aus der README.

**Status:** Noch nicht umgesetzt — aktuell existieren nur die zwei genannten
Treiber, kein `pyvisa` oder generischer SCPI-Layer.


## 3. Wiederverwendbare Testcase-Bausteine

Häufige Schrittfolgen (z.B. ein Entladeprofil) sollen als Vorlage/Baustein
gespeichert und in andere Testcases eingefügt werden können, statt sie jedes
Mal neu aufzubauen.

**Status:** Noch nicht umgesetzt. Es gibt aktuell nur PSU-Device-Presets
("Preset P1/P2/P3"), aber keinen Mechanismus für wiederverwendbare
Test-Schrittfolgen/Templates.


## 4. [Toolchain/Build] Simulationsmodus nur im Dev-Build

Der Simulationsmodus soll nur im Entwicklungspfad verfügbar sein. Alles, was
released wird, soll die Option nicht mehr anbieten (in Release-Builds
komplett ausgeblendet/nicht anwählbar).

**Zu klären bei Umsetzung:** Wie wird Dev- vs. Release-Build unterschieden
(Build-Flag, Environment-Variable, PyInstaller-Spec-Unterscheidung)?


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
  (`SafetyMonitor`), inzwischen geräte-individuell statt global (siehe
  [BUGS.md](BUGS.md) Punkt 2 — dort inzwischen ebenfalls erledigt)

Umgesetzt in der Session vom 2026-09-01 (v0.7.0/v0.8.0):

- **Hinzufügen-Button: Plus vs. Menü-Pfeil getrennt** — `SplitIconButton`
  (`lab_gui/icons.py`), verwendet im Testablauf-Reiter (`lab_gui/testcase_tab.py`)
- **Individuelle Panel-Hintergrundfarben (Dashboard + Control)** — neues Modul
  `lab_gui/panel_color.py` (`PanelColorButton`, `apply_panel_tint`),
  Farbwerte in `theme.Palette.panel_tints`, an/aus schaltbar im
  Einstellungen-Tab
- **Aufnahme-Start/Stopp zu einem Button vereint** (statisch/blinkend rot) —
  `lab_gui/timeline_tab.py` (`_recording_toggle_button`), dafür
  `IconButton.set_color_override` in `lab_gui/icons.py`
- **Aufnahme zeichnet nur aktivierte Diagramm-Signale auf** —
  `Recorder.set_active_signals` (`lab_gui/recording.py`),
  `TimelineTab.active_signals_changed`/`active_signal_keys`
- **Desktop-Benachrichtigung bei Lauf-Ende/Fehler** — `QSystemTrayIcon` in
  `lab_gui/main_window.py`, an/aus schaltbar im Einstellungen-Tab
- **Arbiträrsignal-Generator: Dreieck-/Sägezahn-Form, Tastgrad bei Rechteck** —
  `lab_gui/testcase_model.py::arb_value`, neues Feld `TestStep.arb_duty`
- **Diagramm-Anzeige: vertikale Gitterlinien + feste Y-Achsen-Skalierung** —
  `lab_gui/timeline_tab.py` (`_ScopeChart.set_y_mode`/`set_fixed_range`,
  neuer `_YAxisDialog`)

Umgesetzt in der Session vom 2026-09-01 (v0.9.0):

- **Startzeit der .exe + Bootloader-Splash** — PyInstaller-`Splash` in
  `LabControl.spec` (Bild aus `tools/generate_splash.py`), geschlossen
  in `lab_gui/main.py` per `pyi_splash.close()`. Zusätzlich: nicht genutzte
  Qt-Submodule (u.a. `QtWebEngineCore`, 205 MB) über `excludes` im `.spec`
  ausgeschlossen — .exe-Größe von 258 MB auf ~92 MB reduziert, das senkt
  auch die Onefile-Entpackzeit bei jedem Start. `LabControl.spec` löst dabei
  alle älteren, pro Version manuell angelegten `.spec`-Dateien ab (liest die
  Versionsnummer dynamisch aus `lab_gui/version.py`) und wird jetzt auch vom
  GitHub-Actions-Workflow verwendet (`pyinstaller LabControl.spec` statt
  bisheriger CLI-Flags ohne Splash/Excludes) — einzige `.spec`-Datei, die
  nicht mehr in `.gitignore` ausgeschlossen ist.
- **Pfeil-Icons für Spin-Buttons** — aus `qtawesome` generiert
  (`mdi.chevron-up`/`-down`) statt statischer PNG-Datei, dadurch größer
  (14×14 statt 10×10px) und themefähig (`lab_gui/theme.py`).
- **Software-Presets im Control-Tab** — neues Modul `lab_gui/presets.py`
  (`PresetStore`, JSON-Persistenz), neue Preset-Zeile (Laden/Speichern/
  Löschen) in `lab_gui/control_tab.py` für Last UND Netzteil. Ersetzt die
  frühere Testablauf-Editor-Aktion "Preset P1/P2/P3 abrufen"
  (`lab_gui/testcase_model.py`, `lab_gui/device_worker.py`) — die alten
  geräteseitigen HCS-34xx-Methoden (`recall_memory` u.a.) bleiben
  unangetastet im Treiber, nur ungenutzt.

"""Kommandozeile ohne Fenster (0.58.0, FT-4): `labcontrol run` und `labcontrol devices`.

    labcontrol run ablauf.json [--report x.html] [--junit x.xml] [--json x.json]
                               [--var name=wert ...] [--simulation] [--wait-devices 15] [--timeout 0]
    labcontrol devices [--simulation] [--wait 5]

Aufruf aus dem Quellcode: python lab_gui/main.py run ...; als .exe: LabControl.exe run ...

Exit-Codes (FP-3 a): 0 bestanden, 1 nicht bestanden, 2 Abbruch/Fehler/Sicherheitsabschaltung,
3 Konfiguration/Geraete fehlen (auch: LabControl laeuft schon), 130 unterbrochen (Strg+C/SIGTERM).
Am Ende eines Laufs immer ALLE AUS (FP-4 a); beim Beenden gibt LabControl die Netzteile wie beim
Schliessen des Fensters mit 1 V / 0 A zur Handbedienung frei.

Laeuft LabControl schon (Kiosk am Pi), oeffnet die Kommandozeile die Geraete nicht selbst (zwei Instanzen
oeffneten dieselben Ports), sondern startet den Lauf ueber die REST-Schnittstelle der laufenden Instanz
(FP-2 c, 0.59.0, instance_cli.py).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

COMMANDS = ("run", "devices", "coverage", "timing", "flash", "history")
DEFAULT_WAIT_DEVICES_S = 15.0
REASON_CLI_END = "cli: Lauf beendet"


def is_cli(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] in COMMANDS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="labcontrol", description="LabControl ohne Fenster")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Testablauf ausführen")
    run.add_argument("testcase", help="Testablauf-Datei (.json)")
    run.add_argument("--report", help="HTML-Bericht hierhin schreiben")
    run.add_argument("--junit", help="JUnit-XML hierhin schreiben")
    run.add_argument("--json", help="run-result.json hierhin schreiben")
    run.add_argument("--var", action="append", default=[], metavar="NAME=WERT",
                     help="Laufvariable vorbelegen (mehrfach)")
    run.add_argument("--variant", default="", metavar="NAME",
                     help="Variante (Name im Ordner 'varianten' oder Datei): Rollen und Parameter (FT-5)")
    run.add_argument("--matrix", metavar="CSV",
                     help="eine Zeile = ein Lauf; Spalte 'variant' optional, sonst Parameter (FT-5)")
    run.add_argument("--simulation", action="store_true", help="simulierte Geräte statt Hardware")
    run.add_argument("--wait-devices", type=float, default=DEFAULT_WAIT_DEVICES_S, metavar="S",
                     help="so lange auf die Geräte des Ablaufs warten (Standard 15 s)")
    run.add_argument("--timeout", type=float, default=0.0, metavar="S",
                     help="Lauf nach S Sekunden abbrechen (0 = ohne)")
    flash = sub.add_parser("flash", help="Firmware über CAN flashen (canflash, PI-54)")
    flash.add_argument("firmware", help="Firmware-Datei (.hex/.srec)")
    flash.add_argument("--wait", type=float, default=0.0, metavar="S",
                       help="so lange auf den Bootloader warten (nach Reset/Aus-Ein von Hand)")
    flash.add_argument("--ecu", default="", help="Projektname bei mehreren Steuergeräten am Bus")
    flash.add_argument("--report", help="HTML-Bericht hierhin schreiben")
    flash.add_argument("--junit", help="JUnit-XML hierhin schreiben")
    flash.add_argument("--json", help="run-result.json hierhin schreiben")
    flash.add_argument("--simulation", action="store_true", help="simulierte Geräte statt Hardware")
    flash.add_argument("--wait-devices", type=float, default=DEFAULT_WAIT_DEVICES_S, metavar="S",
                       help="so lange auf das CAN-Interface warten (Standard 15 s)")
    devices = sub.add_parser("devices", help="verbundene Geräte auflisten")
    devices.add_argument("--simulation", action="store_true")
    devices.add_argument("--wait", type=float, default=5.0, metavar="S", help="Suchdauer (Standard 5 s)")
    coverage = sub.add_parser("coverage", help="Anforderungs-Abdeckung aus den Laufergebnissen (FT-3)")
    coverage.add_argument("--results", action="append", default=[], metavar="ORDNER",
                          help="Ordner mit run_*.json (mehrfach); Standard: Berichtsordner aller Rechner")
    coverage.add_argument("--register", action="append", default=[], metavar="DATEI",
                          help="Anforderungsregister (.sdoc/.csv/.md); Standard: aus den Einstellungen")
    coverage.add_argument("--csv", help="als CSV schreiben")
    coverage.add_argument("--md", help="als Markdown schreiben")
    history = sub.add_parser("history", help="Ergebnis-Übersicht und Trends aus den Laufergebnissen (FT-11)")
    history.add_argument("--results", action="append", default=[], metavar="ORDNER",
                         help="Ordner mit run_*.json (mehrfach); Standard: Berichtsordner aller Rechner")
    history.add_argument("--testcase", help="nur dieser Ablauf (Name wie im Bericht)")
    history.add_argument("--last", type=int, default=50, metavar="N", help="je Ablauf die letzten N Läufe")
    history.add_argument("--html", help="Übersicht mit Trends als HTML schreiben")
    timing = sub.add_parser("timing", help="Zeitprüfung auf einer Aufzeichnung oder Oszi-Erfassung (FT-7)")
    timing.add_argument("file", help="LabControl-Aufzeichnung (.csv/.mf4) oder Oszi-Erfassung (.csv)")
    timing.add_argument("--channel", help="Kanal, z. B. 'Netzteil 1/Spannung' oder 'A' (bei mehreren nötig)")
    timing.add_argument("--list", action="store_true", help="Kanäle der Datei auflisten")
    timing.add_argument("--band", metavar="MIN:MAX", help="Wert bleibt im Band")
    timing.add_argument("--reach", metavar="'OP WERT'", help="Wert erreicht z. B. '>= 11.5' …")
    timing.add_argument("--within", type=float, metavar="S", help="… innerhalb S Sekunden ab --from")
    timing.add_argument("--from", dest="t_from", type=float, metavar="S", help="Fensterbeginn (s ab Aufnahme)")
    timing.add_argument("--to", dest="t_to", type=float, metavar="S", help="Fensterende (s ab Aufnahme)")
    timing.add_argument("--json", help="Ergebnis als JSON hierhin schreiben")
    return parser


def _parse_vars(items: list[str]) -> dict[str, float]:
    result = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"--var {item!r}: erwartet NAME=WERT")
        try:
            result[name.strip()] = float(value.replace(",", "."))
        except ValueError:
            raise ValueError(f"--var {item!r}: Wert ist keine Zahl") from None
    return result


def _say(text: str) -> None:
    print(text, flush=True)


class _Rig:
    """Worker, Sicherheit, Registry und Geraeteverzeichnis wie im Fenster, nur ohne Widgets."""

    def __init__(self, settings, simulation: bool) -> None:
        from PySide6.QtCore import QThread

        from device_registry import DeviceRegistry
        from device_worker import DeviceWorker
        from run_record import RunRecorder
        from run_session import DeviceDirectory, Interlock, RunSession
        from safety import SafetyMonitor

        self.directory = DeviceDirectory()
        self.registry = DeviceRegistry()
        self.recorder = RunRecorder()
        self.registry.device_known.connect(self.recorder.on_device_known)
        self.registry.label_changed.connect(self.recorder.on_label_changed)
        for kind, device_id, label in self.registry.known_devices():
            self.recorder.on_device_known(kind, device_id, label)
        self.thread = QThread()
        self.worker = DeviceWorker(simulation_mode=simulation, can_configs=settings.can_configs,
                                   scpi_scopes=settings.scpi_scopes)
        self.worker.moveToThread(self.thread)
        self.worker.set_battsim_voltage_limits(settings.battsim_voltage_limits)
        self.worker.device_added.connect(self.registry.on_device_added)
        self.worker.device_removed.connect(self.registry.on_device_removed)
        for kind in ("load", "psu", "can", "hil", "fg", "picoscope", "scpiscope", "battsim"):
            getattr(self.worker, f"{kind}_connected").connect(
                lambda device_id, online, kind=kind: self.directory.set_online(kind, device_id, online))
        self.safety = SafetyMonitor(settings.safety_limits)
        self.worker.load_measurement.connect(self.safety.on_load_measurement)
        self.worker.psu_measurement.connect(self.safety.on_psu_measurement)
        self.worker.can_stats.connect(self.safety.on_can_stats)
        self.worker.hil_digital_state.connect(self.safety.on_hil_digital_state)
        self.worker.fg_state.connect(self.safety.on_fg_state)   # Heartbeat (PI-78 a)
        self.worker.battsim_state.connect(self.safety.on_battsim_state)
        self.worker.device_removed.connect(self.safety.on_device_removed)
        self.safety.all_off_requested.connect(self.worker.all_outputs_off)
        self.interlock = Interlock(settings.interlock_groups, self.directory.is_online)
        self.session = RunSession(self.worker, self.safety, self.recorder, self.directory, self.interlock)
        import data_store
        import instruments
        self.instruments = instruments.Instruments(lambda: data_store.store().shared_file(instruments.FILE_NAME))
        instruments.connect_worker(self.worker, self.instruments)
        self.session.instruments = self.instruments
        import requirements
        self.session.requirement_register = lambda: requirements.load_register(settings.requirement_registers)
        # UDS am Pruefling (FT-1): wie im Fenster
        from uds_service import UdsService
        self.uds = UdsService(self.worker, lambda: data_store.store().shared_file("x").parent)
        self.worker.can_connected.connect(self.uds.on_can_connected)
        self.uds.configure(settings.dut_config, simulation)
        self.session.set_uds(self.uds)
        from xcp_service import XcpService
        self.xcp = XcpService(self.worker)
        self.worker.can_connected.connect(self.xcp.on_can_connected)
        self.xcp.configure(settings.dut_config, simulation)
        self.session.set_xcp(self.xcp)
        # Flashen ueber CAN (PI-54): canflash als Unterprozess, Kanal ueber den Worker freigeben
        from canflash_service import CanFlashService
        self.flasher = CanFlashService()
        self.flasher.configure(settings.dut_config, simulation)
        self.flasher.hold_requested.connect(self.worker.hold_can)
        self.worker.can_hold_changed.connect(self.flasher.on_can_hold_changed)
        self.flasher.progress.connect(lambda _r, _p, line: _say(f"  {line.strip()}"))
        self.session.set_flasher(self.flasher)
        self.worker.set_restbus_configs(settings.restbus_configs)   # FT-6: vor dem Start, noch kein Thread
        self.thread.started.connect(self.worker.start)
        self.thread.start()

    def all_off_async(self, reason: str) -> None:
        """ALLE AUS ueber die Warteschlange des Worker-Threads (wie MainWindow._safe_stop)."""
        from PySide6.QtCore import QMetaObject, Q_ARG, Qt

        QMetaObject.invokeMethod(self.worker, "all_outputs_off", Qt.ConnectionType.QueuedConnection,
                                 Q_ARG(str, reason))

    def label(self, device_id: str) -> str:
        return self.recorder._device_meta.get(device_id, ("", device_id))[1]

    def shutdown(self, reason: str) -> None:
        from PySide6.QtCore import QMetaObject, Q_ARG, Qt

        from device_worker import REASON_WINDOW_CLOSE

        self.uds.shutdown()   # UDS-Verbindung schliessen, solange der Bus offen ist
        self.xcp.shutdown()
        # Erst ALLE AUS (FP-4 a), dann wie beim Schliessen des Fensters: Netzteile mit 1 V / 0 A zur
        # Handbedienung frei (das HCS hat keine Ausgangstaste, gesperrt waere es von Hand nicht bedienbar)
        QMetaObject.invokeMethod(self.worker, "all_outputs_off", Qt.ConnectionType.BlockingQueuedConnection,
                                 Q_ARG(str, reason))
        QMetaObject.invokeMethod(self.worker, "all_outputs_off", Qt.ConnectionType.BlockingQueuedConnection,
                                 Q_ARG(str, REASON_WINDOW_CLOSE))
        QMetaObject.invokeMethod(self.worker, "shutdown_can_nodes", Qt.ConnectionType.BlockingQueuedConnection)
        QMetaObject.invokeMethod(self.worker, "stop_timers", Qt.ConnectionType.BlockingQueuedConnection)
        self.thread.quit()
        self.thread.wait(3000)


def _spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return bool(until()) if until is not None else False


def _make_app():
    """QGuiApplication (Bericht rendert Kurven mit QImage). Unter Linux ohne Anzeige offscreen."""
    if sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication(sys.argv[:1])


def _prepare_steps(path: Path, rig: _Rig):
    """(steps, meta, problem): Datei laden, Geraete wie im Editor zuordnen, Verbund-IDs aufloesen."""
    import app_logging
    import data_store
    import verbund_ids
    from testcase_model import load_testcase_linked, remap_devices

    steps, meta, file_devices, problems = load_testcase_linked(path, data_store.target_dir("blocks"))
    for problem in problems:
        _say(problem)
    known = {device_id: (kind, label) for kind, device_id, label in rig.registry.known_devices()}
    own = app_logging.host_name()
    local_ids = set(known)
    for step in steps:
        for attr in ("device_id", "cond_device_id"):
            setattr(step, attr, verbund_ids.localize(getattr(step, attr), own, "", local_ids))
    remap_devices(steps, file_devices, known)
    partner = sorted({getattr(s, a) for s in steps if s.enabled for a in ("device_id", "cond_device_id")
                      if verbund_ids.is_qualified(getattr(s, a))})
    if partner:
        return steps, meta, "Geräte eines Verbund-Partners gehen ohne Fenster nicht: " + ", ".join(partner)
    if any(s.enabled and s.step_type == "action" and s.device_kind == "jlink" for s in steps):
        return steps, meta, "J-Link-Schritte gehen ohne Fenster noch nicht"
    confirms = [i + 1 for i, s in enumerate(steps) if s.enabled and s.step_type == "confirm"]
    if confirms:
        # PI-71 a: niemand kann bestaetigen -- vor dem Start ablehnen, nicht still weiterlaufen
        return steps, meta, (f"Bestätigungsschritt (Schritt {confirms[0]}) braucht das LabControl-Fenster – "
                             "Ablauf dort starten (oder bei laufender Instanz über sie)")
    releases = [i + 1 for i, s in enumerate(steps) if s.enabled and s.step_type == "await_release"]
    if releases:
        # PI-74 a: ohne Fenster laeuft kein Server, ueber den ein Programm freigeben koennte -- vor dem Start ablehnen
        return steps, meta, (f"Warten auf Freigabe (Schritt {releases[0]}) braucht das LabControl-Fenster mit "
                             "Netzwerkfreigabe – Ablauf dort starten (oder bei laufender Instanz über sie)")
    return steps, meta, ""


def _flash_as_run(args) -> Path:
    """`flash` ist ein Lauf mit genau einem Schritt "Firmware flashen (CAN)" -- so gelten Sperre, laufende
    Instanz (FP-2 c), Bericht, JUnit und Exit-Codes wie bei `run`. args wird dafuer umgeschrieben."""
    import tempfile

    from testcase_model import TestStep, save_steps

    step = TestStep(device_kind="uds", device_id="uds:dut", action="UDS_FLASH",
                    params={"file": str(Path(args.firmware).resolve()), "wait": max(0.0, args.wait), "ecu": args.ecu})
    handle, name = tempfile.mkstemp(prefix="labcontrol_flash_", suffix=".json")
    os.close(handle)
    path = Path(name)
    save_steps([step], path, meta={"description": f"Flashen {Path(args.firmware).name}"})
    args.command, args.testcase, args.var, args.timeout = "run", str(path), [], 0.0
    args.variant, args.matrix = "", None
    return path


MATRIX_SEVERITY = (130, 3, 2, 1, 0)   # schlimmster Exit-Code einer Matrix zuerst


def _run_matrix(args, run_one) -> int:
    """`run --matrix datei.csv` (FT-5): je Zeile ein Lauf mit Variante/Parametern der Zeile; Ausgabedateien
    bekommen die Zeilennummer angehaengt. Exit-Code = schlimmster Lauf; Unterbrechen beendet die Matrix."""
    import copy

    import variants

    try:
        rows = variants.read_matrix(Path(args.matrix))
        base_vars = _parse_vars(args.var)
    except (OSError, ValueError) as exc:
        _say(f"Fehler in der Matrix: {exc}")
        return 3
    results = []
    for number, row in enumerate(rows, start=1):
        row_args = copy.copy(args)
        row_args.matrix = None
        row_args.variant = row["variant"] or args.variant
        row_args.var = [f"{k}={v!r}" for k, v in {**base_vars, **row["vars"]}.items()]
        for option in ("json", "junit", "report"):
            value = getattr(args, option)
            if value:
                path = Path(value)
                setattr(row_args, option, str(path.with_name(f"{path.stem}_{number:02d}{path.suffix}")))
        _say(f"=== Matrix {number}/{len(rows)}: {row['label']}")
        code = run_one(row_args)
        results.append((row["label"], code))
        if code == 130:
            break
    _say("=== Matrix-Ergebnis")
    for number, (label, code) in enumerate(results, start=1):
        _say(f"  {number:2d}  Exit {code:3d}  {label}")
    codes = {code for _label, code in results}
    return next((c for c in MATRIX_SEVERITY if c in codes), max(codes) if codes else 3)


def _cmd_devices(args, settings) -> int:
    app = _make_app()
    rig = _Rig(settings, args.simulation)
    _spin(app, max(0.5, args.wait))
    found = sorted((kind, d) for kind, ids in rig.directory.online.items() for d in ids)
    for kind, device_id in found:
        _say(f"{kind:10s} {device_id:32s} {rig.label(device_id)}")
    if not found:
        _say("Keine Geräte gefunden")
    rig.shutdown(REASON_CLI_END)
    return 0


def _cmd_run(args, settings) -> int:
    from run_session import EXIT_ABORTED, EXIT_SETUP, exit_code
    import shutdown_signals

    try:
        variables = _parse_vars(args.var)
    except ValueError as exc:
        _say(f"Fehler: {exc}")
        return EXIT_SETUP
    path = Path(args.testcase)
    if not path.is_file():
        _say(f"Fehler: Datei nicht gefunden: {path}")
        return EXIT_SETUP

    app = _make_app()
    rig = _Rig(settings, args.simulation)
    try:
        steps, meta, problem = _prepare_steps(path, rig)
        variant = None
        if not problem:
            # FT-5 (PI-55 a): Variante laden, Rollen einsetzen, Parameter als Startvariablen
            import data_store
            import variants
            if getattr(args, "variant", ""):
                variant = variants.find_variant(args.variant, data_store.target_dir(variants.FOLDER))
            problems = variants.apply_roles(steps, variant)
            problem = problems[0] if problems else ""
            variables = variants.run_variables(variant, variables)
            meta = dict(meta)
            if variant is not None:
                meta["variant"] = variant.name
                _say(f"Variante: {variant.name}")
            if variables:
                meta["parameters"] = dict(variables)
    except (OSError, ValueError, TypeError) as exc:
        rig.shutdown(REASON_CLI_END)
        _say(f"Fehler beim Laden: {exc}")
        return EXIT_SETUP
    if problem:
        rig.shutdown(REASON_CLI_END)
        _say(f"Fehler: {problem}")
        return EXIT_SETUP

    _say(f"Warte auf Geräte (bis {args.wait_devices:g} s) …")
    def dut_missing() -> list[str]:
        """UDS-/XCP-Schritte (FT-1/FT-10): das CAN-Interface des Prueflings muss verbunden sein (0.64.0)."""
        kinds = {st.device_kind for st in steps if st.enabled and st.step_type == "action"}
        online = rig.directory.online.get("can", set())
        problems = []
        for kind, service in (("uds", rig.uds), ("xcp", rig.xcp)):
            if kind in kinds:
                can = service.can_device()
                if (can and can not in online) or (not can and not online):
                    problems.append(f"CAN-Interface des Prüflings ({can or 'automatisch'}) nicht verbunden")
        return problems

    _spin(app, max(0.0, args.wait_devices), lambda: not rig.directory.missing(steps) and not dut_missing())
    missing = rig.directory.missing(steps) + dut_missing()
    refusal = missing[0] if missing else rig.session.refusal(steps)
    if refusal:
        rig.shutdown(REASON_CLI_END)
        _say(f"Nicht gestartet: {refusal}")
        return EXIT_SETUP

    state = {"interrupted": False, "ended": False, "timed_out": False}

    def interrupt() -> None:
        if state["interrupted"]:
            return
        state["interrupted"] = True
        _say("Unterbrochen – ALLE AUS")
        rig.session.runner.stop()

    shutdown_signals.install(interrupt, app)
    rig.session.run_ended.connect(lambda _outcome: state.update(ended=True))
    rig.session.runner.step_failed.connect(lambda index, message: _say(f"Schritt {index + 1}: {message}"))
    rig.session.runner.step_failed.connect(lambda _i, message: rig.all_off_async(f"step failed: {message}"))
    rig.session.runner.step_result.connect(
        lambda index, ok, value: _say(f"Schritt {index + 1}: {'bestanden' if ok else 'NICHT bestanden'} ({value:g})"))
    rig.safety.tripped.connect(lambda device_id, reason: (_say(f"Sicherheitsabschaltung {device_id}: {reason}"),
                                                         rig.session.runner.stop()))

    name = (meta.get("name") or path.stem)
    _say(f"Start: {name} ({len(steps)} Schritte)")
    rig.session.start(steps, name, meta, variables)
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    while not state["ended"]:
        _spin(app, 0.2, lambda: state["ended"])
        if deadline is not None and time.monotonic() > deadline and not state["timed_out"]:
            state["timed_out"] = True
            rig.session.runner.abort(f"Zeitlimit {args.timeout:g} s überschritten")
    rig.shutdown(REASON_CLI_END)

    record = rig.recorder.record()
    code = exit_code(record, state["interrupted"]) if record is not None else EXIT_ABORTED
    _write_outputs(args, record, state["interrupted"])
    _write_reports(record, state["interrupted"])
    verdict = {0: "BESTANDEN", 1: "NICHT BESTANDEN", 2: "ABGEBROCHEN", 130: "UNTERBROCHEN"}.get(code, str(code))
    _say(f"Ergebnis: {verdict} (Exit {code})")
    return code


def _write_reports(record, interrupted: bool) -> None:
    """Wie im Fenster (FT-11): run-result.json, JUnit und Bericht in den Berichtsordner, der sie in den
    gemeinsamen Ordner schiebt -- so erscheinen auch Nachtlaeufe ohne --json in Uebersicht und Trends."""
    if record is None:
        return
    import data_store
    import run_report
    import run_results

    folder = data_store.store().local_dir("reports")
    stem = f"run_{time.strftime('%Y%m%d-%H%M%S')}-cli"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        run_results.write_json(record, folder / f"{stem}.json", interrupted)
        run_results.write_junit(record, folder / f"{stem}.xml", interrupted)
        run_report.write_html_report(record, folder / f"{stem}.html")
        data_store.store().sync_now()
    except OSError as exc:
        _say(f"Ergebnis nicht im Berichtsordner abgelegt: {exc}")


def _write_outputs(args, record, interrupted: bool) -> None:
    if record is None:
        return
    import run_report
    import run_results

    for option, writer in ((args.json, lambda p: run_results.write_json(record, p, interrupted)),
                           (args.junit, lambda p: run_results.write_junit(record, p, interrupted)),
                           (args.report, lambda p: run_report.write_html_report(record, Path(p)))):
        if not option:
            continue
        try:
            writer(Path(option))
            _say(f"Geschrieben: {option}")
        except OSError as exc:
            _say(f"Konnte {option} nicht schreiben: {exc}")


def _cmd_coverage(args, settings) -> int:
    """Abdeckung der Anforderungen ueber alle Laufergebnisse (FP-24 a: CSV/Markdown)."""
    import app_logging
    import data_store
    import requirements

    folders = [Path(f) for f in args.results]
    if not folders:
        network = app_logging.effective_network_dir(settings.network_dir)
        if network and Path(network).is_dir():
            folders += sorted(p for p in Path(network).glob("*/reports") if p.is_dir())
        folders.append(data_store.store().local_dir("reports"))
    register = requirements.load_register(args.register or settings.requirement_registers)
    results = requirements.collect_results(folders)
    rows = requirements.coverage(results, register)
    for option, render in ((args.csv, requirements.coverage_csv), (args.md, requirements.coverage_markdown)):
        if option:
            Path(option).parent.mkdir(parents=True, exist_ok=True)
            Path(option).write_text(render(rows), encoding="utf-8")
            _say(f"Geschrieben: {option}")
    if not (args.csv or args.md):
        _say(requirements.coverage_markdown(rows))
    counts = {v: sum(1 for r in rows if r.get("verdict") == v) for v in requirements.VERDICT_DE}
    _say(f"{len(rows)} Anforderungen aus {len(results)} Läufen: "
         + ", ".join(f"{counts[v]} {requirements.VERDICT_DE[v]}" for v in requirements.VERDICT_DE))
    return 0


def _cmd_history(args, settings) -> int:
    """Ergebnis-Uebersicht (FT-11): je Ablauf Laeufe, Quote, letztes Ergebnis, wackelnde Pruefungen; --html."""
    import app_logging
    import data_store
    import run_history

    folders = [Path(f) for f in args.results] or run_history.default_folders(
        app_logging.effective_network_dir(settings.network_dir), data_store.store().local_dir("reports"))
    runs = run_history.load_runs(folders)
    names = [name for name, _n in run_history.testcases(runs)]
    if args.testcase:
        if args.testcase not in names:
            _say(f"Keine Läufe von '{args.testcase}' gefunden ({len(runs)} Läufe in {len(folders)} Ordnern)")
            return 3
        names = [args.testcase]
    if not names:
        _say(f"Keine Laufergebnisse in {len(folders)} Ordnern")
    for name in names:
        selected = run_history.for_testcase(runs, name)[-max(1, args.last):]
        last = selected[-1]
        rate = run_history.pass_rate(selected) or 0.0
        outcome = run_history.OUTCOME_DE.get(last.outcome, last.outcome)
        _say(f"{name}: {len(selected)} Läufe, {rate * 100:.0f} % bestanden; zuletzt {outcome} "
             f"({last.started[:16].replace('T', ' ')}, {last.host}{', ' + last.variant if last.variant else ''})")
        for entry in run_history.flaky_checks(selected):
            _say(f"  wackelt: {entry['label']} ({entry['flips']} Wechsel, {entry['passed']}× bestanden, "
                 f"{entry['failed']}× nicht)")
    if args.html:
        Path(args.html).parent.mkdir(parents=True, exist_ok=True)
        Path(args.html).write_text(run_history.html_overview(runs, args.testcase, max(1, args.last)), encoding="utf-8")
        _say(f"Geschrieben: {args.html}")
    return 0


def _cmd_timing(args) -> int:
    """Zeitpruefung auf einer Datei (0.63.1): Exit 0 bestanden, 1 verletzt, 3 Aufruf/Datei falsch."""
    import json
    import timing_files
    from run_session import EXIT_FAILED, EXIT_PASSED, EXIT_SETUP
    try:
        if args.list:
            for name, (times, _values) in sorted(timing_files.read_series(args.file).items()):
                span = f"{times[0]:g}–{times[-1]:g} s" if times else "leer"
                _say(f"{name}  ({len(times)} Werte, {span})")
            return EXIT_PASSED
        if bool(args.band) == bool(args.reach):
            raise timing_files.TimingFileError("genau eine Prüfung angeben: --band MIN:MAX oder --reach 'OP WERT'")
        if args.band:
            lo, sep, hi = args.band.replace(",", ".").partition(":")
            if not sep:
                raise timing_files.TimingFileError("--band erwartet MIN:MAX")
            config = {"mode": "band", "min": float(lo), "max": float(hi)}
        else:
            op, _sep, value = args.reach.strip().partition(" ")
            if args.within is None:
                raise timing_files.TimingFileError("--reach braucht --within S")
            config = {"mode": "reach", "op": op, "value": float(value.replace(",", ".")), "within_s": args.within}
        result = timing_files.check_file(args.file, args.channel, config, args.t_from, args.t_to)
    except (timing_files.TimingFileError, ValueError, OSError) as exc:
        _say(f"Fehler: {exc}")
        return EXIT_SETUP
    resolution = f"{result['resolution_ms']:g} ms" if result["resolution_ms"] is not None else "–"
    _say(f"{result['check']}: {'BESTANDEN' if result['passed'] else 'VERLETZT'} – Wert {result['value']:g}, "
         f"Auflösung {resolution}, {result['samples']} Werte" + (f" – {result['message']}" if result["message"] else ""))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return EXIT_PASSED if result["passed"] else EXIT_FAILED


def main(argv: list[str]) -> int:
    import app_logging
    import data_store
    from instance_lock import InstanceLock
    from settings import Settings
    from version import __version__

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv[1:])
    settings = Settings()
    app_logging.setup_logging(settings.network_dir)
    data_store.setup(app_logging.effective_network_dir(settings.network_dir))
    logger.info("LAB CONTROL v%s Kommandozeile: %s", __version__, " ".join(argv[1:]))
    temp_testcase = _flash_as_run(args) if args.command == "flash" else None
    if args.command in ("coverage", "timing", "history"):   # liest nur Dateien, weder Geraete noch Sperre
        try:
            if args.command == "history":
                return _cmd_history(args, settings)
            return _cmd_coverage(args, settings) if args.command == "coverage" else _cmd_timing(args)
        finally:
            data_store.store().stop()
            app_logging.shutdown()
    lock = InstanceLock()
    if not lock.acquire():
        # LabControl laeuft schon (Kiosk am Pi): ueber dessen REST-Schnittstelle (FP-2 c, 0.59.0)
        holder = lock.holder()
        logger.info("Kommandozeile über die laufende Instanz (%s)", holder or "?")
        try:
            import instance_cli   # erst hier: zieht run_session/cantools nach (Startzeit, 0.60.2)
            if getattr(args, "matrix", None):
                return _run_matrix(args, lambda row_args: instance_cli.main(row_args, settings))
            return instance_cli.main(args, settings)
        finally:
            if temp_testcase is not None:
                temp_testcase.unlink(missing_ok=True)
            app_logging.shutdown()
    try:
        if args.command == "devices":
            return _cmd_devices(args, settings)
        if getattr(args, "matrix", None):
            return _run_matrix(args, lambda row_args: _cmd_run(row_args, settings))
        return _cmd_run(args, settings)
    finally:
        if temp_testcase is not None:
            temp_testcase.unlink(missing_ok=True)
        lock.release()
        data_store.store().stop()
        app_logging.shutdown()

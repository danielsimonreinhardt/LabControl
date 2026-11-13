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

COMMANDS = ("run", "devices", "coverage")
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
    run.add_argument("--simulation", action="store_true", help="simulierte Geräte statt Hardware")
    run.add_argument("--wait-devices", type=float, default=DEFAULT_WAIT_DEVICES_S, metavar="S",
                     help="so lange auf die Geräte des Ablaufs warten (Standard 15 s)")
    run.add_argument("--timeout", type=float, default=0.0, metavar="S",
                     help="Lauf nach S Sekunden abbrechen (0 = ohne)")
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
    import verbund_ids
    from testcase_model import load_device_map, load_testcase, remap_devices

    steps, meta = load_testcase(path)
    known = {device_id: (kind, label) for kind, device_id, label in rig.registry.known_devices()}
    own = app_logging.host_name()
    local_ids = set(known)
    for step in steps:
        for attr in ("device_id", "cond_device_id"):
            setattr(step, attr, verbund_ids.localize(getattr(step, attr), own, "", local_ids))
    remap_devices(steps, load_device_map(path), known)
    partner = sorted({getattr(s, a) for s in steps if s.enabled for a in ("device_id", "cond_device_id")
                      if verbund_ids.is_qualified(getattr(s, a))})
    if partner:
        return steps, meta, "Geräte eines Verbund-Partners gehen ohne Fenster nicht: " + ", ".join(partner)
    if any(s.enabled and s.step_type == "action" and s.device_kind == "jlink" for s in steps):
        return steps, meta, "J-Link-Schritte gehen ohne Fenster noch nicht"
    return steps, meta, ""


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
    except (OSError, ValueError, TypeError) as exc:
        rig.shutdown(REASON_CLI_END)
        _say(f"Fehler beim Laden: {exc}")
        return EXIT_SETUP
    if problem:
        rig.shutdown(REASON_CLI_END)
        _say(f"Fehler: {problem}")
        return EXIT_SETUP

    _say(f"Warte auf Geräte (bis {args.wait_devices:g} s) …")
    _spin(app, max(0.0, args.wait_devices), lambda: not rig.directory.missing(steps))
    missing = rig.directory.missing(steps)
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
    verdict = {0: "BESTANDEN", 1: "NICHT BESTANDEN", 2: "ABGEBROCHEN", 130: "UNTERBROCHEN"}.get(code, str(code))
    _say(f"Ergebnis: {verdict} (Exit {code})")
    return code


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


def main(argv: list[str]) -> int:
    import app_logging
    import data_store
    import instance_cli
    from instance_lock import InstanceLock
    from run_session import EXIT_SETUP
    from settings import Settings
    from version import __version__

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv[1:])
    settings = Settings()
    app_logging.setup_logging(settings.network_dir)
    data_store.setup(app_logging.effective_network_dir(settings.network_dir))
    logger.info("LAB CONTROL v%s Kommandozeile: %s", __version__, " ".join(argv[1:]))
    if args.command == "coverage":   # liest nur Dateien, braucht weder Geraete noch Sperre
        try:
            return _cmd_coverage(args, settings)
        finally:
            data_store.store().stop()
            app_logging.shutdown()
    lock = InstanceLock()
    if not lock.acquire():
        # LabControl laeuft schon (Kiosk am Pi): ueber dessen REST-Schnittstelle (FP-2 c, 0.59.0)
        holder = lock.holder()
        logger.info("Kommandozeile über die laufende Instanz (%s)", holder or "?")
        try:
            return instance_cli.main(args, settings)
        finally:
            app_logging.shutdown()
    try:
        if args.command == "devices":
            return _cmd_devices(args, settings)
        return _cmd_run(args, settings)
    finally:
        lock.release()
        data_store.store().stop()
        app_logging.shutdown()

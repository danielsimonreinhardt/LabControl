"""Sammelt waehrend eines Testlaufs alle Ereignisse und Messwerte als
Grundlage fuer den Nachlauf-Report (siehe run_report.py).

Haengt sich rein passiv an die bestehenden TestRunner-/DeviceWorker-Signale
(siehe main_window._wire_testcase_tab) -- die Signalsignaturen von
TestRunner werden bewusst NICHT geaendert, das wuerde bestehende Slots wie
testcase_tab.on_step_result brechen. Fehlende Zeitstempel/Iterationsangaben
erzeugt dieser Recorder daher selbst beim Empfang der jeweiligen Signale.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Slot

from recording import Sample
from testcase_model import TestStep

# Deckel gegen ausufernde Ereignislisten bei sehr langen/oft wiederholten
# Laeufen (z.B. eine 100000er-Schleife) -- darueber hinausgehende Ereignisse
# werden nur noch gezaehlt (events_dropped), nicht mehr gespeichert.
MAX_EVENTS = 5000


@dataclass
class StepEvent:
    t: float                       # time.time() beim Signalempfang
    kind: str                      # "started" | "result" | "failed"
    index: int                     # Schrittindex in RunRecord.steps
    iteration: int = 0             # innerste gerade aktive Schleife/While (0 = keine)
    iteration_total: int = 0       # 0 = While/unbegrenzt
    passed: bool | None = None     # nur kind=="result"
    measured: float | None = None  # nur kind=="result"
    message: str = ""              # nur kind=="failed"


@dataclass
class RunRecord:
    testcase_name: str = ""
    steps: list[TestStep] = field(default_factory=list)  # Snapshot bei Laufstart
    started_at: float = 0.0
    ended_at: float = 0.0
    # "running" | "passed" | "failed_checks" | "error" | "stopped"
    outcome: str = "running"
    error_index: int = -1
    error_message: str = ""
    events: list[StepEvent] = field(default_factory=list)
    events_dropped: int = 0
    samples: list[Sample] = field(default_factory=list)
    device_meta: dict[str, tuple[str, str]] = field(default_factory=dict)  # device_id -> (kind, label)
    checks_total: int = 0
    checks_failed: int = 0


class RunRecorder(QObject):
    """Passiver Beobachter eines Testlaufs (siehe Moduldoc).

    begin() startet die Aufzeichnung eines neuen Laufs; record() liefert den
    zuletzt begonnenen Lauf zurueck (waehrend er laeuft oder nach Abschluss),
    sonst None. Alle Slots ignorieren Ereignisse, wenn kein Lauf aktiv ist --
    das deckt insbesondere den quittierenden Stop-Klick nach einem bereits
    per step_failed beendeten Lauf ab (der Runner emittiert dabei kein
    run_stopped mehr).
    """

    def __init__(self) -> None:
        super().__init__()
        self._record: RunRecord | None = None
        self._active = False
        self._iteration = 0
        self._iteration_total = 0
        # Ueber die gesamte Session hinweg gesammelt (nicht pro Lauf
        # zurueckgesetzt) und per Referenz an jeden RunRecord gehaengt --
        # device_known/label_changed feuern nur beim ersten Erscheinen bzw.
        # bei Aenderung eines Geraets, nicht erneut bei jedem Testlauf.
        self._device_meta: dict[str, tuple[str, str]] = {}

    def record(self) -> RunRecord | None:
        return self._record

    @property
    def has_record(self) -> bool:
        return self._record is not None

    def begin(self, steps: list[TestStep], testcase_name: str) -> None:
        self._record = RunRecord(
            testcase_name=testcase_name,
            steps=list(steps),
            started_at=time.time(),
            device_meta=self._device_meta,
        )
        self._active = True
        self._iteration = 0
        self._iteration_total = 0

    def _finish(self, outcome: str) -> None:
        if self._record is None:
            return
        self._record.outcome = outcome
        self._record.ended_at = time.time()
        self._active = False

    def _append_event(self, event: StepEvent) -> None:
        record = self._record
        if record is None:
            return
        if len(record.events) >= MAX_EVENTS:
            record.events_dropped += 1
            return
        record.events.append(event)

    # -- TestRunner-Signale ----------------------------------------------------

    @Slot(int, object)
    def on_step_started(self, index: int, _step: TestStep) -> None:
        if not self._active:
            return
        self._append_event(
            StepEvent(
                t=time.time(), kind="started", index=index,
                iteration=self._iteration, iteration_total=self._iteration_total,
            )
        )

    @Slot(int, bool, float)
    def on_step_result(self, index: int, passed: bool, measured: float) -> None:
        if not self._active or self._record is None:
            return
        self._record.checks_total += 1
        if not passed:
            self._record.checks_failed += 1
        self._append_event(
            StepEvent(
                t=time.time(), kind="result", index=index,
                iteration=self._iteration, iteration_total=self._iteration_total,
                passed=passed, measured=measured,
            )
        )

    @Slot(int, str)
    def on_step_failed(self, index: int, message: str) -> None:
        if not self._active or self._record is None:
            return
        self._append_event(StepEvent(t=time.time(), kind="failed", index=index, message=message))
        self._record.error_index = index
        self._record.error_message = message
        self._finish("error")

    @Slot()
    def on_run_finished(self) -> None:
        if not self._active or self._record is None:
            return
        outcome = "failed_checks" if self._record.checks_failed else "passed"
        self._finish(outcome)

    @Slot()
    def on_run_stopped(self) -> None:
        if not self._active:
            return
        self._finish("stopped")

    @Slot(int, int, int)
    def on_iteration_changed(self, _start_index: int, iteration: int, total: int) -> None:
        self._iteration = iteration
        self._iteration_total = total

    # -- Messwerte (DeviceWorker) -----------------------------------------------

    @Slot(str, float, float, float)
    def on_load_measurement(self, device_id: str, voltage: float, current: float, power: float) -> None:
        if not self._active or self._record is None:
            return
        now = time.time()
        self._record.samples.append(Sample(now, device_id, "voltage", voltage))
        self._record.samples.append(Sample(now, device_id, "current", current))
        self._record.samples.append(Sample(now, device_id, "power", power))

    @Slot(str, float, float, bool)
    def on_psu_measurement(
        self, device_id: str, voltage: float, current: float, _constant_current: bool
    ) -> None:
        if not self._active or self._record is None:
            return
        now = time.time()
        self._record.samples.append(Sample(now, device_id, "voltage", voltage))
        self._record.samples.append(Sample(now, device_id, "current", current))
        # Leistung wie ueberall sonst (Runner, Recorder) aus U*I abgeleitet --
        # das HCS-34xx meldet keine eigene Leistungsmessung.
        self._record.samples.append(Sample(now, device_id, "power", voltage * current))

    # -- Geraeteregistrierung (von DeviceRegistry gespeist) ---------------------

    @Slot(str, str, str)
    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        self._device_meta[device_id] = (kind, label)

    @Slot(str, str, str)
    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self._device_meta[device_id] = (kind, label)

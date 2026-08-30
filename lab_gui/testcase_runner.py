"""Fuehrt eine Liste von TestStep-Schritten aus, inklusive Ablaufsteuerung
(Schleifen, If/Else, While, Variablen).

Fuer jeden normalen Aktionsschritt wird die Aktion per Signal an den
DeviceWorker-Thread geschickt und auf dessen Ergebnis (action_completed)
gewartet, bevor die konfigurierte Wartezeit beginnt und der naechste Schritt
startet. Ein Arbiträrsignal-Schritt (siehe testcase_model.is_arb_action)
laeuft stattdessen als Folge einzelner Sollwert-Updates im Abstand von
step.arb_interval_ms, bis step.duration erreicht ist -- die Geraete koennen
keine echten Signalformen ausgeben, nur diskrete Sollwerte annehmen. Der
naechste Sample wird erst verschickt, nachdem der vorherige bestaetigt wurde
(kein "totes" Nachlegen in die Warteschlange, falls ein Geraet langsamer
antwortet als das konfigurierte Intervall).

Kontrollfluss-Schritte (loop/while/if/else/end/set_var/inc_var, siehe
testcase_model.CONTROL_STEP_TYPES) veraendern statt einer Geraete-Aktion nur
den internen Ausfuehrungszustand: einen Stack offener Bloecke (_Frame) sowie
einen einfachen Variablenspeicher. _advance() ist deshalb kein simpler
"naechster Index"-Schritt mehr, sondern eine kleine Interpreterschleife, die
so lange Marker-Schritte synchron abarbeitet, bis entweder ein Aktionsschritt
dispatcht wird oder der Ablauf endet/fehlschlaegt. JEDER Ruecksprung (Ende
einer Schleifeniteration) laeuft ueber einen 0ms-QTimer statt direkt
weiterzuspringen, damit die Qt-Event-Loop dazwischen atmen kann -- sonst
wuerden waehrend einer eng getakteten While-Schleife (leerer/kurzer Rumpf)
weder die GUI reagieren noch die fuer Bedingungen benoetigten
Messwert-Signale (siehe on_load_measurement/on_psu_measurement) ankommen.

Bedingungen (while/if) werden gegen einen kleinen Cache der zuletzt
empfangenen Messwerte ausgewertet (_measurements) -- der Runner haengt sich
dafuer direkt an DeviceWorker.load_measurement/psu_measurement (500ms
Poll-Intervall), analog zu Timeline-Tab/Recorder. Eine veraltete oder fehlende
Messung laesst den Testablauf fehlschlagen (fail-fast) statt still zu warten
oder mit einem stehengebliebenen Wert weiterzurechnen -- bei Akku-Tests darf
eine tote Messleitung den Ablauf nicht unbemerkt fortsetzen.

Schlaegt ein Schritt (oder eine Bedingung) fehl (Geraet nicht verbunden,
Kommunikationsfehler, unbekannte Variable, veraltete Messung, ...), wird der
Ablauf sofort angehalten (step_failed) statt weiterzumachen. Deaktivierte
Schritte werden uebersprungen (bei einem Block-Start ueberspringt das
Deaktivieren den GESAMTEN Block). Dieser Runner laeuft im GUI-Thread,
blockiert ihn aber nicht (Wartezeiten laufen ueber QTimer statt time.sleep).
"""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal, Slot

from i18n import tr
from testcase_model import (
    BlockMatch,
    TestStep,
    arb_value,
    is_arb_action,
    kind_label,
    validate_structure,
)

# Eine Messung, die aelter als dieser Wert ist, gilt als "keine aktuelle
# Messung" (siehe _eval_condition) -- das 4-fache des Poll-Intervalls
# (device_worker.POLL_INTERVAL_MS = 500ms) toleriert einzelne verpasste
# Zyklen, ohne eine tatsaechlich getrennte/eingefrorene Quelle zu uebersehen.
MEASUREMENT_STALE_S = 2.0


@dataclass
class _Frame:
    """Ein offener Kontrollfluss-Block auf dem Ausfuehrungs-Stack."""

    kind: str            # "loop" | "while" | "if"
    start_index: int
    else_index: int | None
    end_index: int
    iteration: int       # 1-basiert
    started: float       # monotonic()-Zeitpunkt des (letzten) Blockeintritts


class TestRunner(QObject):
    execute_action = Signal(str, str, str, float)  # device_id, device_kind, action, value
    step_started = Signal(int, object)         # index, TestStep
    step_failed = Signal(int, str)             # index, Fehlermeldung
    run_finished = Signal()
    run_stopped = Signal()
    # Block-Start-Index, aktueller Durchlauf (1-basiert), Gesamtzahl
    # (0 = unbegrenzt/While) -- fuer die Fortschrittsanzeige im Editor.
    iteration_changed = Signal(int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self._steps: list[TestStep] = []
        self._index = -1
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)

        # Zustand fuer einen laufenden Arbiträrsignal-Schritt.
        self._arb_active = False
        self._arb_clock = QElapsedTimer()
        self._arb_timer = QTimer(self)
        self._arb_timer.setSingleShot(True)
        self._arb_timer.timeout.connect(self._send_arb_sample)

        # -- Ablaufsteuerung ---------------------------------------------
        self._matching: dict[int, BlockMatch] = {}
        self._frames: list[_Frame] = []
        self._vars: dict[str, float] = {}
        self._run_started = 0.0
        # device_id -> (voltage, current, power, monotonic-Zeitstempel).
        self._measurements: dict[str, tuple[float, float, float, float]] = {}

    def is_running(self) -> bool:
        return self._running

    def start(self, steps: list[TestStep]) -> None:
        if self._running:
            return
        self._steps = steps
        matching, _depths, errors = validate_structure(steps)
        if errors:
            # Backstop: der Editor sperrt den Start-Button bereits bei
            # Strukturfehlern, aber Dateien koennen von Hand bearbeitet oder
            # aus einer aelteren Programmversion geladen worden sein.
            index, message = errors[0]
            self.step_failed.emit(index, message)
            return
        self._matching = matching
        self._frames = []
        self._vars = {}
        self._run_started = monotonic()
        self._index = -1
        self._running = True
        self._advance()

    def stop(self) -> None:
        if not self._running:
            return
        self._timer.stop()
        self._arb_timer.stop()
        self._arb_active = False
        self._running = False
        self._frames = []
        self._vars = {}
        self.run_stopped.emit()

    # -- Messwert-Cache (fuer while/if-Bedingungen) ---------------------------

    @Slot(str, float, float, float)
    def on_load_measurement(self, device_id: str, voltage: float, current: float, power: float) -> None:
        self._measurements[device_id] = (voltage, current, power, monotonic())

    @Slot(str, float, float, bool)
    def on_psu_measurement(
        self, device_id: str, voltage: float, current: float, _constant_current: bool
    ) -> None:
        self._measurements[device_id] = (voltage, current, voltage * current, monotonic())

    @Slot(str, str)
    def on_device_removed(self, _kind: str, device_id: str) -> None:
        self._measurements.pop(device_id, None)

    @Slot(bool, str)
    def on_action_completed(self, success: bool, message: str) -> None:
        if not self._running:
            return  # Ergebnis eines bereits gestoppten Laufs -- ignorieren
        if not success:
            self._fail_at(self._index, message)
            return
        if self._arb_active:
            self._continue_arb()
            return
        step = self._steps[self._index]
        self._timer.start(max(0, round(step.duration * 1000)))

    def _fail_at(self, index: int, message: str) -> None:
        self._running = False
        self._arb_active = False
        self.step_failed.emit(index, message)

    # -- Interpreterschleife --------------------------------------------------

    def _current_block_started(self) -> float:
        """monotonic()-Zeitpunkt des Eintritts in den innersten umschliessenden
        Loop/While (fuer cond_time_ref=="block" einer If-Bedingung). Ohne
        umschliessende Schleife bleibt nur der Testablaufstart als Referenz."""
        for frame in reversed(self._frames):
            if frame.kind in ("loop", "while"):
                return frame.started
        return self._run_started

    def _advance(self) -> None:
        if not self._running:
            return

        while True:
            self._index += 1
            if self._index >= len(self._steps):
                self._running = False
                self.run_finished.emit()
                return

            step = self._steps[self._index]
            t = step.step_type

            if t == "action":
                if not step.enabled:
                    continue
                self.step_started.emit(self._index, step)
                if is_arb_action(step.action):
                    self._start_arb(step)
                else:
                    self.execute_action.emit(step.device_id, step.device_kind, step.action, step.value)
                return

            if t in ("set_var", "inc_var"):
                if not step.enabled:
                    continue
                self.step_started.emit(self._index, step)
                if t == "inc_var" and step.var_name not in self._vars:
                    self._fail_at(
                        self._index,
                        tr("Variable '{name}' ist nicht gesetzt", name=step.var_name),
                    )
                    return
                if t == "set_var":
                    self._vars[step.var_name] = step.value
                else:
                    self._vars[step.var_name] = self._vars[step.var_name] + step.value
                if step.duration > 0:
                    self._timer.start(max(0, round(step.duration * 1000)))
                    return
                continue

            if t == "loop":
                match = self._matching[self._index]
                if not step.enabled or step.loop_count < 1:
                    self._index = match.end_index
                    continue
                self._frames.append(_Frame("loop", self._index, None, match.end_index, 1, monotonic()))
                self.iteration_changed.emit(self._index, 1, step.loop_count)
                continue

            if t == "while":
                match = self._matching[self._index]
                if not step.enabled:
                    self._index = match.end_index
                    continue
                # "block" bezieht sich bei einer While-Bedingung auf DIESEN
                # Block selbst (z.B. "solange Zeit seit Blockstart < 2h") --
                # der Referenzzeitpunkt ist deshalb "jetzt", nicht der einer
                # umschliessenden Schleife.
                now = monotonic()
                ok, err = self._eval_condition(step, now)
                if ok is None:
                    self._fail_at(self._index, err)
                    return
                if not ok:
                    self._index = match.end_index
                    continue
                self._frames.append(_Frame("while", self._index, None, match.end_index, 1, now))
                self.iteration_changed.emit(self._index, 1, 0)
                continue

            if t == "if":
                match = self._matching[self._index]
                if not step.enabled:
                    self._index = match.end_index
                    continue
                ok, err = self._eval_condition(step, self._current_block_started())
                if ok is None:
                    self._fail_at(self._index, err)
                    return
                self._frames.append(
                    _Frame("if", self._index, match.else_index, match.end_index, 1, monotonic())
                )
                if not ok:
                    self._index = match.else_index if match.else_index is not None else match.end_index - 1
                continue

            if t == "else":
                # Linear erreicht = Ende des "Dann"-Zweigs -> hinter den Block.
                self._index = self._frames[-1].end_index - 1
                continue

            if t == "end":
                frame = self._frames[-1]
                if frame.kind == "if":
                    self._frames.pop()
                    continue
                start_step = self._steps[frame.start_index]
                if frame.kind == "loop":
                    if frame.iteration >= start_step.loop_count:
                        self._frames.pop()
                        continue
                else:  # "while"
                    if start_step.max_iterations and frame.iteration >= start_step.max_iterations:
                        self._fail_at(
                            frame.start_index,
                            tr(
                                "Maximale Anzahl Durchläufe erreicht ({n}) -- "
                                "Endlosschleife? Siehe 'Max. Durchläufe' in der Bedingung.",
                                n=start_step.max_iterations,
                            ),
                        )
                        return
                    ok, err = self._eval_condition(start_step, frame.started)
                    if ok is None:
                        self._fail_at(frame.start_index, err)
                        return
                    if not ok:
                        self._frames.pop()
                        continue
                frame.iteration += 1
                self.iteration_changed.emit(
                    frame.start_index,
                    frame.iteration,
                    start_step.loop_count if frame.kind == "loop" else 0,
                )
                self._index = frame.start_index
                # Ruecksprung: Event-Loop atmen lassen (siehe Moduldoc oben),
                # statt synchron in der naechsten Iteration weiterzumachen.
                self._timer.start(0)
                return

            # Unbekannter step_type (z.B. aus einer neueren Programmversion
            # geladen) -- als No-Op ueberspringen statt abzustuerzen.
            continue

    # -- Bedingungsauswertung --------------------------------------------------

    @staticmethod
    def _compare(a: float, op: str, b: float) -> bool:
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        return False

    def _eval_condition(self, step: TestStep, block_started: float) -> tuple[bool | None, str]:
        """Wertet die Bedingung eines while/if-Schritts aus.

        Rueckgabe (None, fehlermeldung) bei einem Auswertungsfehler (fehlende/
        veraltete Messung, mehrdeutiges Zielgeraet, unbekannte Variable) --
        das laesst den Ablauf ueber _fail_at() sofort und sichtbar scheitern,
        statt eine Bedingung stillschweigend als falsch zu behandeln.
        """
        if step.cond_source == "measurement":
            device_id = step.cond_device_id
            if device_id:
                entry = self._measurements.get(device_id)
                if entry is None or (monotonic() - entry[3]) > MEASUREMENT_STALE_S:
                    return None, tr(
                        "Keine aktuelle Messung für Gerät '{device_id}'", device_id=device_id
                    )
            else:
                prefix = f"{step.cond_device_kind}:"
                candidates = [
                    did
                    for did, entry in self._measurements.items()
                    if did.startswith(prefix) and (monotonic() - entry[3]) <= MEASUREMENT_STALE_S
                ]
                if not candidates:
                    return None, tr(
                        "Keine aktuelle Messung für ein Gerät vom Typ '{kind}'",
                        kind=kind_label(step.cond_device_kind),
                    )
                if len(candidates) > 1:
                    return None, tr(
                        "Mehrere Geräte vom Typ '{kind}' verbunden -- bitte Zielgerät "
                        "in der Bedingung auswählen",
                        kind=kind_label(step.cond_device_kind),
                    )
                device_id = candidates[0]
            voltage, current, power, _ts = self._measurements[device_id]
            value = {"voltage": voltage, "current": current, "power": power}[step.cond_field]
            return self._compare(value, step.cond_op, step.cond_value), ""

        if step.cond_source == "time":
            reference = block_started if step.cond_time_ref == "block" else self._run_started
            elapsed = monotonic() - reference
            return self._compare(elapsed, step.cond_op, step.cond_value), ""

        if step.cond_source == "variable":
            if step.cond_var not in self._vars:
                return None, tr("Variable '{name}' ist nicht gesetzt", name=step.cond_var)
            return self._compare(self._vars[step.cond_var], step.cond_op, step.cond_value), ""

        return None, tr("Unbekannte Bedingungsquelle '{source}'", source=step.cond_source)

    # -- Arbiträrsignal-Ausfuehrung --------------------------------------------

    def _start_arb(self, step: TestStep) -> None:
        self._arb_active = True
        self._arb_clock.start()
        self._send_arb_sample()

    def _send_arb_sample(self) -> None:
        step = self._steps[self._index]
        t = self._arb_clock.elapsed() / 1000.0
        value = arb_value(step, t)
        self.execute_action.emit(step.device_id, step.device_kind, step.arb_target, value)

    def _continue_arb(self) -> None:
        step = self._steps[self._index]
        elapsed_s = self._arb_clock.elapsed() / 1000.0
        if elapsed_s >= step.duration:
            self._arb_active = False
            self._advance()
            return
        self._arb_timer.start(max(1, step.arb_interval_ms))

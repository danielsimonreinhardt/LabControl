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

Kontrollfluss-Schritte (loop/while/if/else/end/set_var/inc_var/wait, siehe
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

Pass/Fail-Pruefungen (step.check_enabled, siehe testcase_model.TestStep):
Nach Ablauf der Wartezeit eines Aktionsschritts (bzw. nach dem Signalende
eines Arbiträrsignal-Schritts) wird NICHT der Cache-Stand bewertet -- der
kann bis zu ein Poll-Intervall alt sein und bei kurzer Wartezeit noch von
VOR dem Setzen des Sollwerts stammen (falsches PASS). Stattdessen wird eine
"pending"-Pruefung armiert und die naechste danach eintreffende Messung des
Zielgeraets bewertet (on_load_measurement/on_psu_measurement ->
_maybe_complete_check, in der Praxis <= 500ms spaeter). Bleibt die Messung
laenger als MEASUREMENT_STALE_S aus (Geraet tot/getrennt), schlaegt der
Schritt fehl -- dieselbe fail-fast-Semantik wie bei Bedingungen.

Schlaegt ein Schritt (oder eine Bedingung) fehl (Geraet nicht verbunden,
Kommunikationsfehler, unbekannte Variable, veraltete Messung, ...), wird der
Ablauf sofort angehalten (step_failed) statt weiterzumachen. Eine verletzte
Pass/Fail-Pruefung stoppt den Ablauf dagegen nur, wenn der Schritt das
ausdruecklich verlangt (check_abort) -- sonst wird das Ergebnis nur per
step_result gemeldet (rote Zeile im Editor) und weitergemacht. Deaktivierte
Schritte werden uebersprungen (bei einem Block-Start ueberspringt das
Deaktivieren den GESAMTEN Block). Dieser Runner laeuft im GUI-Thread,
blockiert ihn aber nicht (Wartezeiten laufen ueber QTimer statt time.sleep).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal, Slot

from device_worker import PICOSCOPE_RETRY_MESSAGE
from i18n import tr
from testcase_model import (
    COND_FIELD_UNITS,
    READ_ACTIONS,
    BlockMatch,
    TestStep,
    arb_value,
    is_arb_action,
    kind_label,
    validate_structure,
)

# Eine Messung, die aelter als dieser Wert ist, gilt als "keine aktuelle
# Messung" (siehe _eval_condition) -- bewusst als absoluter Wert (deutlich
# groesser als device_worker.POLL_INTERVAL_MS) statt an das Poll-Intervall
# gekoppelt, toleriert einzelne verpasste/verzoegerte Zyklen, ohne eine
# tatsaechlich getrennte/eingefrorene Quelle zu uebersehen.
MEASUREMENT_STALE_S = 2.0

# Verzoegerung vor einem automatischen Retry nach PICOSCOPE_RETRY_MESSAGE
# (siehe device_worker._execute_picoscope_action) -- muss laenger sein als
# ein voller PicoScope-Reconnect-Zyklus (~4,5s, siehe device_worker.
# PICOSCOPE_RECONNECT_INTERVAL_MS-Kommentar), damit der Retry mit hoher
# Wahrscheinlichkeit NICHT mehr in denselben verschachtelten Aufruf laeuft,
# der den ersten Versuch scheitern liess. Begrenzte Anzahl Versuche, damit
# eine dauerhaft haengende Situation den Testlauf nicht endlos blockiert,
# sondern irgendwann regulaer fehlschlaegt.
PICOSCOPE_RETRY_DELAY_S = 6.0
PICOSCOPE_MAX_RETRIES = 3

logger = logging.getLogger(__name__)


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
    execute_action = Signal(str, str, str, float, int)  # device_id, device_kind, action, value, channel
    # device_id, arbitration_id, data (Hex-String), extended -- eigener Signal-
    # Pfad statt execute_action, da eine CAN-ID + Datenbytes nicht in dessen
    # einzelnen float-Wert passen (siehe testcase_model.py: can_id/can_data).
    execute_can_send = Signal(str, int, str, bool)
    step_started = Signal(int, object)         # index, TestStep
    step_failed = Signal(int, str)             # index, Fehlermeldung
    # Ergebnis einer Pass/Fail-Pruefung: index, bestanden, Messwert. Wird auch
    # bei check_abort VOR dem step_failed emittiert, damit der Editor die
    # Zeile in jedem Fall einfaerben kann.
    step_result = Signal(int, bool, float)
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

        # Eigener Timer fuer die Wartezeit NACH einem Aktionsschritt --
        # bewusst getrennt von self._timer, der auch fuer die 0ms-Ruecksprünge
        # am Ende einer Schleifeniteration und die set_var-Wartezeit dient:
        # diese Pfade muessen direkt in _advance() muenden, waehrend der
        # Aktionsschritt-Abschluss ueber _finish_step() laeuft (dort haengt
        # die optionale Pass/Fail-Pruefung).
        self._wait_timer = QTimer(self)
        self._wait_timer.setSingleShot(True)
        self._wait_timer.timeout.connect(self._finish_step)

        # Zustand einer armierten Pass/Fail-Pruefung: (Schrittindex,
        # aufgeloeste device_id), None wenn keine Pruefung wartet. Der Timeout
        # greift, wenn nach Ablauf der Wartezeit keine Messung des Zielgeraets
        # mehr eintrifft (Geraet tot/getrennt).
        self._pending_check: tuple[int, str] | None = None
        self._check_timeout = QTimer(self)
        self._check_timeout.setSingleShot(True)
        self._check_timeout.timeout.connect(self._on_check_timeout)

        # Zustand fuer einen laufenden Arbiträrsignal-Schritt.
        self._arb_active = False
        self._arb_clock = QElapsedTimer()
        self._arb_timer = QTimer(self)
        self._arb_timer.setSingleShot(True)
        self._arb_timer.timeout.connect(self._send_arb_sample)

        # Automatischer Retry nach PICOSCOPE_RETRY_MESSAGE (siehe deren
        # Kommentar in device_worker.py) -- Anzahl bereits versuchter
        # Wiederholungen fuer den AKTUELLEN Schritt, zurueckgesetzt bei jedem
        # neuen Aktionsschritt (siehe _advance).
        self._pico_retry_count = 0
        self._pico_retry_timer = QTimer(self)
        self._pico_retry_timer.setSingleShot(True)
        self._pico_retry_timer.timeout.connect(self._retry_current_action)

        # -- Ablaufsteuerung ---------------------------------------------
        self._matching: dict[int, BlockMatch] = {}
        self._frames: list[_Frame] = []
        self._vars: dict[str, float] = {}
        self._run_started = 0.0
        # device_id -> (voltage, current, power, monotonic-Zeitstempel).
        self._measurements: dict[str, tuple[float, float, float, float]] = {}
        # microHIL-Kanaele als while/if-Bedingungsquelle (BUGS_GESCHLOSSEN.md
        # #35) -- device_id -> (AIN1-4 in mV, IN1-8, monotonic-Zeitstempel),
        # analog zu _measurements oben, aber ueber eigene Signale gefuellt
        # (on_hil_analog_input/on_hil_digital_state) statt load_measurement/
        # psu_measurement, siehe _eval_hil_condition.
        self._hil_measurements: dict[str, tuple[list[float], list[bool], float]] = {}
        # CAN-Signale als while/if-Bedingungsquelle (BUGS_GESCHLOSSEN.md #35)
        # -- Schluessel "{device_id}:{message_name}.{signal_name}" (Punkt
        # trennt Nachricht/Signal, Doppelpunkt Geraet/Signalpfad, siehe
        # on_can_signals_decoded/_eval_can_condition), Wert (dekodierter
        # Zahlenwert, monotonic-Zeitstempel). Nicht-numerische DBC-Value-
        # Table-Signale (str) werden nicht aufgenommen, sind als Bedingung
        # nicht sinnvoll vergleichbar.
        self._can_signals: dict[str, tuple[float, float]] = {}
        # Zuletzt von einer Lese-Aktion (READ_ACTIONS -- microHIL oder
        # PicoScope) gelesener Wert -- anders als _measurements kein Cache
        # mehrerer Geraete/Kanaele, da immer nur EIN Lese-Schritt gleichzeitig
        # laufen kann und der Wert sofort (noch in derselben Ausfuehrung)
        # durch _finish_step verbraucht wird (siehe dort).
        self._last_read_value = 0.0

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
        self._pending_check = None
        self._run_started = monotonic()
        self._index = -1
        self._running = True
        self._advance()

    def stop(self) -> None:
        if not self._running:
            return
        self._timer.stop()
        self._wait_timer.stop()
        self._check_timeout.stop()
        self._pending_check = None
        self._arb_timer.stop()
        self._arb_active = False
        self._pico_retry_timer.stop()
        self._pico_retry_count = 0
        self._running = False
        self._frames = []
        self._vars = {}
        self.run_stopped.emit()

    # -- Messwert-Cache (fuer while/if-Bedingungen) ---------------------------

    @Slot(str, float, float, float)
    def on_load_measurement(self, device_id: str, voltage: float, current: float, power: float) -> None:
        self._measurements[device_id] = (voltage, current, power, monotonic())
        self._maybe_complete_check(device_id)

    @Slot(str, float, float, bool)
    def on_psu_measurement(
        self, device_id: str, voltage: float, current: float, _constant_current: bool
    ) -> None:
        self._measurements[device_id] = (voltage, current, voltage * current, monotonic())
        self._maybe_complete_check(device_id)

    @Slot(str, list)
    def on_hil_analog_input(self, device_id: str, values_mv: list) -> None:
        _ain, digital_in, _ts = self._hil_measurements.get(device_id, ([], [], 0.0))
        self._hil_measurements[device_id] = (list(values_mv), digital_in, monotonic())

    @Slot(str, list, list)
    def on_hil_digital_state(self, device_id: str, inputs: list, outputs: list) -> None:
        # outputs bewusst NICHT gecacht -- BUGS_OFFEN.md #35 fragte nur nach
        # Digital-/AnalogEINgang als Bedingungsquelle (der Ausgangszustand
        # ist ohnehin von der App selbst gesetzt, also bereits bekannt).
        ain, _digital_in, _ts = self._hil_measurements.get(device_id, ([], [], 0.0))
        self._hil_measurements[device_id] = (ain, list(inputs), monotonic())

    @Slot(str, int, object)
    def on_can_signals_decoded(self, device_id: str, arbitration_id: int, decoded) -> None:
        now = monotonic()
        for sig in decoded.signals:
            if isinstance(sig.value, str):
                continue  # DBC-Value-Table-Enum, nicht sinnvoll vergleichbar
            self._can_signals[f"{device_id}:{decoded.message_name}.{sig.name}"] = (float(sig.value), now)

    @Slot(str, str)
    def on_device_removed(self, _kind: str, device_id: str) -> None:
        self._measurements.pop(device_id, None)
        self._hil_measurements.pop(device_id, None)
        prefix = f"{device_id}:"
        for key in [k for k in self._can_signals if k.startswith(prefix)]:
            del self._can_signals[key]

    @Slot(bool, str, float)
    def on_action_completed(self, success: bool, message: str, value: float) -> None:
        if not self._running:
            return  # Ergebnis eines bereits gestoppten Laufs -- ignorieren
        if not success:
            if message == PICOSCOPE_RETRY_MESSAGE:
                self._retry_pico_action()
                return
            self._fail_at(self._index, message)
            return
        self._pico_retry_count = 0
        if self._arb_active:
            self._continue_arb()
            return
        step = self._steps[self._index]
        if step.action in READ_ACTIONS:
            self._last_read_value = value
            if step.store_var:
                # Macht die Lesung (microHIL oder PicoScope) als while/if-
                # Bedingung nutzbar (dort cond_source="variable"), unabhaengig
                # von der separaten Pass/Fail-Pruefung in _finish_step.
                self._vars[step.store_var] = value
        self._wait_timer.start(max(0, round(step.duration * 1000)))

    def _retry_pico_action(self) -> None:
        """Reagiert auf PICOSCOPE_RETRY_MESSAGE (siehe deren Kommentar in
        device_worker.py): kein echter Fehler, sondern eine erkannte
        Verschachtelung mit einem noch laufenden PicoScope-Reconnect-Tick --
        nach PICOSCOPE_RETRY_DELAY_S automatisch derselbe Schritt erneut,
        begrenzt auf PICOSCOPE_MAX_RETRIES Versuche, damit eine dauerhaft
        haengende Situation den Lauf nicht endlos blockiert."""
        self._pico_retry_count += 1
        if self._pico_retry_count > PICOSCOPE_MAX_RETRIES:
            self._pico_retry_count = 0
            logger.warning(
                "PicoScope-Schritt %d nach %d Versuchen aufgegeben (Reconnect-Kollision)",
                self._index, PICOSCOPE_MAX_RETRIES,
            )
            self._fail_at(
                self._index,
                tr("Oszilloskop dauerhaft belegt (Reconnect kollidiert wiederholt)"),
            )
            return
        logger.info(
            "PicoScope-Schritt %d kollidierte mit Reconnect-Tick, Retry %d/%d in %.0fs",
            self._index, self._pico_retry_count, PICOSCOPE_MAX_RETRIES, PICOSCOPE_RETRY_DELAY_S,
        )
        self._pico_retry_timer.start(round(PICOSCOPE_RETRY_DELAY_S * 1000))

    def _retry_current_action(self) -> None:
        if not self._running:
            return
        step = self._steps[self._index]
        self.execute_action.emit(step.device_id, step.device_kind, step.action, step.value, step.hil_channel)

    def _fail_at(self, index: int, message: str) -> None:
        self._running = False
        self._arb_active = False
        self._wait_timer.stop()
        self._check_timeout.stop()
        self._pending_check = None
        self._pico_retry_timer.stop()
        self._pico_retry_count = 0
        self.step_failed.emit(index, message)

    # -- Pass/Fail-Pruefung nach der Wartezeit ---------------------------------

    def _finish_step(self) -> None:
        """Abschluss eines Aktionsschritts nach dessen Wartezeit (bzw. nach
        dem Signalende eines ARB-Schritts): ohne Pruefung direkt weiter, sonst
        Pruefung armieren und auf die naechste Messung des Zielgeraets warten
        (siehe Moduldoc)."""
        if not self._running:
            return
        step = self._steps[self._index]
        if not step.check_enabled:
            self._advance()
            return
        if step.action in READ_ACTIONS:
            # Der Wert kam mit der Aktion selbst (siehe on_action_completed),
            # KEINE Messwert-Cache-Quelle wie bei Last/Netzteil -- also direkt
            # auswerten statt eine "pending"-Pruefung auf die naechste
            # eintreffende Messung zu armieren (die es fuer microHIL-/
            # PicoScope-Kanaele hier gar nicht gibt).
            value = self._last_read_value
            passed = step.check_min <= value <= step.check_max
            self.step_result.emit(self._index, passed, value)
            if not passed and step.check_abort:
                self._fail_at(
                    self._index,
                    tr("Messwert {value:g} außerhalb {lo:g}–{hi:g}", value=value, lo=step.check_min, hi=step.check_max),
                )
                return
            self._advance()
            return
        device_id = step.device_id
        if not device_id:
            device_id, status = self._resolve_fresh_device(step.device_kind)
            if device_id is None:
                if status == "ambiguous":
                    message = tr(
                        "Mehrere Geräte vom Typ '{kind}' verbunden -- bitte Zielgerät "
                        "in der Testcase-Zeile auswählen",
                        kind=kind_label(step.device_kind),
                    )
                else:
                    message = tr(
                        "Keine aktuelle Messung für ein Gerät vom Typ '{kind}'",
                        kind=kind_label(step.device_kind),
                    )
                self._fail_at(self._index, message)
                return
        self._pending_check = (self._index, device_id)
        self._check_timeout.start(round(MEASUREMENT_STALE_S * 1000))

    def _maybe_complete_check(self, device_id: str) -> None:
        if not self._running or self._pending_check is None:
            return
        if device_id != self._pending_check[1]:
            return
        index, _ = self._pending_check
        self._pending_check = None
        self._check_timeout.stop()
        step = self._steps[index]
        voltage, current, power, _ts = self._measurements[device_id]
        values = {"voltage": voltage, "current": current, "power": power}
        if step.check_field not in values:
            # Von Hand editierte/zukuenftige Datei -- klarer Fehler statt KeyError.
            self._fail_at(index, tr("Unbekannte Messgröße '{field}'", field=step.check_field))
            return
        value = values[step.check_field]
        passed = step.check_min <= value <= step.check_max
        self.step_result.emit(index, passed, value)
        if not passed and step.check_abort:
            unit = COND_FIELD_UNITS.get(step.check_field, "")
            self._fail_at(
                index,
                tr(
                    "Messwert {value:g} {unit} außerhalb {lo:g}–{hi:g} {unit}",
                    value=value, unit=unit, lo=step.check_min, hi=step.check_max,
                ),
            )
            return
        self._advance()

    def _on_check_timeout(self) -> None:
        if not self._running or self._pending_check is None:
            return
        index, device_id = self._pending_check
        self._pending_check = None
        self._fail_at(index, tr("Keine aktuelle Messung für Gerät '{device_id}'", device_id=device_id))

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
                self._pico_retry_count = 0
                self.step_started.emit(self._index, step)
                if is_arb_action(step.action):
                    self._start_arb(step)
                elif step.action == "CAN_SEND":
                    self.execute_can_send.emit(step.device_id, step.can_id, step.can_data, step.can_extended)
                else:
                    self.execute_action.emit(
                        step.device_id, step.device_kind, step.action, step.value, step.hil_channel
                    )
                return

            if t == "wait":
                if not step.enabled:
                    continue
                self.step_started.emit(self._index, step)
                if step.duration > 0:
                    self._timer.start(max(0, round(step.duration * 1000)))
                    return
                continue

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
            # microHIL/CAN haben eigene Cache-Strukturen (siehe __init__/
            # on_hil_analog_input/on_can_signals_decoded) statt _measurements
            # -- BUGS_GESCHLOSSEN.md #35.
            if step.cond_device_kind == "hil":
                return self._eval_hil_condition(step)
            if step.cond_device_kind == "can":
                return self._eval_can_condition(step)
            device_id = step.cond_device_id
            if device_id:
                entry = self._measurements.get(device_id)
                if entry is None or (monotonic() - entry[3]) > MEASUREMENT_STALE_S:
                    return None, tr(
                        "Keine aktuelle Messung für Gerät '{device_id}'", device_id=device_id
                    )
            else:
                device_id, status = self._resolve_fresh_device(step.cond_device_kind)
                if device_id is None:
                    if status == "ambiguous":
                        return None, tr(
                            "Mehrere Geräte vom Typ '{kind}' verbunden -- bitte Zielgerät "
                            "in der Bedingung auswählen",
                            kind=kind_label(step.cond_device_kind),
                        )
                    return None, tr(
                        "Keine aktuelle Messung für ein Gerät vom Typ '{kind}'",
                        kind=kind_label(step.cond_device_kind),
                    )
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

    def _eval_hil_condition(self, step: TestStep) -> tuple[bool | None, str]:
        """microHIL-Kanal (Analog- oder Digitaleingang) als while/if-
        Bedingungsquelle (BUGS_GESCHLOSSEN.md #35) -- liest aus
        _hil_measurements statt _measurements, sonst analog zum measurement-
        Zweig oben (gleiche "automatisch"-Aufloesung/Frische-Pruefung)."""
        device_id = step.cond_device_id
        if device_id:
            entry = self._hil_measurements.get(device_id)
            if entry is None or (monotonic() - entry[2]) > MEASUREMENT_STALE_S:
                return None, tr(
                    "Keine aktuelle Messung für Gerät '{device_id}'", device_id=device_id
                )
        else:
            device_id, status = self._resolve_fresh_device("hil", self._hil_measurements)
            if device_id is None:
                if status == "ambiguous":
                    return None, tr(
                        "Mehrere Geräte vom Typ '{kind}' verbunden -- bitte Zielgerät "
                        "in der Bedingung auswählen", kind=kind_label("hil"),
                    )
                return None, tr(
                    "Keine aktuelle Messung für ein Gerät vom Typ '{kind}'", kind=kind_label("hil"),
                )
        ain, digital_in, _ts = self._hil_measurements[device_id]
        values = ain if step.cond_field == "hil_ain" else digital_in if step.cond_field == "hil_in" else None
        if values is None:
            return None, tr("Unbekannte Messgröße '{field}' für microHIL", field=step.cond_field)
        channel = step.hil_channel
        if not (1 <= channel <= len(values)):
            return None, tr("Ungültiger Kanal {channel} für microHIL", channel=channel)
        return self._compare(float(values[channel - 1]), step.cond_op, step.cond_value), ""

    def _eval_can_condition(self, step: TestStep) -> tuple[bool | None, str]:
        """DBC-decodiertes CAN-Signal als while/if-Bedingungsquelle
        (BUGS_GESCHLOSSEN.md #35) -- `cond_field` ist hier (anders als bei
        load/psu/hil) keiner der festen COND_FIELDS-Codes, sondern der frei
        eingegebene Signalpfad "Nachricht.Signal" (siehe condition_dialog.py:
        ConditionDialog, Feld nur fuer cond_device_kind=="can" sichtbar).
        Liest aus _can_signals (gefuellt von on_can_signals_decoded)."""
        signal = step.cond_field.strip()
        if not signal:
            return None, tr("Kein CAN-Signal angegeben (Format: Nachricht.Signal)")
        device_id = step.cond_device_id
        if device_id:
            entry = self._can_signals.get(f"{device_id}:{signal}")
            if entry is None or (monotonic() - entry[1]) > MEASUREMENT_STALE_S:
                return None, tr(
                    "Kein aktueller Wert für CAN-Signal '{signal}' auf Gerät '{device_id}'",
                    signal=signal, device_id=device_id,
                )
        else:
            suffix = f":{signal}"
            fresh = [
                (key, entry) for key, entry in self._can_signals.items()
                if key.startswith("can:") and key.endswith(suffix)
                and (monotonic() - entry[1]) <= MEASUREMENT_STALE_S
            ]
            if not fresh:
                return None, tr(
                    "Kein aktueller Wert für CAN-Signal '{signal}' auf einem verbundenen "
                    "CAN-Interface", signal=signal,
                )
            device_ids = {key[: -len(suffix)] for key, _entry in fresh}
            if len(device_ids) > 1:
                return None, tr(
                    "CAN-Signal '{signal}' auf mehreren verbundenen Interfaces gesehen -- "
                    "bitte Zielgerät in der Bedingung auswählen", signal=signal,
                )
            entry = fresh[0][1]
        value, _ts = entry
        return self._compare(value, step.cond_op, step.cond_value), ""

    def _resolve_fresh_device(
        self, kind: str, measurements: dict | None = None
    ) -> tuple[str | None, str]:
        """Loest device_id=="" ("automatisch") auf das einzige Geraet der Art
        auf, von dem eine frische Messung vorliegt.

        `measurements` erlaubt die Wiederverwendung fuer _hil_measurements
        (BUGS_GESCHLOSSEN.md #35, siehe _eval_hil_condition) -- default
        bleibt _measurements (load/psu). Beide Cache-Formen tragen den
        monotonic-Zeitstempel als LETZTES Tupel-Element (entry[-1]).

        Rueckgabe (device_id, "") bei Erfolg, sonst (None, "missing") wenn
        kein Kandidat bzw. (None, "ambiguous") bei mehreren -- die
        Fehlermeldung baut der Aufrufer, weil der passende Loesungshinweis
        vom Kontext abhaengt (Bedingungs-Dialog vs. Testcase-Zeile).
        """
        if measurements is None:
            measurements = self._measurements
        prefix = f"{kind}:"
        candidates = [
            did
            for did, entry in measurements.items()
            if did.startswith(prefix) and (monotonic() - entry[-1]) <= MEASUREMENT_STALE_S
        ]
        if not candidates:
            return None, "missing"
        if len(candidates) > 1:
            return None, "ambiguous"
        return candidates[0], ""

    # -- Arbiträrsignal-Ausfuehrung --------------------------------------------

    def _start_arb(self, step: TestStep) -> None:
        self._arb_active = True
        self._arb_clock.start()
        self._send_arb_sample()

    def _send_arb_sample(self) -> None:
        step = self._steps[self._index]
        t = self._arb_clock.elapsed() / 1000.0
        value = arb_value(step, t)
        self.execute_action.emit(step.device_id, step.device_kind, step.arb_target, value, step.hil_channel)

    def _continue_arb(self) -> None:
        step = self._steps[self._index]
        elapsed_s = self._arb_clock.elapsed() / 1000.0
        if elapsed_s >= step.duration:
            self._arb_active = False
            # Ueber _finish_step statt _advance, damit auch ein ARB-Schritt
            # eine Pass/Fail-Pruefung tragen kann (gemessen wird dann der
            # Zustand nach dem Signalende).
            self._finish_step()
            return
        self._arb_timer.start(max(1, step.arb_interval_ms))

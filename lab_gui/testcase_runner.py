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

Kontrollfluss-Schritte (loop/while/if/else/end/set_var/inc_var/wait, seit 1.0.1 auch
confirm/wait_since -- Bestaetigung im Fenster bzw. Mindestzeit seit einer Aktion, PI-71 a -- und
await_release -- Freigabe durch ein Programm ueber REST/MCP mit Hoechstzeit, PI-74 a; siehe
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
import math
import timing_checks
from dataclasses import dataclass
import time
from time import monotonic

from PySide6.QtCore import QElapsedTimer, QObject, Qt, QTimer, Signal, Slot

from device_worker import PICOSCOPE_RETRY_MESSAGE
from i18n import tr
from variants import resolve_refs
from scope_steps import SCOPE_ACTIONS
from testcase_model import (
    ARB_RAMP,
    COND_FIELD_UNITS,
    READ_ACTIONS,
    BlockMatch,
    TestStep,
    action_label,
    arb_spacing_ms,
    arb_value,
    confirm_text,
    is_arb_action,
    release_key,
    release_summary,
    kind_label,
    validate_structure,
    wait_since_summary,
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

# "Warten seit Aktion" an einem Partnergeraet (PI-77 a): der Stand des Besitzers kommt mit dem sekuendlichen
# Abgleich. Waere die Frist laut einem Stand von VOR dem Schrittbeginn schon um, wartet der Schritt auf einen
# neueren (eine Schaltung kurz vor dem Schritt fehlt darin evtl. noch) -- so oft nachsehen, hoechstens so lange,
# danach vorsichtshalber die volle Zeit ab Schrittbeginn.
SINCE_FRESH_POLL_MS = 200
SINCE_FRESH_TIMEOUT_S = 5.0
# Waechst die Frist beim Nachsehen um mehr als das, gab es eine neue Schaltung (Vermerk); kleinere Spruenge
# sind Laufzeitschwankungen des Abgleichs und verlaengern nur still
SINCE_NEW_SWITCH_S = 1.0

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
    # device_id, Aktion, Datei -- J-Link (PI-31 a), ausgefuehrt vom JLinkService
    execute_jlink = Signal(str, str, str)
    # Restbus (FT-6): device_id, Aktion, Botschaft, Signal, Wert (Text)
    execute_restbus = Signal(str, str, str, str, str)
    # Schrittindex, TestStep -- UDS am Pruefling (FT-1), ausgefuehrt von RunSession/UdsService
    execute_uds = Signal(int, object)
    # Schrittindex, TestStep -- XCP am Pruefling (FT-10), ausgefuehrt von RunSession/XcpService
    execute_xcp = Signal(int, object)
    # Schrittindex, TestStep -- Oszilloskop scharf schalten / auf Erfassung warten (1.0.1, PI-72 a), ausgefuehrt
    # von RunSession ueber den ScopeService; Ergebnis wie immer ueber on_action_completed (bzw. fail_check)
    execute_scope = Signal(int, object)
    step_started = Signal(int, object)         # index, TestStep
    step_failed = Signal(int, str)             # index, Fehlermeldung
    # Ergebnis einer Pass/Fail-Pruefung: index, bestanden, Messwert. Wird auch
    # bei check_abort VOR dem step_failed emittiert, damit der Editor die
    # Zeile in jedem Fall einfaerben kann.
    step_result = Signal(int, bool, float)
    run_finished = Signal()
    run_stopped = Signal()
    # Ergebnis eines Blocks "Ueberwachen" (0.63.0, FT-7): Schrittindex, dict (siehe _monitor_result)
    monitor_result = Signal(int, object)
    # FP-18 b (0.64.5): device_id, Feld ("hil_ain"/"hil_in"), Kanal, an/aus -- microHIL-Kanal schneller abfragen
    hil_focus_requested = Signal(str, str, int, bool)
    # Gesendeter Sollwert eines Arbitraersignal-Schritts (0.62.0, FT-2): index, device_id, Zielaktion, Wert --
    # fuer Soll gegen Ist und die tatsaechlichen Abstaende im Bericht
    setpoint_sent = Signal(int, str, str, float)
    # Block-Start-Index, aktueller Durchlauf (1-basiert), Gesamtzahl
    # (0 = unbegrenzt/While) -- fuer die Fortschrittsanzeige im Editor.
    iteration_changed = Signal(int, int, int)
    # FT-12 (PI-58 a): angehalten vor Schritt (Index) bzw. weiter
    paused = Signal(int)
    resumed = Signal()
    # PI-71 a: Bestaetigungsschritt wartet auf "Weiter"/"Abbrechen" in der Oberflaeche (Index, Text) -- Antwort
    # ueber confirm(). Nur wenn confirm_available gesetzt ist (Fenster), sonst bricht der Schritt den Lauf ab.
    confirm_requested = Signal(int, str)
    # Worauf der Lauf gerade wartet (Bestaetigung, Warten seit, Freigabe), fuer Laufregister/MCP: dict oder None
    waiting_changed = Signal(object)
    # Vermerk zu einem Schritt fuer die Laufaufzeichnung (Index, Text), z. B. "bestätigt", tatsaechliche Wartezeit
    step_note = Signal(int, str)
    # PI-74 a: "Warten auf Freigabe" freigegeben (Index, wer) -- fuer das Laufregister (REST/MCP)
    released = Signal(int, str)

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

        # -- PI-71 a: Bestaetigung und "Warten seit" ---------------------------------------------
        # Ohne Fenster (Kommandozeile) kann niemand bestaetigen: dann scheitert der Schritt (RunSession.refusal
        # laesst solche Ablaeufe dort gar nicht erst starten). MainWindow setzt True.
        self.confirm_available = False
        self._confirm_index = -1
        self._waiting: dict | None = None
        # (device_kind, device_id, action) -> monotonic() der letzten erfolgreich quittierten Ausfuehrung im Lauf
        self._action_times: dict[tuple[str, str, str], float] = {}
        # "Warten seit": eigener, praeziser Timer (der grobe darf bis 5 % zu frueh feuern -- hier gilt eine
        # Mindestzeit) mit Frist; feuert er doch vor der Frist (Pause, Systemuhr), wird nachgelegt.
        self._since_deadline = 0.0
        # PI-77 a: Bezug auch ausserhalb des Laufs -- (device_kind, device_id, action) -> switch_log.LastSwitch | None
        # (RunSession: eigener Worker bzw. Besitzer im Verbund). None = nur Ausfuehrungen in diesem Lauf.
        self.switch_lookup = None
        self._since_step: TestStep | None = None
        self._since_started = 0.0
        self._since_gave_up = False   # kein aktueller Stand vom Besitzer -> volle Zeit, nicht weiter nachfragen
        self._since_timer = QTimer(self)
        self._since_timer.setSingleShot(True)
        self._since_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._since_timer.timeout.connect(self._on_since_timeout)

        # -- PI-74 a: "Warten auf Freigabe" ------------------------------------------------------
        # Freigeben kann nur ein Programm mit Steuerrecht ueber REST/MCP (MainWindow -> release()). Ohne
        # erreichbaren Server kann niemand freigeben: dann lehnt RunSession.refusal den Lauf ab, der Schritt
        # selbst scheitert sofort. Wert: bool oder Aufruf ohne Argumente (MainWindow: laeuft der Server?).
        self.release_available = False
        self._release_index = -1
        self._release_granted = False
        self._release_timer = QTimer(self)
        self._release_timer.setSingleShot(True)
        self._release_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._release_timer.timeout.connect(self._on_release_timeout)

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
        self._arb_final_sent = False
        self._arb_last_send_ms = 0
        self._arb_clock = QElapsedTimer()
        self._arb_timer = QTimer(self)
        self._arb_timer.setSingleShot(True)
        self._arb_timer.timeout.connect(self._send_arb_sample)
        # Praezise statt grob: der grobe Timer darf bis 5 % zu frueh feuern -- der Mindestabstand (FP-16 b) gilt
        self._arb_timer.setTimerType(Qt.TimerType.PreciseTimer)

        # Automatischer Retry nach PICOSCOPE_RETRY_MESSAGE (siehe deren
        # Kommentar in device_worker.py) -- Anzahl bereits versuchter
        # Wiederholungen fuer den AKTUELLEN Schritt, zurueckgesetzt bei jedem
        # neuen Aktionsschritt (siehe _advance).
        self._pico_retry_count = 0
        self._pico_retry_timer = QTimer(self)
        self._pico_retry_timer.setSingleShot(True)
        self._pico_retry_timer.timeout.connect(self._retry_current_action)

        # -- Zeitpruefungen (FT-7): Startindex -> [Monitor, Quelle, gemeldet?] ----------------
        self._monitors: dict[int, list] = {}
        self._monitor_timer = QTimer(self)
        self._monitor_timer.setInterval(50)
        self._monitor_timer.timeout.connect(self._tick_monitors)

        # -- Debuggen (FT-12, PI-58 a): Haltepunkte (Zeilenindizes, nur in der Sitzung), Anhalten/Weiter/
        # Einzelschritt. Angehalten wird an Schrittgrenzen; eine laufende Wartezeit haelt an und laeuft mit dem
        # Rest weiter. Geraete, Grenzwerte, Watchdog und Ueberwachen-Bloecke laufen waehrenddessen weiter.
        self._breakpoints: set[int] = set()
        self._pause_requested = False
        self._paused = False
        self._step_mode = False
        self._skip_break_at = -1
        self._held_timers: list[tuple[QTimer, int]] = []

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
        # XCP-Messliste (FT-10, 0.64.3): A2L-Name -> (Wert, monotonic-Zeitstempel)
        self._xcp_values: dict[str, tuple[float, float]] = {}
        # Zuletzt von einer Lese-Aktion (READ_ACTIONS -- microHIL oder
        # PicoScope) gelesener Wert -- anders als _measurements kein Cache
        # mehrerer Geraete/Kanaele, da immer nur EIN Lese-Schritt gleichzeitig
        # laufen kann und der Wert sofort (noch in derselben Ausfuehrung)
        # durch _finish_step verbraucht wird (siehe dort).
        self._last_read_value = 0.0

    def is_running(self) -> bool:
        return self._running

    def start(self, steps: list[TestStep], variables: dict[str, float] | None = None, start_index: int = 0) -> None:
        """variables: Startwerte der Laufvariablen (Kommandozeile --var name=wert, 0.58.0). start_index: ab
        dieser Zeile (FT-12 "Ab hier starten", nur auf oberster Ebene, siehe start_refusal)."""
        if self._running:
            return
        self._steps = steps
        self._reset_debug()
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
        self._vars = {str(k): float(v) for k, v in (variables or {}).items()}
        self._pending_check = None
        self._run_started = monotonic()
        self._action_times = {}
        self._since_step = None
        self._confirm_index = -1
        self._release_index = -1
        self._release_granted = False
        self._waiting = None
        self._index = max(0, min(int(start_index), len(steps))) - 1
        self._running = True
        self._advance()

    # -- Debuggen (FT-12) ------------------------------------------------------------

    @staticmethod
    def start_refusal(steps: list[TestStep], start_index: int) -> str:
        """"" = ab dieser Zeile startbar; sonst der Grund (innerhalb eines Blocks fehlte dessen Anfang)."""
        if not 0 <= start_index < len(steps):
            return tr("Keine Zeile gewählt")
        _matching, depths, errors = validate_structure(steps)
        if errors:
            return errors[0][1]
        if depths[start_index] != 0 or steps[start_index].step_type in ("else", "end"):
            return tr("Ab hier starten geht nur auf oberster Ebene, nicht innerhalb eines Blocks")
        return ""

    def set_breakpoints(self, indices) -> None:
        self._breakpoints = {int(i) for i in indices}

    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        """Anhalten: eine laufende Wartezeit sofort (Rest wird gemerkt), sonst vor dem naechsten Schritt."""
        if not self._running or self._paused:
            return
        held = []
        if not self._arb_active:
            for timer in (self._timer, self._wait_timer, self._since_timer, self._release_timer):
                if timer.isActive():
                    held.append((timer, max(0, timer.remainingTime())))
                    timer.stop()
        if held:
            self._held_timers = held
            self._paused = True
            self.paused.emit(max(self._index, 0))
            return
        self._pause_requested = True

    def resume(self, single_step: bool = False) -> None:
        """Weiter (single_step: nur den naechsten Schritt, dann wieder anhalten)."""
        if not self._running or not self._paused:
            if single_step and self._running:
                self._step_mode = True
            return
        self._paused = False
        self._step_mode = single_step
        self.resumed.emit()
        if self._held_timers:
            held, self._held_timers = self._held_timers, []
            for timer, remaining in held:
                timer.start(remaining)
            return
        self._skip_break_at = self._index
        self._index -= 1
        self._advance()

    def debug_snapshot(self) -> dict:
        """Variablen und letzte Messwerte fuer die Anzeige waehrend einer Pause."""
        now = monotonic()
        values: dict[str, tuple[float, str, float]] = {}
        for device_id, (voltage, current, power, ts) in self._measurements.items():
            for name, value, unit in (("U", voltage, "V"), ("I", current, "A"), ("P", power, "W")):
                values[f"{device_id} {name}"] = (value, unit, now - ts)
        for device_id, (ain, inputs, ts) in self._hil_measurements.items():
            for i, mv in enumerate(ain, start=1):
                values[f"{device_id} AIN{i}"] = (mv, "mV", now - ts)
            values[f"{device_id} IN"] = (float(sum(1 << i for i, on in enumerate(inputs) if on)), "", now - ts)
        for key, (value, ts) in list(self._can_signals.items()) + list(self._xcp_values.items()):
            values[key] = (value, "", now - ts)
        return {"vars": dict(self._vars), "values": values, "index": self._index}

    def _reset_debug(self) -> None:
        self._pause_requested = False
        self._paused = False
        self._step_mode = False
        self._skip_break_at = -1
        self._held_timers = []

    def _should_pause(self, index: int) -> bool:
        if index == self._skip_break_at:
            self._skip_break_at = -1
            return False
        return self._pause_requested or self._step_mode or index in self._breakpoints

    def stop(self) -> None:
        if not self._running:
            return
        self._reset_debug()
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
        self._clear_monitors()
        self._clear_waiting()
        self.run_stopped.emit()

    def abort(self, message: str) -> None:
        """Bricht einen laufenden Ablauf mit Fehlermeldung ab (wie ein fehlgeschlagener Schritt):
        ALLE AUS waehrend des Laufs, Verbindungsverlust zum Verbund-Partner (PI-26 a)."""
        if self._running:
            self._timer.stop()
            self._arb_timer.stop()
            self._fail_at(max(self._index, 0), message)

    # -- Messwert-Cache (fuer while/if-Bedingungen) ---------------------------

    @Slot(str, float, float, float)
    def on_load_measurement(self, device_id: str, voltage: float, current: float, power: float) -> None:
        self._measurements[device_id] = (voltage, current, power, monotonic())
        self._feed_monitors(("load", "psu"), device_id, {"voltage": voltage, "current": current, "power": power})
        self._maybe_complete_check(device_id)

    @Slot(str, float, float, bool)
    def on_psu_measurement(
        self, device_id: str, voltage: float, current: float, _constant_current: bool
    ) -> None:
        self._measurements[device_id] = (voltage, current, voltage * current, monotonic())
        self._feed_monitors(("load", "psu"), device_id,
                            {"voltage": voltage, "current": current, "power": voltage * current})
        self._maybe_complete_check(device_id)

    @Slot(str, list)
    def on_hil_analog_input(self, device_id: str, values_mv: list) -> None:
        _ain, digital_in, _ts = self._hil_measurements.get(device_id, ([], [], 0.0))
        self._hil_measurements[device_id] = (list(values_mv), digital_in, monotonic())
        self._feed_monitors(("hil",), device_id, {"hil_ain": list(values_mv)})

    @Slot(str, list, list)
    def on_hil_digital_state(self, device_id: str, inputs: list, outputs: list) -> None:
        # outputs bewusst NICHT gecacht -- BUGS_OFFEN.md #35 fragte nur nach
        # Digital-/AnalogEINgang als Bedingungsquelle (der Ausgangszustand
        # ist ohnehin von der App selbst gesetzt, also bereits bekannt).
        ain, _digital_in, _ts = self._hil_measurements.get(device_id, ([], [], 0.0))
        self._hil_measurements[device_id] = (ain, list(inputs), monotonic())
        self._feed_monitors(("hil",), device_id, {"hil_in": [float(v) for v in inputs]})

    @Slot(str, int, object)
    def on_can_signals_decoded(self, device_id: str, arbitration_id: int, decoded) -> None:
        now = monotonic()
        for sig in decoded.signals:
            if isinstance(sig.value, str):
                continue  # DBC-Value-Table-Enum, nicht sinnvoll vergleichbar
            self._can_signals[f"{device_id}:{decoded.message_name}.{sig.name}"] = (float(sig.value), now)
            self._feed_monitors(("can",), device_id, {f"{decoded.message_name}.{sig.name}": float(sig.value)})

    @Slot(object)
    def on_xcp_values(self, payload: dict) -> None:
        """Abfrage der XCP-Messliste (XcpService.values_polled) -- Quelle fuer Bedingungen und Ueberwachen."""
        now = monotonic()
        values = {str(k): float(v) for k, v in (payload or {}).get("values", {}).items()}
        for name, value in values.items():
            self._xcp_values[name] = (value, now)
        self._feed_monitors(("xcp",), "xcp:dut", values)

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
        # PI-71 a: Bezug fuer "Warten seit" -- Zeitpunkt der Quittung (spaeter als das Senden, also vorsichtig)
        self._action_times[(step.device_kind, step.device_id, step.action)] = monotonic()
        if step.action in READ_ACTIONS:
            self._last_read_value = value
            if step.store_var:
                # Macht die Lesung (microHIL oder PicoScope) als while/if-
                # Bedingung nutzbar (dort cond_source="variable"), unabhaengig
                # von der separaten Pass/Fail-Pruefung in _finish_step.
                self._vars[step.store_var] = value
        self._wait_timer.start(max(0, round(step.duration * 1000)))

    def fail_check(self, message: str, value: float = 0.0) -> None:
        """Die Aktion des laufenden Schritts hat ihre eingebaute Erwartung verfehlt (UDS: falsche Antwort, DTC
        fehlt): zaehlt als verletzte Pruefung und bricht ab -- wie eine Pruefung mit "Bei Verletzung abbrechen"
        (Exit 1 statt 2, run_session.failed_by_check)."""
        if not self._running:
            return
        self.step_result.emit(self._index, False, value)
        self._fail_at(self._index, message)

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
        self._reset_debug()
        self._clear_monitors()
        self._running = False
        self._arb_active = False
        self._wait_timer.stop()
        self._check_timeout.stop()
        self._pending_check = None
        self._pico_retry_timer.stop()
        self._pico_retry_count = 0
        self._clear_waiting()
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
            if step.enabled and self._should_pause(self._index):
                # FT-12: vor diesem Schritt anhalten (Haltepunkt, Anhalten oder Einzelschritt)
                self._pause_requested = False
                self._step_mode = False
                self._paused = True
                self.paused.emit(self._index)
                return
            if step.enabled and (step.params or {}).get("refs"):
                # FT-5 (PI-55 a): Sollwert/Dauer/Grenzen aus Parametern der Variante bzw. Laufvariablen
                problem = resolve_refs(step, self._vars)
                if problem:
                    self.step_started.emit(self._index, step)
                    self._fail_at(self._index, problem)
                    return

            if t == "action":
                if not step.enabled:
                    continue
                self._pico_retry_count = 0
                self.step_started.emit(self._index, step)
                if is_arb_action(step.action):
                    self._start_arb(step)
                elif step.action == "CAN_SEND":
                    self.execute_can_send.emit(step.device_id, step.can_id, step.can_data, step.can_extended)
                elif step.action.startswith("RESTBUS_"):
                    params = step.params or {}
                    value = params.get("value", step.value)
                    self.execute_restbus.emit(step.device_id, step.action, str(params.get("message", "")),
                                              str(params.get("signal", "")),
                                              f"{value:g}" if isinstance(value, (int, float)) else str(value))
                elif step.device_kind == "jlink":
                    # Eigener Weg (JLinkService statt DeviceWorker), Ergebnis wieder ueber on_action_completed
                    self.execute_jlink.emit(step.device_id, step.action, step.file_path)
                elif step.device_kind == "xcp":
                    self.execute_xcp.emit(self._index, step)
                elif step.action in SCOPE_ACTIONS:
                    self.execute_scope.emit(self._index, step)
                elif step.device_kind == "uds":
                    # UDS (FT-1): Ergebnis ueber on_action_completed bzw. fail_check (Erwartung verletzt)
                    self.execute_uds.emit(self._index, step)
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

            if t == "confirm":
                if not step.enabled:
                    continue
                self.step_started.emit(self._index, step)
                text = confirm_text(step)
                if not self.confirm_available:
                    self._fail_at(self._index, tr("Bestätigung „{prompt}“ geht nur im LabControl-Fenster – Lauf "
                                                  "abgebrochen", prompt=text))
                    return
                self._confirm_index = self._index
                self._set_waiting({"kind": "confirm", "step": self._index + 1, "text": text,
                                   "message": tr("wartet auf Bestätigung: {prompt}", prompt=text)})
                self.confirm_requested.emit(self._index, text)
                return

            if t == "wait_since":
                if not step.enabled:
                    continue
                self.step_started.emit(self._index, step)
                if self._start_wait_since(step):
                    return
                continue

            if t == "await_release":
                if not step.enabled:
                    continue
                self.step_started.emit(self._index, step)
                self._start_await_release(step)
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

            if t == "monitor":
                match = self._matching[self._index]
                if not step.enabled:
                    self._index = match.end_index
                    continue
                error = self._start_monitor(self._index, step)
                if error:
                    self._fail_at(self._index, error)
                    return
                self._frames.append(_Frame("monitor", self._index, None, match.end_index, 1, monotonic()))
                continue

            if t == "end":
                frame = self._frames[-1]
                if frame.kind == "if":
                    self._frames.pop()
                    continue
                if frame.kind == "monitor":
                    self._frames.pop()
                    if not self._finish_monitor(frame.start_index):
                        return
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

    # -- Bestaetigung und "Warten seit" (PI-71 a) ----------------------------------

    @Slot(bool)
    def confirm(self, accepted: bool) -> None:
        """Antwort auf confirm_requested: Weiter (True) oder Abbrechen (False, wie Stopp). Nur aus der
        Oberflaeche -- es gibt bewusst keinen Weg ueber Netzwerk/MCP."""
        if not self._running or self._confirm_index < 0 or self._confirm_index != self._index:
            return
        index, self._confirm_index = self._confirm_index, -1
        self.step_note.emit(index, tr("bestätigt") if accepted else tr("abgebrochen"))
        if not accepted:
            self.stop()
            return
        self._clear_waiting()
        self._advance()

    def waiting(self) -> dict | None:
        return dict(self._waiting) if self._waiting else None

    def _set_waiting(self, info: dict | None) -> None:
        self._waiting = dict(info) if info else None
        self.waiting_changed.emit(self.waiting())

    def _clear_waiting(self) -> None:
        self._confirm_index = -1
        self._release_index = -1
        self._release_granted = False
        self._since_timer.stop()
        self._release_timer.stop()
        if self._waiting is not None:
            self._set_waiting(None)

    def _last_action_time(self, step: TestStep) -> float | None:
        """Letzte quittierte Ausfuehrung der Aktion im Lauf. Eine leere Kennung ("automatisch") passt auf jedes
        Geraet der Art -- vorsichtig: der juengste Treffer zaehlt."""
        times = [ts for (kind, device_id, action), ts in self._action_times.items()
                 if kind == step.device_kind and action == step.action
                 and (device_id == step.device_id or not device_id or not step.device_id)]
        return max(times) if times else None

    def _since_reference(self, step: TestStep) -> tuple[float | None, str, float]:
        """(monotonic() der juengsten bekannten Ausfuehrung | None, Quelle bzw. Grund, Stand ab).

        Bezug sind die Ausfuehrungen in diesem Lauf und (PI-77 a) jede Schaltung ueber LabControl, die der
        Besitzer des Geraets kennt (switch_lookup: Kachel, MCP/REST, Verbund-Partner, ALLE AUS ...). "Stand ab":
        ab wann die Auskunft gilt (lokal jetzt, beim Partner die letzte Abfrage)."""
        now = monotonic()
        own = self._last_action_time(step)
        info = None
        if self.switch_lookup is not None:
            try:
                info = self.switch_lookup(step.device_kind, step.device_id, step.action)
            except Exception:  # noqa: BLE001 -- Nachsehen darf den Lauf nicht anhalten; ohne Auskunft volle Zeit
                logger.exception("Letzte Schaltung von %s nicht lesbar", step.device_id or step.device_kind)
        fresh = info.fresh_since if info is not None else now
        if info is not None and info.at is not None and (own is None or info.at > own):
            return info.at, tr(info.source) if info.source else "?", fresh
        if own is not None:
            return own, tr("Ablauf"), fresh
        return None, info.source if info is not None else "", fresh

    def _since_waiting(self, step: TestStep, remaining: float) -> None:
        self._set_waiting({"kind": "wait_since", "step": self._index + 1, "text": wait_since_summary(step),
                           "remaining_s": round(remaining, 1), "until": time.time() + remaining,
                           "message": tr("wartet {summary}", summary=wait_since_summary(step))})

    def _start_wait_since(self, step: TestStep) -> bool:
        """True = wartet (Timer laeuft), False = Frist schon um."""
        now = monotonic()
        minimum = max(0.0, step.duration)
        self._since_step = step
        self._since_started = now
        self._since_gave_up = False
        last, source, fresh = self._since_reference(step)
        action = action_label(step.device_kind, step.action)
        if last is None:
            deadline = now + minimum
            note = (tr("{action}: letzter Zeitpunkt unbekannt ({reason}) – volle {seconds:g} s",
                       action=action, reason=source, seconds=minimum) if source else
                    tr("{action} seit dem Start von LabControl noch nicht ausgeführt – volle {seconds:g} s",
                       action=action, seconds=minimum))
        else:
            deadline = last + minimum
            note = tr("{action} zuletzt vor {elapsed:.1f} s ({source}) – wartet {remaining:.1f} s",
                      action=action, elapsed=now - last, source=source, remaining=max(0.0, deadline - now))
        self.step_note.emit(self._index, note)
        self._since_deadline = deadline
        if deadline <= now and fresh >= now:
            return False
        self._since_waiting(step, max(0.0, deadline - now))
        self._since_timer.start(max(1, math.ceil((deadline - now) * 1000)) if deadline > now
                                else SINCE_FRESH_POLL_MS)
        return True

    def _on_since_timeout(self) -> None:
        """Frist um (oder nachsehen): neu nachsehen -- eine Schaltung waehrend des Wartens (Kachel, Partner) oder
        ein neuerer Stand des Besitzers verlaengert die Frist, nie verkuerzt sie."""
        if not self._running or self._since_step is None:
            return
        step = self._since_step
        now = monotonic()
        minimum = max(0.0, step.duration)
        action = action_label(step.device_kind, step.action)
        last, source, fresh = self._since_reference(step)
        if last is not None and last + minimum > self._since_deadline:
            if last + minimum - self._since_deadline > SINCE_NEW_SWITCH_S:
                self.step_note.emit(self._index, tr(
                    "{action} erneut ausgeführt ({source}) – wartet weitere {remaining:.1f} s", action=action,
                    source=source, remaining=max(0.0, last + minimum - now)))
            self._since_deadline = last + minimum
            self._since_waiting(step, max(0.0, self._since_deadline - now))
        if fresh < self._since_started and not self._since_gave_up:
            if now - self._since_started < SINCE_FRESH_TIMEOUT_S:
                if self._since_deadline <= now:
                    self._since_timer.start(SINCE_FRESH_POLL_MS)
                    return
            else:
                self._since_gave_up = True
                full = self._since_started + minimum
                if full > self._since_deadline:
                    self._since_deadline = full
                    self.step_note.emit(self._index, tr(
                        "{action}: kein aktueller Stand vom Besitzer – volle {seconds:g} s", action=action,
                        seconds=minimum))
                    self._since_waiting(step, max(0.0, full - now))
        rest = self._since_deadline - now
        if rest > 0:
            self._since_timer.start(max(1, math.ceil(rest * 1000)))
            return
        self._clear_waiting()
        self._advance()

    # -- Warten auf Freigabe (PI-74 a) ---------------------------------------------

    def release_possible(self) -> bool:
        available = self.release_available
        return bool(available()) if callable(available) else bool(available)

    def _start_await_release(self, step: TestStep) -> None:
        key = release_key(step)
        if not self.release_possible():
            self._fail_at(self._index, tr("Warten auf Freigabe geht nur mit Netzwerkfreigabe (REST/MCP) – Lauf "
                                          "abgebrochen"))
            return
        limit = max(0.0, step.duration)
        self._release_index = self._index
        self._release_granted = False
        self._set_waiting({"kind": "release", "step": self._index + 1, "key": key, "text": key,
                           "timeout_s": limit, "remaining_s": round(limit, 1), "until": time.time() + limit,
                           "message": tr("wartet auf Freigabe {summary}", summary=release_summary(step))})
        self._release_timer.start(max(1, math.ceil(limit * 1000)))

    def release(self, who: str, key: str | None = None, step: int | None = None) -> str:
        """Freigabe eines wartenden Schritts "Warten auf Freigabe" (PI-74 a) -- nur ueber REST/MCP (MainWindow
        prueft vorher das Steuerrecht). key/step (1-basiert), falls angegeben, muessen zum wartenden Schritt
        passen. "" = freigegeben, sonst Fehlercode: not_running, confirm_local_only (wartet auf eine
        Bestaetigung, die nur im Fenster geht), not_waiting_release, release_mismatch."""
        if not self._running:
            return "not_running"
        if self._release_index < 0 or self._release_index != self._index or self._release_granted:
            return "confirm_local_only" if self._confirm_index >= 0 else "not_waiting_release"
        step_obj = self._steps[self._release_index]
        if (key is not None and key.strip() != release_key(step_obj)) or (
                step is not None and int(step) != self._release_index + 1):
            return "release_mismatch"
        index = self._release_index
        self.step_note.emit(index, tr("freigegeben von {who} um {time}", who=who or "?",
                                      time=time.strftime("%H:%M:%S")))
        self.released.emit(index, who or "")
        if self._paused:
            # Angehalten (FT-12): freigegeben, weiter erst nach "Weiter" -- der gehaltene Timer feuert dann sofort
            self._release_granted = True
            self._held_timers = [(timer, 0 if timer is self._release_timer else remaining)
                                 for timer, remaining in self._held_timers]
            if self._waiting is not None:
                self._set_waiting(None)
            return ""
        self._clear_waiting()
        self._advance()
        return ""

    def _on_release_timeout(self) -> None:
        if not self._running or self._release_index < 0:
            return
        if self._release_granted:   # waehrend einer Pause freigegeben
            self._clear_waiting()
            self._advance()
            return
        step = self._steps[self._release_index]
        self._fail_at(self._release_index, tr("keine Freigabe {summary} – Höchstzeit abgelaufen",
                                              summary=release_summary(step)))

    # -- Zeitpruefungen (FT-7, "Ueberwachen … Ende") -------------------------------

    @Slot(str, int, str, bool, float)
    def on_can_frame(self, device_id: str, arbitration_id: int, _data: str, _extended: bool, _ts: float) -> None:
        """Jeder empfangene CAN-Frame -- fuer CAN-Luecken (can_gap)."""
        if not self._monitors:
            return
        now = monotonic()
        for index, entry in list(self._monitors.items()):
            monitor, source, _reported = entry
            if (monitor.mode == "can_gap" and monitor.config["can_id"] == arbitration_id
                    and source["device_id"] in ("", device_id)):
                self._handle_violation(index, monitor.feed(now))

    def _start_monitor(self, index: int, step: TestStep) -> str:
        """Monitor anlegen; "" = ok, sonst Fehlermeldung (Quelle nicht eindeutig/nicht verbunden)."""
        config = timing_checks.clean_config((step.params or {}).get("monitor"))
        kind = step.cond_device_kind
        device_id = step.cond_device_id
        if config["mode"] == "can_gap":
            kind = "can"
        elif not device_id and kind in ("load", "psu"):
            device_id, status = self._resolve_fresh_device(kind)
            if device_id is None:
                return (tr("Mehrere Geräte vom Typ '{kind}' verbunden -- bitte Zielgerät in der Überwachung "
                           "auswählen", kind=kind_label(kind)) if status == "ambiguous" else
                        tr("Keine aktuelle Messung für ein Gerät vom Typ '{kind}'", kind=kind_label(kind)))
        elif not device_id and kind == "hil":
            device_id, status = self._resolve_fresh_device("hil", self._hil_measurements)
            if device_id is None:
                return tr("Keine aktuelle Messung für ein Gerät vom Typ '{kind}'", kind=kind_label("hil"))
        source = {"kind": kind, "device_id": device_id or "", "field": step.cond_field,
                  "channel": step.hil_channel}
        if kind == "hil" and device_id and step.cond_field in ("hil_ain", "hil_in"):
            source["focus"] = True
            self.hil_focus_requested.emit(device_id, step.cond_field, step.hil_channel, True)
        self._monitors[index] = [timing_checks.Monitor(config, monotonic()), source, False]
        self.step_started.emit(index, step)
        if not self._monitor_timer.isActive():
            self._monitor_timer.start()
        return ""

    def _feed_monitors(self, kinds: tuple, device_id: str, values: dict) -> None:
        if not self._monitors or not self._running:
            return
        now = monotonic()
        for index, entry in list(self._monitors.items()):
            monitor, source, _reported = entry
            if monitor.mode == "can_gap" or source["kind"] not in kinds:
                continue
            if source["device_id"] not in ("", device_id):
                continue
            value = values.get(source["field"])
            if isinstance(value, list):
                channel = source["channel"]
                value = value[channel - 1] if 1 <= channel <= len(value) else None
            if value is None:
                continue
            self._handle_violation(index, monitor.feed(now, float(value)))

    def _tick_monitors(self) -> None:
        if not self._monitors:
            self._monitor_timer.stop()
            return
        now = monotonic()
        for index, entry in list(self._monitors.items()):
            self._handle_violation(index, entry[0].tick(now))

    def _handle_violation(self, index: int, verdict) -> None:
        """Erste Verletzung eines Monitors melden; mit "Bei Verletzung abbrechen" endet der Lauf hier."""
        entry = self._monitors.get(index)
        if verdict is None or entry is None or entry[2] or not self._running:
            return
        monitor = entry[0]
        entry[2] = True
        self.step_result.emit(index, False, verdict.value)
        self.monitor_result.emit(index, self._monitor_result(index, monitor, entry[1], verdict, monotonic()))
        if monitor.config["abort"]:
            self._fail_at(index, verdict.message)

    def _finish_monitor(self, index: int) -> bool:
        """Blockende: Urteil melden. False = der Lauf wurde dabei beendet."""
        entry = self._monitors.pop(index, None)
        if not self._monitors:
            self._monitor_timer.stop()
        if entry is not None:
            self._release_focus(entry[1])
        if entry is None:
            return True
        monitor, source, reported = entry
        now = monotonic()
        verdict = monitor.finish(now)
        if reported:
            return True
        self.step_result.emit(index, verdict.passed, verdict.value)
        self.monitor_result.emit(index, self._monitor_result(index, monitor, source, verdict, now))
        if not verdict.passed and monitor.config["abort"]:
            self._fail_at(index, verdict.message)
            return False
        return True

    def _monitor_result(self, index: int, monitor, source: dict, verdict, now: float) -> dict:
        offset = time.time() - now          # monotonic -> Wanduhr (Bericht)
        return {"index": index, "config": dict(monitor.config), "passed": verdict.passed, "value": verdict.value,
                "message": verdict.message, "resolution_s": timing_checks.resolution_s(monitor.times),
                "samples": len(monitor.times), "source": dict(source),
                "t_start": monitor.t_start + offset, "t_end": now + offset,
                "series": [(t + offset, v) for t, v in monitor.series()]}

    def _release_focus(self, source: dict) -> None:
        if source.pop("focus", False):
            self.hil_focus_requested.emit(source["device_id"], source["field"], source["channel"], False)

    def _clear_monitors(self) -> None:
        for _monitor, source, _reported in self._monitors.values():
            self._release_focus(source)
        self._monitors.clear()
        self._monitor_timer.stop()

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
            if step.cond_device_kind == "xcp":
                entry = self._xcp_values.get(step.cond_field.strip())
                if entry is None or (monotonic() - entry[1]) > MEASUREMENT_STALE_S:
                    return None, tr("Kein aktueller XCP-Wert für '{name}' (Messliste)", name=step.cond_field)
                return self._compare(entry[0], step.cond_op, step.cond_value), ""
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
        self._arb_final_sent = False
        self._arb_clock.start()
        self._send_arb_sample()

    def _send_arb_sample(self) -> None:
        if not self._running or not self._arb_active:
            return
        step = self._steps[self._index]
        self._arb_last_send_ms = self._arb_clock.elapsed()
        value = arb_value(step, self._arb_last_send_ms / 1000.0)
        self.setpoint_sent.emit(self._index, step.device_id, step.arb_target, value)
        self.execute_action.emit(step.device_id, step.device_kind, step.arb_target, value, step.hil_channel)

    def _continue_arb(self) -> None:
        """Nach der Quittung eines Sollwerts: naechster Sollwert, sobald der Mindestabstand (von Senden zu Senden,
        arb_spacing_ms) um ist -- quittiert das Geraet langsamer, sofort (FP-16 b: kein Rueckstau)."""
        step = self._steps[self._index]
        elapsed_ms = self._arb_clock.elapsed()
        wait_ms = (arb_spacing_ms(step.arb_interval_ms, partner=step.device_id.startswith("@"))
                   - (elapsed_ms - self._arb_last_send_ms))
        if elapsed_ms >= step.duration * 1000.0:
            if step.arb_shape == ARB_RAMP and not self._arb_final_sent:
                # Rampe: genau den Zielwert setzen, statt beim letzten Zwischenwert stehenzubleiben -- auch er
                # haelt den Mindestabstand ein; ab der Schrittdauer liefert arb_value den Zielwert
                self._arb_final_sent = True
                self._arb_timer.start(max(0, math.ceil(wait_ms)))
                return
            self._arb_active = False
            # Ueber _finish_step statt _advance, damit auch ein ARB-Schritt
            # eine Pass/Fail-Pruefung tragen kann (gemessen wird dann der
            # Zustand nach dem Signalende).
            self._finish_step()
            return
        self._arb_timer.start(max(0, math.ceil(wait_ms)))

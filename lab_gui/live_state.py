"""Thread-sicherer Schnappschuss aller Live-Messwerte fuer den HTTP-Server-
Thread der Netzwerk-Freigabe (siehe share_server.py).

DIES IST DIE EINZIGE BRUECKE zwischen GUI-Thread und Server-Thread.

  Geschrieben  ausschliesslich aus dem GUI-Thread ueber Qt-Slots -- exakt
               dasselbe Muster wie recording.Recorder, verdrahtet in
               MainWindow._setup_worker() gleich neben self._recorder.
  Gelesen      ausschliesslich per snapshot() aus dem Server-Thread.
  Uebergeben   werden NUR tiefkopierte Standard-Python-Typen (str/int/float/
               bool/list/dict) -- niemals ein QObject, ein Widget, ein
               Treiber-Handle oder eine Referenz auf eine hier gehaltene
               Struktur.

Was der Server-Thread NICHT darf (und warum):
  * Settings lesen           -- macht Datei-I/O und ist nicht thread-sicher.
  * i18n.tr() aufrufen       -- Translator ist ein QObject. Stattdessen
                                rendert der GUI-Thread die Feldnamen bei
                                language_changed hier vor (refresh_labels).
  * DeviceRegistry/DeviceWorker/Widgets anfassen -- alles Qt-Objekte des
                                GUI- bzw. Worker-Threads.

Damit hat die App drei Threads statt bisher zwei (siehe auch den
Modul-Docstring von device_worker.py): GUI, DeviceWorker und der
HTTP-Server samt einem Thread je Verbindung.

Das Lock wird immer nur fuer wenige Dict-Zuweisungen bzw. eine Tiefkopie
gehalten -- niemals ueber I/O. Bewusst ein einfaches Lock und kein RLock:
Wiedereintritt wird nicht gebraucht, und ein RLock wuerde ein
versehentliches verschachteltes Acquire verstecken statt es zu verklemmen.
"""
from __future__ import annotations

import copy
import threading
import time

from PySide6.QtCore import QObject, Slot

from field_catalog import FIELD_DEFS, LOAD_MODE_SHORT
from i18n import tr

# Ab wann ein Wert als "steht" gilt. Bewusst identisch zu
# safety.STALE_TIMEOUT_S (dort loest dieselbe Frist den Watchdog aus) --
# ein Wert, den der Sicherheits-Watchdog als veraltet ansieht, soll auch
# ueber die Netzwerk-Freigabe als veraltet erscheinen.
STALE_TIMEOUT_S = 2.0

# Voreinstellung der Netzwerk-Freigabe. Bewusst vollstaendig inert: aus,
# nur Loopback, nichts freigegeben, Token noetig. Ein Upgrade einer
# aelteren settings.json erbt damit ein Feature, das nichts oeffnet.
# settings.Settings.share_config merged den gespeicherten Stand hierueber
# -- genau wie safety.default_device_limits() fuer die Grenzwerte.
DEFAULT_SHARE_CONFIG: dict = {
    "enabled": False,
    "bind": "127.0.0.1",
    "port": 8420,
    "token": "",
    "read_requires_token": True,
    "access_log": False,
    # Wie lange der Hauptschalter "Fernsteuerung aktiv" nach dem Einschalten
    # gilt. Der Schalter selbst wird bewusst NICHT gespeichert (siehe
    # LiveState.set_remote_control) -- nur diese Dauer ist eine Einstellung.
    "control_timeout_min": 60,
    # Zugriffe VOM SELBEN RECHNER (z.B. der MCP-Server) brauchen den Hauptschalter
    # nicht. Token, "Steuern" je Geraet und die Sperren (Testlauf, Sicherheits-
    # abschaltung) gelten trotzdem. Gedacht fuer Programme, die der Nutzer selbst auf
    # diesem PC betreibt; das Zeitfenster schuetzt vor allem vor Zugriffen von aussen.
    "local_bypass": True,
    # device_id -> {"read": bool, "control": bool}. "control" existiert von
    # Anfang an mit Default False, damit die JSON-Form stabil bleibt, wenn
    # die Fernsteuerung (Phase 2) dazukommt.
    "devices": {},
}

DEFAULT_DEVICE_SHARE: dict = {"read": False, "control": False}


def default_share_config() -> dict:
    return copy.deepcopy(DEFAULT_SHARE_CONFIG)


class LiveState(QObject):
    """Haelt je Geraet den zuletzt gemeldeten Stand aller Messgroessen.

    Kein Ringpuffer und keine Historie -- das ist Aufgabe von
    timeline_tab.SignalSeries bzw. recording.Recorder. Hier steht bewusst
    nur der JEWEILS AKTUELLE Wert plus sein Zeitstempel, weil eine
    Netzwerk-Anfrage genau das braucht und ein Verlauf ueber die Leitung
    nur Bandbreite kostet.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        # device_id -> {"kind", "label", "online", "fields": {...}, "ts"}
        self._devices: dict[str, dict] = {}
        self._safety_state = "off"      # von SafetyMonitor.state_changed
        self._test_running = False      # von MainWindow (TestRunner)
        self._share = default_share_config()
        # Hauptschalter der Fernsteuerung als Ablaufzeit auf der monotonen
        # Uhr (0 = aus). Bewusst eine ZEIT statt eines Flags: die Freigabe
        # erlischt dann von selbst, auch wenn im GUI-Thread nichts mehr
        # laeuft, das sie zuruecknehmen koennte -- der Server-Thread
        # vergleicht bei jeder Anfrage selbst gegen die Uhr.
        self._remote_until = 0.0
        # device_id -> {feld: {"enabled", "value"}} (rohe Form aus
        # Settings.safety_limits), fuer die Sollwert-Pruefung der
        # Fernsteuerung (siehe remote_actions.limit_violation).
        self._safety_limits: dict = {}
        # (kind, field) -> uebersetzter Anzeigename, im GUI-Thread
        # vorgerendert (siehe Modul-Docstring: kein tr() im Server-Thread).
        self._labels: dict[tuple[str, str], str] = {}
        self.refresh_labels()

    # -- Interne Helfer (immer unter gehaltenem Lock aufzurufen) -------------

    def _entry(self, device_id: str) -> dict:
        entry = self._devices.get(device_id)
        if entry is None:
            # Kann vor on_device_known() passieren: der DeviceWorker
            # emittiert seine erste Messung ggf. bevor die Registry das
            # Geraet gemeldet hat. kind aus dem ID-Praefix ableiten (gleiche
            # Regel wie safety.device_kind), label spaeter nachreichen.
            entry = {
                "kind": device_id.split(":", 1)[0],
                "label": device_id,
                "online": True,
                "fields": {},
                "ts": time.monotonic(),
            }
            self._devices[device_id] = entry
        return entry

    def _write(self, device_id: str, **fields) -> None:
        with self._lock:
            entry = self._entry(device_id)
            entry["fields"].update(fields)
            entry["ts"] = time.monotonic()

    # -- Geraeteregistrierung (von DeviceRegistry gespeist) ------------------
    # Signatur und Verdrahtung exakt wie recording.Recorder.on_device_known/
    # on_label_changed.

    @Slot(str, str, str)
    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        with self._lock:
            entry = self._entry(device_id)
            entry["kind"] = kind
            entry["label"] = label

    @Slot(str, str, str)
    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self.on_device_known(kind, device_id, label)

    def set_online(self, device_id: str, online: bool) -> None:
        with self._lock:
            self._entry(device_id)["online"] = bool(online)

    # -- Messwerte (von DeviceWorker gespeist) -------------------------------

    @Slot(str, float, float, float)
    def on_load_measurement(self, device_id: str, voltage: float, current: float, power: float) -> None:
        self._write(device_id, voltage=voltage, current=current, power=power)

    @Slot(str, str)
    def on_load_function_state(self, device_id: str, code: str) -> None:
        # Gleiche Uebersetzung wie im Dashboard (dashboard.set_load_mode):
        # Hardware liefert CC/CV/CR/CW, der Mock die SET-Codes CURR/VOLT/...
        self._write(device_id, mode=LOAD_MODE_SHORT.get(code, code))

    @Slot(str, float, float, bool)
    def on_psu_measurement(self, device_id: str, voltage: float, current: float, constant_current: bool) -> None:
        # Dieselbe Ableitung wie dashboard.update_psu: das HCS-34xx meldet
        # keinen Funktionscode, nur ein CC-Flag.
        self._write(device_id, voltage=voltage, current=current,
                    mode="CC" if constant_current else "CV")

    @Slot(str, float, float)
    def on_psu_ratings(self, device_id: str, max_voltage: float, max_current: float) -> None:
        """Nennwerte (GMAX) des Netzteils -- die Fernsteuerung prueft Sollwerte
        gegen SIE statt gegen feste 60 V / 10 A (siehe remote_actions.validate)."""
        with self._lock:
            self._entry(device_id)["ratings"] = {
                "max_voltage": max_voltage, "max_current": max_current}

    @Slot(str, int, int)
    def on_can_stats(self, device_id: str, tx_count: int, rx_count: int) -> None:
        self._write(device_id, tx_count=tx_count, rx_count=rx_count)

    @Slot(str, list, list)
    def on_hil_digital_state(self, device_id: str, inputs: list, outputs: list) -> None:
        self._write(device_id,
                    **{f"in{n}": int(bool(v)) for n, v in enumerate(inputs, start=1)},
                    **{f"out{n}": int(bool(v)) for n, v in enumerate(outputs, start=1)})

    @Slot(str, list)
    def on_hil_relay_state(self, device_id: str, relays: list) -> None:
        self._write(device_id, **{f"rel{n}": int(bool(v)) for n, v in enumerate(relays, start=1)})

    @Slot(str, list)
    def on_hil_analog_input(self, device_id: str, values: list) -> None:
        self._write(device_id, **{f"ain{n}": v for n, v in enumerate(values, start=1)})

    @Slot(str, list, list)
    def on_hil_pwr12_state(self, device_id: str, enables: list, currents: list) -> None:
        # Nur die Stromsense-Werte -- die Enable-Flags stehen so nicht im
        # Feldkatalog (siehe field_catalog.HIL_SIGNAL_FIELDS, dort ebenfalls
        # nur pwr12_N_current).
        self._write(device_id, **{f"pwr12_{n}_current": v for n, v in enumerate(currents, start=1)})

    @Slot(str, str, str, str)
    def on_picoscope_state(self, device_id: str, status: str, variant: str, serial: str) -> None:
        self._write(device_id, status=status)

    # -- Sperrzustand --------------------------------------------------------

    @Slot(str)
    def on_safety_state_changed(self, state: str) -> None:
        with self._lock:
            self._safety_state = state

    def set_test_running(self, running: bool) -> None:
        """Wird von MainWindow gesetzt, NICHT von DeviceWorker.

        device_worker.set_test_running() ist trotz des gleichen Namens etwas
        anderes: es pausiert nur die PicoScope-Reconnect-Probe und weiss
        nichts ueber den Testablauf als solchen. Massgeblich ist der
        TestRunner im GUI-Thread (siehe main_window._on_run_requested und
        das run_finished/run_stopped/step_failed-Tripel).
        """
        with self._lock:
            self._test_running = bool(running)

    def _lock_state(self) -> str:
        """Abgeleiteter Sperrzustand, Rangfolge tripped > running > free.

        Nur unter gehaltenem Lock aufrufen -- er wird zusammen mit den
        Messwerten in EINEM Schnappschuss ausgeliefert, damit eine Anfrage
        nie einen zerrissenen Zustand sieht (Werte von vor einem Trip mit
        lock:"free" von danach).

        Der schreibende Endpoint antwortet bei != "free" mit HTTP 409 --
        UND der GUI-Thread-Slot (main_window._on_share_action) prueft
        unmittelbar vor dem Dispatch erneut self._safety.is_tripped() /
        self._test_runner.is_running(): der Schnappschuss darf zwischen
        Pruefung und Ausfuehrung veraltet sein (TOCTOU). ALLE AUS ist die
        einzige Schreib-Aktion, die davon ausgenommen ist.
        """
        if self._safety_state == "tripped":
            return "safety_tripped"
        if self._test_running:
            return "test_running"
        return "free"

    # -- Fernsteuerung: Hauptschalter und Grenzwerte --------------------------

    def set_remote_control(self, active: bool, duration_s: float = 0.0) -> None:
        """Schaltet die Fernsteuerung fuer `duration_s` Sekunden frei bzw. sofort
        ab. Nur aus dem GUI-Thread aufrufen.

        Beim App-Start ist die Fernsteuerung IMMER aus -- der Zustand wird nicht
        in settings.json abgelegt. Wer sie einschaltet, gibt bewusst ein
        Zeitfenster her (siehe control_timeout_min), keine Dauerfreigabe.
        """
        with self._lock:
            self._remote_until = time.monotonic() + max(0.0, duration_s) if active else 0.0

    def remote_remaining_s(self) -> float:
        """Restdauer der Freigabe in Sekunden (0 = aus oder abgelaufen)."""
        with self._lock:
            return max(0.0, self._remote_until - time.monotonic())

    def remote_active(self) -> bool:
        return self.remote_remaining_s() > 0.0

    @Slot(dict)
    def set_safety_limits(self, limits: dict) -> None:
        with self._lock:
            self._safety_limits = copy.deepcopy(limits)

    # -- Freigabekonfiguration -----------------------------------------------

    @Slot(dict)
    def set_share_config(self, config: dict) -> None:
        with self._lock:
            self._share = copy.deepcopy(config)

    # -- Uebersetzte Feldnamen -----------------------------------------------

    def refresh_labels(self) -> None:
        """Rendert alle Feld-Anzeigenamen im GUI-Thread vor.

        An Translator.language_changed zu haengen (siehe main_window.
        _wire_share). Der Server-Thread darf tr() nicht selbst aufrufen,
        weil Translator ein QObject ist.
        """
        labels = {key: tr(name) for key, (name, _unit) in FIELD_DEFS.items()}
        with self._lock:
            self._labels = labels

    # -- Lesen (Server-Thread!) ----------------------------------------------

    def snapshot(self, device_ids: list[str] | None = None) -> dict:
        """Tiefkopierter Gesamtzustand fuer EINE HTTP-Anfrage.

        Die einzige Methode, die der Server-Thread aufrufen darf (neben
        share_config()). device_ids=None liefert alle bekannten Geraete;
        gefiltert wird NICHT hier, sondern in share_api -- dieses Modul
        kennt die Freigaberegeln absichtlich nicht, es reicht sie nur
        zusammen mit den Werten heraus.
        """
        now = time.monotonic()
        with self._lock:
            ids = list(self._devices) if device_ids is None else device_ids
            devices = {d: copy.deepcopy(self._devices[d]) for d in ids if d in self._devices}
            share = copy.deepcopy(self._share)
            labels = dict(self._labels)
            lock = self._lock_state()
            safety_state = self._safety_state
            test_running = self._test_running
            remaining = max(0.0, self._remote_until - now)
            safety_limits = copy.deepcopy(self._safety_limits)
        # Ausserhalb des Locks: reine Rechnung auf der bereits geloesten Kopie.
        for entry in devices.values():
            age = now - entry.pop("ts")
            entry["age_s"] = round(age, 3)
            entry["stale"] = age > STALE_TIMEOUT_S
        return {
            "devices": devices,
            "share": share,
            "labels": labels,
            "lock": lock,
            "safety": safety_state,
            "test_running": test_running,
            "remote": {"active": remaining > 0.0, "remaining_s": round(remaining, 1)},
            "safety_limits": safety_limits,
        }

    def share_config(self) -> dict:
        """Nur die Freigabekonfiguration (Server-Thread), ohne die Messwerte
        zu kopieren -- fuer Anfragen, die gar keine Werte brauchen."""
        with self._lock:
            return copy.deepcopy(self._share)

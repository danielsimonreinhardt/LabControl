"""Fehlerbox-Dienst (FT-9, 0.61.0): setzt und hebt Fehler (faultbox.py) am microHIL auf, haelt den aktiven
Fehler und beantwortet, welche HIL-Kanaele gesperrt sind (FP-27 a).

Ausfuehrung ueber DeviceWorker.execute_remote_action mit eigenem Anfrage-ID-Bereich (der Weg der
Netzwerk-Fernsteuerung, ohne deren Pruefungen -- die Fehlerbox ist eine Funktion dieser App). Eine Schaltfolge
laeuft Schritt fuer Schritt: jede Aktion wartet auf die Bestaetigung des Geraets. Schlaegt eine fehl, wird der
Rest abgebrochen, der Fehler gilt als nicht gesetzt und seine Kanaele bleiben gesperrt, bis Aufheben oder ALLE AUS
den Ruhezustand herstellt (lieber gesperrt als ein halb gesetzter Fehler, den niemand bemerkt).
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

import faultbox
from i18n import tr

logger = logging.getLogger(__name__)

REQUEST_ID_BASE = 1_500_000_000      # getrennt von share_remote.RemoteBridge (zaehlt ab 1)


class FaultService(QObject):
    # Anfrage-ID, ok, Meldung -- Ende von set_fault/clear
    finished = Signal(int, bool, str)
    # {"faults": [Namen], "active": Name|None, "busy": bool, "last": str, "locked": {hil_id: [(art, kanal)]}}
    state_changed = Signal(dict)
    # an DeviceWorker.execute_remote_action (Queued in den Worker-Thread)
    _dispatch = Signal(int, str, str, str, float, int)

    def __init__(self, worker, path_provider, hil_resolver, parent=None) -> None:
        super().__init__(parent)
        self._path = path_provider           # () -> Path der faultbox.json
        self._resolve_hil = hil_resolver      # (hil_id_oder_leer) -> (hil_id|None, Fehlertext)
        self._dispatch.connect(worker.execute_remote_action)
        worker.remote_action_completed.connect(self._on_completed)
        self._faults: list[faultbox.Fault] = []
        self._load_error = ""
        self._active: faultbox.Fault | None = None
        self._active_hil = ""
        self._locked: dict[str, set] = {}     # hil_id -> {(art, kanal)} gesperrt
        self._queue: list[tuple[str, str, int, float]] = []   # (hil_id, Aktion, Kanal, Wert)
        self._job: dict | None = None
        self._next_id = REQUEST_ID_BASE
        self._pending_action = 0
        self._last = ""
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.timeout.connect(self._next_action)
        self.reload()

    # -- Daten ---------------------------------------------------------------------------

    def reload(self) -> None:
        try:
            self._faults = faultbox.load(Path(self._path()))
            faultbox.validate(self._faults)
            self._load_error = ""
        except (OSError, ValueError) as exc:
            logger.warning("Fehlerbox nicht lesbar: %s", exc)
            self._faults, self._load_error = [], str(exc)
        self._emit_state()

    def save(self, faults: list[faultbox.Fault]) -> None:
        """Aus den Einstellungen: speichern (FaultError bei ungueltiger Schaltfolge) und neu laden."""
        if self._active is not None and self._active.name not in {f.name for f in faults}:
            raise faultbox.FaultError(tr("Der aktive Fehler '{name}' kann nicht entfernt werden – erst aufheben",
                                         name=self._active.name))
        faultbox.save(Path(self._path()), faults)
        self.reload()

    def faults(self) -> list[faultbox.Fault]:
        return list(self._faults)

    def load_error(self) -> str:
        return self._load_error

    def fault(self, name: str) -> faultbox.Fault | None:
        return next((f for f in self._faults if f.name == name), None)

    def active(self) -> str | None:
        return self._active.name if self._active is not None else None

    def busy(self) -> bool:
        return self._job is not None

    def blocked(self, device_id: str, action: str, channel: int) -> str:
        """Name des Fehlers, dem dieser HIL-Kanal gerade gehoert ("" = frei)."""
        if not device_id.startswith("hil:"):
            return ""
        used = faultbox.channel_of_action(action, channel)
        if used is None or used not in self._locked.get(device_id, set()):
            return ""
        return self.active() or (self._job or {}).get("name") or tr("unvollständig gesetzter Fehler")

    def state(self) -> dict:
        return {"faults": [f.name for f in self._faults], "active": self.active(), "busy": self.busy(),
                "last": self._last, "error": self._load_error,
                "locked": {h: sorted(c) for h, c in self._locked.items() if c}}

    def _emit_state(self) -> None:
        self.state_changed.emit(self.state())

    # -- Befehle ---------------------------------------------------------------------------

    def set_fault(self, name: str, replace: bool = False) -> int:
        """Fehler setzen. replace=True (Ablauf, FP-26 c): einen anderen aktiven erst aufheben, warten, setzen;
        False (Kachel): dann abgelehnt. Ergebnis ueber `finished`."""
        request = self._new_request()
        fault = self.fault(name)
        if fault is None:
            return self._finish_now(request, False, tr("Unbekannter Fehler '{name}'", name=name))
        if self._job is not None:
            return self._finish_now(request, False, tr("Fehlerbox ist beschäftigt"))
        if self._active is not None and self._active.name == name:
            return self._finish_now(request, True, tr("'{name}' ist schon gesetzt", name=name))
        if self._active is not None and not replace:
            return self._finish_now(request, False, tr("Erst '{name}' aufheben", name=self._active.name))
        hil, error = self._resolve_hil(fault.hil)
        if hil is None:
            return self._finish_now(request, False, error)
        steps: list[tuple[str, str, int, float]] = []
        if self._active is not None:
            steps += [(self._active_hil, a, c, v) for a, c, v in self._active.clear_actions()]
            steps.append(("", "WAIT", max(fault.settle_ms, self._active.settle_ms), 0.0))
        steps += [(hil, a, c, v) for a, c, v in fault.set_actions()]
        self._start(request, "set", fault, hil, steps)
        return request

    def clear(self, name: str | None = None) -> int:
        """Aktiven Fehler aufheben (name gesetzt: nur wenn er es ist; sonst nichts zu tun)."""
        request = self._new_request()
        if self._job is not None:
            return self._finish_now(request, False, tr("Fehlerbox ist beschäftigt"))
        if self._active is None and not self._locked:
            return self._finish_now(request, True, tr("Kein Fehler aktiv"))
        if name and self._active is not None and self._active.name != name:
            return self._finish_now(request, True, tr("'{name}' ist nicht aktiv", name=name))
        if self._active is None:
            # nach einem abgebrochenen Setzen: gesperrte Kanaele in den Ruhezustand
            steps = [(hil, *faultbox.to_action(kind, channel, 0)) for hil, chans in self._locked.items()
                     for kind, channel in sorted(chans)]
            self._start(request, "clear", None, "", steps)
            return request
        steps = [(self._active_hil, a, c, v) for a, c, v in self._active.clear_actions()]
        self._start(request, "clear", self._active, self._active_hil, steps)
        return request

    @Slot(str)
    def on_all_off(self, _failures: str = "") -> None:
        """ALLE AUS hat alle microHIL-Ausgaenge abgeschaltet: kein Fehler mehr aktiv (Ruhezustand)."""
        if self._active is not None or self._locked:
            logger.info("Fehlerbox: ALLE AUS hebt '%s' auf", self.active() or "?")
        self._active, self._active_hil, self._locked = None, "", {}
        if self._job is None:
            self._last = tr("durch ALLE AUS aufgehoben") if self._last else self._last
        self._emit_state()

    # -- Ablauf einer Schaltfolge ---------------------------------------------------------------

    def _new_request(self) -> int:
        self._next_id += 1
        return self._next_id

    def _finish_now(self, request: int, ok: bool, message: str) -> int:
        QTimer.singleShot(0, lambda: self.finished.emit(request, ok, message))
        if not ok:
            self._last = message
            self._emit_state()
        return request

    def _start(self, request: int, kind: str, fault, hil: str, steps) -> None:
        self._job = {"request": request, "kind": kind, "fault": fault, "hil": hil,
                     "name": fault.name if fault else "", "steps": list(steps)}
        if kind == "set":
            # Kanaele schon waehrend des Setzens sperren (alter und neuer Fehler)
            self._locked.setdefault(hil, set()).update(fault.channels())
        self._emit_state()
        self._next_action()

    def _next_action(self) -> None:
        job = self._job
        if job is None:
            return
        if not job["steps"]:
            self._job_done(True, "")
            return
        hil, action, channel, value = job["steps"].pop(0)
        if action == "WAIT":
            # alter Fehler ist aufgehoben: seine Kanaele frei, nur der neue bleibt gesperrt
            self._locked = {job["hil"]: set(job["fault"].channels())}
            self._active, self._active_hil = None, ""
            self._delay.start(int(channel))
            return
        self._pending_action = self._new_request()
        self._dispatch.emit(self._pending_action, hil, "hil", action, value, channel)

    @Slot(int, bool, str, float)
    def _on_completed(self, request_id: int, ok: bool, message: str, _value: float) -> None:
        if self._job is None or request_id != self._pending_action:
            return
        self._pending_action = 0
        if not ok:
            self._job_done(False, message or tr("Gerät hat abgelehnt"))
            return
        self._next_action()

    def _job_done(self, ok: bool, message: str) -> None:
        job, self._job = self._job, None
        name = job["name"]
        if job["kind"] == "set":
            if ok:
                self._active, self._active_hil = job["fault"], job["hil"]
                self._locked = {job["hil"]: set(job["fault"].channels())}
                self._last = tr("'{name}' gesetzt", name=name)
            else:
                self._active, self._active_hil = None, ""
                self._last = tr("'{name}' nicht gesetzt: {error}", name=name, error=message)
        else:
            if ok:
                self._active, self._active_hil, self._locked = None, "", {}
                self._last = tr("'{name}' aufgehoben", name=name) if name else tr("Kanäle in Ruhezustand")
            else:
                self._last = tr("Aufheben gescheitert: {error}", error=message)
        logger.info("Fehlerbox: %s", self._last)
        self._emit_state()
        self.finished.emit(job["request"], ok, "" if ok else self._last)

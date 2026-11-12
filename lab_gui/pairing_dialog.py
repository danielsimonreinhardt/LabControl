"""Oberflaeche der Kopplung (pairing.py): Dialog der fragenden Seite und Bestaetigung der anderen.

PairingSession fuehrt die Anfrage in einem eigenen Thread (HTTP blockiert), PairingDialog zeigt Code und
Stand, PairConfirmDialog fragt auf der anderen Seite "Koppeln?" mit demselben Code.
"""
from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from i18n import tr
from pairing import PAIR_TTL_S, format_code
from partner_client import PartnerClient, PartnerError
from partner_link import error_text

logger = logging.getLogger(__name__)

POLL_S = 0.5


class PairingSession(QObject):
    """Fragende Seite: Anfrage stellen, auf Bestaetigung warten, eigenen Token uebergeben."""

    # Code (6 Ziffern), Rechnername der Gegenseite
    code_received = Signal(str, str)
    # ok, Meldung (deutscher Text = Uebersetzungsschluessel, tr() erst im GUI-Thread), Zusatz (unuebersetzt),
    # Ergebnis {"token", "port", "host"} (nur bei ok)
    finished = Signal(bool, str, str, dict)

    def __init__(self, url: str, own_host: str, own_name: str, own_port: int, own_token: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = PartnerClient(url, "", own_host)
        self._own = (own_host, own_name, own_port, own_token)
        self._cancel = threading.Event()
        self._pair_id = ""
        self._thread = threading.Thread(target=self._run, name="Pairing", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        host, name, port, token = self._own
        try:
            answer = self._client.pair_request(host, name, port)
        except PartnerError as exc:
            self.finished.emit(False, *self._reason(exc), {})
            return
        self._pair_id = answer["pair"]
        self.code_received.emit(answer["code"], str(answer.get("host") or ""))
        deadline = time.monotonic() + PAIR_TTL_S + 2.0
        while not self._cancel.wait(POLL_S):
            if time.monotonic() > deadline:
                self.finished.emit(False, "Keine Bestätigung am anderen Rechner", "", {})
                return
            try:
                status = self._client.pair_status(self._pair_id)
            except PartnerError as exc:
                if exc.code == "unreachable":
                    continue   # kurz weg: weiter versuchen bis zur Frist
                self.finished.emit(False, *self._reason(exc), {})
                return
            state = status.get("status")
            if state == "pending":
                continue
            if state == "accepted":
                try:
                    self._client.pair_complete(self._pair_id, token)
                except PartnerError as exc:
                    self.finished.emit(False, *self._reason(exc), {})
                    return
                logger.info("Kopplung mit %s abgeschlossen", self._client.base_url)
                self.finished.emit(True, "", "", {"token": str(status.get("token") or ""),
                                                  "port": status.get("port"), "host": str(status.get("host") or "")})
                return
            texts = {"rejected": "Am anderen Rechner abgelehnt",
                     "expired": "Keine Bestätigung am anderen Rechner",
                     "cancelled": "Abgebrochen"}
            self.finished.emit(False, texts.get(state, str(state)), "", {})
            return
        # abgebrochen: der Gegenseite Bescheid geben, damit ihr Dialog verschwindet
        try:
            self._client.pair_cancel(self._pair_id)
        except PartnerError:
            pass
        self.finished.emit(False, "Abgebrochen", "", {})

    @staticmethod
    def _reason(exc: PartnerError) -> tuple[str, str]:
        if exc.code == "pair_busy":
            return "Am anderen Rechner läuft schon eine Kopplung – kurz warten und erneut versuchen", ""
        if exc.code in ("not_found", "not_implemented"):
            return "Der andere Rechner kann noch nicht koppeln (LabControl 0.50 oder neuer nötig)", ""
        return error_text(exc.code), (str(exc) if exc.code == "unreachable" else "")


def _code_label(code: str) -> QLabel:
    label = QLabel(format_code(code))
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 2.4)
    font.setBold(True)
    label.setFont(font)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return label


class PairingDialog(QDialog):
    """Fragende Seite: Adresse -> Code -> wartet auf die Bestaetigung am anderen Rechner."""

    def __init__(self, address: str, session: PairingSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Mit einem zweiten LabControl koppeln"))
        self._session = session
        self._done = False
        layout = QVBoxLayout(self)
        self._intro = QLabel(tr("Verbinde mit {address} …", address=address))
        self._intro.setWordWrap(True)
        layout.addWidget(self._intro)
        self._code = _code_label("------")
        self._code.hide()
        layout.addWidget(self._code)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        row = QHBoxLayout()
        row.addStretch(1)
        self._button = QPushButton(tr("Abbrechen"))
        self._button.clicked.connect(self._on_button)
        row.addWidget(self._button)
        layout.addLayout(row)
        session.code_received.connect(self._on_code)
        session.finished.connect(self._on_finished)

    def _on_code(self, code: str, host: str) -> None:
        self._intro.setText(tr("Am anderen Rechner{host} bestätigen", host=f" ({host})" if host else ""))
        self._code.setText(format_code(code))
        self._code.show()
        self._status.setText(tr("Warte auf die Bestätigung …"))

    def _on_finished(self, ok: bool, message: str, detail: str, result: dict) -> None:
        self._done = True
        self._code.setEnabled(False)
        self._status.setText(tr("Gekoppelt mit {host}. Der Verbund ist eingeschaltet.",
                                host=result.get("host") or "?") if ok
                             else tr(message) + (f" ({detail})" if detail else ""))
        self._status.setProperty("tone", "success" if ok else "danger")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self._button.setText(tr("Schließen"))

    def _on_button(self) -> None:
        if not self._done:
            self._session.cancel()
            self._button.setEnabled(False)
            self._status.setText(tr("Breche ab …"))
            return
        self.accept()

    def reject(self) -> None:   # Esc / Fenster schliessen
        if not self._done:
            self._session.cancel()
        super().reject()


class PairConfirmDialog(QDialog):
    """Bestaetigende Seite: "<Rechner> (<IP>) möchte koppeln -- Code 123 456 -- [Ablehnen] [Koppeln]"."""

    decided = Signal(str, bool)   # Anfrage-ID, angenommen?

    def __init__(self, pair_id: str, label: str, client_ip: str, code: str, parent: QWidget | None = None,
                 program: bool = False) -> None:
        super().__init__(parent)
        self.pair_id = pair_id
        self.setWindowTitle(tr("Kopplung anfragen"))
        layout = QVBoxLayout(self)
        # program: ein Programm (Skript, MCP-Server auf einem anderen Rechner) statt eines Verbund-Partners (PI-44 b)
        text = QLabel(tr("Programm „{name}“ ({ip}) möchte koppeln und steuern", name=label, ip=client_ip) if program
                      else tr("„{name}“ ({ip}) möchte koppeln", name=label, ip=client_ip))
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addWidget(_code_label(code))
        row = QHBoxLayout()
        row.addStretch(1)
        reject = QPushButton(tr("Ablehnen"))
        reject.clicked.connect(lambda: self._decide(False))
        # Der Knopf sagt, worauf es ankommt (vorher ein eigener Hinweis): nur bei gleichem Code koppeln
        accept = QPushButton(tr("Code gleich – koppeln"))
        accept.setDefault(True)
        accept.clicked.connect(lambda: self._decide(True))
        row.addWidget(reject)
        row.addWidget(accept)
        layout.addLayout(row)

    def _decide(self, accept: bool) -> None:
        self.decided.emit(self.pair_id, accept)
        self.done(QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected)

    def reject(self) -> None:   # Esc / Fenster schliessen = Ablehnen
        self.decided.emit(self.pair_id, False)
        super().reject()

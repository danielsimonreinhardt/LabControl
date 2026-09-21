"""HTTP-Server der Netzwerk-Freigabe: Socket- und Thread-Lebensdauer.

Bewusst duenn. Die gesamte Routing-, Auth- und Darstellungslogik steht in
share_api.py/share_render.py (beide ohne Qt und ohne Socket pruefbar) --
hier steht nur, was ohne echten Server nicht zu haben ist: binden, einen
Thread starten, sauber wieder abraeumen und Fehler nach oben melden.

Thread-Bild: der serve_forever()-Thread plus ein Thread je Verbindung. Sie
duerfen ausschliesslich live_state.LiveState.snapshot() aufrufen -- kein Qt,
kein Settings, kein Treiber (siehe den Modul-Docstring von live_state.py).
Die eine Ausnahme sind schreibende Anfragen: sie gehen ueber den `executor`
(share_remote.RemoteBridge), der genau dafuer gebaut ist, die Grenze zum
GUI- und Worker-Thread zu ueberqueren.

ZWEI FALLEN, die hier abgefangen werden und beide nur im Release auffallen
wuerden:

1. sys.stderr ist None. Die .exe wird mit --windowed gebaut, es gibt also
   keinen stderr. BaseHTTPRequestHandler.log_message()/log_error()
   schreiben aber bedingungslos dorthin -- ohne die Overrides unten wuerfe
   JEDE Anfrage in der .exe einen AttributeError, waehrend im Dev-Betrieb
   alles unauffaellig laeuft.
2. allow_reuse_address. Pythons HTTPServer setzt es auf 1. Unter Windows
   erlaubt SO_REUSEADDR damit einem FREMDEN Prozess, den bereits gebundenen
   Port zu uebernehmen (anders als unter Unix, wo es nur TIME_WAIT
   ueberbrueckt). Deshalb hier explizit False.
"""
from __future__ import annotations

import logging
import logging.handlers
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

import share_api
from paths import app_dir
from version import __version__

logger = logging.getLogger(__name__)

# Anfragen laufen in je einem Thread. Die Semaphore deckelt, wie viele
# gleichzeitig arbeiten duerfen -- billige Versicherung dagegen, dass ein
# fehlkonfigurierter oder boesartiger Client im LAN beliebig viele Threads
# erzeugt. Darueber gibt es 503 statt einer Warteschlange.
MAX_CONCURRENT_REQUESTS = 8
SOCKET_TIMEOUT_S = 10.0
SHUTDOWN_JOIN_S = 2.0

# Fehlgeschlagene Auth wird pro Client-IP hoechstens alle 10 s geloggt --
# sonst koennte ein Client in einer Schleife das Log fluten.
AUTH_LOG_INTERVAL_S = 10.0

# Eigenes Zugriffslog, getrennt von labdash.log. Grund: labdash.log ist auf
# 1 MB x 3 gedeckelt und die Datei, die man nach einer naechtlichen
# Sicherheitsabschaltung liest -- eine Zeile je Anfrage bei 1 Hz Polling
# haette sie binnen Stunden leergerollt.
ACCESS_LOG_PATH = app_dir() / "share_access.log"
ACCESS_LOG_MAX_BYTES = 200_000
ACCESS_LOG_BACKUPS = 1


def lan_address() -> str:
    """Vermutlich vom LAN aus erreichbare IPv4-Adresse dieses Rechners.

    Ueber einen UDP-Socket zu einer beliebigen externen Adresse: der Kernel
    waehlt dabei die Schnittstelle der Standardroute, es wird aber KEIN
    Paket gesendet -- funktioniert also auch ohne Internetzugang.
    socket.gethostbyname(gethostname()) waere naheliegender, liefert unter
    Windows aber gern 127.0.0.1 oder die Adresse eines VPN-Adapters.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1, per RFC 5737 nie geroutet
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _access_logger(enabled: bool) -> logging.Logger | None:
    if not enabled:
        return None
    log = logging.getLogger("share.access")
    log.propagate = False   # nicht zusaetzlich in labdash.log
    log.setLevel(logging.INFO)
    if not log.handlers:
        try:
            handler = logging.handlers.RotatingFileHandler(
                ACCESS_LOG_PATH, maxBytes=ACCESS_LOG_MAX_BYTES,
                backupCount=ACCESS_LOG_BACKUPS, encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            log.addHandler(handler)
        except OSError:
            return None   # Protokollierung ist nie wichtiger als der Dienst
    return log


class _ShareHTTPServer(ThreadingHTTPServer):
    daemon_threads = True        # kein Verbindungsthread haelt das Beenden auf
    allow_reuse_address = False  # siehe Modul-Docstring, Falle 2
    request_queue_size = 16

    def __init__(self, address, handler, state, app_info, access_log, executor=None):
        self.share_state = state
        self.share_executor = executor
        self.share_app_info = app_info
        self.share_access_log = access_log
        self.share_slots = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
        self.share_started_at = time.monotonic()
        self.share_auth_log_times: dict[str, float] = {}
        super().__init__(address, handler)

    def handle_error(self, request, client_address) -> None:
        """Ersetzt socketserver.BaseServer.handle_error, das einen Traceback nach
        stderr druckt.

        Zwei Gruende: (1) im --windowed-Build gibt es keinen stderr (siehe
        Modul-Docstring, Falle 1); (2) ein Client, der mitten in der Antwort
        die Verbindung verliert -- fuer einen ESP32 im WLAN Alltag --, ist
        kein Fehler des Servers und soll keinen Traceback erzeugen.
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            logger.debug("Netzwerk-Freigabe: Verbindung von %s abgebrochen (%s)", client_address[0], exc)
            return
        logger.exception("Netzwerk-Freigabe: unerwarteter Fehler bei Anfrage von %s", client_address[0])


class _ShareRequestHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 (der Default) heisst: jede Antwort schliesst die Verbindung.
    # Bewusst so gelassen -- es macht shutdown() prompt (keine haengenden
    # Keep-Alive-Threads) und ist die freundlichste Form fuer die minimalen
    # HTTP-Clients auf einem ESP32.
    server_version = "LabControl"
    sys_version = ""
    # Ein Client, der die Verbindung oeffnet und dann schweigt, soll
    # seinen Thread nicht dauerhaft belegen (socketserver wertet dieses
    # Klassenattribut je Verbindung aus).
    timeout = SOCKET_TIMEOUT_S

    # -- Falle 1: kein stderr im --windowed-Build ---------------------------

    def log_message(self, fmt: str, *args) -> None:
        access = getattr(self.server, "share_access_log", None)
        if access is not None:
            access.info("%s %s", self.client_address[0], fmt % args)

    def log_error(self, fmt: str, *args) -> None:
        logger.debug("Netzwerk-Freigabe: %s", fmt % args)

    # -----------------------------------------------------------------------

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        body = self._read_body()
        if body is not None:
            self._handle("POST", body)

    def _read_body(self) -> bytes | None:
        """Liest den Rumpf einer POST-Anfrage oder antwortet selbst mit dem
        Fehler und liefert None.

        Nur Content-Length, kein Chunked: ein Rumpf unbekannter Laenge liesse
        sich nicht sicher begrenzen. Die Obergrenze wird geprueft, BEVOR
        gelesen wird -- ein Client, der 2 GB ankuendigt, bekommt sein 413,
        ohne dass der Server sie annimmt.
        """
        def reject(status: int, code: str) -> None:
            self._send(share_api.Response(
                status, share_api.JSON_TYPE,
                ('{"v":1,"error":"%s"}' % code).encode()), "POST")

        if self.headers.get("Transfer-Encoding"):
            reject(411, "length_required")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            reject(411, "length_required")
            return None
        if length < 0 or length > share_api.MAX_BODY_BYTES:
            reject(413, "body_too_large")
            return None
        try:
            return self.rfile.read(length)
        except (TimeoutError, OSError):
            # Client kuendigt Daten an und liefert sie nicht: der Handler-
            # Timeout (SOCKET_TIMEOUT_S) beendet das Warten.
            reject(408, "request_timeout")
            return None

    def _handle(self, method: str, body: bytes = b"") -> None:
        slots = self.server.share_slots
        if not slots.acquire(blocking=False):
            self._send(share_api.Response(
                503, share_api.JSON_TYPE, b'{"v":1,"error":"busy"}'), method)
            return
        try:
            app_info = dict(self.server.share_app_info)
            app_info["uptime_s"] = time.monotonic() - self.server.share_started_at
            app_info["base_url"] = f"http://{self.headers.get('Host', '')}"
            response = share_api.dispatch(
                method, self.path, self.headers.get, self.server.share_state, app_info,
                body=body, executor=self.server.share_executor, client=self.client_address[0])
            if response.status == 401:
                self._log_auth_failure()
            elif method == "POST" and response.status not in (200, 202):
                # Abgewiesene Schreibversuche stehen im Hauptlog -- schreibende
                # Anfragen sind selten, ein Logeintrag je Versuch ist tragbar.
                logger.info("Fernsteuerung abgewiesen von %s: POST %s -> HTTP %s",
                            self.client_address[0], self.path.split("?")[0], response.status)
            self._send(response, method)
        except Exception:
            # Ein durchgereichter Fehler wuerde im Server-Thread landen, wo
            # ihn niemand sieht -- lieber 500 und eine Zeile im Log.
            logger.exception("Netzwerk-Freigabe: Anfrage fehlgeschlagen (%s %s)", method, self.path)
            self._send(share_api.Response(
                500, share_api.JSON_TYPE, b'{"v":1,"error":"internal"}'), method)
        finally:
            slots.release()

    def _log_auth_failure(self) -> None:
        ip = self.client_address[0]
        now = time.monotonic()
        last = self.server.share_auth_log_times.get(ip, 0.0)
        if now - last < AUTH_LOG_INTERVAL_S:
            return
        self.server.share_auth_log_times[ip] = now
        logger.warning("Netzwerk-Freigabe: Zugriff ohne gueltigen Token von %s", ip)

    def _send(self, response, method: str) -> None:
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Client (z.B. ein ESP32 mit knappem Timeout) hat aufgelegt --
            # voellig normal, kein Grund fuer einen Logeintrag pro Vorfall.
            logger.debug("Netzwerk-Freigabe: Verbindung vorzeitig beendet")


class ShareServer(QObject):
    """Lebensdauer des HTTP-Servers, gesteuert aus dem GUI-Thread.

    apply() ist die einzige Stelle, die den Server startet oder stoppt --
    sie haengt an Settings.share_config_changed und startet nur neu, wenn
    sich tatsaechlich (enabled, bind, port) geaendert hat. Token, Freigaben
    und die Lese-Auth wirken ohne Neustart, weil der Handler sie bei jeder
    Anfrage frisch aus LiveState liest.
    """

    started = Signal(str)   # anzeigbare Adresse
    stopped = Signal()
    error = Signal(str)     # Bindfehler o.ae. -- fuer den Einstellungen-Tab

    def __init__(self, state, executor=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = state
        self._executor = executor
        self._server: _ShareHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._current: tuple[bool, str, int] | None = None
        self._app_info = {"version": __version__, "simulation": False}
        self._url = ""

    def set_simulation(self, simulation: bool) -> None:
        # Dict komplett ersetzen statt zu mutieren: der Handler-Thread liest
        # es, und ein Austausch der Referenz ist atomar.
        self._app_info = {**self._app_info, "simulation": bool(simulation)}
        if self._server is not None:
            self._server.share_app_info = self._app_info

    def is_running(self) -> bool:
        return self._server is not None

    def url(self) -> str:
        """Anzeigbare Basisadresse des laufenden Servers ("" wenn er steht)."""
        return self._url

    def apply(self, config: dict) -> None:
        wanted = (bool(config.get("enabled")), config.get("bind", "127.0.0.1"),
                  int(config.get("port", 8420)))
        access_log_wanted = bool(config.get("access_log"))
        if wanted == self._current and self._server is not None:
            # Nur das Zugriffslog kann sich ohne Neustart geaendert haben.
            self._server.share_access_log = _access_logger(access_log_wanted)
            return
        self.stop()
        self._current = wanted
        enabled, bind, port = wanted
        if enabled:
            self._start(bind, port, access_log_wanted)

    def _start(self, bind: str, port: int, access_log: bool) -> None:
        try:
            server = _ShareHTTPServer(
                (bind, port), _ShareRequestHandler, self._state,
                self._app_info, _access_logger(access_log), self._executor)
        except OSError as exc:
            # Haeufigster Fall: Port belegt. Darf NIE in den GUI-Start
            # durchschlagen -- die App muss auch dann normal laufen.
            message = f"Port {port} nicht verfuegbar: {exc}"
            logger.warning("Netzwerk-Freigabe konnte nicht starten (%s:%s): %s", bind, port, exc)
            self._current = None
            self.error.emit(message)
            return
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="ShareServer", daemon=True)
        self._thread.start()
        shown = lan_address() if bind == "0.0.0.0" else bind
        url = f"http://{shown}:{port}"
        self._url = url
        logger.info("Netzwerk-Freigabe gestartet auf %s:%s (erreichbar als %s)", bind, port, url)
        self.started.emit(url)

    def stop(self) -> None:
        """Aus dem GUI-Thread aufzurufen -- niemals aus einem Handler heraus
        (shutdown() wartet auf die Schleife, die der Handler selbst
        blockiert, und verklemmt dann)."""
        server, thread = self._server, self._thread
        self._server = self._thread = None
        self._url = ""
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(SHUTDOWN_JOIN_S)
        logger.info("Netzwerk-Freigabe gestoppt")
        self.stopped.emit()

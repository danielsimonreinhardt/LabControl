"""Hauptfenster: Dashboard (immer sichtbar, abdockbar in ein eigenes Fenster) + Reiter (Control/Testcase) + Statusleiste."""
from __future__ import annotations

import dataclasses
import logging
import os
import re
import threading
import time

import qtawesome as qta
from PySide6.QtCore import QByteArray, QEvent, QMetaObject, QPoint, QThread, QTimer, QUrl, Q_ARG, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

import app_logging
import data_store
import remote_actions
import system_power
import verbund_ids
from control_tab import ControlTab
from dashboard import DashboardWidget
from dashboard_window import DashboardWindow
from device_registry import DeviceRegistry
from device_worker import (
    REASON_WINDOW_CLOSE, SIM_BATTSIM_ID, SIM_CAN_ID, DeviceWorker, battsim_device_id, can_device_id,
)
from i18n import Translator, tr
from live_state import LiveState
from presets import PresetStore
from recording import Recorder
from run_record import RunRecorder
from safety import SafetyMonitor
from scope_service import ScopeService
from settings import Settings
from settings_tab import SettingsTab
from share_api import new_token
from jlink.driver import find_commander as find_jlink_commander
from jlink_service import JLinkService
from pairing_dialog import PairConfirmDialog, PairingDialog, PairingSession
from partner_client import normalize_url, resolve_partner_ip
from partner_link import PartnerLink
from partner_passthrough import PartnerPassthrough
from partner_devices import PartnerDevices
from share_remote import RemoteBridge
from share_server import ShareServer, is_verbund_client
from testcase_model import TestStep, kind_label
from testcase_runner import TestRunner
from testcase_tab import TestcaseTab
from theme import METRICS, PANEL_COLOR_ORDER, Palette, ThemeManager, no_own_background
from timeline_tab import TimelineTab
from touch_ui import FONT_SCALE, NavBar, TopBar, suggest_touch, touch_icon_path
from touch_ui import sizer as touch_sizer
from version import __version__

logger = logging.getLogger(__name__)

# Touch-Profil: Kurznamen fuer die Status-Chips oben (MainWindow._short_name, GUI-Review UX-1)
STATUS_KIND_SHORT = {"psu": "NT", "load": "Last", "can": "CAN", "hil": "HIL", "picoscope": "Scope",
                     "scpiscope": "Scope", "fg": "FG", "battsim": "BS", "jlink": "JL"}
STATUS_CHIP_HEIGHT = 28      # Touch-Profil
STATUS_CHIP_HEIGHT_PC = 22   # PC: passt in die Statusleiste (PI-17/PI-18 a)
SAFETY_ICONS = {"off": "mdi.shield-off-outline", "armed": "mdi.shield-check", "tripped": "mdi.shield-alert"}
# Verbund (PI-33 a): ab dieser Abweichung der Uhren warnt der Leitstand -- Aufzeichnung und Bericht
# mischen Zeitstempel beider Rechner, die Uhren sollen ueber NTP laufen.
CLOCK_WARN_S = 1.0


def lan_scope_hosts(addresses: list) -> list[str]:
    """Adressen (IP/Hostname) der eingetragenen LAN-Oszilloskope (PI-29 a); USB und Simulation zaehlen
    nicht -- die stecken an genau einem Rechner."""
    from unit_mso1000hd.driver import KIND, SIM_RESOURCE, device_id_for, normalize_resource
    hosts = []
    for address in addresses or []:
        try:
            resource = normalize_resource(str(address))
        except ValueError:
            continue
        if resource != SIM_RESOURCE and resource.upper().startswith("TCPIP"):
            hosts.append(device_id_for(resource).removeprefix(f"{KIND}:").lower())
    return sorted(set(hosts))


class MainWindow(QMainWindow):
    # An den DeviceWorker weitergereichte Testablauf-Aktion, nachdem eine leere
    # device_id (siehe testcase_model.TestStep) auf ein konkretes Geraet
    # aufgeloest wurde. Eigenes Signal statt direktem Methodenaufruf, damit die
    # Verbindung -- wie alle anderen Worker-Aufrufe -- ueber eine Queued
    # Connection korrekt in den Worker-Thread gelangt.
    _dispatch_test_action = Signal(str, str, str, float, int)  # device_id, kind, action, value, channel
    # Aktion aus der Netzwerk-Fernsteuerung, nach der letzten Pruefung im GUI-
    # Thread (siehe _on_share_action) an den Worker weitergereicht -- eigenes
    # Signal aus demselben Grund wie _dispatch_test_action, mit Anfrage-ID vorn.
    _dispatch_share_action = Signal(int, str, str, str, float, int)  # id, device_id, kind, action, value, channel
    # device_id, arbitration_id, data (Hex-String), extended -- eigenes Signal
    # aus demselben Grund wie _dispatch_test_action (Queued Connection in den
    # Worker-Thread), siehe testcase_runner.TestRunner.execute_can_send.
    _dispatch_test_can_send = Signal(str, int, str, bool)
    # An DeviceWorker.set_test_running weitergereicht (pausiert den PicoScope-
    # Reconnect-Timer waehrend eines Testlaufs) -- eigenes Signal aus demselben
    # Grund wie _dispatch_test_action (Queued Connection in den Worker-Thread).
    _set_picoscope_test_running = Signal(bool)
    # DeviceWorker.open_picoscope_session() wird NICHT ueber ein eigenes
    # Signal aufgerufen (anders als die uebrigen Worker-Aufrufe hier),
    # sondern per QMetaObject.invokeMethod(..., BlockingQueuedConnection) in
    # _on_run_requested -- ein einfaches emit() kehrt sofort zurueck, WAEHREND
    # das Oeffnen (~4,5s) noch auf dem Worker-Thread laeuft, wodurch der
    # direkt danach gestartete Testlauf seinen ersten PICO_*-Schritt schon
    # vor Fertigstellung der Session ausfuehren konnte (an echter Hardware
    # reproduziert). Die blockierende Variante wartet, bis die Session steht
    # (oder das best-effort Oeffnen scheitert, siehe dortigen Docstring).
    # An DeviceWorker.close_picoscope_sessions weitergereicht -- am Laufende
    # (fertig/gestoppt/fehlgeschlagen), No-Op falls nie eine Session offen war.
    _close_picoscope_sessions = Signal()

    # An den DeviceWorker weitergereichte Sicherheitsabschaltung (Watchdog-Trip,
    # Stop-Button, Schrittfehler, manueller Panic-Button) -- eigenes Signal aus
    # demselben Grund wie _dispatch_test_action (Queued Connection in den
    # Worker-Thread).
    _request_all_off = Signal(str)  # reason
    # Nur bestimmte Geraete abschalten (abgelaufene Verbund-Belegung, PI-26 a): device_ids, reason
    _request_devices_off = Signal(list, str)
    # PI-28: Partner fuehrt einen Batterie-Simulator -> hier kein 0x130 von Hand/aus Ablaeufen
    _request_partner_battsim = Signal(bool)

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"LAB CONTROL v{__version__}")
        self.resize(1000, 700)

        central = QWidget()
        outer = QVBoxLayout(central)
        # Rahmen wie im Touch-Profil (touch_ui.py), seit 0.53.0 auch am PC: Leiste oben statt Statusleiste,
        # Navigation links statt Reiterleiste. Rechts daneben Sicherheitsbanner, Dashboard und Seiten.
        self._touch = False
        self._base_font = None
        self._top_bar = TopBar()
        outer.addWidget(self._top_bar)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)

        self._safety_banner = QWidget()
        self._safety_banner.setObjectName("safetyBanner")
        # Ohne dieses Attribut malt ein einfaches QWidget seinen per QSS
        # gesetzten background-color NICHT (bekanntes Qt-Verhalten, siehe
        # dasselbe Muster in testcase_tab.py) -- der Banner blieb dadurch
        # transparent/auf dem normalen Fensterhintergrund, wodurch der
        # hartkodiert weisse Label-Text im Light-Theme auf hellem statt
        # rotem Grund unleserlich war.
        self._safety_banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        banner_layout = QHBoxLayout(self._safety_banner)
        banner_layout.setContentsMargins(8, 4, 8, 4)
        self._safety_banner_label = QLabel()
        self._safety_banner_label.setWordWrap(True)
        banner_layout.addWidget(self._safety_banner_label, 1)
        self._safety_ack_button = QPushButton()
        self._safety_ack_button.clicked.connect(self._on_safety_acknowledge)
        banner_layout.addWidget(self._safety_ack_button)
        self._safety_banner.hide()
        layout.addWidget(self._safety_banner)

        self.dashboard = DashboardWidget()
        layout.addWidget(self.dashboard)
        # Touch-Profil: das Dashboard (feste Hoehe) oben auf einer eigenen Reiterseite "Uebersicht"
        # Scrollbar, damit die volle Kachelhoehe (zwei Reihen) das Fenster nicht ueber 600 px treibt.
        self._overview_page = QScrollArea()
        self._overview_page.setWidgetResizable(True)
        self._overview_page.setFrameShape(QFrame.Shape.NoFrame)
        overview_holder = QWidget()
        self._overview_layout = QVBoxLayout(overview_holder)
        self._overview_layout.setContentsMargins(0, 0, 0, 0)
        self._overview_layout.addStretch(1)
        self._overview_page.setWidget(overview_holder)
        # Fuer das Abdocken (siehe _detach_dashboard): das Dashboard wird aus
        # diesem Layout genommen und spaeter an derselben Stelle wieder eingesetzt.
        self._central_layout = layout
        self._dashboard_window: DashboardWindow | None = None
        self._closing = False

        self._presets = PresetStore()

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")   # Touch-Profil: ohne Rahmen um den Inhalt (touch_ui.touch_qss)
        self.control_tab = ControlTab(self._presets)
        self.testcase_tab = TestcaseTab()
        self.timeline_tab = TimelineTab()
        self.settings_tab = SettingsTab()
        # Verbund Pi + PC (PI-23 a): Verbindung zur Partner-Instanz. Seit 0.52.0 ohne eigenen Reiter: die
        # Partnergeraete stehen in Uebersicht und Steuerung wie eigene, mit Symbol (partner_devices.py).
        self._partner = PartnerLink(app_logging.host_name(), self)
        self._partner_devices = PartnerDevices(self._partner, self)
        self.tabs.addTab(self.control_tab, "")
        self.tabs.addTab(self.testcase_tab, "")
        self.tabs.addTab(self.timeline_tab, "")
        self.tabs.addTab(self.settings_tab, "")
        # Stretch 1: ueberschuessige Fensterhoehe geht komplett an die Tabs,
        # nie an Dashboard oder Sicherheitsbanner darueber.
        layout.addWidget(self.tabs, 1)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        self._nav = NavBar()
        body.addWidget(self._nav)
        body.addWidget(content, 1)
        outer.addLayout(body, 1)

        self.setCentralWidget(central)
        # Meldungen (statusBar().showMessage) als kurz eingeblendeter Hinweis unten -- die Statusleiste
        # selbst ist verborgen, ihren Platz hat die Leiste oben (_show_toast)
        self._toast = QLabel(central)
        self._toast.setObjectName("statusToast")
        self._toast.setWordWrap(True)
        self._toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._toast.hide()
        self.statusBar().messageChanged.connect(self._show_toast)

        self._status_container = no_own_background(QWidget())
        self._status_layout = QHBoxLayout(self._status_container)
        self._status_layout.setContentsMargins(0, 0, 0, 0)
        self._status_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Sicherheits-Chip steht fest links vor dem wischbaren Bereich (TopBar.adopt, leading)
        self._safety_status_label = QLabel()
        self._safety_status_label.installEventFilter(self)
        self._safety_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        # Verbund-Chip "⇄ Pi" (verbunden/getrennt), nur mit eingetragenem Partner sichtbar
        self._partner_chip = QLabel()
        self._partner_chip.hide()
        self._status_layout.addWidget(self._partner_chip)
        # MCP-Chip (PI-39 b): darf Claude (MCP) an diesem Rechner steuern? Nur mit Netzwerk-Freigabe sichtbar
        self._mcp_chip = QLabel()
        self._mcp_chip.hide()
        self._status_layout.addWidget(self._mcp_chip)
        self._status_container.installEventFilter(self)   # Tipp/Klick -> Liste (_show_status_details)
        self._status_container.setCursor(Qt.CursorShape.PointingHandCursor)   # am PC: klickbar (PI-17 a)
        self._status_layout.setSpacing(6)

        self._status_labels: dict[str, QLabel] = {}
        self._device_labels: dict[str, str] = {}
        self._device_kinds: dict[str, str] = {}
        self._device_online: dict[str, bool] = {}
        # Netzteile in Handbedienung (siehe _on_psu_manual_state)
        self._manual_devices: set[str] = set()
        self._online_devices: dict[str, set[str]] = {
            "load": set(), "psu": set(), "can": set(), "hil": set(), "picoscope": set(), "fg": set(),
            "scpiscope": set(), "battsim": set(), "jlink": set(),
        }

        self._registry = DeviceRegistry()
        self._settings = settings if settings is not None else Settings()
        self._recorder = Recorder()
        self._run_recorder = RunRecorder()
        self._safety = SafetyMonitor(self._settings.safety_limits)
        # Wertespeicher und HTTP-Server der Netzwerk-Freigabe. LiveState ist
        # eine reine Senke wie self._recorder (siehe _setup_worker), der
        # Server startet erst, wenn die Einstellungen es hergeben.
        self._live_state = LiveState()
        self._share_bridge = RemoteBridge(self)
        # Oszilloskop-Erfassungen ueber Netzwerk/MCP (scope_api, Phase 2). Eigener
        # Thread fuer die DLL-Aufrufe; Erfassungen landen zusaetzlich als CSV in
        # captures/ neben der App (Entscheidung E3). Mit Netzlaufwerk schiebt data_store sie
        # dorthin; der CSV-Pfad der Erfassung zieht dann mit.
        self._scope_service = ScopeService(data_store.store().local_dir("captures"), self)
        data_store.store().on_moved(self._scope_service.relocate_csv)
        self._scope_service.connection_changed.connect(self._on_scope_connection_changed)
        # device_id -> capture_id der letzten Erfassung (Knopf "Kurve" der Kachel)
        self._last_scope_capture: dict[str, str] = {}
        self._share_server = ShareServer(self._live_state, self._share_bridge, self,
                                         scopes=self._scope_service)
        # Sekundentakt nur, solange der Hauptschalter der Fernsteuerung an ist:
        # aktualisiert die Restzeit-Anzeige und bemerkt den Ablauf. Die
        # Freigabe selbst endet unabhaengig davon (LiveState vergleicht bei
        # jeder Anfrage gegen die Uhr).
        self._remote_timer = QTimer(self)
        self._remote_timer.setInterval(1000)
        self._remote_timer.timeout.connect(self._refresh_remote_status)

        self._setup_worker()
        self._wire_safety()
        self._wire_registry()
        self._wire_control_tab()
        self._wire_testcase_tab()
        self._wire_recording()
        self._wire_settings_tab()
        self._wire_dashboard_view()
        self._wire_notifications()
        self._wire_panel_colors()
        self._wire_panel_order()
        self._wire_control_tile_order()
        # Muss nach _setup_worker() (braucht self._worker) und vor
        # _replay_known_devices() laufen, damit die Kacheln ihre Labels
        # bekommen -- gleiche Bedingung wie bei _wire_panel_order().
        self._wire_share()
        self._replay_known_devices()

        # Sichtbarer Hinweis, solange die Fernsteuerung freigegeben ist -- in der Leiste oben, damit man
        # ihn auf jedem Reiter sieht. Vor dem ALLE-AUS-Knopf, der ganz rechts bleibt (TopBar.adopt in
        # _wire_touch).
        self._remote_status_label = QLabel()
        self._remote_status_label.hide()
        self._all_off_button = QPushButton()
        self._all_off_button.clicked.connect(
            lambda: self._safe_stop("manual all-off", propagate=True, stop_run=True))
        self._style_all_off_button()

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._wire_touch()
        self._wire_partner()
        self._wire_jlink()
        self._style_safety_banner()
        self._retranslate()

    def _retranslate(self) -> None:
        for page, text in ((self.control_tab, tr("Steuerung")), (self.testcase_tab, tr("Testablauf")),
                           (self.timeline_tab, tr("Verlauf")), (self.settings_tab, tr("Einstellungen"))):
            self.tabs.setTabText(self.tabs.indexOf(page), text)
        self._render_partner_chip()
        self._render_mcp_chip()
        if self.tabs.indexOf(self._overview_page) >= 0:
            self.tabs.setTabText(self.tabs.indexOf(self._overview_page), tr("Übersicht"))
        self._nav.retranslate()
        self._top_bar.retranslate()
        for device_id in self._status_labels:
            self._render_status_label(device_id)
        self._safety_ack_button.setText(tr("Quittieren"))
        self._render_safety_status(self._safety.current_state())
        self._all_off_button.setText(tr("ALLE AUS"))
        self._all_off_button.setToolTip(tr("Alle Ausgänge sofort abschalten"))

    def _setup_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = DeviceWorker(
            simulation_mode=self._settings.simulation_mode,
            can_configs=self._settings.can_configs,
            scpi_scopes=self._settings.scpi_scopes,
        )
        self._worker.moveToThread(self._thread)

        self._worker.device_added.connect(self._registry.on_device_added)
        self._worker.device_removed.connect(self._registry.on_device_removed)
        self._worker.load_connected.connect(self._on_load_connected)
        self._worker.psu_connected.connect(self._on_psu_connected)
        self._worker.can_connected.connect(self._on_can_connected)
        self._worker.hil_connected.connect(self._on_hil_connected)
        self._worker.hil_info.connect(self.settings_tab.set_hil_firmware_version)
        self._worker.fg_connected.connect(self._on_fg_connected)
        self._worker.fg_state.connect(self.dashboard.update_fg)
        self._worker.fg_state.connect(self.control_tab.set_fg_state)
        self._worker.fg_state.connect(self._live_state.on_fg_state)
        self._worker.picoscope_connected.connect(self._on_picoscope_connected)
        self._worker.picoscope_state.connect(self.dashboard.update_picoscope_state)
        self._worker.picoscope_state.connect(self.control_tab.update_picoscope_state)
        self._worker.picoscope_connected.connect(self._scope_service.on_picoscope_connected)
        self._worker.picoscope_state.connect(self._scope_service.on_picoscope_state)
        self.dashboard.picoscope_release_requested.connect(self._scope_service.release_async)
        self._scope_service.capture_added.connect(self._on_scope_capture_added)
        self.dashboard.picoscope_plot_requested.connect(self._show_scope_plot)
        # SCPI-Oszilloskope (UNI-T MSO1000HD ueber LAN, seit 0.21.0): dieselben
        # Kacheln und derselbe ScopeService wie beim PicoScope.
        self._worker.scpiscope_connected.connect(self._on_scpiscope_connected)
        self._worker.scpiscope_state.connect(self.dashboard.update_picoscope_state)
        self._worker.scpiscope_state.connect(self.control_tab.update_picoscope_state)
        self._worker.scpiscope_connected.connect(self._scope_service.on_scope_connected)
        self._worker.scpiscope_state.connect(self._scope_service.on_scope_state)
        self._worker.scpiscope_state.connect(self._live_state.on_picoscope_state)
        # Batterie-Simulator (CAN-Knoten an einem konfigurierten Interface, seit 0.22.0).
        self._worker.battsim_connected.connect(self._on_battsim_connected)
        self._worker.battsim_state.connect(self.dashboard.update_battsim)
        self._worker.battsim_state.connect(self.control_tab.set_battsim_state)
        self._worker.battsim_state.connect(self._live_state.on_battsim_state)
        self._worker.set_battsim_voltage_limits(self._settings.battsim_voltage_limits)
        self._worker.load_measurement.connect(self.dashboard.update_load)
        self._worker.psu_measurement.connect(self.dashboard.update_psu)
        self._worker.can_stats.connect(self.dashboard.update_can)
        self._worker.hil_digital_state.connect(self.dashboard.update_hil_digital)
        self._worker.hil_relay_state.connect(self.dashboard.update_hil_relays)
        self._worker.hil_analog_input.connect(self.dashboard.update_hil_analog_in)
        self._worker.hil_pwr12_state.connect(self.dashboard.update_hil_pwr12)
        self._worker.hil_digital_state.connect(self.control_tab.set_hil_digital_state)
        self._worker.hil_relay_state.connect(self.control_tab.set_hil_relay_state)
        self._worker.hil_pwr12_state.connect(self.control_tab.set_hil_pwr12_state)
        self._worker.load_measurement.connect(self.timeline_tab.update_load)
        self._worker.psu_measurement.connect(self.timeline_tab.update_psu)
        self._worker.hil_digital_state.connect(self.timeline_tab.update_hil_digital)
        self._worker.hil_relay_state.connect(self.timeline_tab.update_hil_relays)
        self._worker.hil_analog_input.connect(self.timeline_tab.update_hil_analog_in)
        self._worker.hil_pwr12_state.connect(self.timeline_tab.update_hil_pwr12)
        self._worker.can_signals_decoded.connect(self.timeline_tab.on_can_signals_decoded)
        self._worker.load_measurement.connect(self._recorder.on_load_measurement)
        self._worker.psu_measurement.connect(self._recorder.on_psu_measurement)
        # Netzwerk-Freigabe: dieselbe Senken-Rolle wie der Recorder, nur
        # ohne Historie (siehe live_state.py).
        self._worker.load_measurement.connect(self._live_state.on_load_measurement)
        self._worker.psu_measurement.connect(self._live_state.on_psu_measurement)
        self._worker.load_function_state.connect(self._live_state.on_load_function_state)
        self._worker.load_input_state.connect(self._live_state.on_load_input_state)
        self._worker.psu_output_state.connect(self._live_state.on_psu_output_state)
        self._worker.psu_setpoints.connect(self._live_state.on_psu_setpoints)
        self._worker.can_stats.connect(self._live_state.on_can_stats)
        self._worker.hil_digital_state.connect(self._live_state.on_hil_digital_state)
        self._worker.hil_relay_state.connect(self._live_state.on_hil_relay_state)
        self._worker.hil_analog_input.connect(self._live_state.on_hil_analog_input)
        self._worker.hil_pwr12_state.connect(self._live_state.on_hil_pwr12_state)
        self._worker.picoscope_state.connect(self._live_state.on_picoscope_state)
        self._worker.load_input_state.connect(self.control_tab.set_load_input_state)
        self._worker.load_function_state.connect(self.dashboard.set_load_mode)
        self._worker.psu_output_state.connect(self.control_tab.set_psu_output_state)
        self._worker.psu_setpoints.connect(self.control_tab.set_psu_setpoints)
        self._worker.psu_manual_state.connect(self._on_psu_manual_state)
        # Nennwerte VOR den Schwellen verbinden (Reihenfolge der Verbindungen =
        # Reihenfolge der Aufrufe): erst die Bereiche der Felder, dann OVP/OCP.
        self._worker.psu_ratings.connect(self.control_tab.set_psu_ratings)
        self._worker.psu_ratings.connect(self.settings_tab.set_psu_ratings)
        self._worker.psu_ratings.connect(self.testcase_tab.on_psu_ratings)
        self._worker.psu_ratings.connect(self._live_state.on_psu_ratings)
        self._worker.psu_limits.connect(self.control_tab.set_psu_limits)
        self._worker.psu_limits.connect(self.testcase_tab.on_psu_limits)
        self._worker.can_frame_received.connect(self.control_tab.on_can_frame)
        self._worker.can_signals_decoded.connect(self.control_tab.on_can_signals_decoded)

        self._thread.started.connect(self._worker.start)
        self._thread.start()

    def _wire_safety(self) -> None:
        self._worker.load_measurement.connect(self._safety.on_load_measurement)
        self._worker.psu_measurement.connect(self._safety.on_psu_measurement)
        self._worker.can_stats.connect(self._safety.on_can_stats)
        self._worker.hil_digital_state.connect(self._safety.on_hil_digital_state)
        self._worker.device_removed.connect(self._safety.on_device_removed)
        self._safety.all_off_requested.connect(self._worker.all_outputs_off)
        self._request_all_off.connect(self._worker.all_outputs_off)
        self._request_devices_off.connect(self._worker.devices_off)
        self._request_partner_battsim.connect(self._worker.set_partner_battsim)
        self.control_tab.operation_source.connect(self._worker.set_operation_source)   # PI-15: Preset im Log
        self._worker.all_off_finished.connect(self._on_all_off_finished)
        self._settings.safety_limits_changed.connect(self._safety.set_limits)
        self._safety.tripped.connect(self._on_safety_tripped)
        self._safety.state_changed.connect(self._render_safety_status)
        # Sperrzustand der Netzwerk-Freigabe: ein Trip macht sich sofort
        # im Status-Endpoint bemerkbar (siehe live_state._lock_state).
        self._safety.state_changed.connect(self._live_state.on_safety_state_changed)

    def _safe_stop(self, reason: str, propagate: bool = False, stop_run: bool = False) -> None:
        """propagate: auch am Verbund-Partner ALLE AUS (PI-27 a) -- fuer ALLE AUS per Knopf, MCP oder
        Netzwerk, nicht fuer Stop/Fehler eines Testablaufs (der betrifft nur die eigenen Geraete).
        stop_run: ein laufender Testablauf wird abgebrochen -- sonst schaltete sein naechster Schritt
        die gerade abgeschalteten Ausgaenge wieder ein (seit 0.47.0, auch ohne Verbund)."""
        logger.warning("Safe-Stop ausgelöst: %s", reason)
        self._request_all_off.emit(reason)
        if stop_run and self._test_runner.is_running():
            self._test_runner.abort(tr("ALLE AUS – Ablauf abgebrochen"))
        if propagate:
            self._partner.all_off(reason)
        self._passthrough.release("ALLE AUS")

    # -- Verbund Pi + PC (PI-23 a, PI-27 a) ------------------------------------

    def _wire_partner(self) -> None:
        self.settings_tab.partner_config_edited.connect(lambda cfg: self._settings.set_partner_config(**cfg))
        self._settings.partner_config_changed.connect(self._on_partner_config)
        # Kopplung (pairing.py): fragende Seite (Knopf "Koppeln…") und bestaetigende Seite (_check_pairing)
        self.settings_tab.partner_pair_requested.connect(self._start_pairing)
        self._pair_session: PairingSession | None = None
        self._pair_dialog: PairingDialog | None = None
        self._pair_confirm: PairConfirmDialog | None = None
        self._partner.connection_changed.connect(lambda *_: self._render_partner_chip())
        self._partner.snapshot_changed.connect(lambda *_: self._render_partner_chip())
        self._partner.snapshot_changed.connect(self._on_partner_snapshot)
        self._partner.all_off_finished.connect(self._on_partner_all_off_finished)
        # Leitstand (V2): Bedienungen eines Testablaufs, Belegung und deren Verlust
        self._partner_host_name = ""
        self._partner_battsim = False
        self._verbund_warnings: list[str] = []
        self._partner_known: dict[str, str] = {}   # Partnergeraet -> Anzeigename in Verlauf/Aufzeichnung
        self._pending_partner_req = 0
        self._pending_run_steps: list[TestStep] | None = None
        self._partner.action_finished.connect(self._on_partner_action_finished)
        self._partner.lease_acquired.connect(self._on_partner_lease_acquired)
        self._partner.lease_lost.connect(self._on_partner_lease_lost)
        self._run_has_lease = False
        for sig in (self._test_runner.run_finished, self._test_runner.run_stopped):
            sig.connect(self._release_run_lease)
        self._test_runner.step_failed.connect(lambda *_args: self._release_run_lease())
        # Claude (MCP) im Verbund (PI-32 a): Befehle an durchgereichte Partnerkacheln
        self._passthrough = PartnerPassthrough(self._partner, self._share_bridge, self._passthrough_blocked, self)
        # Besitzer (V2): Belegungen durch den Partner anzeigen, abgelaufene abschalten
        self._lease_view: dict[str, str] = {}
        self._lease_timer = QTimer(self)
        self._lease_timer.setInterval(250)
        self._lease_timer.timeout.connect(self._check_leases)
        self._lease_timer.start()
        self._wire_partner_devices()
        self._on_partner_config(self._settings.partner_config)

    def _wire_partner_devices(self) -> None:
        """Partnergeraete in Uebersicht und Steuerung (0.52.0): dieselben Slots wie fuer eigene Geraete."""
        pd = self._partner_devices
        dash, ctrl = self.dashboard, self.control_tab
        pd.device_known.connect(self._on_partner_device_known)
        pd.device_forgotten.connect(lambda _kind, rid: (dash.forget_device(rid), ctrl.forget_device(rid)))
        pd.online_changed.connect(self._on_partner_device_online)
        pd.control_changed.connect(ctrl.set_remote_controllable)
        for signal, slots in (
                (pd.psu_measurement, (dash.update_psu,)),
                (pd.psu_output_state, (ctrl.set_psu_output_state,)),
                (pd.psu_setpoints, (ctrl.set_psu_setpoints,)),
                (pd.psu_ratings, (ctrl.set_psu_ratings,)),
                (pd.load_measurement, (dash.update_load,)),
                (pd.load_function_state, (dash.set_load_mode,)),
                (pd.load_input_state, (ctrl.set_load_input_state,)),
                (pd.can_stats, (dash.update_can,)),
                (pd.battsim_state, (dash.update_battsim, ctrl.set_battsim_state)),
                (pd.fg_state, (dash.update_fg, ctrl.set_fg_state)),
                (pd.hil_digital_state, (dash.update_hil_digital, ctrl.set_hil_digital_state)),
                (pd.hil_relay_state, (dash.update_hil_relays, ctrl.set_hil_relay_state)),
                (pd.hil_analog_input, (dash.update_hil_analog_in,))):
            for slot in slots:
                signal.connect(slot)
        pd.action_failed.connect(self._on_partner_tile_action_failed)

    def _on_partner_device_known(self, kind: str, rid: str, label: str) -> None:
        host, _tile_id = verbund_ids.split(rid)
        self.dashboard.on_device_known(kind, rid, label)
        self.dashboard.set_remote(rid, host or "")
        if kind != "can":   # CAN des Partners: nur Zaehler in der Uebersicht, Senden geht nicht ueber den Verbund
            self.control_tab.on_device_known(kind, rid, label)   # section_created -> _on_control_section_created
            self.control_tab.set_remote(rid, host or "")

    def _on_partner_device_online(self, kind: str, rid: str, online: bool) -> None:
        for view in (self.dashboard, self.control_tab):
            setter = getattr(view, f"set_{kind}_online", None)
            if setter is not None:
                setter(rid, online)

    def _on_partner_tile_action_failed(self, rid: str, action: str, message: str) -> None:
        """Bedienung einer Partnerkachel abgelehnt: kurz an der Kachel zeigen (Touch hat keine Statusleiste)."""
        from partner_link import error_text
        text = f"✕ {action}: {tr(error_text(message))}"
        logger.warning("Verbund: %s %s abgelehnt: %s", rid, action, message)
        section = self.control_tab.section(rid)
        if section is not None and section.isVisible():
            QToolTip.showText(section.mapToGlobal(section.rect().center()), text, section, section.rect(), 6000)
        else:
            self.statusBar().showMessage(text, 8000)

    def _release_run_lease(self) -> None:
        if self._run_has_lease:
            self._run_has_lease = False
            self._partner.release_lease()

    def _passthrough_blocked(self) -> str:
        """Fuer PartnerPassthrough: dieselbe letzte Pruefung wie _on_share_action fuer eigene Geraete
        (durchgereicht wird nur an Aufrufer auf diesem Rechner, deshalb gilt die lokale Ausnahme)."""
        if self._safety.is_tripped() or self._test_runner.is_running() or self._pending_run_steps is not None:
            return "locked"
        if not (self._live_state.remote_active() or self._settings.share_config["local_bypass"]):
            return "inactive"
        return ""

    def _resolve_partner_ip(self, address: str) -> None:
        """PI-39 b: Partner-Adresse -> IP fuer share_api.is_partner_caller. Rechnernamen loest ein kurzer
        Hintergrund-Thread auf (DNS kann haengen); bis dahin gilt die alte Adresse nicht mehr."""
        self._live_state.set_partner_ip("")
        if not address:
            return
        live_state = self._live_state
        generation = self._partner_ip_gen = getattr(self, "_partner_ip_gen", 0) + 1

        def work() -> None:
            ip = resolve_partner_ip(address)
            if generation == self._partner_ip_gen:
                live_state.set_partner_ip(ip)
        threading.Thread(target=work, name="PartnerIp", daemon=True).start()

    def _on_partner_config(self, config: dict) -> None:
        self.settings_tab.set_partner_config(config)
        self._passthrough.release("Verbund umgestellt")
        self._partner.apply(config)
        visible = self._partner.enabled
        self._resolve_partner_ip(config.get("address", "") if visible else "")
        self.testcase_tab.set_verbund(app_logging.host_name(), self._partner_host_name, visible)
        if not visible:
            self._set_partner_battsim(False)
            self._partner_devices.forget_all()
        self._render_partner_chip()

    def _check_verbund_warnings(self, snap) -> None:
        """Hinweise zum Verbund (Chip, Einstellungen, Log bei Wechsel): dasselbe LAN-Oszilloskop an
        beiden Rechnern eingetragen (PI-29 a), Uhren weichen ab (PI-33 a, NTP)."""
        warnings = []
        kinds = []   # Art der Warnung: ins Log nur, wenn eine Art neu auftritt (nicht bei jedem neuen Messwert)
        both = sorted(set(lan_scope_hosts(self._settings.scpi_scopes)) & {s.lower() for s in snap.lan_scopes})
        if both:
            warnings.append(tr("LAN-Oszilloskop {hosts} ist auch bei {name} eingetragen – es gehört einem Rechner, "
                               "bitte an einem austragen", hosts=", ".join(both), name=self._partner.name()))
            kinds.append(("scope", tuple(both)))
        offset = snap.clock_offset_s
        if offset is not None and abs(offset) > CLOCK_WARN_S:
            warnings.append(tr("Uhren weichen um {s} s ab – NTP an beiden Rechnern prüfen",
                               s=f"{offset:+.1f}"))
            kinds.append(("clock",))
        if warnings != self._verbund_warnings:
            old_kinds = getattr(self, "_verbund_warning_kinds", [])
            for kind, text in zip(kinds, warnings):
                if kind not in old_kinds:
                    logger.warning("Verbund: %s", text)
            self._verbund_warning_kinds = kinds
            self._verbund_warnings = warnings
            self._render_partner_chip()

    def verbund_warnings(self) -> list[str]:
        return list(self._verbund_warnings)

    def _set_partner_battsim(self, present: bool) -> None:
        if present != self._partner_battsim:
            self._partner_battsim = present
            logger.info("Verbund: Partner %s Batterie-Simulator%s", "führt einen" if present else "ohne",
                        " - 0x130 wird hier nicht gesendet" if present else "")
            self._request_partner_battsim.emit(present)

    # -- J-Link (Verbund V3, PI-31 a; jlink_service.py) ------------------------

    def _wire_jlink(self) -> None:
        self._jlink = JLinkService(self)
        self._pending_jlink_req = 0
        self._jlink.device_added.connect(self._on_jlink_added)
        self._jlink.device_removed.connect(self._on_jlink_removed)
        self._jlink.state_changed.connect(self.dashboard.update_jlink)
        self._jlink.state_changed.connect(self.control_tab.set_jlink_state)
        self._jlink.finished.connect(self._on_jlink_finished)
        self.control_tab.jlink_action_requested.connect(
            lambda device_id, action, path: self._jlink.run(device_id, action, path, source="Kachel"))
        self._test_runner.execute_jlink.connect(self._on_test_execute_jlink)
        self.settings_tab.jlink_config_edited.connect(lambda cfg: self._settings.set_jlink_config(**cfg))
        self._settings.jlink_config_changed.connect(self._on_jlink_config)
        self._on_jlink_config(self._settings.jlink_config)

    def _on_jlink_config(self, config: dict) -> None:
        self.settings_tab.set_jlink_config(config, find_jlink_commander(config.get("commander", "")))
        self._jlink.apply(config, self._settings.simulation_mode)

    def _on_jlink_added(self, kind: str, device_id: str) -> None:
        self._registry.on_device_added(kind, device_id)
        self._set_online(kind, device_id, True)
        self.dashboard.set_jlink_online(device_id, True)
        self.control_tab.set_jlink_online(device_id, True)
        state = self._jlink.state(device_id)
        self.dashboard.update_jlink(device_id, state)
        self.control_tab.set_jlink_state(device_id, state)

    def _on_jlink_removed(self, kind: str, device_id: str) -> None:
        self._registry.on_device_removed(kind, device_id)
        self._set_online(kind, device_id, False)
        self.dashboard.set_jlink_online(device_id, False)
        self.control_tab.set_jlink_online(device_id, False)

    def _on_test_execute_jlink(self, device_id: str, action: str, file_path: str) -> None:
        resolved_id, error = self._resolve_device_id("jlink", device_id)
        if resolved_id is None:
            self._test_runner.on_action_completed(False, error, 0.0)
            return
        self._pending_jlink_req = self._jlink.run(resolved_id, action, file_path, source="Testablauf")

    def _on_jlink_finished(self, request: int, _device_id: str, _action: str, ok: bool, message: str) -> None:
        if request and request == self._pending_jlink_req:
            self._pending_jlink_req = 0
            self._test_runner.on_action_completed(ok, message, 0.0)

    # -- Verbund V2: Leitstand (PI-24 a, PI-26 a) ------------------------------

    def _on_partner_snapshot(self, snap) -> None:
        """Partnergeraete fuer den Testablauf: in die Geraeteauswahl ("Pi · Netzteil 1") und ihre
        Messwerte in Bedingungen, Pruefungen und Bericht."""
        if snap is None or not snap.host:
            self._live_state.set_partner(None)
            return
        # PI-32 a: Kacheln fuer den MCP-Server an diesem Rechner (share_api.partner_tiles)
        self._live_state.set_partner({"host": snap.host, "name": self._partner.name(), "tiles": snap.tiles,
                                      "received": time.monotonic()})
        if snap.host != self._partner_host_name:
            self._partner_host_name = snap.host
            self.testcase_tab.set_verbund(app_logging.host_name(), snap.host, self._partner.enabled)
        # nur ein antwortender Simulator zaehlt -- ein eingetragener ohne Adapter sendet kein 0x130
        self._set_partner_battsim(any(t.get("kind") == "battsim" and t.get("online", True) and not t.get("stale")
                                      for t in snap.tiles))
        self._check_verbund_warnings(snap)
        name = self._partner.name()
        for tile in snap.tiles:
            kind, remote_id = tile.get("kind", ""), tile.get("id", "")
            if not remote_id or kind not in remote_actions.CONTROL_KINDS:
                continue
            device_id = verbund_ids.qualify(snap.host, remote_id)
            label = f"{name} · {tile.get('label') or remote_id}"
            if not self.testcase_tab.knows_device(device_id, kind, label):
                self.testcase_tab.on_device_known(kind, device_id, label)
            if self._partner_known.get(device_id) != label:
                # PI-33 a: der Leitstand zeichnet Partnerwerte mit auf -- Verlauf, Aufzeichnung (CSV/MF4)
                # und Bericht kennen das Geraet unter "Pi · Netzteil 1"
                self._partner_known[device_id] = label
                for sink in (self.timeline_tab, self._recorder, self._run_recorder):
                    sink.on_device_known(kind, device_id, label)
            if tile.get("stale") or not tile.get("online", True):
                continue   # alte Werte nicht als frisch melden (Bedingung/Pruefung schlaegt dann fehl)
            values = {f.get("key"): f.get("value") for f in tile.get("fields", []) if "value" in f}
            voltage, current = values.get("voltage"), values.get("current")
            if kind == "psu" and isinstance(voltage, (int, float)) and isinstance(current, (int, float)):
                cc = any(f.get("key") == "mode" and f.get("text") == "CC" for f in tile.get("fields", []))
                for sink in (self._test_runner, self._run_recorder, self._recorder):
                    sink.on_psu_measurement(device_id, float(voltage), float(current), cc)
                self.timeline_tab.update_psu(device_id, float(voltage), float(current), cc)
            elif kind == "load" and isinstance(voltage, (int, float)) and isinstance(current, (int, float)):
                power = values.get("power")
                power = float(power) if isinstance(power, (int, float)) else float(voltage) * float(current)
                for sink in (self._test_runner, self._run_recorder, self._recorder):
                    sink.on_load_measurement(device_id, float(voltage), float(current), power)
                self.timeline_tab.update_load(device_id, float(voltage), float(current), power)
            elif kind == "hil":
                ain = [values.get(f"ain{n}") for n in range(1, 5)]
                if all(isinstance(v, (int, float)) for v in ain):
                    self._test_runner.on_hil_analog_input(device_id, [float(v) for v in ain])
                    self.timeline_tab.update_hil_analog_in(device_id, [float(v) for v in ain])
                inputs = [values.get(f"in{n}") for n in range(1, 9)]
                if all(isinstance(v, (int, float)) for v in inputs):
                    self._test_runner.on_hil_digital_state(device_id, [bool(v) for v in inputs], [])
                    self.timeline_tab.update_hil_digital(device_id, [bool(v) for v in inputs], [])

    def _partner_steps(self, steps: list[TestStep]) -> tuple[list[TestStep], dict[str, str], str]:
        """Schritte fuer den Lauf: eigener Rechnername aufgeloest. Liefert (Schritte, Partnergeraete mit
        Aktionen {ID beim Partner: kind}, Fehlertext oder "")."""
        own, partner = app_logging.host_name(), self._partner_host_name
        local_ids = {d for ids in self._online_devices.values() for d in ids}
        result: list[TestStep] = []
        devices: dict[str, str] = {}
        for step in steps:
            step = dataclasses.replace(step)
            step.device_id = verbund_ids.localize(step.device_id, own, partner, local_ids)
            step.cond_device_id = verbund_ids.localize(step.cond_device_id, own, partner, local_ids)
            result.append(step)
            if not step.enabled or step.step_type != "action":
                continue
            remote = verbund_ids.partner_device(step.device_id, partner)
            if remote is None:
                continue
            if step.action not in remote_actions.REMOTE_ACTIONS.get(step.device_kind, {}):
                return result, {}, tr("Aktion {action} geht über den Verbund nicht ({device})",
                                      action=step.action, device=step.device_id)
            devices[remote] = step.device_kind
        for step in result:
            for device_id in (step.device_id, step.cond_device_id):
                host, _rest = verbund_ids.split(device_id)
                if host is not None and not verbund_ids.same_host(host, partner) and step.enabled:
                    return result, {}, tr("Gerät {device} gehört zu keinem Rechner im Verbund", device=device_id)
        return result, devices, ""

    def _on_partner_lease_acquired(self, ok: bool, message: str) -> None:
        steps, self._pending_run_steps = self._pending_run_steps, None
        if steps is None:
            if self._passthrough.expects_lease():   # Belegung fuer Claude (PI-32 a)
                self._passthrough.on_lease_acquired(ok, message)
            elif ok:
                self._partner.release_lease()   # Lauf inzwischen verworfen
            return
        if not ok:
            self._refuse_run(tr("Geräte beim {name} nicht belegt: {reason}", name=self._partner.name(),
                                reason=tr(message)))
            return
        self._run_has_lease = True
        self._start_run(steps)

    def _on_partner_lease_lost(self, reason: str) -> None:
        self._run_has_lease = False
        self._passthrough.on_lease_lost(reason)
        if self._test_runner.is_running():
            self._test_runner.abort(tr("Verbund: Verbindung zu {name} verloren ({reason}) – Ablauf abgebrochen",
                                       name=self._partner.name(), reason=tr(reason)))

    def _on_partner_action_finished(self, request_id: int, _device_id: str, _action: str,
                                    ok: bool, message: str) -> None:
        if not request_id or request_id != self._pending_partner_req:
            return
        self._pending_partner_req = 0
        self._test_runner.on_action_completed(
            ok, "" if ok else tr("{name}: {message}", name=self._partner.name(), message=tr(message)), 0.0)

    def _send_partner_test_action(self, remote_id: str, device_id: str, kind: str, action: str,
                                  value: float, channel: int) -> None:
        definition = remote_actions.REMOTE_ACTIONS.get(kind, {}).get(action)
        if definition is None:
            self._test_runner.on_action_completed(
                False, tr("Aktion {action} geht über den Verbund nicht ({device})", action=action, device=device_id),
                0.0)
            return
        label = self.testcase_tab.device_label(device_id) or device_id
        request_id = self._partner.send_action(
            remote_id, action, value if definition.needs_value else None,
            channel if definition.channels else None, label)
        if not request_id:
            self._test_runner.on_action_completed(False, tr("Verbund aus"), 0.0)
            return
        self._pending_partner_req = request_id

    # -- Verbund V2: Besitzer (PI-25 a, PI-26 a) -------------------------------

    # -- Kopplung: Token austauschen statt abtippen (pairing.py) ----------------

    def _start_pairing(self, address: str) -> None:
        """Fragende Seite: Anfrage an die eingetragene Adresse, Dialog mit Code."""
        url = normalize_url(address)
        if not url:
            self.statusBar().showMessage(tr("Adresse ungültig: {address}", address=address), 8000)
            return
        if self._pair_session is not None:
            return   # laeuft schon
        if not self._settings.share_config["token"]:
            self._settings.set_share_token(new_token())   # der Partner braucht einen Token von hier
        share = self._settings.share_config
        session = PairingSession(url, app_logging.host_name(), "", share["port"], share["token"], self)
        dialog = PairingDialog(address, session, self)
        session.finished.connect(
            lambda ok, message, detail, result: self._on_pairing_finished(address, ok, message, detail, result))
        self._pair_session, self._pair_dialog = session, dialog
        logger.info("Kopplung: Anfrage an %s", url)
        dialog.open()
        session.start()

    def _on_pairing_finished(self, address: str, ok: bool, message: str, detail: str, result: dict) -> None:
        self._pair_session = None
        if not ok:
            logger.warning("Kopplung mit %s nicht abgeschlossen: %s%s", address, message,
                           f" ({detail})" if detail else "")
            return
        self._settings.set_partner_config(enabled=True, address=address, token=result.get("token", ""))
        logger.warning("Kopplung mit %s (%s): Token ausgetauscht, Verbund eingeschaltet",
                       result.get("host") or "?", address)
        self._pairing_share_hint(result.get("host") or address)

    def _pairing_share_hint(self, partner: str) -> None:
        """Erreicht der Partner diesen Rechner? Sonst Hinweis (die Freigabe stellt der Nutzer selbst um)."""
        share = self._settings.share_config
        if share["enabled"] and share["bind"] != "127.0.0.1":
            self.statusBar().showMessage(tr("Gekoppelt mit {name}", name=partner), 10000)
            return
        self.statusBar().showMessage(tr(
            "Gekoppelt mit {name}. Damit {name} diesen Rechner erreicht (ALLE AUS, Verbund-Reiter), hier unter "
            "Netzwerk die Freigabe für alle Geräte im lokalen Netzwerk einschalten.", name=partner), 20000)

    def _check_pairing(self) -> None:
        """Bestaetigende Seite (alle 250 ms): neue Anfrage -> Dialog, erledigte/abgebrochene -> Dialog zu,
        abgeschlossene -> Verbund-Einstellungen uebernehmen."""
        table = self._live_state.pairing
        req = table.take_new()
        if req is not None:
            if self._pair_confirm is not None:
                self._pair_confirm.done(0)
            logger.warning("Kopplung: Anfrage von %s (%s), Code %s", req.label(), req.client_ip, req.code)
            dialog = PairConfirmDialog(req.pair_id, req.label(), req.client_ip, req.code, self)
            dialog.decided.connect(self._on_pair_decided)
            self._pair_confirm = dialog
            dialog.open()
        confirm = self._pair_confirm
        if confirm is not None:
            current = table.current()
            if current is None or current.pair_id != confirm.pair_id or current.status != "pending":
                if confirm.isVisible():
                    confirm.done(0)   # abgebrochen oder abgelaufen; done() meldet kein "decided"
                if current is None or current.status not in ("accepted",):
                    self._pair_confirm = None
        done = table.take_done()
        if done is not None:
            self._pair_confirm = None
            self._settings.set_partner_config(enabled=True, address=done.address(), token=done.peer_token)
            logger.warning("Kopplung mit %s (%s): Token ausgetauscht, Verbund eingeschaltet",
                           done.label(), done.address())
            self._pairing_share_hint(done.label())

    def _on_pair_decided(self, pair_id: str, accept: bool) -> None:
        if self._live_state.pairing.decide(pair_id, accept):
            logger.warning("Kopplung %s", "bestätigt" if accept else "abgelehnt")

    def _check_leases(self) -> None:
        """Alle 250 ms: Belegungen ohne Lebenszeichen abschalten, Kacheln sperren/freigeben."""
        self._check_pairing()
        leases = self._live_state.leases
        for lease in leases.pop_expired():
            names = ", ".join(self._device_labels.get(d, d) for d in lease.devices)
            logger.error("Verbund: kein Lebenszeichen von %s seit %.0f s - belegte Geräte werden abgeschaltet: %s",
                         lease.holder, lease.ttl_s, names)
            self._request_devices_off.emit(list(lease.devices),
                                           f"Verbund-Lebenszeichen fehlt ({lease.holder_name()})")
            self.statusBar().showMessage(
                tr("Verbund: kein Lebenszeichen von {holder} – abgeschaltet: {devices}",
                   holder=lease.holder_name(), devices=names), 30000)
        view = {device_id: tr("belegt durch {holder} · {run}", holder=lease.holder_name(),
                              run=lease.run or tr("Testablauf"))
                for device_id, lease in leases.snapshot().items()}
        for device_id in set(self._lease_view) - set(view):
            self.control_tab.set_lease(device_id, "")
        for device_id, text in view.items():
            self.control_tab.set_lease(device_id, text)
        self._lease_view = view

    def _render_mcp_chip(self, *_args) -> None:
        """PI-39 b: Chip "MCP" -- grün, wenn Claude (MCP) an diesem Rechner steuern darf (Schalter in den
        Einstellungen oder Hauptschalter an), sonst grau (nur lesen). Ohne Netzwerk-Freigabe verborgen."""
        chip = getattr(self, "_mcp_chip", None)
        if chip is None:
            return
        config = self._settings.share_config
        if not config.get("enabled"):
            chip.hide()
            return
        pal = ThemeManager.instance().palette
        allowed = bool(config.get("local_bypass")) or self._live_state.remote_active()
        color = pal.success if allowed else pal.text_muted
        size = self._chip_icon_size()
        icon = touch_icon_path("mdi.robot-outline" if allowed else "mdi.robot-off-outline", color, size)
        chip.setText(f'<img src="{icon}" width="{size}" height="{size}" style="vertical-align: middle"> MCP')
        chip.setToolTip(tr("Claude (MCP) darf steuern") if allowed else tr("Claude (MCP) darf nur lesen"))
        self._style_chip(chip, color)
        chip.show()

    def _render_partner_chip(self) -> None:
        chip = self._partner_chip
        link = self._partner
        if not link.enabled:
            chip.hide()
            self.settings_tab.set_partner_status(tr("Verbund aus"), "muted")
            return
        pal = ThemeManager.instance().palette
        name = link.name()
        if link.is_connected():
            snap = link.snapshot()
            count = len(snap.tiles) if snap is not None else 0
            warnings = self._verbund_warnings
            # Seit 0.52.0 ohne Verbund-Reiter: ob die Partnergeraete bedienbar sind, zeigt der Chip
            locked = snap is not None and not (snap.remote_effective and snap.lock == "free")
            notes = [tr("Fernsteuerung dort aus") if snap is not None and not snap.remote_effective else "",
                     tr("dort gesperrt (Testlauf oder Sicherheitsabschaltung)")
                     if snap is not None and snap.lock != "free" else ""]
            chip.setText(f"⇄ {name}" + (" ⚠" if warnings else ""))
            chip.setToolTip("\n".join([tr("Verbund: verbunden mit {name} ({n} Geräte)", name=name, n=count),
                                       *[n for n in notes if n], *warnings]))
            self._style_chip(chip, pal.warning if warnings or locked else pal.success)
            self.settings_tab.set_partner_status(
                "\n".join([tr("Verbunden mit {name}, LabControl {version}, {n} Geräte freigegeben", name=name,
                              version=snap.version if snap else "?", n=count), *warnings]),
                "warning" if warnings else "success")
        else:
            chip.setText(f"✕ {name}")
            chip.setToolTip(tr("Verbund: {name} nicht verbunden – {reason}", name=name, reason=tr(link.reason())))
            self._style_chip(chip, pal.danger)
            self.settings_tab.set_partner_status(
                tr("Nicht verbunden: {reason}", reason=tr(link.reason())), "danger")
        chip.show()

    def _on_partner_all_off_finished(self, ok: bool, message: str) -> None:
        if ok:
            return
        self.statusBar().showMessage(
            tr("ALLE AUS am Verbund-Partner {name} nicht bestätigt: {message}",
               name=self._partner.name(), message=tr(message)), 15000)

    def _on_all_off_finished(self, failures: str) -> None:
        if not failures:
            return
        logger.warning("ALL OFF: fehlgeschlagene Geräte: %s", failures)
        self.statusBar().showMessage(
            tr(
                "Sicherheitsabschaltung bei folgenden Geräten fehlgeschlagen: {failures}",
                failures=failures,
            ),
            10000,
        )

    # -- Sicherheits-Watchdog: Banner + Statusanzeige --------------------------

    def _on_safety_tripped(self, device_id: str, reason: str) -> None:
        self._test_runner.stop()
        # Die eigenen Ausgaenge schaltet der Watchdog selbst ab (all_off_requested); der Partner
        # bekommt ALLE AUS dazu (PI-27 a).
        self._partner.all_off(f"Sicherheitsabschaltung {device_id}")
        self._passthrough.release("Sicherheitsabschaltung")
        self._safety_banner_label.setText(reason)
        self._safety_banner.show()

    def _on_safety_acknowledge(self) -> None:
        self._safety.acknowledge()
        self._safety_banner.hide()

    def _render_safety_status(self, state: str) -> None:
        pal = ThemeManager.instance().palette
        texts = {"off": tr("AUS"), "armed": tr("AKTIV"), "tripped": tr("AUSGELÖST")}
        colors = {"off": pal.text_muted, "armed": pal.success, "tripped": pal.danger}
        color = colors.get(state, pal.text)
        label = self._safety_status_label
        # Chip wie die Geraete, Schild-Symbol statt "Sicherheit:" (Nutzerwunsch 11.11.2026, seit PI-18 a auch am PC)
        icon = SAFETY_ICONS.get(state, "mdi.shield-outline")
        size = self._chip_icon_size()
        label.setText(f'<img src="{touch_icon_path(icon, color, size)}" width="{size}" height="{size}"'
                      f' style="vertical-align: middle"> {texts.get(state, state)}')
        label.setToolTip(f"{tr('Sicherheit:')} {texts.get(state, state)}")
        self._style_chip(label, color)

    def _chip_height(self) -> int:
        return STATUS_CHIP_HEIGHT if self._touch else STATUS_CHIP_HEIGHT_PC

    def _chip_icon_size(self) -> int:
        return 18 if self._touch else 15

    def _style_chip(self, label: QLabel, color: str) -> None:
        """Status-Chip (Geraete, Sicherheit): Rahmen und Schrift in der Zustandsfarbe, natuerliche Breite."""
        height = self._chip_height()
        padding = "1px 9px" if self._touch else "0px 7px"
        label.setStyleSheet(f"color: {color}; font-weight: bold; border: 1px solid {color};"
                            f" border-radius: {height // 2 - 1}px; padding: {padding}; background: transparent;")
        # Natuerliche Groesse statt die ganze Leiste zu fuellen (am Pi-Display gesehen)
        label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        label.setFixedHeight(height)

    def _style_safety_banner(self) -> None:
        pal = ThemeManager.instance().palette
        self._safety_banner.setStyleSheet(
            f"#safetyBanner {{ background-color: {pal.danger}; border-radius: {METRICS.radius_sm}px; }}"
        )
        # background: transparent noetig, sonst uebernimmt das Label (eigenes
        # Instanz-Stylesheet) die globale "QWidget { background-color: pal.bg }"-
        # Regel statt den roten Banner-Hintergrund durchscheinen zu lassen --
        # weisser Text auf fast-weissem Grund (siehe BUGS_OFFEN.md #26).
        self._safety_banner_label.setStyleSheet(
            f"color: {pal.on_status}; font-weight: bold; background: transparent;"
        )

    def _style_all_off_button(self) -> None:
        pal = ThemeManager.instance().palette
        padding = "6px 18px" if self._touch else "2px 10px"   # Touch: mindestens 64 x 44 px
        # Mit Selektor statt nackter Deklaration, damit auch der Tastaturfokus
        # sichtbar ist (ein Instanz-Stylesheet verdeckt die :focus-Regel aus theme.py).
        self._all_off_button.setStyleSheet(
            f"QPushButton {{ background-color: {pal.danger}; color: {pal.on_status}; font-weight: bold; "
            f"border: 1px solid {pal.danger}; border-radius: {METRICS.radius_sm}px; padding: {padding}; }}"
            f"QPushButton:focus {{ border: {METRICS.focus_width}px solid {pal.focus}; }}"
        )
        self._all_off_button.setMinimumSize(*((96, 44) if self._touch else (0, 0)))

    def _wire_registry(self) -> None:
        self._registry.device_known.connect(self.dashboard.on_device_known)
        self._registry.device_known.connect(self.control_tab.on_device_known)
        self._registry.device_known.connect(self.testcase_tab.on_device_known)
        self._registry.device_known.connect(self.timeline_tab.on_device_known)
        self._registry.device_known.connect(self._recorder.on_device_known)
        self._registry.device_known.connect(self._live_state.on_device_known)
        self._registry.device_known.connect(self._run_recorder.on_device_known)
        self._registry.device_known.connect(self._on_device_known_status)
        self._registry.device_known.connect(self.settings_tab.on_device_known)
        self._registry.device_known.connect(self._on_device_known_safety_limits)

        self._registry.label_changed.connect(self.dashboard.on_label_changed)
        self._registry.label_changed.connect(self.control_tab.on_label_changed)
        self._registry.label_changed.connect(self.testcase_tab.on_label_changed)
        self._registry.label_changed.connect(self.timeline_tab.on_label_changed)
        self._registry.label_changed.connect(self._recorder.on_label_changed)
        self._registry.label_changed.connect(self._live_state.on_label_changed)
        self._registry.label_changed.connect(self._run_recorder.on_label_changed)
        self._registry.label_changed.connect(self._on_label_changed_status)
        self._registry.label_changed.connect(self.settings_tab.on_label_changed)

        # Umbenennen-Button sitzt seit BUGS.md #10c nur noch im Control-Tab
        # (dort aus dem Dashboard-Panel entfernt).
        self.control_tab.rename_requested.connect(self._registry.rename)

    def _replay_known_devices(self) -> None:
        """Stellt beim App-Start die aus der letzten Sitzung gespeicherten
        Geraete wieder her (DeviceRegistry.known_devices, siehe deren
        Docstring), noch BEVOR der Worker ueberhaupt eine echte (Wieder-)
        Verbindung gefunden hat -- ohne das war ein vor dem Neustart bereits
        getrenntes Geraet nach dem Start spurlos verschwunden (Dashboard
        zeigte gar kein Panel mehr dafuer), obwohl sein Name/Einstellungen
        weiterhin in device_labels.json standen.

        Muss NACH allen _wire_*()-Aufrufen oben laufen, damit device_known
        bereits Abonnenten hat (Dashboard/Control-Tab/Testablauf/Verlauf/
        Statusleiste/Einstellungen-Tab, siehe _wire_registry). Nutzt
        denselben Pfad wie eine echte Verbindungsaenderung
        (_on_load_connected/_on_psu_connected mit online=False), damit alle
        Abonnenten konsistent bleiben -- findet der Worker das Geraet
        anschliessend tatsaechlich, ueberschreibt dessen online=True-Signal
        diesen Startzustand ganz normal."""
        for kind, device_id, label in self._registry.known_devices():
            self._registry.device_known.emit(kind, device_id, label)
            if kind == "load":
                self._on_load_connected(device_id, False)
            elif kind == "psu":
                self._on_psu_connected(device_id, False)
            elif kind == "hil":
                self._on_hil_connected(device_id, False)
            elif kind == "fg":
                self._on_fg_connected(device_id, False)
            elif kind == "picoscope":
                self._on_picoscope_connected(device_id, False)
            elif kind == "scpiscope":
                self._on_scpiscope_connected(device_id, False)
            elif kind == "battsim":
                self._on_battsim_connected(device_id, False)
            elif kind == "jlink":
                self._set_online("jlink", device_id, False)
                self.dashboard.set_jlink_online(device_id, False)
                self.control_tab.set_jlink_online(device_id, False)
            else:
                self._on_can_connected(device_id, False)

    def _on_device_known_safety_limits(self, kind: str, device_id: str, _label: str) -> None:
        # Initialbefuellung der pro Geraet erzeugten Grenzwert-Sektion (siehe
        # settings_tab._DeviceSafetyGroup) mit dem persistierten Stand --
        # settings_tab.on_device_known selbst kennt nur kind/device_id/label,
        # nicht die gespeicherten Werte.
        self.settings_tab.set_device_safety_limits(
            device_id, self._settings.device_safety_limits(device_id, kind)
        )

    def _wire_control_tab(self) -> None:
        self.control_tab.section_created.connect(self._on_control_section_created)

    def _on_control_section_created(self, kind: str, device_id: str, section) -> None:
        if self._partner_devices.is_remote(device_id):
            self._partner_devices.wire_section(kind, device_id, section)   # Bedienung geht an den Partner
            return
        if kind == "load":
            section.apply_function.connect(self._worker.set_load_function)
            # Direkt verbunden (wie bei der PSU-Sektion unten) statt ueber eine
            # eigene MainWindow-Methode, die den Worker per Python-Methodenaufruf
            # anspricht -- ein solcher Direktaufruf wuerde Qt's Thread-Routing
            # umgehen und die blockierende Seriell-I/O synchron im GUI-Thread
            # ausfuehren (kurzes Einfrieren/Flackern des Panels beim "Übernehmen").
            section.apply_setpoint.connect(self._worker.set_load_setpoint)
            section.set_input.connect(self._worker.set_load_input)
        elif kind == "psu":
            section.set_voltage.connect(self._worker.set_psu_voltage)
            section.set_current.connect(self._worker.set_psu_current)
            section.set_output.connect(self._worker.set_psu_output)
            section.set_manual.connect(self._worker.set_psu_manual)
            section.set_ovp.connect(self._worker.set_psu_ovp)
            section.set_ocp.connect(self._worker.set_psu_ocp)
            section.recall_memory.connect(self._worker.recall_psu_memory)
        elif kind == "hil":
            section.set_output.connect(self._worker.set_hil_output)
            section.set_analog_output.connect(self._worker.set_hil_analog_output)
            # Zusaetzlich direkt ans Dashboard (GUI-Thread zu GUI-Thread, am
            # Worker/Poll-Zyklus vorbei) -- fuer AOUT gibt es kein `AOUT?` zum
            # Zuruecklesen, ohne diesen Weg bliebe die Dashboard-Anzeige trotz
            # eines im Control-Tab tatsaechlich gesetzten Sollwerts bei "--"
            # stehen (siehe dashboard.set_hil_analog_out()-Docstring).
            section.set_analog_output.connect(self.dashboard.set_hil_analog_out)
            section.set_relay.connect(self._worker.set_hil_relay)
            section.set_pwm.connect(self._worker.set_hil_pwm)
            section.set_pwr12.connect(self._worker.set_hil_pwr12)
            section.set_current_limit.connect(self._worker.set_hil_current_limit)
        elif kind == "fg":
            section.set_output.connect(self._worker.set_fg_output)
            section.set_waveform.connect(self._worker.set_fg_waveform)
            section.set_frequency.connect(self._worker.set_fg_frequency)
            section.set_amplitude.connect(self._worker.set_fg_amplitude)
            section.set_offset.connect(self._worker.set_fg_offset)
            section.set_duty.connect(self._worker.set_fg_duty)
            section.set_phase.connect(self._worker.set_fg_phase)
        elif kind == "picoscope":
            section.launch_requested.connect(self.dashboard.launch_picoscope)
            section.release_requested.connect(self._scope_service.release_async)
            section.plot_requested.connect(self._show_scope_plot)
        elif kind == "scpiscope":
            section.release_requested.connect(self._scope_service.release_async)
            section.plot_requested.connect(self._show_scope_plot)
        elif kind == "battsim":
            limit = self._settings.battsim_voltage_limits.get(device_id)
            if limit is not None:
                section.set_voltage_limit(limit)
            section.set_remote.connect(self._worker.set_battsim_remote)
            section.set_output.connect(self._worker.set_battsim_output)
            section.set_cell_voltage.connect(self._worker.set_battsim_cell_voltage)
            section.set_all_voltages.connect(self._worker.set_battsim_all_voltages)
            section.set_cell_enabled.connect(self._worker.set_battsim_cell_enabled)
            section.set_all_enabled.connect(self._worker.set_battsim_all_enabled)
            section.reset_fail.connect(self._worker.reset_battsim_fail)
            section.voltage_limit_changed.connect(self._settings.set_battsim_voltage_limit)
            section.voltage_limit_changed.connect(self._worker.set_battsim_voltage_limit)
        elif kind == "jlink":
            pass   # Kachel meldet ueber control_tab.jlink_action_requested (siehe _wire_jlink)
        else:
            section.send_frame.connect(self._worker.send_can_frame)

    def _wire_recording(self) -> None:
        # Aufzeichnung-Steuerung sitzt im Verlauf-Tab (siehe timeline_tab.py-
        # Docstring), Recorder haelt die Daten -- hier nur die Verkabelung.
        self.timeline_tab.start_requested.connect(self._recorder.start)
        self.timeline_tab.stop_requested.connect(self._recorder.stop)
        self.timeline_tab.clear_requested.connect(self._recorder.clear)
        self._recorder.recording_changed.connect(self.timeline_tab.on_recording_changed)
        self._recorder.stats_changed.connect(self.timeline_tab.on_stats_changed)
        self.timeline_tab.export_csv_to.connect(self._on_export_csv)
        self.timeline_tab.export_mf4_to.connect(self._on_export_mf4)

        # Nur Signale aufzeichnen, die aktuell einem Diagramm im Verlauf-Tab
        # zugeordnet sind (FEATURES.md Punkt 4) -- initial synchronisieren,
        # da active_signals_changed erst bei der naechsten Zuordnungsaenderung
        # feuert.
        self.timeline_tab.active_signals_changed.connect(self._recorder.set_active_signals)
        self._recorder.set_active_signals(self.timeline_tab.active_signal_keys())

    def _on_export_csv(self, path) -> None:
        try:
            self._recorder.export_csv(path)
        except OSError as exc:
            self.timeline_tab.show_export_error(str(exc))
            return
        self.timeline_tab.show_export_success(path)

    def _on_export_mf4(self, path) -> None:
        try:
            self._recorder.export_mf4(path)
        except ImportError:
            self.timeline_tab.show_export_error(
                tr("MF4-Export benötigt das Paket 'asammdf' (siehe requirements.txt).")
            )
            return
        except (OSError, ValueError) as exc:
            self.timeline_tab.show_export_error(str(exc))
            return
        self.timeline_tab.show_export_success(path)

    def _on_open_report(self) -> None:
        record = self._run_recorder.record()
        if record is None:
            return
        import run_report

        try:
            path = run_report.write_html_report(record)
        except OSError as exc:
            self.statusBar().showMessage(
                tr("Report konnte nicht erstellt werden: {error}", error=str(exc)), 8000
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.statusBar().showMessage(tr("Report erstellt: {path}", path=str(path)), 8000)

    def _on_export_report_pdf(self, path) -> None:
        record = self._run_recorder.record()
        if record is None:
            return
        import run_report

        try:
            run_report.export_pdf(record, path)
        except OSError as exc:
            self.statusBar().showMessage(
                tr("Report konnte nicht erstellt werden: {error}", error=str(exc)), 8000
            )
            return
        self.statusBar().showMessage(tr("PDF exportiert: {path}", path=str(path)), 8000)

    def _wire_settings_tab(self) -> None:
        self.settings_tab.set_simulation_mode(self._settings.simulation_mode)
        self.settings_tab.simulation_mode_toggled.connect(self._settings.set_simulation_mode)
        self._settings.simulation_mode_changed.connect(self._worker.set_simulation_mode)

        self.settings_tab.set_dark_mode(self._settings.dark_mode)
        self.settings_tab.dark_mode_toggled.connect(self._settings.set_dark_mode)
        self._settings.dark_mode_changed.connect(ThemeManager.instance().apply)

        self.settings_tab.set_language(self._settings.language)
        self.settings_tab.language_selected.connect(self._settings.set_language)
        self._settings.language_changed.connect(Translator.instance().set_language)

        # Netzlaufwerk fuer Log und Betriebsdaten: Ziel sofort umstellen, Status alle 2 s
        # nachfuehren (Wechsel auf lokal und zurueck passieren in eigenen Threads, ohne Signal).
        self.settings_tab.set_network_dir(self._settings.network_dir,
                                          os.environ.get(app_logging.NETWORK_DIR_ENV, ""))
        self.settings_tab.network_dir_changed.connect(self._on_network_dir_changed)
        self._log_status_timer = QTimer(self)
        self._log_status_timer.timeout.connect(self._update_storage_status)
        self._log_status_timer.start(2000)
        self._update_storage_status()

        self.settings_tab.set_notifications_enabled(self._settings.notifications_enabled)
        self.settings_tab.notifications_toggled.connect(self._settings.set_notifications_enabled)

        self.settings_tab.safety_limit_changed.connect(self._settings.set_safety_limit)
        self.settings_tab.reset_devices_requested.connect(self._on_reset_devices_requested)
        self.settings_tab.remove_offline_devices_requested.connect(
            self._on_remove_offline_devices_requested)

        self.settings_tab.set_can_configs(self._settings.can_configs)
        self.settings_tab.can_configs_changed.connect(self._settings.set_can_configs)
        self.settings_tab.can_configs_changed.connect(self._on_can_configs_changed)
        self._settings.can_configs_changed.connect(self._worker.set_can_configs)
        self._worker.can_connect_error.connect(self.settings_tab.on_can_connect_error)
        self._worker.can_connected.connect(self.settings_tab.on_can_connected)

        self.settings_tab.set_scpi_scopes(self._settings.scpi_scopes)
        self.settings_tab.scpi_scopes_changed.connect(self._settings.set_scpi_scopes)
        self._settings.scpi_scopes_changed.connect(self._worker.set_scpi_scopes)

    def _on_network_dir_changed(self, path: str) -> None:
        self._settings.set_network_dir(path)
        logger.info("Netzlaufwerk (Log und Daten) eingestellt: %s", path or "keines, nur lokal")
        app_logging.set_network_dir(self._settings.network_dir)
        data_store.store().set_network_dir(app_logging.effective_network_dir(self._settings.network_dir))

    def _update_storage_status(self) -> None:
        self.settings_tab.set_log_status(app_logging.status())
        self.settings_tab.set_data_status(data_store.store().status())

    def _on_reset_devices_requested(self) -> None:
        """Reagiert auf den "Geraetezuordnung loeschen"-Button (settings_tab.
        py) -- die Rueckfrage lief bereits dort, hier nur noch die Ausfuehrung.

        Loescht Labels (DeviceRegistry) und geraete-individuelle
        Einstellungen (Settings: Sicherheits-Grenzwerte, Panel-Farben) und
        rollt das Ergebnis SOFORT live aus, ohne Neustart. Zwei Faelle pro
        zuvor bekanntem Geraet:

        - AKTUELL VERBUNDEN (siehe _online_devices): sein Panel/seine
          Sektion wird weiter gebraucht, bekommt daher nur einen frischen
          Standardnamen -- ueber denselben Weg wie beim allerersten
          Verbinden (DeviceRegistry.on_device_added), das emittiert erneut
          device_known und frischt darueber alle Abonnenten auf (siehe
          _wire_registry).
        - NICHT verbunden (z.B. eine ausgegraute Dashboard-Kachel, siehe
          dashboard.py): wird komplett vergessen statt nur umbenannt --
          ohne das blieb trotz Klick auf den Button ueberall eine sichtbare
          Spur des Geraets stehen (Dashboard-Kachel, Control-Tab-Sektion,
          Sicherheits-Grenzwert-Sektion im Einstellungen-Tab, Statuszeile,
          Geraete-Auswahl im Testablauf-Editor) -- genau der gemeldete Bug.
          _forget_device() raeumt das ueberall auf; ein spaeteres
          tatsaechliches Verbinden desselben Geraets legt alles ganz normal
          wieder neu an (siehe on_device_known der jeweiligen Widgets)."""
        known = self._registry.reset_all()
        self._settings.reset_device_settings()
        online_ids = set().union(*self._online_devices.values())
        for kind, device_id in known:
            if device_id in online_ids:
                self._registry.on_device_added(kind, device_id)
            else:
                self._forget_device(device_id)

    def _on_remove_offline_devices_requested(self) -> None:
        """Button "Nicht verbundene Geraete loeschen" (settings_tab.py, die
        Rueckfrage lief dort): vergisst nur die Geraete, die gerade NICHT
        verbunden sind -- Label (DeviceRegistry.forget), geraete-individuelle
        Einstellungen (Settings.forget_device_settings) und alle Kacheln/
        Sektionen/Zeilen (_forget_device), genau wie "Alle Geraete loeschen"
        es fuer diese Geraete tut. Verbundene Geraete behalten Namen,
        Grenzwerte, Farben und Freigabe. Ein spaeter wieder angeschlossenes
        Geraet taucht ganz normal mit einem frischen Standardnamen auf."""
        online_ids = set().union(*self._online_devices.values())
        removed = [device_id for _kind, device_id, _label in self._registry.known_devices()
                   if device_id not in online_ids]
        for device_id in removed:
            self._registry.forget(device_id)
            self._settings.forget_device_settings(device_id)
            self._forget_device(device_id)
        logger.info("Nicht verbundene Geraete geloescht: %s", ", ".join(removed) or "keine")
        self.statusBar().showMessage(
            tr("{count} nicht verbundene Geräte gelöscht", count=len(removed)) if removed
            else tr("Keine nicht verbundenen Geräte vorhanden"), 5000)

    def _on_can_configs_changed(self, configs: list) -> None:
        """Ein in den Einstellungen per Entfernen-Button geloeschtes
        CAN-Interface soll komplett vergessen werden (Kachel/Sektion
        verschwindet vollstaendig), statt wie bei Last/Netzteil/HIL nur als
        ausgegraute "getrennt"-Kachel liegen zu bleiben (Nutzerfeedback).

        Last/Netzteil/HIL haben echtes Hotplug: ein device_id bleibt dort
        auch nach dem Trennen sinnvoll, weil dasselbe Geraet jederzeit
        wieder auftauchen kann (siehe _replay_known_devices). CAN-Interfaces
        haben KEIN Hotplug (siehe device_worker.py-Modulkommentar) -- ein
        device_id wird ausschliesslich ueber die Konfigurationsliste
        bekannt. Faellt ein Eintrag hier weg, gibt es fuer sein device_id
        prinzipiell keinen Weg mehr, je wieder automatisch zu verbinden;
        eine ausgegraute Karteileiche waere daher reine Verwirrung -- exakt
        das gemeldete Verhalten.

        Vergleicht die NEUE Konfigurationsliste gegen alle der Registry
        bekannten CAN-device_ids (aktuell verbunden ODER aus einer
        frueheren Sitzung als ausgegraute Kachel bekannt, siehe
        DeviceRegistry.known_devices) -- jedes fehlende wird sofort
        vergessen. SIM_CAN_ID (Simulationsmodus-Mock) bewusst ausgenommen:
        es stammt nie aus dieser Konfigurationsliste, sondern aus dem
        Simulationsmodus-Umschalter, waere also hier faelschlich IMMER
        "fehlend"."""
        wanted_ids = {can_device_id(cfg) for cfg in configs}
        # Batterie-Simulator: nur, solange sein Interface ihn in der Spalte
        # "Geraet am Bus" fuehrt -- sonst wie ein geloeschtes Interface vergessen.
        wanted_battsims = {battsim_device_id(can_device_id(cfg)) for cfg in configs
                           if cfg.get("device") == "battsim"}
        for kind, device_id, _label in self._registry.known_devices():
            if kind == "can" and device_id != SIM_CAN_ID and device_id not in wanted_ids:
                self._registry.forget(device_id)
                self._forget_device(device_id)
            elif kind == "battsim" and device_id != SIM_BATTSIM_ID and device_id not in wanted_battsims:
                self._registry.forget(device_id)
                self._forget_device(device_id)

    def _forget_device(self, device_id: str) -> None:
        """Entfernt ein NICHT verbundenes Geraet vollstaendig aus der
        laufenden App -- Aufrufer: _on_reset_devices_requested (Button
        "Geraetezuordnung loeschen") und _on_can_configs_changed (ein
        einzelnes, in den Einstellungen geloeschtes CAN-Interface). Betrifft
        nur
        Widgets, die ein Geraet als eigene Kachel/Sektion/Zeile darstellen;
        recorder.py/run_record.py/timeline_tab.py sind bewusst NICHT
        einbezogen (deren geraete-bezogene Daten sind Beschriftungen fuer
        bereits aufgezeichnete Messwerte/Diagramm-Zuordnungen, die absichtlich
        auch nach dem Trennen erhalten bleiben, siehe deren Docstrings)."""
        self.dashboard.forget_device(device_id)
        self.control_tab.forget_device(device_id)
        self.settings_tab.forget_device(device_id)
        self.testcase_tab.forget_device(device_id)
        status_label = self._status_labels.pop(device_id, None)
        if status_label is not None:
            self._status_layout.removeWidget(status_label)
            status_label.deleteLater()
        self._device_labels.pop(device_id, None)
        self._device_online.pop(device_id, None)

    def _wire_share(self) -> None:
        """Netzwerk-Freigabe: Einstellungen <-> Settings <-> LiveState/Server.

        Die Messwert-Senken sind bereits in _setup_worker()/_wire_registry()/
        _wire_safety() verdrahtet -- hier steht nur die Konfiguration, damit
        jede Senke bei ihresgleichen bleibt.
        """
        self.settings_tab.set_share_config(self._settings.share_config)
        self.settings_tab.share_enabled_toggled.connect(self._settings.set_share_enabled)
        self.settings_tab.share_bind_selected.connect(self._settings.set_share_bind)
        self.settings_tab.share_port_changed.connect(self._settings.set_share_port)
        self.settings_tab.share_read_token_toggled.connect(
            self._settings.set_share_read_requires_token)
        self.settings_tab.share_access_log_toggled.connect(self._settings.set_share_access_log)
        self.settings_tab.share_device_changed.connect(self._settings.set_share_device)
        self.settings_tab.share_token_regenerate_requested.connect(
            self._on_share_token_regenerate)

        self._settings.share_config_changed.connect(self.settings_tab.set_share_config)
        self._settings.share_config_changed.connect(self._live_state.set_share_config)
        self._settings.share_config_changed.connect(self._share_server.apply)
        # Beispiel-URL nachfuehren, wenn sich die Freigabe aendert -- das
        # passiert ohne Serverneustart, also feuert started nicht erneut.
        self._settings.share_config_changed.connect(self._refresh_share_url)
        self._settings.share_config_changed.connect(self._render_mcp_chip)

        # -- Fernsteuerung ------------------------------------------------
        # Explizit QueuedConnection: submit_*() emittieren aus dem Server-
        # Thread, und die Slots duerfen nur im GUI-Thread laufen.
        self._share_bridge.action_requested.connect(
            self._on_share_action, Qt.ConnectionType.QueuedConnection)
        self._share_bridge.all_off_requested.connect(
            self._on_share_all_off, Qt.ConnectionType.QueuedConnection)
        self._dispatch_share_action.connect(self._worker.execute_remote_action)
        self._worker.remote_action_completed.connect(self._share_bridge.on_worker_result)
        self._worker.all_off_finished.connect(self._share_bridge.on_all_off_finished)
        self.settings_tab.share_control_toggled.connect(self._on_share_control_toggled)
        self.settings_tab.share_local_bypass_toggled.connect(self._settings.set_share_local_bypass)
        self.settings_tab.share_control_timeout_changed.connect(
            self._settings.set_share_control_timeout_min)
        # Grenzwerte fuer die Sollwert-Pruefung der Fernsteuerung.
        self._settings.safety_limits_changed.connect(self._live_state.set_safety_limits)
        self._live_state.set_safety_limits(self._settings.safety_limits)

        # Wer die Freigabe einschaltet, soll sofort einen Token haben: ohne
        # einen antwortet die API auf jeden Zugriff mit 503 (token_not_configured),
        # und der Weg dorthin ("Neu erzeugen" druecken) ist nicht offensichtlich.
        self._settings.share_config_changed.connect(self._ensure_share_token)
        self._ensure_share_token(self._settings.share_config)

        self._share_server.started.connect(self._on_share_started)
        self._share_server.error.connect(self._on_share_error)
        self._share_server.stopped.connect(self._on_share_stopped)

        # Feldnamen werden im GUI-Thread vorgerendert -- der Server-Thread
        # darf tr() nicht aufrufen (siehe live_state.py).
        Translator.instance().language_changed.connect(
            lambda *_args: self._live_state.refresh_labels())

        self._share_server.set_simulation(self._settings.simulation_mode)
        self._share_server.set_lan_scopes(lan_scope_hosts(self._settings.scpi_scopes))
        self._settings.scpi_scopes_changed.connect(
            lambda addresses: self._share_server.set_lan_scopes(lan_scope_hosts(addresses)))
        self._live_state.set_share_config(self._settings.share_config)
        self._share_server.apply(self._settings.share_config)

    def _on_share_action(self, request_id: int, device_id: str, kind: str, action: str,
                         value: float, channel: int, local: bool, verbund: bool = False) -> None:
        """Letzte Station im GUI-Thread, bevor eine Fernsteuer-Aktion den Worker
        erreicht.

        Prueft Sicherheitsabschaltung, Testlauf und Hauptschalter NOCH EINMAL,
        obwohl der Server-Thread es schon getan hat: sein Schnappschuss kann
        zwischen Pruefung und Ausfuehrung veraltet sein (ein Trip oder der
        Start eines Testlaufs in genau dieser Luecke). Die massgeblichen
        Zustaende leben hier im GUI-Thread und sind hier ohne Verzug lesbar.
        """
        if self._safety.is_tripped() or self._test_runner.is_running():
            self._share_bridge.complete(request_id, False, "locked", "", 0.0)
            return
        # Dieselbe Regel wie share_api.remote_effective: Hauptschalter an, gekoppelter Partner (PI-39 b,
        # Verbund noch eingeschaltet) ODER lokaler Aufruf mit Schalter "Claude (MCP) darf steuern". Hier
        # frisch gelesen -- der Server-Thread hat mit einem Schnappschuss gearbeitet.
        if not (self._live_state.remote_active()
                or (verbund and self._partner.enabled)
                or (local and self._settings.share_config["local_bypass"])):
            self._share_bridge.complete(request_id, False, "inactive", "", 0.0)
            return
        self._dispatch_share_action.emit(request_id, device_id, kind, action, value, channel)

    def _on_share_all_off(self, _request_id: int, client: str) -> None:
        # Derselbe Weg wie der ALLE-AUS-Knopf. Ohne die Pruefungen von oben --
        # der Notaus geht immer. Kommt es vom Verbund-Partner, nicht zurueckreichen (er hat es
        # selbst ausgeloest); von MCP oder einem anderen Client schon (PI-27 a).
        self._safe_stop(f"remote all-off ({client})", propagate=not is_verbund_client(client), stop_run=True)

    def _on_share_control_toggled(self, active: bool) -> None:
        """Hauptschalter "Fernsteuerung aktiv" im Netzwerk-Reiter."""
        if active:
            minutes = self._settings.share_config["control_timeout_min"]
            self._live_state.set_remote_control(True, minutes * 60.0)
            self._remote_timer.start()
            logger.warning("Fernsteuerung freigegeben fuer %d min", minutes)
        else:
            self._remote_timer.stop()
            self._live_state.set_remote_control(False)
            logger.warning("Fernsteuerung ausgeschaltet")
        self._refresh_remote_status()

    def _refresh_remote_status(self) -> None:
        self._render_mcp_chip()
        remaining = self._live_state.remote_remaining_s()
        if remaining <= 0.0:
            if self._remote_timer.isActive():
                # Zeitfenster abgelaufen (und nicht von Hand ausgeschaltet).
                self._remote_timer.stop()
                logger.warning("Fernsteuerung abgelaufen")
                self.statusBar().showMessage(tr("Fernsteuerung abgelaufen"), 10000)
            self._remote_status_label.hide()
            self.settings_tab.set_share_control_state(False, tr("Fernsteuerung aus"))
            return
        total = int(remaining)
        text = tr("Fernsteuerung aktiv – noch {time}",
                  time=f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
                  if total >= 3600 else f"{total // 60}:{total % 60:02d}")
        pal = ThemeManager.instance().palette
        self._remote_status_label.setStyleSheet(f"color: {pal.warning}; font-weight: bold;")
        self._remote_status_label.setText(text)
        self._remote_status_label.show()
        self.settings_tab.set_share_control_state(True, text)

    def _ensure_share_token(self, config: dict) -> None:
        if config.get("enabled") and not config.get("token"):
            self._settings.set_share_token(new_token())

    def _on_share_token_regenerate(self) -> None:
        self._settings.set_share_token(new_token())

    def _on_share_started(self, url: str) -> None:
        self.settings_tab.set_share_status(tr("Server läuft auf {url}").format(url=url), False)
        # Fertige Kachel-URL zum Abtippen in den ESP32-Sketch. Die erste
        # freigegebene Kachel als Beispiel -- ohne Freigabe die Startseite,
        # die dann selbst erklaert, dass noch nichts freigegeben ist.
        shared = [d for d, e in self._settings.share_config["devices"].items() if e.get("read")]
        self.settings_tab.set_share_url(f"{url}/display?tile={shared[0]}" if shared else url)

    def _on_share_error(self, message: str) -> None:
        self.settings_tab.set_share_status(message, True)

    def _refresh_share_url(self, _config: dict) -> None:
        if self._share_server.is_running():
            self._on_share_started(self._share_server.url())

    def _on_share_stopped(self) -> None:
        self.settings_tab.set_share_status(tr("Server gestoppt"), False)

    def _wire_dashboard_view(self) -> None:
        # Der Ansicht-Umschalter sitzt unten rechts im Dashboard selbst (siehe
        # DashboardWidget) -- hier nur die Verkabelung mit der persistierten
        # Einstellung, die beim Start wiederhergestellt wird.
        self.dashboard.compact_toggle_requested.connect(
            lambda: self._settings.set_dashboard_compact(not self._settings.dashboard_compact)
        )
        self._settings.dashboard_compact_changed.connect(self.dashboard.set_compact)
        self.dashboard.set_compact(self._settings.dashboard_compact)
        # Abdocken in ein eigenes Fenster (z.B. zweiter Bildschirm). Ein beim
        # letzten Beenden abgedocktes Dashboard geht erst nach dem Anzeigen des
        # Hauptfensters wieder auf (singleShot), sonst stuende es dahinter.
        self.dashboard.detach_toggle_requested.connect(self._toggle_dashboard_detached)
        if self._settings.dashboard_detached:
            QTimer.singleShot(0, self._detach_dashboard)

    def _toggle_dashboard_detached(self) -> None:
        if self._dashboard_window is None:
            self._detach_dashboard()
        else:
            self._dashboard_window.close()  # -> closed -> _dock_dashboard

    def _detach_dashboard(self) -> None:
        if self._dashboard_window is not None or self._closing or self._touch:
            return   # im Touch-Profil ist das Dashboard ein eigener Reiter
        self._central_layout.removeWidget(self.dashboard)
        window = DashboardWindow()
        window.set_dashboard(self.dashboard)
        window.closed.connect(self._dock_dashboard)
        geometry = self._settings.dashboard_window_geometry
        if not (geometry and window.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))):
            # Erstes Abdocken: so breit wie das Hauptfenster, leicht versetzt.
            window.resize(self.width(), window.sizeHint().height())
            window.move(self.pos() + QPoint(40, 40))
        self._dashboard_window = window
        self.dashboard.set_detached(True)
        self._settings.set_dashboard_detached(True)
        window.show()
        window.raise_()

    def _dock_dashboard(self) -> None:
        window = self._dashboard_window
        if window is None:
            return
        self._dashboard_window = None
        self._settings.set_dashboard_window_geometry(bytes(window.saveGeometry().toBase64()).decode("ascii"))
        # Direkt unter dem Sicherheitsbanner, wie beim Start.
        self._central_layout.insertWidget(self._central_layout.indexOf(self._safety_banner) + 1, self.dashboard)
        self.dashboard.show()
        self.dashboard.set_detached(False)
        # Beim Beenden bleibt "abgedockt" gespeichert -> naechster Start wieder im Fenster.
        if not self._closing:
            self._settings.set_dashboard_detached(False)
        window.deleteLater()

    # -- Touch-Profil (touch_ui.py, Pi-Plan Phase 4) --------------------------

    def _wire_touch(self) -> None:
        self._nav.add_page(self._overview_page, "mdi.view-dashboard-outline", lambda: tr("Übersicht"))
        self._nav.add_page(self.control_tab, "mdi.tune-variant", lambda: tr("Steuerung"))
        self._nav.add_page(self.testcase_tab, "mdi.playlist-play", lambda: tr("Testablauf"))
        self._nav.add_page(self.timeline_tab, "mdi.chart-line", lambda: tr("Verlauf"))
        self._nav.add_page(self.settings_tab, "mdi.cog-outline", lambda: tr("Einstellungen"))
        self._nav.selected.connect(self.tabs.setCurrentWidget)
        self.tabs.currentChanged.connect(lambda index: self._nav.set_current(self.tabs.widget(index)))
        self._top_bar.fullscreen_requested.connect(self._toggle_fullscreen)
        self._top_bar.power_requested.connect(self._on_power_requested)
        self._top_bar.arrange_toggled.connect(self._on_arrange_toggled)
        self._top_bar.set_power_available(system_power.available())
        # Rahmen fuer PC und Touch gleich (0.53.0): Reiterleiste und Statusleiste verborgen, Geraetezustand,
        # Fernsteuerungs-Hinweis und ALLE AUS in der Leiste oben
        self.tabs.tabBar().hide()
        self.statusBar().hide()
        self._top_bar.adopt(self._status_container, [self._remote_status_label, self._all_off_button],
                            leading=self._safety_status_label)
        # QScrollArea.setWidget zeigt das Widget nicht von selbst; der Fernsteuerungs-Hinweis bleibt verborgen,
        # bis der Hauptschalter an ist (_refresh_remote_status)
        self._status_container.show()
        self._all_off_button.show()
        self._nav.set_touch(False)
        self._top_bar.set_touch(False)
        self._nav.set_page_visible(self._overview_page, False)
        self._nav.set_current(self.tabs.currentWidget())
        chosen = self._settings.touch_mode
        on = suggest_touch(self.screen()) if chosen is None else chosen
        self.settings_tab.set_touch_mode(on, automatic=chosen is None and on)
        self.settings_tab.touch_mode_toggled.connect(self._settings.set_touch_mode)
        self._settings.touch_mode_changed.connect(self._apply_touch)
        self._settings.touch_mode_changed.connect(lambda value: self.settings_tab.set_touch_mode(value))
        self._apply_touch(on)

    def _apply_touch(self, on: bool) -> None:
        if on == self._touch:
            return
        if on and self._dashboard_window is not None:
            self._dashboard_window.close()   # -> _dock_dashboard, danach Reiter
        self._touch = on
        app = QApplication.instance()
        # Schrift VOR dem Stylesheet: Regeln mit font-weight (Kacheltitel) frieren beim Polieren die
        # damalige Schrift samt Groesse ein -- mit umgekehrter Reihenfolge blieben Verlauf und Testablauf
        # bei 12 px statt 14 px wie die Steuerung (GUI-Review UX-6, 11.11.2026). set_touch() wendet das
        # Stylesheet neu an und poliert damit alles mit der neuen Schrift.
        if on:
            self._base_font = app.font()
            font = QFont(self._base_font)
            font.setPointSizeF(self._base_font.pointSizeF() * FONT_SCALE)
            app.setFont(font)
        elif self._base_font is not None:
            app.setFont(self._base_font)
        ThemeManager.instance().set_touch(on)
        self._repolish_top_down()
        touch_sizer().set_active(on)
        # Am PC steht das Dashboard als Streifen ueber allen Seiten (hohe Aufloesung, abdockbar), im
        # Touch-Profil ist es die eigene Seite "Uebersicht"
        if on:
            self._central_layout.removeWidget(self.dashboard)
            self._overview_layout.insertWidget(0, self.dashboard)
            self.tabs.addTab(self._overview_page, tr("Übersicht"))
            self.tabs.setCurrentWidget(self._overview_page)
        else:
            self.tabs.removeTab(self.tabs.indexOf(self._overview_page))
            self._overview_layout.removeWidget(self.dashboard)
            self._central_layout.insertWidget(self._central_layout.indexOf(self._safety_banner) + 1, self.dashboard)
            self.tabs.setCurrentIndex(0)
        self._nav.set_page_visible(self._overview_page, on)
        self._nav.set_touch(on)
        self._top_bar.set_touch(on)
        self.dashboard.set_detach_available(not on)
        self.dashboard.set_multi_row(on)   # Uebersicht: Kacheln umbrechen statt waagerecht scrollen
        # Touch: Ziehen ist Wischen; Kacheln verschieben nur im Modus "Anordnen" (_on_arrange_toggled)
        self.dashboard.set_drag_allowed(not on)
        self.control_tab.set_drag_allowed(not on)
        self.dashboard.show()
        self._nav.set_current(self.tabs.currentWidget())
        self._style_all_off_button()
        for device_id in self._status_labels:   # Chips (Touch) bzw. "Name: Zustand" (PC)
            self._render_status_label(device_id)
        self._render_safety_status(self._safety.current_state())
        self._render_partner_chip()
        self._render_mcp_chip()
        logger.info("Touch-Bedienung %s", "an" if on else "aus")

    def _repolish_top_down(self) -> None:
        """Alle Widgets von oben nach unten neu polieren. Beim Neu-Anwenden des Stylesheets poliert Qt in
        beliebiger Reihenfolge, und jedes Widget uebernimmt die Schrift seines Elternteils zu diesem
        Zeitpunkt -- ein vor seinem Elternteil poliertes Kind behielt die alte Groesse (Verlauf 12 px
        statt 14 px, GUI-Review UX-6). Nur beim Umschalten des Touch-Profils, einmalig."""
        def depth(widget: QWidget) -> int:
            n = 0
            while (widget := widget.parentWidget()) is not None:
                n += 1
            return n
        for widget in sorted(self.findChildren(QWidget), key=depth):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)

    def _on_arrange_toggled(self, on: bool) -> None:
        """Modus "Anordnen" (Touch-Leiste): Kacheln in Uebersicht und Steuerung ziehen statt wischen."""
        allowed = on or not self._touch
        self.dashboard.set_drag_allowed(allowed)
        self.control_tab.set_drag_allowed(allowed)
        touch_sizer().set_swipe_blocked(
            [self.control_tab.scroll_area(), self.dashboard.scroll_area(), self._overview_page], on)
        # Herausgezoomtes Abbild aller Kacheln: im Anordnen-Modus laesst sich nicht wischen, Kacheln
        # ausserhalb des sichtbaren Bereichs waren sonst nicht erreichbar (Nutzerfeedback 12.11.2026)
        for overlay in getattr(self, "_arrange_overlays", []):
            overlay.close_overlay()
        self._arrange_overlays = []
        if on and self._touch:
            from arrange_overlay import ArrangeOverlay
            # Uebersicht: im Touch-Profil scrollt die Seite um das Dashboard (_overview_page), nicht dessen
            # eigene Flaeche -- das Abbild muss ueber dem sichtbaren Ausschnitt liegen
            self._arrange_overlays = [ArrangeOverlay(self._overview_page, self.dashboard),
                                      ArrangeOverlay(self.control_tab.scroll_area(), self.control_tab)]

    def _show_toast(self, message: str) -> None:
        """statusBar().showMessage(...) ohne sichtbare Statusleiste: unten mittig einblenden, solange die
        Meldung gilt (QStatusBar raeumt sie nach ihrer Zeit ab -> messageChanged(""))."""
        toast = self._toast
        if not message:
            toast.hide()
            return
        parent = toast.parentWidget()
        toast.setText(message)
        width = min(parent.width() - 40, max(240, toast.fontMetrics().horizontalAdvance(message) + 40))
        toast.setFixedWidth(max(120, width))
        toast.adjustSize()
        toast.move((parent.width() - toast.width()) // 2, parent.height() - toast.height() - 16)
        toast.raise_()
        toast.show()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.WindowStateChange:
            self._top_bar.set_fullscreen(self.isFullScreen())
        super().changeEvent(event)

    def _on_power_requested(self) -> None:
        """Knopf in der Touch-Leiste: Rechner herunterfahren oder neu starten (system_power.py).
        Mit Rueckfrage -- ein Fehltipp soll nicht den Pi ausschalten."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Ausschalten"))
        box.setText(tr("Rechner herunterfahren oder neu starten?"))
        box.setInformativeText(tr(
            "LabControl gibt vorher die Netzteile zur Handbedienung frei (1 V / 0 A, Ausgang EIN) und schaltet "
            "Last, Funktionsgenerator und microHIL-Ausgänge aus."))
        off = box.addButton(tr("Herunterfahren"), QMessageBox.ButtonRole.AcceptRole)
        restart = box.addButton(tr("Neu starten"), QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(tr("Abbrechen"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is off:
            ok = system_power.power_off()
        elif clicked is restart:
            ok = system_power.reboot()
        else:
            return
        if not ok:
            QMessageBox.warning(self, tr("Ausschalten"), tr("Das System hat das Ausschalten abgelehnt (siehe Log)."))

    def _wire_notifications(self) -> None:
        # Desktop-Benachrichtigung bei Lauf-Ende/Fehler (FEATURES.md Punkt 8),
        # praktisch bei langen unbeaufsichtigten Laeufen. QSystemTrayIcon ist
        # auf dem Kiosk-Pi (Vollbild, keine Taskleiste) i.d.R. nicht verfuegbar
        # -- in dem Fall bleibt _tray_icon None und _show_notification wird
        # zum stillen No-Op, statt zu crashen.
        self._tray_icon: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(qta.icon("mdi.flask-outline"), self)
            self._tray_icon.setToolTip(f"LAB CONTROL v{__version__}")
            self._tray_icon.show()
        self.testcase_tab.notify_requested.connect(self._show_notification)

    def _show_notification(self, title: str, message: str) -> None:
        if self._tray_icon is None or not self._settings.notifications_enabled:
            return
        self._tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)

    def _wire_panel_colors(self) -> None:
        # Individuelle Panel-Hintergrundfarben (FEATURES.md Punkt 2): eine
        # Farbe pro Geraete-ID, geteilt zwischen Dashboard und Control-Tab
        # (siehe panel_color.py), global an/ausschaltbar im Einstellungen-Tab.
        self.settings_tab.set_panel_colors_enabled(self._settings.panel_colors_enabled)
        self.settings_tab.panel_colors_toggled.connect(self._settings.set_panel_colors_enabled)
        self._settings.panel_colors_enabled_changed.connect(self.dashboard.set_panel_colors_enabled)
        self._settings.panel_colors_enabled_changed.connect(self.control_tab.set_panel_colors_enabled)
        self.dashboard.set_panel_colors_enabled(self._settings.panel_colors_enabled)
        self.control_tab.set_panel_colors_enabled(self._settings.panel_colors_enabled)

        # Farbauswahl selbst nur noch im Control-Tab (siehe BUGS.md #10b) --
        # Dashboard zeigt die Farbe weiterhin an (set_panel_color unten bleibt
        # verdrahtet), kann sie aber nicht mehr selbst auswaehlen.
        self.control_tab.panel_color_requested.connect(self._settings.set_panel_color)
        self._settings.panel_color_changed.connect(self.dashboard.set_panel_color)
        self._settings.panel_color_changed.connect(self.control_tab.set_panel_color)

        # Neu erzeugte Panels/Sektionen (siehe dashboard.on_device_known/
        # control_tab.on_device_known, beide bereits ueber _wire_registry an
        # device_known angebunden) kennen ihre gespeicherte Farbe noch nicht --
        # hier mit dem persistierten Stand nachziehen. Setzt voraus, dass diese
        # Verbindung NACH den beiden on_device_known-Verbindungen aus
        # _wire_registry hergestellt wird (Qt feuert Slots in Verbindungs-
        # reihenfolge), was durch den Aufruf von _wire_panel_colors() nach
        # _wire_registry() im Konstruktor sichergestellt ist.
        self._registry.device_known.connect(self._on_device_known_panel_color)

        # BUGS.md #10e: beim Aktivieren der Option soll jedes bereits bekannte
        # Geraet automatisch eine (unterschiedliche) Farbe bekommen, statt
        # dass der Nutzer jedes Panel manuell einfaerben muss. Nur beim
        # Einschalten (nicht beim Ausschalten) und nur fuer Geraete ohne
        # bereits gespeicherte Farbe -- eine einmal manuell/automatisch
        # vergebene Farbe bleibt beim erneuten Ein-/Ausschalten erhalten.
        self._settings.panel_colors_enabled_changed.connect(self._on_panel_colors_enabled_changed)

    def _wire_panel_order(self) -> None:
        """Dashboard-Kachel-Reihenfolge (Drag&Drop, siehe dashboard.py:
        DashboardWidget.panel_order_changed/set_panel_order) persistent
        machen -- analog zu _wire_panel_colors, aber ohne Live-Signal in die
        Gegenrichtung: die Reihenfolge wird nur einmal beim Start aus
        Settings geladen (muss VOR _replay_known_devices() passieren, damit
        schon die ersten wiederhergestellten Panels an der richtigen Stelle
        landen, siehe DashboardWidget.set_panel_order()-Docstring), danach
        schreibt ausschliesslich das Dashboard selbst (per Drag) in
        Settings zurueck."""
        self.dashboard.set_panel_order(self._settings.panel_order)
        self.dashboard.panel_order_changed.connect(self._settings.set_panel_order)

    def _wire_control_tile_order(self) -> None:
        """Control-Tab-Kachel-Reihenfolge im festen Raster (Drag&Drop, siehe
        control_tab.py: ControlTab.tile_order_changed/set_tile_order)
        persistent machen -- wortwoertlich dasselbe Muster wie
        _wire_panel_order() oben, nur fuer den Control-Tab: einmalig beim
        Start aus Settings geladen (muss ebenfalls VOR
        _replay_known_devices() passieren, da ControlTab.on_device_known()
        ueber dasselbe DeviceRegistry.device_known-Signal laeuft wie beim
        Dashboard), danach schreibt ausschliesslich der Control-Tab selbst
        (per Drag) in Settings zurueck."""
        self.control_tab.set_tile_order(self._settings.control_tile_order)
        self.control_tab.tile_order_changed.connect(self._settings.set_control_tile_order)
        # Seit 0.20.1: feste Zellen je Kachel (die Reihenfolge bleibt als
        # Startstand fuer Kacheln ohne gespeicherte Zelle).
        self.control_tab.set_tile_cells(self._settings.control_tile_cells)
        self.control_tab.tile_cells_changed.connect(self._settings.set_control_tile_cells)

    def _assign_free_panel_colors(self, device_ids: list[str]) -> None:
        """Vergibt an jedes device_id in device_ids (ohne bereits gespeicherte
        Farbe) die naechste freie Farbe aus PANEL_COLOR_ORDER, unter allen
        bekannten Geraeten bereits belegte Farben ausgenommen. Gemeinsame
        Vergabe-Logik fuer _on_panel_colors_enabled_changed (10e) und
        _on_device_known_panel_color (BUGS.md #15)."""
        unassigned = [d for d in device_ids if self._settings.panel_color(d) is None]
        if not unassigned:
            return
        used = {self._settings.panel_color(d) for d in self._device_labels} - {None}
        free = [c for c in PANEL_COLOR_ORDER if c not in used] or list(PANEL_COLOR_ORDER)
        for i, device_id in enumerate(unassigned):
            self._settings.set_panel_color(device_id, free[i % len(free)])

    def _on_panel_colors_enabled_changed(self, enabled: bool) -> None:
        if not enabled:
            return
        self._assign_free_panel_colors(list(self._device_labels))

    def _on_device_known_panel_color(self, kind: str, device_id: str, label: str) -> None:
        # BUGS.md #15: 10e deckt nur das Einschalten der Option fuer bereits
        # bekannte Geraete ab -- ein danach neu hinzukommendes Geraet bei
        # bereits aktiver Option durchlaeuft nur diesen Handler hier, daher
        # dieselbe Vergabe-Logik auch an dieser Stelle anstossen, bevor die
        # (ggf. gerade erst vergebene) Farbe an Dashboard/Control-Tab geht.
        if self._settings.panel_colors_enabled:
            self._assign_free_panel_colors([device_id])
        color_key = self._settings.panel_color(device_id)
        self.dashboard.set_panel_color(device_id, color_key)
        self.control_tab.set_panel_color(device_id, color_key)

    def _wire_testcase_tab(self) -> None:
        self._test_runner = TestRunner()
        self._test_runner.execute_action.connect(self._on_test_execute_action)
        self._dispatch_test_action.connect(self._worker.execute_action)
        self._test_runner.execute_can_send.connect(self._on_test_execute_can_send)
        self._dispatch_test_can_send.connect(self._worker.execute_can_send)
        self._worker.action_completed.connect(self._test_runner.on_action_completed)
        self._worker.load_measurement.connect(self._test_runner.on_load_measurement)
        self._worker.psu_measurement.connect(self._test_runner.on_psu_measurement)
        # microHIL/CAN als while/if-Bedingungsquelle (BUGS_GESCHLOSSEN.md
        # #35): fuettert die Cache-Strukturen, gegen die TestRunner.
        # _eval_hil_condition/_eval_can_condition auswerten, genau wie
        # load_measurement/psu_measurement oben es fuer _measurements tun.
        self._worker.hil_analog_input.connect(self._test_runner.on_hil_analog_input)
        self._worker.hil_digital_state.connect(self._test_runner.on_hil_digital_state)
        self._worker.can_signals_decoded.connect(self._test_runner.on_can_signals_decoded)
        self._worker.device_removed.connect(self._test_runner.on_device_removed)
        self._test_runner.step_started.connect(self.testcase_tab.on_step_started)
        self._test_runner.step_result.connect(self.testcase_tab.on_step_result)
        self._test_runner.step_failed.connect(self.testcase_tab.on_step_failed)
        self._test_runner.run_finished.connect(self.testcase_tab.on_run_finished)
        self._test_runner.run_stopped.connect(self.testcase_tab.on_run_stopped)
        self._test_runner.iteration_changed.connect(self.testcase_tab.on_iteration_changed)

        # Nachlauf-Report: passiver Mitschnitt des Laufs (siehe run_record.py),
        # unabhaengig vom manuellen Recorder des Verlauf-Tabs.
        self._test_runner.step_started.connect(self._run_recorder.on_step_started)
        self._test_runner.step_result.connect(self._run_recorder.on_step_result)
        self._test_runner.step_failed.connect(self._run_recorder.on_step_failed)
        self._test_runner.run_finished.connect(self._run_recorder.on_run_finished)
        self._test_runner.run_stopped.connect(self._run_recorder.on_run_stopped)
        self._test_runner.iteration_changed.connect(self._run_recorder.on_iteration_changed)
        self._worker.load_measurement.connect(self._run_recorder.on_load_measurement)
        self._worker.psu_measurement.connect(self._run_recorder.on_psu_measurement)
        for sig in (self._test_runner.run_finished, self._test_runner.run_stopped):
            sig.connect(lambda: self.testcase_tab.set_report_available(True))
        self._test_runner.step_failed.connect(
            lambda *_args: self.testcase_tab.set_report_available(True)
        )
        self.testcase_tab.open_report_requested.connect(self._on_open_report)
        self.testcase_tab.export_report_pdf_to.connect(self._on_export_report_pdf)

        # Watchdog-Ueberwachung endet mit dem Testlauf (egal ob normal beendet,
        # gestoppt oder fehlgeschlagen) -- sonst wuerde der Stale-Timer nach
        # einem bewussten Stop noch nachtriggern.
        self._test_runner.run_finished.connect(self._safety.end_run_supervision)
        self._test_runner.run_stopped.connect(self._safety.end_run_supervision)
        self._test_runner.step_failed.connect(lambda *_args: self._safety.end_run_supervision())

        # Pausiert den PicoScope-Reconnect-Timer waehrend eines Testlaufs
        # (siehe device_worker._reconnect_picoscope-Docstring) -- sonst kann
        # dessen periodisches Verbinden mit einer PICO_*-Testablauf-Aktion um
        # das exklusive Handle konkurrieren. Ueber _set_picoscope_test_running
        # (nicht direkt self._worker.set_test_running), da run_finished/
        # run_stopped/step_failed im GUI-Thread emittiert werden -- ein
        # direkter Methodenaufruf auf dem Worker wuerde KEINE Queued
        # Connection nutzen (siehe Kommentar bei _dispatch_test_action oben).
        self._set_picoscope_test_running.connect(self._worker.set_test_running)
        self._test_runner.run_finished.connect(lambda: self._set_picoscope_test_running.emit(False))
        self._test_runner.run_stopped.connect(lambda: self._set_picoscope_test_running.emit(False))
        self._test_runner.step_failed.connect(lambda *_args: self._set_picoscope_test_running.emit(False))

        # Sperrzustand der Netzwerk-Freigabe an dasselbe Tripel haengen --
        # jeder Weg aus einem Laufe heraus muss ihn wieder freigeben.
        self._test_runner.run_finished.connect(lambda: self._live_state.set_test_running(False))
        self._test_runner.run_stopped.connect(lambda: self._live_state.set_test_running(False))
        self._test_runner.step_failed.connect(lambda *_args: self._live_state.set_test_running(False))

        # Einmalig offen gehaltene PicoScope-Verbindung fuer die Laufdauer
        # (siehe _on_run_requested/device_worker.open_picoscope_session) --
        # muss NACH set_test_running(False) verdrahtet sein (Reihenfolge der
        # connect()-Aufrufe = Zustellreihenfolge auf dem Worker-Thread bei
        # SELBER emittierender Signal-Quelle), damit _reconnect_picoscope()
        # beim abschliessenden Status-Refresh in close_picoscope_sessions()
        # nicht mehr durch _test_running blockiert wird.
        self._close_picoscope_sessions.connect(self._worker.close_picoscope_sessions)
        self._test_runner.run_finished.connect(lambda: self._close_picoscope_sessions.emit())
        self._test_runner.run_stopped.connect(lambda: self._close_picoscope_sessions.emit())
        self._test_runner.step_failed.connect(lambda *_args: self._close_picoscope_sessions.emit())
        # Erfassungen ueber Netzwerk/MCP wieder zulassen (siehe _on_run_requested).
        self._test_runner.run_finished.connect(lambda *_args: self._scope_service.end_test())
        self._test_runner.run_stopped.connect(lambda *_args: self._scope_service.end_test())
        self._test_runner.step_failed.connect(lambda *_args: self._scope_service.end_test())

        # Unbeaufsichtigte Laeufe: ein Schrittfehler (Geraetefehler, veraltete
        # Messung, verletzte Pass/Fail-Pruefung mit "Bei Verletzung abbrechen")
        # schaltet sofort alle Ausgaenge ab, statt auf das manuelle Quittieren
        # per Stop-Button zu warten (siehe testcase_tab.on_step_failed).
        self._test_runner.step_failed.connect(
            lambda _index, message: self._safe_stop(f"step failed: {message}")
        )

        self.testcase_tab.run_requested.connect(self._on_run_requested)
        self.testcase_tab.stop_requested.connect(self._test_runner.stop)
        self.testcase_tab.stop_requested.connect(lambda: self._safe_stop("stop button"))

    def _on_run_requested(self) -> None:
        steps = self.testcase_tab.steps()
        if not any(step.enabled and step.step_type == "action" for step in steps):
            return
        if self._safety.is_tripped():
            self.statusBar().showMessage(
                tr("Testlauf gesperrt: Sicherheitsabschaltung zuerst quittieren"), 5000
            )
            return
        if self._test_runner.is_running() or self._pending_run_steps is not None:
            return
        # Verbund (PI-24 a): Partnergeraete erst belegen (PI-25 a), dann starten
        steps, partner_devices, problem = self._partner_steps(steps)
        if problem:
            self._refuse_run(problem)
            return
        if partner_devices:
            if not self._partner.is_connected():
                self._refuse_run(tr("Verbund-Partner {name} nicht verbunden", name=self._partner.name()))
                return
            self._passthrough.release("Testablauf startet")   # PartnerLink fuehrt nur eine Belegung
            self._pending_run_steps = steps
            self.testcase_tab.show_message(tr("Belege Geräte beim {name} …", name=self._partner.name()))
            self._partner.acquire_lease(sorted(partner_devices), self.testcase_tab.current_testcase_name())
            return
        self._start_run(steps)

    def _refuse_run(self, message: str) -> None:
        logger.warning("Testlauf nicht gestartet: %s", message)
        self.testcase_tab.show_message(tr("Nicht gestartet: {reason}", reason=message), "error")
        self.statusBar().showMessage(tr("Nicht gestartet: {reason}", reason=message), 10000)

    def _start_run(self, steps: list[TestStep]) -> None:
        self.testcase_tab.on_run_started()
        self._run_recorder.begin(steps, self.testcase_tab.current_testcase_name())
        step_device_ids = self._resolve_step_device_ids(steps)
        # PicoScope bewusst NICHT an die Watchdog-Verbindungsueberwachung
        # uebergeben (siehe _resolve_step_device_ids-Docstring): anders als
        # PSU/Last/HIL hat es keinen steuerbaren Ausgang (taucht nirgends in
        # all_outputs_off auf) UND liefert waehrend eines Laufs bewusst KEINE
        # periodischen Messwerte (kein Poll-Zyklus, siehe device_worker.py) --
        # ohne diesen Ausschluss wuerde jeder Lauf mit PICO_*-Schritt nach
        # STALE_TIMEOUT_S faelschlich als "veraltet" abgebrochen, obwohl
        # nichts falsch lief (an echter Hardware reproduziert).
        # J-Link ebenso: keine Messwerte, kein Ausgang (PI-31 a)
        self._safety.begin_run_supervision(
            {d for d in step_device_ids if not d.startswith(("picoscope:", "jlink:"))}
        )
        self._set_picoscope_test_running.emit(True)
        # Sperrt (ab Phase 2) den schreibenden Netzwerkzugriff und wird
        # schon jetzt im Status-Endpoint gemeldet.
        self._live_state.set_test_running(True)
        # Nur oeffnen, wenn der Lauf tatsaechlich eine PICO_*-Aktion
        # enthaelt -- ein Lauf ohne Oszilloskop-Beteiligung soll weder die
        # ~4,5s Verbindungszeit noch die PicoScope-7-App fuer die gesamte
        # Laufdauer blockieren (siehe device_worker.open_picoscope_session).
        #
        # BLOCKIEREND (BlockingQueuedConnection) statt per Signal-emit(), wie
        # bereits bei closeEvent()/all_outputs_off(): ein einfaches emit()
        # gibt sofort zurueck, WAEHREND open_picoscope_session() auf dem
        # Worker-Thread noch die ~4,5s Verbindung aufbaut -- der direkt
        # danach gestartete Testlauf konnte dadurch bereits seinen ersten
        # PICO_*-Schritt ausfuehren, bevor die Session ueberhaupt existierte
        # (an echter Hardware reproduziert, Nutzerfeedback). Der Aufruf hier
        # blockiert den GUI-Thread bewusst bis die Session steht (oder das
        # Oeffnen best-effort scheitert, siehe open_picoscope_session-
        # Docstring) -- exakt einmal pro Lauf mit PicoScope-Beteiligung.
        for device_id in step_device_ids:
            if device_id.startswith("picoscope:"):
                # Vorrang des Testablaufs: der ScopeService (Erfassungen ueber
                # Netzwerk/MCP) nimmt keine neuen Erfassungen mehr an, wartet
                # eine laufende ab und gibt das exklusive Handle frei, BEVOR
                # die Session geoeffnet wird. Blockiert hoechstens so lange wie
                # eine Erfassung (Trigger-Timeout max. 30 s).
                self._scope_service.begin_test(device_id)
                QMetaObject.invokeMethod(
                    self._worker,
                    "open_picoscope_session",
                    Qt.ConnectionType.BlockingQueuedConnection,
                    Q_ARG(str, device_id),
                )
        self._test_runner.start(steps)

    def _resolve_step_device_ids(self, steps: list[TestStep]) -> set[str]:
        """Ermittelt die an einem Testlauf beteiligten Geraete-IDs -- genutzt
        sowohl fuer die Watchdog-Verbindungsueberwachung (siehe
        safety.begin_run_supervision) als auch dafuer, welche PicoScope-
        Sessions ein Lauf oeffnen muss (siehe _on_run_requested unten).
        Liefert bewusst ALLE Geraetearten inkl. "picoscope" -- der Aufrufer
        filtert fuer die Watchdog-Uebergabe selbst heraus, was er braucht
        (PicoScope wird dort ausgeschlossen, siehe Kommentar dort).

        Nicht aufloesbare Ziele (z.B. "automatisch" ohne aktuell verbundenes
        Geraet dieser Art) werden uebersprungen -- der Runner scheitert an
        solchen Schritten ohnehin sofort (step_failed), was ueber die
        step_failed-Verdrahtung oben ebenfalls zum Safe-Stop fuehrt.
        """
        device_ids: set[str] = set()
        for step in steps:
            if not step.enabled:
                continue
            if step.step_type == "action":
                resolved, _ = self._resolve_device_id(step.device_kind, step.device_id)
                if resolved:
                    device_ids.add(resolved)
            elif step.step_type in ("while", "if") and step.cond_source == "measurement":
                resolved, _ = self._resolve_device_id(step.cond_device_kind, step.cond_device_id)
                if resolved:
                    device_ids.add(resolved)
        return device_ids

    def _on_test_execute_action(
        self, device_id: str, kind: str, action: str, value: float, channel: int
    ) -> None:
        # Verbund (PI-24 a): Partnergeraet -> ueber die Freigabe des Partners, mit Belegung
        remote_id = verbund_ids.partner_device(device_id, self._partner_host_name)
        if remote_id is not None:
            self._send_partner_test_action(remote_id, device_id, kind, action, value, channel)
            return
        resolved_id, error = self._resolve_device_id(kind, device_id)
        if resolved_id is None:
            self._test_runner.on_action_completed(False, error, 0.0)
            return
        self._dispatch_test_action.emit(resolved_id, kind, action, value, channel)

    def _on_test_execute_can_send(self, device_id: str, arbitration_id: int, data_hex: str, extended: bool) -> None:
        resolved_id, error = self._resolve_device_id("can", device_id)
        if resolved_id is None:
            self._test_runner.on_action_completed(False, error, 0.0)
            return
        self._dispatch_test_can_send.emit(resolved_id, arbitration_id, data_hex, extended)

    def _resolve_device_id(self, kind: str, device_id: str) -> tuple[str | None, str]:
        if device_id:
            # Gegen die aktuell verbundenen Geraete dieser Art pruefen, statt
            # jede nicht-leere ID blind zu akzeptieren -- sonst wird ein
            # veralteter/nie existenter device_id-Wert aus einer gespeicherten
            # Testablauf-Datei (z.B. durch manuelles Bearbeiten oder einen
            # Import) unbemerkt in die Watchdog-Ueberwachung uebernommen
            # (siehe BUGS_OFFEN.md #28: "psu0001" ohne ":"-Trenner passt zu
            # keinem von _resolve_device_ids() je erzeugten Format und war nie
            # ein reales Geraet -- der Watchdog wartete trotzdem dauerhaft
            # vergeblich auf dessen erste Messung und loeste nach
            # STALE_TIMEOUT_S faelschlich "veraltet" aus).
            if device_id in self._online_devices.get(kind, set()):
                return device_id, ""
            return None, tr("Gerät '{device_id}' nicht verbunden", device_id=device_id)
        candidates = self._online_devices.get(kind, set())
        if len(candidates) == 1:
            return next(iter(candidates)), ""
        label = kind_label(kind)
        if not candidates:
            return None, tr("Kein Gerät des Typs '{label}' verbunden", label=label)
        return None, tr(
            "Mehrere Geräte des Typs '{label}' verbunden -- bitte Zielgerät in der Testcase-Zeile auswählen",
            label=label,
        )

    @Slot(str, bool)
    def _on_load_connected(self, device_id: str, online: bool) -> None:
        self._set_online("load", device_id, online)
        self.dashboard.set_load_online(device_id, online)
        self.control_tab.set_load_online(device_id, online)

    @Slot(str, bool)
    def _on_psu_connected(self, device_id: str, online: bool) -> None:
        if not online:
            self._manual_devices.discard(device_id)
        self._set_online("psu", device_id, online)
        self.dashboard.set_psu_online(device_id, online)
        self.control_tab.set_psu_online(device_id, online)

    @Slot(str, bool)
    def _on_psu_manual_state(self, device_id: str, manual: bool) -> None:
        """Netzteil in Handbedienung: fuer Dashboard, Statusleiste, Netzwerk-Freigabe/MCP wie nicht
        verfuegbar (es werden keine Messwerte mehr geliefert und Befehle abgelehnt), die Kachel im
        Control-Tab bleibt sichtbar, damit man zurueckschalten kann."""
        self.control_tab.set_psu_manual_state(device_id, manual)
        if manual:
            self._manual_devices.add(device_id)
        else:
            self._manual_devices.discard(device_id)
        self._set_online("psu", device_id, not manual)
        self.dashboard.set_psu_online(device_id, not manual)
        self._render_status_label(device_id)

    @Slot(str, bool)
    def _on_can_connected(self, device_id: str, online: bool) -> None:
        self._set_online("can", device_id, online)
        self.dashboard.set_can_online(device_id, online)
        self.control_tab.set_can_online(device_id, online)

    @Slot(str, bool)
    def _on_hil_connected(self, device_id: str, online: bool) -> None:
        self._set_online("hil", device_id, online)
        self.dashboard.set_hil_online(device_id, online)
        self.control_tab.set_hil_online(device_id, online)

    @Slot(str, bool)
    def _on_fg_connected(self, device_id: str, online: bool) -> None:
        self._set_online("fg", device_id, online)
        self.dashboard.set_fg_online(device_id, online)
        self.control_tab.set_fg_online(device_id, online)

    @Slot(str, bool)
    def _on_picoscope_connected(self, device_id: str, online: bool) -> None:
        self._set_online("picoscope", device_id, online)
        self.dashboard.set_picoscope_online(device_id, online)
        self.control_tab.set_picoscope_online(device_id, online)

    @Slot(str, bool)
    def _on_battsim_connected(self, device_id: str, online: bool) -> None:
        self._set_online("battsim", device_id, online)
        self.dashboard.set_battsim_online(device_id, online)
        self.control_tab.set_battsim_online(device_id, online)

    @Slot(str, bool)
    def _on_scpiscope_connected(self, device_id: str, online: bool) -> None:
        self._set_online("scpiscope", device_id, online)
        self.dashboard.set_picoscope_online(device_id, online)
        self.control_tab.set_picoscope_online(device_id, online)

    def _set_online(self, kind: str, device_id: str, online: bool) -> None:
        if online:
            self._online_devices[kind].add(device_id)
        else:
            self._online_devices[kind].discard(device_id)
        self._device_online[device_id] = online
        self._live_state.set_online(device_id, online)
        self._render_status_label(device_id)

    # -- Statusleiste: ein Label je bekanntem Geraet --------------------------

    def _on_device_known_status(self, kind: str, device_id: str, label: str) -> None:
        if device_id not in self._status_labels:
            status_label = QLabel()
            self._status_labels[device_id] = status_label
            self._status_layout.addWidget(status_label)
            self._device_online.setdefault(device_id, False)
        self._device_kinds[device_id] = kind
        self._device_labels[device_id] = label
        self._render_status_label(device_id)

    def _status_state(self, device_id: str) -> tuple[str, str, str]:
        """(Symbol, Zustandstext, Farbe) eines Geraets fuer Leiste und Liste."""
        pal = ThemeManager.instance().palette
        if device_id in self._manual_devices:
            return "Ⓗ", tr("Handbedienung"), pal.warning
        if self._device_online.get(device_id, False):
            return "✓", tr("verbunden"), pal.success
        return "✕", tr("getrennt"), pal.danger

    def _short_name(self, device_id: str) -> str:
        """Kurzname fuer den Status-Chip im Touch-Profil: Geraeteart + Nummer aus dem Namen ("Netzteil 2"
        -> "NT2"); ohne Nummer am Ende der Name selbst, gekuerzt."""
        label = self._device_labels.get(device_id, device_id)
        number = re.search(r"(\d+)\s*$", label)
        kind = self._device_kinds.get(device_id, "")
        if number and kind in STATUS_KIND_SHORT:
            return tr(STATUS_KIND_SHORT[kind]) + number.group(1)
        return label if len(label) <= 10 else label[:9] + "…"

    def _on_label_changed_status(self, kind: str, device_id: str, label: str) -> None:
        self._device_labels[device_id] = label
        self._render_status_label(device_id)

    def _render_status_label(self, device_id: str) -> None:
        status_label = self._status_labels.get(device_id)
        if status_label is None:
            return
        label = self._device_labels.get(device_id, device_id)
        symbol, state, chip_color = self._status_state(device_id)
        # Kurzer Chip mit Symbol statt "Name: Zustand" -- die volle Zeile passte ab vier Geraeten nicht mehr und war
        # vor allem an der Farbe lesbar (GUI-Review UX-1; seit PI-17 a auch am PC). Tipp/Klick -> Liste.
        status_label.setText(f"{symbol} {self._short_name(device_id)}")
        status_label.setToolTip(f"{label}: {state}")
        self._style_chip(status_label, chip_color)

    def _show_status_details(self) -> None:
        """Tipp/Klick auf die Statusanzeige -> alle Geraete mit vollem Namen und Zustand."""
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Gerätezustand"))
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)
        safety = QLabel(self._safety_status_label.toolTip() or self._safety_status_label.text())
        safety.setStyleSheet("font-weight: bold;")
        layout.addWidget(safety)
        if self._partner.enabled:
            partner = QLabel(self._partner_chip.toolTip())
            partner.setStyleSheet("font-weight: bold;")
            layout.addWidget(partner)
        if not self._mcp_chip.isHidden():
            mcp = QLabel(self._mcp_chip.toolTip())
            mcp.setStyleSheet("font-weight: bold;")
            layout.addWidget(mcp)
        for device_id in self._status_labels:
            symbol, state, color = self._status_state(device_id)
            row = QLabel(f"{symbol}  {self._device_labels.get(device_id, device_id)} – {state}")
            row.setStyleSheet(f"color: {color}; font-weight: bold;")
            layout.addWidget(row)
        if not self._status_labels:
            layout.addWidget(QLabel(tr("Kein Gerät verbunden")))
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.setMinimumWidth(420)
        dialog.exec()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        # Tipp/Klick auf die Statusanzeige oeffnet die Liste (_show_status_details; am PC seit PI-17 a). Das
        # Druecken annehmen, sonst kommt das Loslassen nicht an; Wischen erledigt im Touch-Profil der QScroller davor.
        if obj is getattr(self, "_status_container", None) or obj is getattr(self, "_safety_status_label", None):
            if event.type() == QEvent.Type.MouseButtonPress:
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                QTimer.singleShot(0, self._show_status_details)
                return True
        return super().eventFilter(obj, event)

    def _on_theme_changed(self, _palette: Palette) -> None:
        for device_id in self._status_labels:
            self._render_status_label(device_id)
        self._render_safety_status(self._safety.current_state())
        self._render_partner_chip()
        self._render_mcp_chip()
        self._style_safety_banner()
        self._style_all_off_button()

    @Slot(str, str)
    def _on_scope_connection_changed(self, device_id: str, status: str) -> None:
        """ScopeService hat verbunden ("mcp") oder getrennt ("free"): Kachel und
        Netzwerk-Zustand nachziehen. Nach dem Trennen ist das Geraet frei --
        es war ja eben noch von uns geoeffnet."""
        self.dashboard.update_picoscope_state(device_id, status, "", "")
        self.control_tab.update_picoscope_state(device_id, status, "", "")
        self._live_state.on_picoscope_state(device_id, status, "", "")

    @Slot(str, str, str)
    def _on_scope_capture_added(self, device_id: str, capture_id: str, text: str) -> None:
        self._last_scope_capture[device_id] = capture_id
        self.dashboard.update_picoscope_capture(device_id, capture_id, text)
        self.control_tab.update_picoscope_capture(device_id, capture_id, text)

    @Slot(str)
    def _show_scope_plot(self, device_id: str) -> None:
        """Knopf "Kurve" der Oszilloskop-Kachel: letzte Erfassung als Bild."""
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QDialog

        from scope_api.base import ScopeError

        capture_id = self._last_scope_capture.get(device_id, "")
        # Touch-Profil (4.6): Bild passend zum 1024 x 600-Display statt 1000 x 560 + Rand + Knopf
        width, height = (960, 440) if self._touch else (1000, 560)
        try:
            png = self._scope_service.plot(capture_id, None, None, None, width, height)
        except ScopeError as exc:
            QMessageBox.information(self, tr("Kurve"), exc.message)
            return
        pixmap = QPixmap()
        pixmap.loadFromData(png, "PNG")
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Letzte Erfassung {capture_id}", capture_id=capture_id))
        layout = QVBoxLayout(dialog)
        image = QLabel()
        image.setPixmap(pixmap)
        layout.addWidget(image)
        if self._touch:   # im Vollbild am Pi gibt es keine Fensterleiste zum Schliessen
            from PySide6.QtWidgets import QDialogButtonBox
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def closeEvent(self, event) -> None:
        # Als allererstes: keine Anfrage darf die App ueberleben, und der
        # Socket muss vor Prozessende frei sein (sonst scheitert ein
        # sofortiger Neustart am belegten Port). Ausserdem darf ein
        # schreibender Zugriff (Phase 2) keinen Worker im Abbau treffen.
        self._share_server.stop()
        # Verbund: nur die Abfrage beenden. Beenden HIER schaltet am Partner nichts ab -- seine
        # Geraete gehoeren ihm (Grundsatz 1); belegte Geraete regelt ab V2 das Lebenszeichen.
        self._partner.stop()
        # Abgedocktes Dashboard-Fenster mit schliessen (es hat kein Eltern-Fenster,
        # siehe dashboard_window.py); der Zustand bleibt fuer den naechsten Start.
        self._closing = True
        if self._dashboard_window is not None:
            self._dashboard_window.close()
        # Offenes Oszilloskop-Handle freigeben (sonst bleibt es bis Prozessende belegt).
        self._scope_service.shutdown()
        # Synchron (BlockingQueuedConnection) statt per _request_all_off, damit
        # der Kill garantiert VOR thread.quit()/wait() abgeschlossen ist --
        # sonst koennte die Anwendung schliessen, bevor der Worker die
        # Ausgaenge tatsaechlich abgeschaltet hat.
        QMetaObject.invokeMethod(
            self._worker,
            "all_outputs_off",
            Qt.ConnectionType.BlockingQueuedConnection,
            Q_ARG(str, REASON_WINDOW_CLOSE),
        )
        # CAN-Knoten geordnet beenden (Batterie-Simulator: Ausgang sperren und
        # Fernsteuerung abgeben), bevor ihre Sende-Threads mit dem Worker enden.
        QMetaObject.invokeMethod(
            self._worker, "shutdown_can_nodes", Qt.ConnectionType.BlockingQueuedConnection,
        )
        QMetaObject.invokeMethod(
            self._worker, "stop_timers", Qt.ConnectionType.BlockingQueuedConnection,
        )
        self._thread.quit()
        self._thread.wait(2000)
        super().closeEvent(event)

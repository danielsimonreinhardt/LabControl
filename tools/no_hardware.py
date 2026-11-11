"""Gemeinsame Sperre fuer Pruefungen: keine echten Geraete suchen oder verbinden.

Der Simulationsmodus fuegt nur simulierte Geraete HINZU -- echte Netzteile, Lasten, microHIL,
Funktionsgeneratoren und das PicoScope sucht der DeviceWorker trotzdem (gewollt: am PC laesst sich
beides mischen). Eine Pruefung mit MainWindow oder gestartetem DeviceWorker hat deshalb an einem
Rechner mit angesteckten Geraeten die echten Geraete verbunden: am Pi (11.11.2026) oeffnete
check_touch_ui die laufende Last mit (Linux erlaubt zwei Programme am selben seriellen Port ->
vermischte Antworten, Last getrennt), und beim Schliessen ginge ALLE AUS bzw. die Netzteil-Freigabe
an die echten Geraete. tools/check_hardware_isolation.py verlangt diese Sperre in jeder Pruefung,
die ein MainWindow oder einen DeviceWorker baut.

Aufruf VOR dem Bau von MainWindow/DeviceWorker:  import no_hardware; no_hardware.block()
"""
from __future__ import annotations


def block() -> None:
    import device_worker as dw

    dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
    dw.HCS34xx.discover_ports = staticmethod(lambda: [])
    dw.MicroHIL.discover = staticmethod(lambda: None)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [])
    dw.picoscope_usb_present = lambda: False   # echtes Oszilloskop nicht anfassen

"""Prueft die plattformabhaengigen Stellen fuer Linux (Raspberry Pi), ausgefuehrt unter Windows:
sys.platform wird auf "linux" gestellt und ctypes.WinDLL entfernt, dann werden die Module neu geladen.

- picoscope2000.driver laesst sich ohne WinDLL importieren (frueher Startabbruch der ganzen App),
  usb_present() liest sysfs (hier ein nachgebauter Ordnerbaum).
- can_bus.driver: INTERFACE_LIST ohne Vector, mit SocketCAN; SocketCAN bekommt keine Bitrate.

Echte Linux-Hardware prueft das nicht. Aufruf:  python tools/check_platform_linux.py
"""
from __future__ import annotations

import ctypes
import importlib
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device-driver"))

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def reload_as(platform: str, module: str):
    saved_platform = sys.platform
    saved_windll = getattr(ctypes, "WinDLL", None)
    sys.platform = platform
    if platform != "win32" and saved_windll is not None:
        del ctypes.WinDLL
    try:
        sys.modules.pop(module, None)
        return importlib.import_module(module), None
    except Exception as exc:   # noqa: BLE001 -- genau das soll die Pruefung zeigen
        return None, exc
    finally:
        sys.platform = saved_platform
        if saved_windll is not None:
            ctypes.WinDLL = saved_windll


def main() -> int:
    # Fremdbibliotheken unter der echten Plattform laden (pyserial waehlt sonst seine
    # POSIX-Variante und ruft os.uname auf); neu geladen werden nur die eigenen Module.
    import can  # noqa: F401
    import serial.tools.list_ports  # noqa: F401
    import picoscope2000.common  # noqa: F401
    try:
        import picosdk.ps2000  # noqa: F401
    except OSError:
        pass

    print("PicoScope-Treiber unter Linux")
    pico, exc = reload_as("linux", "picoscope2000.driver")
    check("Import ohne ctypes.WinDLL", pico is not None, repr(exc))
    if pico is not None:
        check("keine SetupAPI geladen", not hasattr(pico, "_setupapi"))
        root = pathlib.Path(tempfile.mkdtemp())
        (root / "usb1").mkdir()
        (root / "usb1" / "idVendor").write_text("1d6b\n")
        (root / "usb1" / "idProduct").write_text("0002\n")
        (root / "1-1_1.0").mkdir()   # wie Schnittstellen-Eintrag "1-1:1.0" (":" geht unter Windows nicht): ohne idVendor
        check("sysfs: PicoScope fehlt", pico._usb_present_sysfs(0x0CE9, 0x1007, root) is False)
        (root / "1-1").mkdir()
        (root / "1-1" / "idVendor").write_text("0ce9\n")
        (root / "1-1" / "idProduct").write_text("1007\n")
        check("sysfs: PicoScope 0ce9:1007 erkannt", pico._usb_present_sysfs(0x0CE9, 0x1007, root) is True)
        check("sysfs: fehlender Ordner -> False", pico._usb_present_sysfs(0x0CE9, 0x1007, root / "gibt-es-nicht") is False)
        # Suchorte leeren: auf einem Windows-Rechner mit PicoScope 7 wuerde launch_app() die App sonst wirklich starten.
        pico._INSTALL_DIR_CANDIDATES = []
        check("launch_app ohne PicoScope.exe -> False", pico.launch_app() is False)

    print("CAN-Treiber unter Linux")
    canmod, exc = reload_as("linux", "can_bus.driver")
    check("Import", canmod is not None, repr(exc))
    if canmod is not None:
        check("INTERFACE_LIST = socketcan, pcan, slcan", canmod.INTERFACE_LIST == ["socketcan", "pcan", "slcan"],
              str(canmod.INTERFACE_LIST))
        seen: dict = {}

        class FakeBus:
            def __init__(self, **kwargs):
                seen.update(kwargs)

        real_bus = canmod.can.interface.Bus
        canmod.can.interface.Bus = FakeBus
        try:
            bus = canmod.CanBus("socketcan", "can0", bitrate=500_000)
        finally:
            canmod.can.interface.Bus = real_bus
        check("SocketCAN ohne Bitrate an python-can", "bitrate" not in seen and seen.get("channel") == "can0", str(seen))
        check("Bitrate bleibt als Konfigurationswert", bus.bitrate == 500_000)

    print("Gegenprobe Windows")
    canwin, exc = reload_as("win32", "can_bus.driver")
    check("Windows: INTERFACE_LIST unveraendert", canwin is not None and canwin.INTERFACE_LIST == ["vector", "pcan", "slcan"],
          repr(exc) if exc else str(canwin.INTERFACE_LIST))
    if sys.platform == "win32":
        picowin, exc = reload_as("win32", "picoscope2000.driver")
        check("Windows: SetupAPI geladen", picowin is not None and hasattr(picowin, "_setupapi"), repr(exc))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok. (Linux nur nachgestellt, nicht auf echtem Linux/Pi geprueft)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

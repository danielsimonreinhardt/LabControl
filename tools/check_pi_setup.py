"""Prueft die Pi-Einrichtung (tools/pi, 10.11.2026) ohne Pi:

- LABCONTROL_WINDOW: maximized / fullscreen / leer -> Fensterzustand (main.show_window)
- feste Wayland-App-ID "labcontrol"
- systemd-Dienst: Startbefehl passt zu setup_pi.sh (venv, Repo), Neustart nur nach Absturz,
  genug Zeit fuer die Netzteil-Freigabe, SIGTERM zuerst an den Hauptprozess
- Autostart-Eintrag startet genau diesen Dienst
- Shell-Skripte: Syntax (bash -n), wenn bash vorhanden

Offscreen, keine Hardware.  Aufruf:  python tools/check_pi_setup.py
"""
from __future__ import annotations

import configparser
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PI = ROOT / "tools" / "pi"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def check_window_mode() -> None:
    print("Fensterzustand beim Start")
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    import main as main_mod

    for mode, attr in (("maximized", "isMaximized"), ("fullscreen", "isFullScreen")):
        w = QWidget()
        used = main_mod.show_window(w, f"  {mode.upper()} ")
        app.processEvents()
        check(f"{mode}: Zustand gesetzt", used == mode and getattr(w, attr)(), f"{used}, {w.windowState()}")
        w.close()
    w = QWidget()
    used = main_mod.show_window(w, "kiosk")
    check("unbekannter Wert: normales Fenster", used == "normal" and w.isVisible() and not w.isMaximized())
    w.close()
    os.environ[main_mod.WINDOW_ENV] = "maximized"
    w = QWidget()
    check("ohne Argument gilt die Umgebungsvariable", main_mod.show_window(w) == "maximized")
    w.close()
    del os.environ[main_mod.WINDOW_ENV]
    w = QWidget()
    check("ohne Variable: normal wie bisher", main_mod.show_window(w) == "normal")
    w.close()
    check("Wayland-App-ID", main_mod.DESKTOP_FILE_NAME == "labcontrol")


def unit_values(path: pathlib.Path) -> dict[str, list[str]]:
    """Schluessel -> alle Werte (systemd erlaubt Wiederholungen, z. B. Environment)."""
    values: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), []).append(value.strip())
    return values


def check_service() -> None:
    print("systemd-Dienst und Autostart")
    unit = unit_values(PI / "labcontrol.service")
    setup = (PI / "setup_pi.sh").read_text(encoding="utf-8")
    check("Startbefehl: venv-Python und main.py aus dem Repo",
          unit.get("ExecStart") == ["%h/labcontrol-venv/bin/python %h/LabControl/lab_gui/main.py"], str(unit.get("ExecStart")))
    check("setup_pi.sh legt venv und Repo an denselben Orten an",
          'VENV="$HOME/labcontrol-venv"' in setup and 'REPO="$HOME/LabControl"' in setup)
    check("Neustart nur nach Absturz (Fenster schliessen beendet)", unit.get("Restart") == ["on-failure"])
    stop = int(unit.get("TimeoutStopSec", ["0"])[0])
    check("Zeit fuer Netzteil-Freigabe beim Beenden (>= 15 s)", stop >= 15, str(stop))
    check("SIGTERM zuerst nur an LabControl (KillMode=mixed)", unit.get("KillMode") == ["mixed"])
    env = " ".join(unit.get("Environment", []))
    check("Wayland und Fensterzustand gesetzt",
          "QT_QPA_PLATFORM=wayland" in env and "LABCONTROL_WINDOW=maximized" in env, env)
    check("Neustart-Bremse", unit.get("StartLimitBurst") == ["5"])

    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(PI / "labcontrol-autostart.desktop", encoding="utf-8")
    exec_line = desktop.get("Desktop Entry", "Exec", fallback="")
    check("Autostart startet labcontrol.service mit Wayland-Umgebung",
          "systemctl --user start labcontrol.service" in exec_line and "WAYLAND_DISPLAY" in exec_line, exec_line)
    check("setup_pi.sh installiert Dienst und Autostart",
          "labcontrol.service" in setup and "autostart/labcontrol.desktop" in setup)


def check_scripts() -> None:
    print("Shell-Skripte")
    bash = shutil.which("bash")
    for script in sorted(PI.glob("*.sh")):
        raw = script.read_bytes()
        check(f"{script.name}: beginnt mit #!/bin/bash, ohne BOM", raw.startswith(b"#!/bin/bash"))
        if bash and sys.platform != "win32":
            result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
            check(f"{script.name}: Syntax", result.returncode == 0, result.stderr.strip())


def main() -> int:
    check_window_mode()
    check_service()
    check_scripts()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

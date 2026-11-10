#!/bin/bash
# Richtet LabControl auf einem Raspberry Pi ein (Raspberry Pi OS 64 Bit mit Desktop).
# Wiederholbar: bereits Erledigtes wird uebersprungen bzw. ueberschrieben.
#
# Voraussetzung: Repo in ~/LabControl (siehe tools/pi/README.md), Benutzer mit sudo.
# Aufruf am Pi:  bash ~/LabControl/tools/pi/setup_pi.sh
set -euo pipefail

REPO="$HOME/LabControl"
VENV="$HOME/labcontrol-venv"
HERE="$REPO/tools/pi"

echo "== Python-Umgebung ($VENV)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
export PIP_NO_CACHE_DIR=1           # SD-Karte: keinen pip-Cache anlegen
"$VENV/bin/pip" install -q --upgrade pip
# Nur die Qt-Basismodule (QtCore/Gui/Widgets/Svg/Test ...): LabControl braucht die Addons nicht,
# spart gut 300 MB. Ohne Compiler am Pi gehen nur fertige arm64-Pakete (Wheels).
"$VENV/bin/pip" install -q PySide6-Essentials
grep -vE '^(PySide6|pyinstaller)' "$REPO/requirements.txt" > /tmp/labcontrol-req-pi.txt
"$VENV/bin/pip" install -q -r /tmp/labcontrol-req-pi.txt

echo "== Dienst und Autostart"
install -Dm644 "$HERE/labcontrol.service" "$HOME/.config/systemd/user/labcontrol.service"
install -Dm644 "$HERE/labcontrol-autostart.desktop" "$HOME/.config/autostart/labcontrol.desktop"
systemctl --user daemon-reload

echo "== SD-Karte schonen"
# Systemprotokoll: Raspberry Pi OS haelt es schon nur im Arbeitsspeicher
# (/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf) -- nur pruefen.
# Das LabControl-Log (labdash.log) ist davon unabhaengig und bleibt erhalten.
grep -qs '^Storage=volatile' /usr/lib/systemd/journald.conf.d/*.conf /etc/systemd/journald.conf.d/*.conf \
  || echo "WARNUNG: Systemprotokoll wird auf die SD-Karte geschrieben (journald Storage pruefen)"
# Auslagern nur komprimiert im Arbeitsspeicher, keine Datei auf der Karte (wirkt nach Neustart)
sudo install -Dm644 /dev/stdin /etc/rpi/swap.conf.d/50-labcontrol.conf <<'EOF'
# LabControl-Pi: keine Auslagerungsdatei auf der SD-Karte (tools/pi/setup_pi.sh)
[Main]
Mechanism=zram
EOF
# noatime fuer / setzt Raspberry Pi OS bereits, /tmp ist tmpfs -- nur pruefen
findmnt -no OPTIONS / | grep -q noatime || echo "WARNUNG: / ohne noatime eingehaengt (/etc/fstab pruefen)"

echo "== Rechte fuer Geraete"
for g in dialout plugdev; do
  id -nG | grep -qw "$g" || { sudo usermod -aG "$g" "$USER"; echo "zur Gruppe $g hinzugefuegt (wirkt nach Neuanmeldung)"; }
done

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/labcontrol"
echo
echo "Fertig. Datenordner: $DATA"
echo "Start: Neuanmeldung/Neustart oder  systemctl --user start labcontrol"
echo "Zustand: systemctl --user status labcontrol   Protokoll: journalctl --user -u labcontrol"

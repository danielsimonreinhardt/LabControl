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

echo "== Herunterfahren"
# LabControl haelt eine logind-Sperre und gibt bei PrepareForShutdown die Netzteile frei, bevor der
# Desktop endet (lab_gui/shutdown_inhibit.py). logind wartet darauf hoechstens InhibitDelayMaxSec
# (Standard 5 s) -- mit mehreren seriellen Geraeten knapp. Wirkt nach dem naechsten Neustart.
sudo install -Dm644 /dev/stdin /etc/systemd/logind.conf.d/50-labcontrol.conf <<'EOF'
# LabControl-Pi: Zeit fuer die Netzteil-Freigabe beim Herunterfahren (tools/pi/setup_pi.sh)
[Login]
InhibitDelayMaxSec=15
EOF

echo "== CAN (SocketCAN)"
# USB-CAN-Adapter (PCAN-USB: Treiber peak_usb im Kernel) erscheinen als can0, can1 ... und sind
# zunaechst aus. LabControl setzt die Bitrate unter Linux nicht selbst (braucht root); diese Regel
# faehrt jeden Adapter beim Einstecken mit 500 kbit/s hoch (Batterie-Simulator, 10.11.2026).
sudo install -Dm644 /dev/stdin /etc/udev/rules.d/80-labcontrol-can.rules <<'EOF'
# LabControl-Pi: USB-CAN-Adapter (PCAN-USB, peak_usb) mit 500 kbit/s hochfahren (Batterie-Simulator)
ACTION=="add", SUBSYSTEM=="net", KERNEL=="can*", RUN+="/sbin/ip link set %k type can bitrate 500000 restart-ms 100", RUN+="/sbin/ip link set %k up"
EOF
sudo udevadm control --reload

echo "== Rechte fuer Geraete"
for g in dialout plugdev; do
  id -nG | grep -qw "$g" || { sudo usermod -aG "$g" "$USER"; echo "zur Gruppe $g hinzugefuegt (wirkt nach Neuanmeldung)"; }
done

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/labcontrol"
echo
echo "Fertig. Datenordner: $DATA"
echo "Start: Neuanmeldung/Neustart oder  systemctl --user start labcontrol"
echo "Zustand: systemctl --user status labcontrol   Protokoll: journalctl --user -u labcontrol"

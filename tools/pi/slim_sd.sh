#!/bin/bash
# Macht auf einer kleinen SD-Karte (8 GB) Platz -- Entscheidung PI-14 Stufe 2 (10.11.2026).
# Am ersten Pi: von 180 MB auf 3,1 GB frei. Nur bei Bedarf ausfuehren, auf 32-GB-Karten unnoetig.
#
# Danach gibt es am Pi KEINEN Browser und KEINEN Compiler mehr; Python-Pakete gehen nur noch
# als fertige arm64-Wheels. Sprachdateien ausser de/en, man-Seiten und Dokumentation werden
# auch fuer kuenftige Pakete weggelassen (dpkg path-exclude).
# Aufruf am Pi:  bash ~/LabControl/tools/pi/slim_sd.sh   (danach neu starten)
set -euo pipefail
echo "vorher: $(df -h / | tail -1)"

PKGS="firefox chromium chromium-l10n rpi-chromium-mods
  firmware-atheros firmware-mediatek mesa-vulkan-drivers
  vlc vlc-l10n vlc-plugin-base mkvtoolnix pocketsphinx-en-us rpi-imager
  rpi-userguide rpi-userguide-de thonny python3-mypy mypy rpd-wallpaper-trixie
  linux-image-rpi-v8 gcc-14-aarch64-linux-gnu"
# alle Kernel fuer Pi 3/4 (v8) ausser dem laufenden; alte 2712-Kernel nimmt --autoremove mit
running=$(uname -r)
for p in $(dpkg-query -Wf '${Package}\n' 'linux-image-[0-9]*' 'linux-headers-[0-9]*' 2>/dev/null); do
  case "$p" in *"$running"*) ;; *-rpi-v8) PKGS="$PKGS $p" ;; esac
done
installed=""
for p in $PKGS; do dpkg-query -W "$p" >/dev/null 2>&1 && installed="$installed $p"; done

# Desktop-Portale und zenity behalten (Dateidialoge, Rueckfragen von Pi-Werkzeugen)
sudo apt-mark manual xdg-desktop-portal xdg-desktop-portal-wlr xdg-desktop-portal-gtk zenity zenity-common >/dev/null 2>&1 || true
[ -n "$installed" ] && sudo DEBIAN_FRONTEND=noninteractive apt-get purge -y -q --autoremove $installed

sudo install -Dm644 /dev/stdin /etc/dpkg/dpkg.cfg.d/50-labcontrol-schlank <<'EOF'
# LabControl-Pi (PI-14 Stufe 2, 10.11.2026): SD-Karte 8 GB
path-exclude=/usr/share/locale/*
path-include=/usr/share/locale/de*
path-include=/usr/share/locale/en*
path-include=/usr/share/locale/locale.alias
path-exclude=/usr/share/man/*
path-exclude=/usr/share/doc/*
path-include=/usr/share/doc/*/copyright
EOF
sudo find /usr/share/locale -mindepth 1 -maxdepth 1 ! -name 'de*' ! -name 'en*' ! -name locale.alias -exec rm -rf {} +
sudo find /usr/share/man -mindepth 1 -maxdepth 1 -exec rm -rf {} +
sudo find /usr/share/doc -type f ! -name copyright -delete
sudo find /usr/share/doc -type d -empty -delete
sudo apt-get clean
echo "nachher: $(df -h / | tail -1)"
echo "Bitte neu starten (Kernel/Auslagerung)."

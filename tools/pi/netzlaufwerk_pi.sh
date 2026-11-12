#!/usr/bin/env bash
# Netzlaufwerk (SMB/CIFS) am Pi einhaengen und in LabControl eintragen.
#
#   bash ~/LabControl/tools/pi/netzlaufwerk_pi.sh [//server/freigabe] [unterordner] [benutzer]
#   Standard: //192.168.178.1/data_exchange  LABCONTROL  labcontrol
#
# Fragt das Passwort verdeckt ab (oder liest es aus NETZ_PASSWORT). Es landet nur in
# /etc/labcontrol/smb-credentials (root, 0600), nie im Repo oder in settings.json.
#
# Eingehaengt wird per systemd-Automount unter /mnt/<freigabe>: erst beim ersten Zugriff,
# mit nofail und 15 s Frist -- ist der Server weg, startet der Pi trotzdem normal, und
# LabControl schreibt lokal weiter (data_store.py schiebt spaeter nach).
# Danach traegt das Skript <mount>/<unterordner> als Netzlaufwerk in LabControl ein
# (Dienst kurz anhalten, settings.json aendern, Dienst starten).
#
# Rueckgaengig: Zeile mit dem Mountpunkt aus /etc/fstab entfernen (Sicherung:
# /etc/fstab.vor-netzlaufwerk), sudo systemctl daemon-reload, network_dir in LabControl leeren.
set -euo pipefail

SHARE="${1:-//192.168.178.1/data_exchange}"
SUBDIR="${2:-LABCONTROL}"
SMB_USER="${3:-labcontrol}"
MOUNTPOINT="/mnt/$(basename "$SHARE")"
CRED=/etc/labcontrol/smb-credentials
SETTINGS="${XDG_DATA_HOME:-$HOME/.local/share}/labcontrol/settings.json"

if ! [ -x /sbin/mount.cifs ] && ! [ -x /usr/sbin/mount.cifs ]; then
    echo "cifs-utils fehlt -- installiere ..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends cifs-utils
fi

PASSWORD="${NETZ_PASSWORT:-}"
if [ -z "$PASSWORD" ]; then
    read -r -s -p "Passwort fuer $SMB_USER an $SHARE: " PASSWORD
    echo
fi
[ -n "$PASSWORD" ] || { echo "Kein Passwort -- Abbruch."; exit 1; }

sudo install -d -m 755 /etc/labcontrol
printf 'username=%s\npassword=%s\n' "$SMB_USER" "$PASSWORD" | sudo install -m 600 -o root -g root /dev/stdin "$CRED"
unset PASSWORD
echo "Zugangsdaten: $CRED (nur root lesbar)"

sudo mkdir -p "$MOUNTPOINT"
[ -e /etc/fstab.vor-netzlaufwerk ] || sudo cp /etc/fstab /etc/fstab.vor-netzlaufwerk
OPTS="credentials=$CRED,uid=$(id -u),gid=$(id -g),file_mode=0664,dir_mode=0775,iocharset=utf8,_netdev,nofail,x-systemd.automount,x-systemd.mount-timeout=15"
if grep -qs " $MOUNTPOINT " /etc/fstab; then
    echo "fstab hat schon einen Eintrag fuer $MOUNTPOINT -- unveraendert gelassen"
else
    echo "$SHARE $MOUNTPOINT cifs $OPTS 0 0" | sudo tee -a /etc/fstab >/dev/null
    echo "fstab: $SHARE -> $MOUNTPOINT (Automount)"
fi
UNIT="$(systemd-escape -p --suffix=automount "$MOUNTPOINT")"
sudo systemctl daemon-reload
sudo systemctl restart "$UNIT"

TARGET="$MOUNTPOINT/$SUBDIR"
if ! ls "$TARGET" >/dev/null 2>&1; then
    echo "FEHLER: $TARGET nicht erreichbar. Kernel-Meldung:"
    sudo dmesg | grep -i cifs | tail -5 || true
    exit 2
fi
probe="$TARGET/.schreibtest-$(hostname)"
touch "$probe" && rm -f "$probe" && echo "Schreiben in $TARGET: ok"

if [ -f "$SETTINGS" ]; then
    systemctl --user stop labcontrol.service 2>/dev/null || true
    python3 - "$SETTINGS" "$TARGET" <<'EOF'
import json, sys
path, target = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
data["network_dir"] = target
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
print("LabControl: Netzlaufwerk =", target)
EOF
    systemctl --user start labcontrol.service 2>/dev/null || true
else
    echo "Hinweis: $SETTINGS fehlt -- Netzlaufwerk in LabControl unter Einstellungen eintragen: $TARGET"
fi
echo "Fertig."

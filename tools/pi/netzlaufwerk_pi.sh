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
    if sudo test -f "$CRED"; then
        read -r -s -p "Passwort fuer $SMB_USER an $SHARE (Enter = gespeichertes behalten): " PASSWORD
    else
        read -r -s -p "Passwort fuer $SMB_USER an $SHARE: " PASSWORD
    fi
    echo
fi
sudo install -d -m 755 /etc/labcontrol
if [ -n "$PASSWORD" ]; then
    printf 'username=%s\npassword=%s\n' "$SMB_USER" "$PASSWORD" | sudo install -m 600 -o root -g root /dev/stdin "$CRED"
    echo "Zugangsdaten: $CRED (nur root lesbar)"
elif sudo test -f "$CRED"; then
    echo "Zugangsdaten: $CRED behalten"
else
    echo "Kein Passwort -- Abbruch."
    exit 1
fi
unset PASSWORD

# LabControl haelt Dateien auf dem Netzlaufwerk offen (Log) -- fuer das Umhaengen anhalten; auch bei
# einem Abbruch des Skripts danach wieder starten
systemctl --user stop labcontrol.service 2>/dev/null || true
trap 'systemctl --user start labcontrol.service 2>/dev/null || true' EXIT
sudo mkdir -p "$MOUNTPOINT"
[ -e /etc/fstab.vor-netzlaufwerk ] || sudo cp /etc/fstab /etc/fstab.vor-netzlaufwerk
# noserverino: die FritzBox liefert instabile Inode-Nummern ("bogus file nlink value 0"), ohne die Option
# lassen sich offene Dateien nicht erneut oeffnen ("Veraltete Dateizugriffsnummer", 12.11.2026)
OPTS="credentials=$CRED,uid=$(id -u),gid=$(id -g),file_mode=0664,dir_mode=0775,iocharset=utf8,noserverino,_netdev,nofail,x-systemd.automount,x-systemd.mount-timeout=15"
LINE="$SHARE $MOUNTPOINT cifs $OPTS 0 0"
if grep -qsF "$LINE" /etc/fstab; then
    echo "fstab: Eintrag fuer $MOUNTPOINT ist aktuell"
elif grep -qs " $MOUNTPOINT " /etc/fstab; then
    tmp="$(mktemp)"
    awk -v mp="$MOUNTPOINT" -v line="$LINE" '$2 == mp { print line; next } { print }' /etc/fstab > "$tmp"
    sudo install -m 644 -o root -g root "$tmp" /etc/fstab
    rm -f "$tmp"
    echo "fstab: Eintrag fuer $MOUNTPOINT aktualisiert"
else
    echo "$LINE" | sudo tee -a /etc/fstab >/dev/null
    echo "fstab: $SHARE -> $MOUNTPOINT (Automount)"
fi
UNIT="$(systemd-escape -p --suffix=automount "$MOUNTPOINT")"
MOUNT_UNIT="$(systemd-escape -p --suffix=mount "$MOUNTPOINT")"
sudo systemctl stop "$MOUNT_UNIT" 2>/dev/null || true
sudo systemctl daemon-reload
sudo systemctl restart "$UNIT"

TARGET="$MOUNTPOINT/$SUBDIR"
if ! ls "$TARGET" >/dev/null 2>&1; then
    echo "FEHLER: $TARGET nicht erreichbar. Kernel-Meldung:"
    sudo dmesg | grep -i cifs | tail -5 || true
    exit 2
fi
probe="$TARGET/.schreibtest-$(hostname)"
echo eins > "$probe" && echo zwei >> "$probe" && grep -q zwei "$probe" && rm -f "$probe" \
    && echo "Schreiben, Anhaengen und erneutes Oeffnen in $TARGET: ok"

if [ -f "$SETTINGS" ]; then
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
else
    echo "Hinweis: $SETTINGS fehlt -- Netzlaufwerk in LabControl unter Einstellungen eintragen: $TARGET"
fi
echo "Fertig."

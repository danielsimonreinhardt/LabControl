#!/bin/bash
# Nachtlauf am Pi einrichten (0.59.0, FT-4). Als Benutzer labcontrol, kein root noetig.
#
#   bash ~/LabControl/tools/pi/nightly_pi.sh /mnt/data_exchange/LABCONTROL/testcases/nacht.json [02:00]
#   bash ~/LabControl/tools/pi/nightly_pi.sh --off      # abschalten
#   bash ~/LabControl/tools/pi/nightly_pi.sh --now      # einmal sofort ausfuehren (Test)
#
# Ergebnis: journalctl --user -u labcontrol-nightly (Exit-Code), Dateien im Berichtsordner von LabControl.
set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"
CONF_DIR="$HOME/.config/labcontrol"
SRC="$(cd "$(dirname "$0")" && pwd)"

case "${1:-}" in
  --off)
    systemctl --user disable --now labcontrol-nightly.timer 2>/dev/null || true
    echo "Nachtlauf abgeschaltet."
    exit 0 ;;
  --now)
    systemctl --user start labcontrol-nightly.service
    systemctl --user status labcontrol-nightly.service --no-pager | tail -n 5 || true
    exit 0 ;;
  ""|-h|--help)
    sed -n '2,9p' "$0"; exit 1 ;;
esac

TESTCASE="$1"
WHEN="${2:-02:00}"
if [[ ! -f "$TESTCASE" ]]; then
  echo "Ablauf nicht gefunden: $TESTCASE" >&2; exit 1
fi
if [[ ! "$WHEN" =~ ^[0-2][0-9]:[0-5][0-9]$ ]]; then
  echo "Uhrzeit als HH:MM angeben, z. B. 02:00" >&2; exit 1
fi

mkdir -p "$UNIT_DIR" "$CONF_DIR"
printf 'TESTCASE=%q\n' "$TESTCASE" > "$CONF_DIR/nightly.env"
cp "$SRC/labcontrol-nightly.service" "$UNIT_DIR/"
sed "s/@ONCALENDAR@/*-*-* $WHEN:00/" "$SRC/labcontrol-nightly.timer" > "$UNIT_DIR/labcontrol-nightly.timer"
systemctl --user daemon-reload
systemctl --user enable --now labcontrol-nightly.timer
echo "Nachtlauf eingerichtet: $TESTCASE täglich um $WHEN"
systemctl --user list-timers labcontrol-nightly.timer --no-pager || true

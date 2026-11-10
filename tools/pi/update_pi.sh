#!/bin/bash
# Holt den neuen Stand ins Arbeitsverzeichnis am Pi und startet den Dienst neu.
# Wird von tools/pi/deploy_pi.ps1 per SSH aufgerufen, geht aber auch direkt am Pi.
set -euo pipefail
REPO="$HOME/LabControl"
VENV="$HOME/labcontrol-venv"

if [ ! -d "$REPO/.git" ]; then   # erstes Mal: Arbeitskopie aus dem Bare-Repo anlegen
  git clone -q "$HOME/labcontrol.git" "$REPO"
  echo "Arbeitskopie angelegt: $REPO -- jetzt einmal tools/pi/setup_pi.sh ausfuehren"
  exit 0
fi
before=$(git -C "$REPO" rev-parse HEAD)
git -C "$REPO" pull -q --ff-only
after=$(git -C "$REPO" rev-parse HEAD)
echo "Stand: $(git -C "$REPO" log --oneline -1 | cut -c1-100)"

if [ "$before" != "$after" ] && git -C "$REPO" diff --name-only "$before" "$after" | grep -qx requirements.txt; then
  echo "requirements.txt geaendert -> Pakete aktualisieren"
  grep -vE '^(PySide6|pyinstaller)' "$REPO/requirements.txt" > /tmp/labcontrol-req-pi.txt
  PIP_NO_CACHE_DIR=1 "$VENV/bin/pip" install -q -r /tmp/labcontrol-req-pi.txt
fi
if git -C "$REPO" diff --name-only "$before" "$after" | grep -q '^tools/pi/labcontrol'; then
  echo "Dienstdateien geaendert -> setup_pi.sh erneut ausfuehren"
fi

if systemctl --user is-active -q labcontrol; then
  systemctl --user restart labcontrol
  echo "Dienst neu gestartet"
else
  echo "Dienst laeuft nicht (Start: systemctl --user start labcontrol)"
fi

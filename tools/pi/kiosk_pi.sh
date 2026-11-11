#!/bin/bash
# LabControl-Kiosk am Raspberry Pi: beim Booten nur das LabControl-Logo, kein Desktop und keine Leiste,
# danach LabControl im Vollbild (Dienst labcontrol.service, Start ueber ~/.config/autostart wie bisher).
#
#   tools/pi/kiosk_pi.sh          einrichten (idempotent)
#   tools/pi/kiosk_pi.sh --undo   alles zuruecknehmen (Himbeere, Desktop, Leiste wie ausgeliefert)
#
# Was es aendert (Sicherung der Originale beim ersten Lauf in ~/.local/share/labcontrol/kiosk-backup):
#   - Plymouth-Theme "labcontrol" (tools/pi/branding) statt "pix", initramfs wird neu gebaut
#   - /boot/firmware/cmdline.txt: logo.nologo vt.global_cursor_default=0 loglevel=3 (keine Himbeeren,
#     kein Cursor, keine Kernel-Meldungen; "quiet splash" ist schon gesetzt)
#   - /boot/firmware/config.txt: disable_splash=1 (kein Regenbogen der Firmware)
#   - ~/.config/labwc/autostart: nur Hintergrund (Logo per swaybg), kanshi und die XDG-Autostarts
#     (Bildschirmtastatur, Ein/Aus-Taste, LabControl) -- ohne Desktop (pcmanfm-pi) und Leiste (wf-panel-pi)
# Wartung danach per SSH; den Desktop gibt es mit --undo und einem Neustart zurueck.
set -euo pipefail

REPO="$HOME/LabControl"
BRAND="$REPO/tools/pi/branding"
THEME_DIR=/usr/share/plymouth/themes/labcontrol
CMDLINE=/boot/firmware/cmdline.txt
CONFIG=/boot/firmware/config.txt
AUTOSTART="$HOME/.config/labwc/autostart"
BACKUP="$HOME/.local/share/labcontrol/kiosk-backup"
CMDLINE_ADD="logo.nologo vt.global_cursor_default=0 loglevel=3"

if [ "${1:-}" = "--undo" ]; then
  echo "Kiosk zuruecknehmen"
  [ -f "$BACKUP/cmdline.txt" ] && sudo cp "$BACKUP/cmdline.txt" "$CMDLINE" && echo "  cmdline.txt wiederhergestellt"
  [ -f "$BACKUP/config.txt" ] && sudo cp "$BACKUP/config.txt" "$CONFIG" && echo "  config.txt wiederhergestellt"
  rm -f "$AUTOSTART" && echo "  labwc-Autostart entfernt (Desktop und Leiste wieder an)"
  sudo plymouth-set-default-theme -R pix && echo "  Boot-Theme wieder pix"
  echo "Fertig -- wirksam nach einem Neustart."
  exit 0
fi

for f in "$BRAND/splash.png" "$BRAND/labcontrol.script" "$BRAND/labcontrol.plymouth"; do
  [ -f "$f" ] || { echo "fehlt: $f (erst tools/pi/branding/make_branding.py am PC, dann deploy)"; exit 1; }
done

# Originale einmal sichern (spaetere Laeufe ueberschreiben die Sicherung nicht)
mkdir -p "$BACKUP"
[ -f "$BACKUP/cmdline.txt" ] || cp "$CMDLINE" "$BACKUP/cmdline.txt"
[ -f "$BACKUP/config.txt" ] || cp "$CONFIG" "$BACKUP/config.txt"

echo "1/4 Boot-Logo (Plymouth-Theme labcontrol)"
sudo install -d "$THEME_DIR"
sudo install -m 644 "$BRAND/splash.png" "$BRAND/labcontrol.script" "$BRAND/labcontrol.plymouth" "$THEME_DIR/"
if [ "$(plymouth-set-default-theme)" != "labcontrol" ] || [ "${KIOSK_REBUILD:-0}" = "1" ]; then
  echo "    initramfs wird neu gebaut (dauert etwa eine Minute) ..."
  sudo plymouth-set-default-theme -R labcontrol
else
  sudo update-initramfs -u >/dev/null   # Bild/Skript koennen sich geaendert haben
fi

echo "2/4 Kernel-Kommandozeile ohne Himbeeren, Cursor und Meldungen"
line=$(head -n 1 "$CMDLINE")
new="$line"
for token in $CMDLINE_ADD; do
  case " $new " in *" $token "*) ;; *) new="$new $token" ;; esac
done
if [ "$new" != "$line" ]; then
  printf '%s\n' "$new" | sudo tee "$CMDLINE" >/dev/null
  echo "    ergaenzt: $CMDLINE_ADD"
fi

echo "3/4 Firmware ohne Regenbogen"
if ! grep -q '^disable_splash=1' "$CONFIG"; then
  printf '\n# LabControl-Kiosk (tools/pi/kiosk_pi.sh)\n[all]\ndisable_splash=1\n' | sudo tee -a "$CONFIG" >/dev/null
  echo "    disable_splash=1 gesetzt"
fi

echo "4/4 Sitzung ohne Desktop und Leiste, Hintergrund = Logo"
mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<EOF
# LabControl-Kiosk (tools/pi/kiosk_pi.sh): ersetzt /etc/xdg/labwc/autostart -- ohne Desktop (pcmanfm-pi)
# und Leiste (wf-panel-pi). Der Hintergrund zeigt das Boot-Logo, bis LabControl im Vollbild steht.
# Rueckweg: tools/pi/kiosk_pi.sh --undo
/usr/bin/swaybg -m fill -c '#0f172a' -i '$BRAND/splash.png' &
/usr/bin/kanshi &
/usr/bin/lxsession-xdg-autostart
EOF
echo "Fertig -- wirksam nach einem Neustart (sudo reboot)."

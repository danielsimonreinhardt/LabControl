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
#   - eigene Sitzung "labcontrol-kiosk" (/usr/share/wayland-sessions, /usr/local/bin) fuer die automatische
#     Anmeldung (/etc/lightdm/lightdm.conf): wie labwc-pi, aber labwc OHNE -m. Mit -m fuehrt labwc die
#     Autostart-Dateien aus allen Konfig-Ordnern zusammen, und /etc/xdg/labwc/autostart startet Desktop
#     (pcmanfm-pi) und Leiste (wf-panel-pi) immer mit (am Pi gesehen). Ohne -m gilt nur ~/.config/labwc:
#     rc.xml (Touch-Zuordnung), environment (Tastatur) und
#   - ~/.config/labwc/autostart: nur Hintergrund (Logo per swaybg), kanshi und die XDG-Autostarts
#     (Bildschirmtastatur, Ein/Aus-Taste, LabControl)
# Wartung danach per SSH; den Desktop gibt es mit --undo und einem Neustart zurueck.
set -euo pipefail

REPO="$HOME/LabControl"
BRAND="$REPO/tools/pi/branding"
THEME_DIR=/usr/share/plymouth/themes/labcontrol
CMDLINE=/boot/firmware/cmdline.txt
CONFIG=/boot/firmware/config.txt
LIGHTDM=/etc/lightdm/lightdm.conf
SESSION_FILE=/usr/share/wayland-sessions/labcontrol-kiosk.desktop
SESSION_BIN=/usr/local/bin/labcontrol-kiosk-session
AUTOSTART="$HOME/.config/labwc/autostart"
BACKUP="$HOME/.local/share/labcontrol/kiosk-backup"
CMDLINE_ADD="logo.nologo vt.global_cursor_default=0 loglevel=3"

if [ "${1:-}" = "--undo" ]; then
  echo "Kiosk zuruecknehmen"
  [ -f "$BACKUP/cmdline.txt" ] && sudo cp "$BACKUP/cmdline.txt" "$CMDLINE" && echo "  cmdline.txt wiederhergestellt"
  [ -f "$BACKUP/config.txt" ] && sudo cp "$BACKUP/config.txt" "$CONFIG" && echo "  config.txt wiederhergestellt"
  [ -f "$BACKUP/lightdm.conf" ] && sudo cp "$BACKUP/lightdm.conf" "$LIGHTDM" && echo "  lightdm.conf wiederhergestellt"
  sudo rm -f "$SESSION_FILE" "$SESSION_BIN" && echo "  Kiosk-Sitzung entfernt"
  rm -f "$AUTOSTART" && echo "  labwc-Autostart entfernt (Desktop und Leiste wieder an)"
  sudo /usr/sbin/plymouth-set-default-theme -R pix && echo "  Boot-Theme wieder pix"
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
[ -f "$BACKUP/lightdm.conf" ] || cp "$LIGHTDM" "$BACKUP/lightdm.conf"

echo "1/5 Boot-Logo (Plymouth-Theme labcontrol)"
sudo install -d "$THEME_DIR"
sudo install -m 644 "$BRAND/splash.png" "$BRAND/labcontrol.script" "$BRAND/labcontrol.plymouth" "$THEME_DIR/"
if [ "$(/usr/sbin/plymouth-set-default-theme)" != "labcontrol" ] || [ "${KIOSK_REBUILD:-0}" = "1" ]; then
  echo "    initramfs wird neu gebaut (dauert etwa eine Minute) ..."
  sudo plymouth-set-default-theme -R labcontrol
else
  sudo update-initramfs -u >/dev/null   # Bild/Skript koennen sich geaendert haben
fi

echo "2/5 Kernel-Kommandozeile ohne Himbeeren, Cursor und Meldungen"
line=$(head -n 1 "$CMDLINE")
new="$line"
for token in $CMDLINE_ADD; do
  case " $new " in *" $token "*) ;; *) new="$new $token" ;; esac
done
if [ "$new" != "$line" ]; then
  printf '%s\n' "$new" | sudo tee "$CMDLINE" >/dev/null
  echo "    ergaenzt: $CMDLINE_ADD"
fi

echo "3/5 Firmware ohne Regenbogen"
if ! grep -q '^disable_splash=1' "$CONFIG"; then
  printf '\n# LabControl-Kiosk (tools/pi/kiosk_pi.sh)\n[all]\ndisable_splash=1\n' | sudo tee -a "$CONFIG" >/dev/null
  echo "    disable_splash=1 gesetzt"
fi

echo "4/5 Sitzung ohne Desktop und Leiste, Hintergrund = Logo"
mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<EOF
# LabControl-Kiosk (tools/pi/kiosk_pi.sh): ersetzt /etc/xdg/labwc/autostart -- ohne Desktop (pcmanfm-pi)
# und Leiste (wf-panel-pi). Der Hintergrund zeigt das Boot-Logo, bis LabControl im Vollbild steht.
# Rueckweg: tools/pi/kiosk_pi.sh --undo
/usr/bin/swaybg -m fill -c '#0f172a' -i '$BRAND/splash.png' &
/usr/bin/kanshi &
/usr/bin/lxsession-xdg-autostart
EOF

echo "5/5 Kiosk-Sitzung (labwc ohne -m) fuer die automatische Anmeldung"
sudo tee "$SESSION_BIN" >/dev/null <<'EOF'
#!/bin/sh
# LabControl-Kiosk-Sitzung (tools/pi/kiosk_pi.sh): wie /usr/bin/labwc-pi, aber labwc OHNE -m -- sonst
# startet /etc/xdg/labwc/autostart Desktop und Leiste mit. Es gilt nur ~/.config/labwc.
. /usr/bin/setup_env
if [ ! -f "$HOME/.config/kanshi/config" ] ; then
  mkdir -p "$HOME/.config/kanshi"
  touch "$HOME/.config/kanshi/config"
fi
if raspi-config nonint is_pi ; then
  export WLR_DRM_FORCE_LIBLIFTOFF=1
  if ! raspi-config nonint gpu_has_mmu ; then
    export WLR_RENDERER=pixman
  fi
fi
exec /usr/bin/labwc "$@"
EOF
sudo chmod 755 "$SESSION_BIN"
sudo tee "$SESSION_FILE" >/dev/null <<EOF
[Desktop Entry]
Type=Application
Name=LabControl Kiosk
Comment=Nur LabControl im Vollbild, ohne Desktop und Leiste (tools/pi/kiosk_pi.sh)
Exec=$SESSION_BIN
EOF
sudo sed -i 's/^autologin-session=.*/autologin-session=labcontrol-kiosk/' "$LIGHTDM"
grep -q '^autologin-session=labcontrol-kiosk' "$LIGHTDM" || { echo "autologin-session in $LIGHTDM nicht gefunden"; exit 1; }
echo "    automatische Anmeldung startet labcontrol-kiosk"

echo "Fertig -- wirksam nach einem Neustart (sudo reboot)."

#!/bin/bash
# LabControl-Kiosk am Raspberry Pi: beim Einschalten nur das LabControl-Logo (ohne Bootloader-Bild, ohne
# schwarze Luecke), kein Desktop und keine Leiste, danach LabControl im Vollbild.
#
#   tools/pi/kiosk_pi.sh          einrichten (idempotent)
#   tools/pi/kiosk_pi.sh --undo   alles zuruecknehmen (Himbeere, Anmeldung, Desktop, Leiste wie ausgeliefert)
#
# Was es aendert (Sicherung der Originale beim ersten Lauf in ~/.local/share/labcontrol/kiosk-backup):
#   1 Plymouth-Theme "labcontrol" (tools/pi/branding) statt "pix", initramfs wird neu gebaut
#   2 /boot/firmware/cmdline.txt: logo.nologo vt.global_cursor_default=0 loglevel=3 fbcon=map:9 (Textkonsole
#     zeichnet nicht auf den Bildschirm -- sonst uebermalt sie das Boot-Logo, wenn tty1 in den Textmodus faellt)
#   3 /boot/firmware/config.txt: disable_splash=1 (kein Regenbogen der Firmware)
#   4 ~/.config/labwc/autostart: nur Hintergrund (Logo per swaybg), kanshi und die XDG-Autostarts
#     (Bildschirmtastatur, Ein/Aus-Taste, LabControl) -- ohne Desktop (pcmanfm-pi) und Leiste (wf-panel-pi)
#   5 eigener Systemdienst labcontrol-kiosk.service statt der Anmeldung ueber lightdm: startet labwc OHNE -m
#     (mit -m fuehrt labwc den System-Autostart mit Desktop und Leiste dazu) direkt auf tty1. Das Boot-Logo
#     gibt vorher die Grafik frei, bleibt aber stehen (plymouth deactivate), und endet erst, wenn labwc den
#     Logo-Hintergrund zeigt (plymouth quit --retain-splash) -- mit lightdm wurde es vorher ganz beendet,
#     der Bildschirm war kurz schwarz (Nutzerhinweis 11.11.2026). lightdm und die Text-Anmeldung auf tty1
#     werden dafuer abgeschaltet.
#   6 unsichtbarer Mauszeiger: Zeiger-Theme ~/.icons/labcontrol-invisible, XCURSOR_THEME fuer labwc und den
#     Dienst (Ergaenzung kiosk.conf; Qt-Fenster setzen ihren Zeiger selbst)
#   7 Dialoge ohne Fensterleiste: labwc zeichnet keine (rc.xml windowRule), Qt auch nicht
#     (QT_WAYLAND_DISABLE_WINDOWDECORATION=1 in kiosk.conf)
#   8 nur mit KIOSK_BOOTLOADER=1: Bootloader-Bild aus, DISABLE_HDMI=1 im EEPROM (Pi 5). Schreibt den Bootloader
#     mit der neuesten mitgelieferten Firmware neu (Firmware-Update, Entscheidung PI-16)
# Wartung danach per SSH.
set -euo pipefail

REPO="$HOME/LabControl"
BRAND="$REPO/tools/pi/branding"
THEME_DIR=/usr/share/plymouth/themes/labcontrol
CMDLINE=/boot/firmware/cmdline.txt
CONFIG=/boot/firmware/config.txt
LIGHTDM=/etc/lightdm/lightdm.conf
SESSION_FILE=/usr/share/wayland-sessions/labcontrol-kiosk.desktop
SESSION_BIN=/usr/local/bin/labcontrol-kiosk-session
KIOSK_UNIT=/etc/systemd/system/labcontrol-kiosk.service
AUTOSTART="$HOME/.config/labwc/autostart"
LABWC_RC="$HOME/.config/labwc/rc.xml"
LABWC_ENV="$HOME/.config/labwc/environment"
BACKUP="$HOME/.local/share/labcontrol/kiosk-backup"
CMDLINE_ADD="logo.nologo vt.global_cursor_default=0 loglevel=3 fbcon=map:9"
CURSOR_THEME=labcontrol-invisible
CURSOR_DIR="$HOME/.icons/$CURSOR_THEME"
DROPIN_DIR="$HOME/.config/systemd/user/labcontrol.service.d"
DROPIN="$DROPIN_DIR/kiosk.conf"

if [ "${1:-}" = "--undo" ]; then
  echo "Kiosk zuruecknehmen"
  [ -f "$BACKUP/cmdline.txt" ] && sudo cp "$BACKUP/cmdline.txt" "$CMDLINE" && echo "  cmdline.txt wiederhergestellt"
  [ -f "$BACKUP/config.txt" ] && sudo cp "$BACKUP/config.txt" "$CONFIG" && echo "  config.txt wiederhergestellt"
  [ -f "$BACKUP/lightdm.conf" ] && sudo cp "$BACKUP/lightdm.conf" "$LIGHTDM" && echo "  lightdm.conf wiederhergestellt"
  [ -f "$BACKUP/rc.xml" ] && cp "$BACKUP/rc.xml" "$LABWC_RC" && echo "  labwc rc.xml wiederhergestellt"
  if [ -f "$KIOSK_UNIT" ]; then
    sudo systemctl disable labcontrol-kiosk.service >/dev/null 2>&1 || true
    sudo rm -f "$KIOSK_UNIT"
  fi
  sudo systemctl unmask getty@tty1.service >/dev/null 2>&1 || true
  sudo systemctl daemon-reload
  sudo systemctl enable lightdm.service >/dev/null 2>&1 && echo "  Anmeldung ueber lightdm wieder an"
  sudo rm -f "$SESSION_FILE" "$SESSION_BIN"
  rm -f "$AUTOSTART" && echo "  labwc-Autostart entfernt (Desktop und Leiste wieder an)"
  rm -rf "$CURSOR_DIR" "$DROPIN" "$DROPIN_DIR/kiosk-cursor.conf"
  [ -f "$LABWC_ENV" ] && sed -i "/^XCURSOR_THEME=$CURSOR_THEME\$/d" "$LABWC_ENV"
  systemctl --user daemon-reload && echo "  Mauszeiger und Fensterleisten wieder wie ausgeliefert"
  if [ -f "$BACKUP/eeprom.conf" ]; then
    sudo rpi-eeprom-config --apply "$BACKUP/eeprom.conf" >/dev/null && echo "  Bootloader-Einstellung wiederhergestellt"
  fi
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
[ -f "$BACKUP/rc.xml" ] || { [ -f "$LABWC_RC" ] && cp "$LABWC_RC" "$BACKUP/rc.xml" || true; }
[ -f "$BACKUP/eeprom.conf" ] || rpi-eeprom-config > "$BACKUP/eeprom.conf"

echo "1/8 Boot-Logo (Plymouth-Theme labcontrol)"
sudo install -d "$THEME_DIR"
sudo install -m 644 "$BRAND/splash.png" "$BRAND/labcontrol.script" "$BRAND/labcontrol.plymouth" "$THEME_DIR/"
if [ "$(/usr/sbin/plymouth-set-default-theme)" != "labcontrol" ] || [ "${KIOSK_REBUILD:-0}" = "1" ]; then
  echo "    initramfs wird neu gebaut (dauert etwa eine Minute) ..."
  sudo /usr/sbin/plymouth-set-default-theme -R labcontrol
else
  sudo update-initramfs -u >/dev/null   # Bild/Skript koennen sich geaendert haben
fi

echo "2/8 Kernel-Kommandozeile ohne Himbeeren, Cursor und Meldungen"
line=$(head -n 1 "$CMDLINE")
new="$line"
for token in $CMDLINE_ADD; do
  case " $new " in *" $token "*) ;; *) new="$new $token" ;; esac
done
if [ "$new" != "$line" ]; then
  printf '%s\n' "$new" | sudo tee "$CMDLINE" >/dev/null
  echo "    ergaenzt: $CMDLINE_ADD"
fi

echo "3/8 Firmware ohne Regenbogen"
if ! grep -q '^disable_splash=1' "$CONFIG"; then
  printf '\n# LabControl-Kiosk (tools/pi/kiosk_pi.sh)\n[all]\ndisable_splash=1\n' | sudo tee -a "$CONFIG" >/dev/null
  echo "    disable_splash=1 gesetzt"
fi

echo "4/8 labwc-Autostart ohne Desktop und Leiste, Hintergrund = Logo"
mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<EOF
# LabControl-Kiosk (tools/pi/kiosk_pi.sh): statt /etc/xdg/labwc/autostart -- ohne Desktop (pcmanfm-pi)
# und Leiste (wf-panel-pi). Der Hintergrund zeigt das Boot-Logo, bis LabControl im Vollbild steht.
/usr/bin/swaybg -m fill -c '#0f172a' -i '$BRAND/splash.png' &
/usr/bin/kanshi &
/usr/bin/lxsession-xdg-autostart
EOF

echo "5/8 Kiosk-Dienst statt Anmeldebildschirm (Boot-Logo ohne schwarze Luecke)"
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
# Sitzungsdatei bleibt fuer den Rueckweg ueber lightdm (lightdm.conf zeigt darauf)
sudo tee "$SESSION_FILE" >/dev/null <<EOF
[Desktop Entry]
Type=Application
Name=LabControl Kiosk
Comment=Nur LabControl im Vollbild, ohne Desktop und Leiste (tools/pi/kiosk_pi.sh)
Exec=$SESSION_BIN
EOF
sudo sed -i 's/^autologin-session=.*/autologin-session=labcontrol-kiosk/' "$LIGHTDM"
sudo tee "$KIOSK_UNIT" >/dev/null <<EOF
# LabControl-Kiosk (tools/pi/kiosk_pi.sh): labwc-Sitzung direkt auf tty1, ohne Anmeldebildschirm.
# Das Boot-Logo gibt die Grafik frei, bleibt aber stehen, bis labwc den Logo-Hintergrund zeigt.
[Unit]
Description=LabControl Kiosk (labwc auf tty1)
After=systemd-user-sessions.service plymouth-start.service dev-dri-card0.device
Wants=dev-dri-card0.device
Conflicts=getty@tty1.service plymouth-quit.service
After=plymouth-quit.service

[Service]
User=$USER
WorkingDirectory=$HOME
PAMName=login
TTYPath=/dev/tty1
# Kein TTYReset/TTYVHangup: das setzte tty1 (mit dem Boot-Logo) in den Textmodus zurueck, die leere Konsole
# uebermalte das Logo knapp zwei Sekunden lang, bis labwc stand (Nutzerhinweis 11.11.2026)
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland
Environment=XDG_SESSION_CLASS=user
Environment=XDG_SESSION_DESKTOP=labcontrol-kiosk
ExecStartPre=-+/usr/bin/plymouth deactivate
ExecStart=$SESSION_BIN
ExecStartPost=-+/bin/sh -c 'sleep 4; /usr/bin/plymouth quit --retain-splash'
Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
EOF
sudo systemctl daemon-reload
sudo systemctl disable lightdm.service >/dev/null 2>&1 || true
sudo systemctl mask getty@tty1.service >/dev/null
sudo systemctl enable labcontrol-kiosk.service >/dev/null
echo "    labcontrol-kiosk.service an, lightdm und Text-Anmeldung auf tty1 aus"

echo "6/8 Mauszeiger ausblenden (unsichtbares Zeiger-Theme)"
mkdir -p "$CURSOR_DIR/cursors"
# Xcursor-Datei mit einem 1x1-Bild, voll transparent (Format: libXcursor, ein Bild-Eintrag)
python3 - "$CURSOR_DIR/cursors/blank" <<'EOF'
import struct, sys
image = struct.pack("<9I", 36, 0xFFFD0002, 1, 1, 1, 1, 0, 0, 0) + struct.pack("<I", 0)
header = struct.pack("<4sIII", b"Xcur", 16, 0x10000, 1) + struct.pack("<III", 0xFFFD0002, 1, 28)
open(sys.argv[1], "wb").write(header + image)
EOF
names=$(ls /usr/share/icons/*/cursors 2>/dev/null | grep -v ':$' | grep -v '^$' | sort -u)
for name in $names default left_ptr pointer text; do
  [ "$name" = blank ] || ln -sf blank "$CURSOR_DIR/cursors/$name"
done
printf '[Icon Theme]\nName=LabControl unsichtbar\nComment=Kiosk: kein Mauszeiger (tools/pi/kiosk_pi.sh)\n' > "$CURSOR_DIR/index.theme"
touch "$LABWC_ENV"
grep -q "^XCURSOR_THEME=$CURSOR_THEME\$" "$LABWC_ENV" || echo "XCURSOR_THEME=$CURSOR_THEME" >> "$LABWC_ENV"
echo "    $(ls "$CURSOR_DIR/cursors" | wc -l) Zeigernamen auf transparent gesetzt"

echo "7/8 Dialoge ohne Fensterleiste"
mkdir -p "$DROPIN_DIR"
rm -f "$DROPIN_DIR/kiosk-cursor.conf"   # Vorgaenger (0.40.2)
cat > "$DROPIN" <<EOF
# LabControl-Kiosk (tools/pi/kiosk_pi.sh)
[Service]
# kein Mauszeiger ueber Qt-Fenstern
Environment=XCURSOR_THEME=$CURSOR_THEME
# Dialoge ohne Fensterleiste: Qt zeichnet keine eigene (labwc zeichnet keine, siehe rc.xml)
Environment=QT_WAYLAND_DISABLE_WINDOWDECORATION=1
EOF
systemctl --user daemon-reload
python3 - "$LABWC_RC" <<'EOF'
import sys
from pathlib import Path
path = Path(sys.argv[1])
rule = '\t<windowRules>\n\t\t<windowRule identifier="*" serverDecoration="no"/>\n\t</windowRules>\n'
text = path.read_text(encoding="utf-8") if path.exists() else \
    '<?xml version="1.0"?>\n<openbox_config xmlns="http://openbox.org/3.4/rc">\n</openbox_config>\n'
if 'serverDecoration="no"' not in text:
    for close in ("</openbox_config>", "</labwc_config>"):
        if close in text:
            text = text.replace(close, rule + close, 1)
            break
    else:
        raise SystemExit(f"{path}: kein schliessendes Wurzelelement gefunden")
    path.write_text(text, encoding="utf-8")
    print("    labwc: keine Fensterleisten (windowRule)")
EOF

echo "8/8 Bootloader-Bild aus (DISABLE_HDMI=1)"
if rpi-eeprom-config | grep -q '^DISABLE_HDMI=1'; then
  echo "    schon gesetzt"
elif [ "${KIOSK_BOOTLOADER:-0}" != "1" ]; then
  # Schreibt den Bootloader neu, mit der neuesten mitgelieferten Firmware (ein Firmware-Update) -- nur auf
  # ausdruecklichen Wunsch (Entscheidung PI-16): KIOSK_BOOTLOADER=1 tools/pi/kiosk_pi.sh
  echo "    uebersprungen (Firmware-Update, nur mit KIOSK_BOOTLOADER=1)"
else
  tmp=$(mktemp)
  rpi-eeprom-config | grep -v '^DISABLE_HDMI=' > "$tmp"
  echo "DISABLE_HDMI=1" >> "$tmp"
  sudo rpi-eeprom-config --apply "$tmp" >/dev/null
  rm -f "$tmp"
  echo "    gesetzt, das EEPROM wird beim naechsten Neustart aktualisiert"
fi

echo "Fertig -- wirksam nach einem Neustart (sudo reboot)."

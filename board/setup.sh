#!/bin/bash
# Turns a Raspberry Pi 2 with a 3.5" 480x320 SPI touchscreen (ILI9486 display,
# XPT2046 touch, plugs onto the pins) into Zelda's magnet board.
#
# Start from Raspberry Pi OS Lite (32-bit) with Wi-Fi and SSH set up in
# Raspberry Pi Imager. No desktop: this installs just enough to show one page
# full screen on the little display. Safe to run again.
#
#   sudo bash ~/tin-can/board/setup.sh 'http://zelda-phone.local:8080/board#key=...'
#
# Get the link from Zelda's admin page: Settings -> Magnet board.
# Options, set in front of the command (e.g. ROTATE=270 sudo -E bash ...):
#   ROTATE=90|270   which way up the screen is (default 90: landscape, pins on the left)
#   OVERLAY=...     display driver overlay to use (default: piscreen, or waveshare35a)
set -euo pipefail
[ "$(id -u)" = 0 ] || exec sudo -E bash "$0" "$@"
LINK=${1:-}
if [[ ! "$LINK" =~ ^https?://.+/board#key=.+ ]]; then
  echo "Usage: sudo bash $0 'http://zelda-phone.local:8080/board#key=...'"
  echo "(Admin page -> Settings -> Magnet board -> Make a board link)"
  exit 1
fi
APP=$(cd "$(dirname "$0")" && pwd)
U=$(getent passwd 1000 | cut -d: -f1)
H=$(getent passwd 1000 | cut -d: -f6)
OPT=/opt/zelda-board
SERVER=${LINK%%/board*}
ROTATE=${ROTATE:-90}
BOOT=/boot/firmware; [ -d "$BOOT/overlays" ] || BOOT=/boot
CONFIG=$BOOT/config.txt

echo "== Packages: a bare X server, Chromium and emoji fonts"
apt-get update
BROWSER_PKG=chromium; apt-cache show chromium >/dev/null 2>&1 || BROWSER_PKG=chromium-browser
apt-get install -y --no-install-recommends \
  xserver-xorg-core xserver-xorg-video-fbdev xserver-xorg-input-evdev xserver-xorg-input-libinput \
  x11-xserver-utils xinit xinput unclutter "$BROWSER_PKG" fonts-noto-color-emoji curl
BROWSER=$(command -v chromium || command -v chromium-browser)

echo "== The 3.5\" screen and its touch panel"
# piscreen ships with Raspberry Pi OS and drives these ILI9486 + XPT2046 boards.
# Older "LCD-show" scripts break current Raspberry Pi OS, so we don't use them.
OVERLAY=${OVERLAY:-}
if [ -z "$OVERLAY" ]; then
  if [ -e "$BOOT/overlays/piscreen.dtbo" ]; then OVERLAY=piscreen; else OVERLAY=waveshare35a; fi
fi
if [ "$OVERLAY" = waveshare35a ] && [ ! -e "$BOOT/overlays/waveshare35a.dtbo" ]; then
  curl -fsSL -o "$BOOT/overlays/waveshare35a.dtbo" https://github.com/goodtft/LCD-show/raw/master/usr/waveshare35a-overlay.dtb
fi
# Replace our block from an earlier run, keep everything else in config.txt.
# (config.txt only allows comments on their own line, hence the markers.)
sed -i '/^# zelda-board start$/,/^# zelda-board end$/d' "$CONFIG"
cat >> "$CONFIG" <<CFG
# zelda-board start
[all]
dtparam=spi=on
dtoverlay=$OVERLAY,speed=24000000,rotate=$ROTATE
# zelda-board end
CFG

# X draws on the little screen (/dev/fb1), not HDMI.
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/99-zelda-board.conf <<'XCONF'
Section "Device"
    Identifier "spi-screen"
    Driver "fbdev"
    Option "fbdev" "/dev/fb1"
EndSection
Section "ServerFlags"
    Option "AutoAddGPU" "false"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
XCONF
# Touch: line the panel up with the picture. These match ROTATE=90 / 270 on
# most of these boards; if taps land in the wrong place, see README.
if [ "$ROTATE" = 270 ]; then CAL="227 3936 3880 268"; else CAL="3936 227 268 3880"; fi
cat > /etc/X11/xorg.conf.d/99-zelda-touch.conf <<TCONF
Section "InputClass"
    Identifier "zelda touch"
    MatchProduct "ADS7846 Touchscreen"
    Driver "evdev"
    Option "Calibration" "${CALIBRATION:-$CAL}"
    Option "SwapAxes" "1"
EndSection
TCONF

echo "== Wi-Fi: no power saving (it makes USB Wi-Fi sticks drop out)"
if command -v nmcli >/dev/null; then
  nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="802-11-wireless"{print $1}' | while read -r c; do
    nmcli connection modify "$c" 802-11-wireless.powersave 2 || true
  done
fi
echo "options 8192cu rtw_power_mgnt=0 rtw_enusbss=0" > /etc/modprobe.d/zelda-wifi.conf

echo "== Kiosk: log in on the screen and open the board"
install -d "$OPT"
install -m 755 "$APP/screen.sh" "$OPT/screen.sh"
echo "$SERVER" > "$OPT/server"
cat > "$OPT/kiosk.sh" <<KIOSK
#!/bin/bash
# Started by X (see ~/.xinitrc). Waits for Zelda's screen, then shows the board.
xset s off; xset s noblank; xset -dpms
unclutter -idle 0.1 -root &
for _ in \$(seq 1 180); do curl -fsS -o /dev/null "$SERVER/api/version" && break; sleep 2; done
while true; do
  "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run --password-store=basic \\
    --disable-features=Translate,TouchpadOverscrollHistoryNavigation --check-for-update-interval=31536000 \\
    --disable-pinch --overscroll-history-navigation=0 --disable-session-crashed-bubble --touch-events=enabled \\
    --window-position=0,0 --window-size=480,320 --force-device-scale-factor=1 \\
    --disable-gpu --disk-cache-size=10000000 '$LINK'
  sleep 5   # if Chromium ever closes, open it again
done
KIOSK
chmod 755 "$OPT/kiosk.sh"
echo "exec $OPT/kiosk.sh" > "$H/.xinitrc"
# Start X on the screen right after the automatic console login.
PROFILE="$H/.bash_profile"
touch "$PROFILE"
sed -i '/# zelda-board$/d' "$PROFILE"
echo '[ -z "$DISPLAY" ] && [ "$(tty)" = /dev/tty1 ] && exec startx -- -nocursor >/dev/null 2>&1 # zelda-board' >> "$PROFILE"
chown "$U:$U" "$H/.xinitrc" "$PROFILE"
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_boot_behaviour B2   # console, logged in automatically
fi

echo "== On at 08:00, off at 20:00 (or whatever the admin page says)"
cat > /etc/cron.d/zelda-board <<CRON
*/5 * * * * root $OPT/screen.sh
@reboot root sleep 90 && $OPT/screen.sh
CRON

echo
echo "Done. Rebooting into the magnet board (the first start takes a minute)..."
systemctl reboot

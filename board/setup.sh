#!/bin/bash
# Sets up a Pi with a small touchscreen as Zelda's magnet board.
# Needs Raspberry Pi OS *with desktop* (Bookworm), a working touchscreen, and
# the same Wi-Fi as Zelda's screen. Safe to run again.
#   sudo bash ~/tin-can/board/setup.sh 'http://zelda-phone.local:8080/board#key=...'
# Get the link from the admin page: Settings -> Magnet board.
set -euo pipefail
[ "$(id -u)" = 0 ] || exec sudo bash "$0" "$@"
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

# Browser and emoji (the magnets use emoji until you add photos).
NEED=()
command -v chromium >/dev/null || command -v chromium-browser >/dev/null || NEED+=(chromium)
command -v wlr-randr >/dev/null || NEED+=(wlr-randr)
dpkg -s fonts-noto-color-emoji >/dev/null 2>&1 || NEED+=(fonts-noto-color-emoji)
command -v curl >/dev/null || NEED+=(curl)
if [ ${#NEED[@]} -gt 0 ]; then
  apt-get update
  apt-get install -y "${NEED[@]}"
fi
BROWSER=$(command -v chromium || command -v chromium-browser)

# Log straight into the desktop and never blank on its own; we do on/off.
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_boot_behaviour B4
  raspi-config nonint do_blanking 1
fi

install -d "$OPT"
install -m 755 "$APP/screen.sh" "$OPT/screen.sh"
echo "$SERVER" > "$OPT/server"

# Kiosk: wait for Zelda's screen to answer, then open the board full screen.
cat > "$OPT/kiosk.sh" <<KIOSK
#!/bin/bash
for _ in \$(seq 1 180); do curl -fsS -o /dev/null "$SERVER/api/version" && break; sleep 2; done
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run --password-store=basic \\
  --disable-features=Translate --check-for-update-interval=31536000 --disable-pinch \\
  --overscroll-history-navigation=0 --disable-session-crashed-bubble --touch-events=enabled \\
  '$LINK'
KIOSK
chmod 755 "$OPT/kiosk.sh"

# Start it with the desktop: Wayland (labwc, the Bookworm default) and the older X11 desktop.
mkdir -p "$H/.config/labwc" "$H/.config/lxsession/LXDE-pi"
cat > "$H/.config/labwc/autostart" <<AUTO
$OPT/kiosk.sh &
AUTO
cat > "$H/.config/lxsession/LXDE-pi/autostart" <<AUTO
@xset s off
@xset -dpms
@$OPT/kiosk.sh
AUTO
chown -R "$U:$U" "$H/.config"

# On at 08:00, off at 20:00, or whatever the admin page says. Checked every 5 minutes.
cat > /etc/cron.d/zelda-board <<CRON
*/5 * * * * root $OPT/screen.sh
@reboot root sleep 60 && $OPT/screen.sh
CRON

echo
echo "Done. Rebooting into the magnet board..."
systemctl reboot

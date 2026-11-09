#!/bin/bash
# Keeps this Pi on Wi-Fi through router restarts and dropouts. Run once with sudo:
#   sudo bash wifi-keepalive.sh
# It does three things:
#  1. Tells NetworkManager to keep retrying the Wi-Fi forever. By default it gives up
#     after a few failed tries, which is how a router restart left this Pi offline.
#  2. Turns off Wi-Fi power saving, which makes a Pi Zero drop off now and then.
#  3. Installs a watchdog that checks the router every 2 minutes and, if it stops
#     answering: reconnects, then restarts networking, then (after 20 minutes, at most
#     once every 2 hours) reboots the Pi.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run it with sudo: sudo bash $0"; exit 1; }

for con in $(nmcli -t -f UUID,TYPE con show | awk -F: '$2=="802-11-wireless"{print $1}'); do
  nmcli con modify "$con" connection.autoconnect yes connection.autoconnect-retries 0 802-11-wireless.powersave 2
  echo "Wi-Fi connection $(nmcli -g connection.id con show "$con"): retries forever, power saving off"
done
mkdir -p /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave = 2\n' > /etc/NetworkManager/conf.d/wifi-powersave-off.conf

cat > /usr/local/sbin/wifi-watchdog <<'WD'
#!/bin/bash
# Checks the router; if it doesn't answer, gets the Wi-Fi back step by step.
FAILS=/run/wifi-watchdog.fails
LAST_BOOT=/var/lib/wifi-watchdog.last-reboot
GW=$(ip -4 route show default | awk '{print $3; exit}')
if [ -n "$GW" ] && ping -c 2 -W 3 "$GW" >/dev/null 2>&1; then rm -f "$FAILS"; exit 0; fi
n=$(( $(cat "$FAILS" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$FAILS"
logger -t wifi-watchdog "the router isn't answering (check $n)"
iw dev wlan0 set power_save off 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true
CON=$(nmcli -t -f UUID,TYPE con show | awk -F: '$2=="802-11-wireless"{print $1; exit}')
if [ "$n" -le 3 ]; then
  nmcli con up "$CON" >/dev/null 2>&1 || true
elif [ "$n" -le 9 ]; then
  logger -t wifi-watchdog "restarting networking"
  systemctl restart NetworkManager
else
  up=$(awk '{print int($1)}' /proc/uptime)
  last=$(cat "$LAST_BOOT" 2>/dev/null || echo 0)
  if [ "$up" -gt 900 ] && [ $(( $(date +%s) - last )) -gt 7200 ]; then
    logger -t wifi-watchdog "still offline after 20 minutes: rebooting"
    date +%s > "$LAST_BOOT"; sync; systemctl reboot
  else
    nmcli con up "$CON" >/dev/null 2>&1 || true
  fi
fi
WD
chmod 755 /usr/local/sbin/wifi-watchdog

cat > /etc/systemd/system/wifi-watchdog.service <<'U'
[Unit]
Description=Keep the Wi-Fi connected
After=NetworkManager.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/wifi-watchdog
U
cat > /etc/systemd/system/wifi-watchdog.timer <<'U'
[Unit]
Description=Check the Wi-Fi every 2 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
U
systemctl daemon-reload
systemctl enable --now wifi-watchdog.timer
iw dev wlan0 set power_save off 2>/dev/null || true
echo "Done. Wi-Fi watchdog: $(systemctl is-active wifi-watchdog.timer). Power save: $(iw dev wlan0 get power_save 2>/dev/null | awk '{print $3}')"

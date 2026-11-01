#!/bin/bash
# Gives this Pi a fixed address on the home network that never changes, e.g.
#
#   sudo bash ~/tin-can/pin-ip.sh 192.168.68.50
#
# The fixed address is added next to the one the router hands out (DHCP), not
# instead of it, so the Pi can never drop off the network over this: if
# anything is off, it's still reachable the usual way (zelda-phone.local).
# Pick an address below .100: TP-Link Deco routers hand out .100 and up.
set -euo pipefail
[ "$(id -u)" = 0 ] || exec sudo bash "$0" "$@"
WANT=${1:-}
if [[ ! "$WANT" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Usage: sudo bash $0 192.168.68.50"; exit 1
fi
command -v nmcli >/dev/null || { echo "This needs NetworkManager (Raspberry Pi OS Bookworm or newer)."; exit 1; }

DEV=$(ip -4 route show default | awk '{print $5; exit}')
GW=$(ip -4 route show default | awk '{print $3; exit}')
CUR=$(ip -4 -o addr show dev "$DEV" | awk '{print $4}' | grep -v "^$WANT/" | head -1)   # e.g. 192.168.68.143/22
PREFIX=${CUR#*/}
CON=$(nmcli -t -f NAME,DEVICE con show --active | awk -F: -v d="$DEV" '$2 == d {print $1; exit}')
[ -n "$DEV" ] && [ -n "$CUR" ] && [ -n "$CON" ] || { echo "Couldn't find the network connection. Is the Pi online?"; exit 1; }
echo "Network: $CON on $DEV, now $CUR, router $GW"

# Same network as the router? (compare the first three numbers; fine for /22 and /24)
if [ "${WANT%.*}" != "${GW%.*}" ] && [ "$PREFIX" -ge 24 ]; then
  echo "$WANT isn't on this network ($CUR). Pick one like ${GW%.*}.50"; exit 1
fi
# Nobody else using it? (skip if it's already ours)
if ! ip -4 -o addr show dev "$DEV" | grep -q " $WANT/"; then
  if ping -c 2 -W 1 "$WANT" >/dev/null 2>&1; then
    echo "Something else on the network already answers at $WANT. Pick another number."; exit 1
  fi
fi

nmcli con mod "$CON" ipv4.method auto ipv4.addresses "$WANT/$PREFIX"
nmcli dev reapply "$DEV" >/dev/null 2>&1 || true
sleep 2
if ip -4 -o addr show dev "$DEV" | grep -q " $WANT/"; then
  echo "Done: this Pi now also answers at $WANT, every boot."
else
  echo "Saved. It takes effect after a reboot:  sudo reboot"
fi

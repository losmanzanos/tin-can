#!/bin/bash
# Unattended first-time setup, for a copy of this folder on the SD card's boot
# partition (/boot/firmware/zelda-phone) or anywhere else. Runs as root:
#   sudo bash /boot/firmware/zelda-phone/setup.sh
# The usual way is a git clone plus install.sh (see README); this is the fallback.
exec >>/var/log/zelda-phone-setup.log 2>&1
set -x
SRC=$(cd "$(dirname "$0")" && pwd)
H=$(getent passwd 1000 | cut -d: -f6)
U=$(getent passwd 1000 | cut -d: -f1)

# The boot partition is FAT, so run from a proper copy in the home folder.
APP=$SRC
if [[ "$SRC" == /boot/* ]]; then
  APP=$H/tin-can
  mkdir -p "$APP"
  cp -r "$SRC/." "$APP/"
  chown -R "$U:$U" "$APP"
fi

# ZELDA_NUMBER / ZELDA_PIN can be put in a setup.env next to this script.
[ -f "$SRC/setup.env" ] && . "$SRC/setup.env" && export ZELDA_NUMBER ZELDA_PIN
ZELDA_NONINTERACTIVE=1 bash "$APP/install.sh"
touch "$SRC/.done"
echo "Rebooting into the phone book..."
systemctl reboot

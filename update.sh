#!/bin/bash
# Pulls the latest code, reinstalls and restarts. Contacts and photos stay put.
#   bash ~/tin-can/update.sh
set -euo pipefail
APP=$(cd "$(dirname "$0")" && pwd)
U=$(getent passwd 1000 | cut -d: -f1)
if [ "$(id -u)" = 0 ]; then
  sudo -u "$U" git -C "$APP" pull --ff-only
else
  git -C "$APP" pull --ff-only
fi
# The little touchscreen Pi (Tinny / the magnet board) only needs the git pull
# above: its page comes from the main Pi. Installing the main screen here
# would take over the little screen.
H=$(getent passwd 1000 | cut -d: -f6)
if [ -d /opt/zelda-board ] && [ ! -e "$H/zelda-phone-data/contacts.json" ]; then
  echo
  echo "This is the little touchscreen Pi (zelda-board): the git pull above is all it needs."
  echo "To update the main screen, run this on the main Pi instead:  ssh chad@zelda-phone.local"
  exit 0
fi
sudo bash "$APP/install.sh"   # also restarts the screen and the admin page

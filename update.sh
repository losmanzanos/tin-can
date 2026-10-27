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
sudo bash "$APP/install.sh"   # also restarts the screen and the admin page

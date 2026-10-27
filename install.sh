#!/bin/bash
# Installs or updates Zelda's phone book on the Pi Zero W. Safe to run again:
# it never touches existing contacts, photos or the PIN.
#   sudo bash ~/tin-can/install.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || exec sudo bash "$0" "$@"

APP=$(cd "$(dirname "$0")" && pwd)
U=$(getent passwd 1000 | cut -d: -f1)
H=$(getent passwd 1000 | cut -d: -f6)
DATA=$H/zelda-phone-data
OPT=/opt/zelda-phone
ASK=0; [ -t 0 ] && [ -z "${ZELDA_NONINTERACTIVE:-}" ] && ASK=1

# Tools. Raspberry Pi OS Lite ships these; this is only a fallback.
NEED=()
command -v python3 >/dev/null || NEED+=(python3)
command -v git >/dev/null || NEED+=(git)
if [ ${#NEED[@]} -gt 0 ]; then
  apt-get update
  apt-get install -y "${NEED[@]}"
fi

# Data lives outside the git checkout, so git pull never overwrites it.
install -d -m 750 -o "$U" -g "$U" "$DATA" "$DATA/photos"
if [ ! -e "$DATA/contacts.json" ]; then
  NUMBER=${ZELDA_NUMBER:-}
  if [ -z "$NUMBER" ]; then
    # Offer the number from the old hand-edited page, if it's still around.
    OLD=$(grep -ho 'MY_NUMBER *= *"[^"]*"' "$H"/zelda-phone/page-source.html "$H"/zelda-phone/page/index.html 2>/dev/null | head -1 | sed 's/.*"\(.*\)"/\1/' || true)
    if [ "$ASK" = 1 ]; then
      read -r -p "Zelda's phone number${OLD:+ [$OLD]}: " NUMBER </dev/tty || true
    fi
    NUMBER=${NUMBER:-$OLD}
  fi
  sudo -u "$U" python3 "$APP/server.py" --data "$DATA" init --number "$NUMBER"
fi
if ! sudo -u "$U" python3 "$APP/server.py" --data "$DATA" has-pin; then
  if [ -n "${ZELDA_PIN:-}" ]; then
    sudo -u "$U" env ZELDA_PIN="$ZELDA_PIN" python3 "$APP/server.py" --data "$DATA" set-pin
  elif [ "$ASK" = 1 ]; then
    echo "Pick a PIN for the admin page (you'll type it on your phone)."
    sudo -u "$U" python3 "$APP/server.py" --data "$DATA" set-pin </dev/tty
  else
    echo "!! No admin PIN yet. Set one with: python3 $APP/server.py set-pin"
  fi
fi

# Screen painter: runs as root (it writes /dev/fb0). Keeps the old renders/
# folder, which it shows until the admin page uploads its first picture.
install -d "$OPT"
install -m 755 "$APP/paint.py" "$OPT/paint.py"

cat > /etc/systemd/system/zelda-phone.service <<UNIT
[Unit]
Description=Zelda's phone book screen
After=local-fs.target
Conflicts=getty@tty1.service

[Service]
Environment=ZELDA_DATA=$DATA
ExecStart=/usr/bin/python3 $OPT/paint.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/zelda-phone-web.service <<UNIT
[Unit]
Description=Zelda's phone book admin page (port 8080)
After=network.target

[Service]
User=$U
ExecStart=/usr/bin/python3 $APP/server.py --data $DATA serve --port 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# No login prompt on the screen, no cursor, no boot text, no auto-blanking.
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl mask getty@tty1.service
CMD=/boot/firmware/cmdline.txt
[ -f "$CMD" ] || CMD=/boot/cmdline.txt
if [ -f "$CMD" ]; then
  for opt in consoleblank=0 vt.global_cursor_default=0 logo.nologo quiet loglevel=3; do
    grep -qw "$opt" "$CMD" || sed -i "1 s/\$/ $opt/" "$CMD"
  done
fi

systemctl daemon-reload
systemctl enable zelda-phone.service zelda-phone-web.service
systemctl restart zelda-phone-web.service zelda-phone.service

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Done. On your phone (same Wi-Fi) open:"
echo "  http://$(hostname).local:8080/admin${IP:+   or   http://$IP:8080/admin}"

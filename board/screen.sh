#!/bin/bash
# Turns the magnet board's screen on and off with the times from the admin page
# (08:00 / 20:00 if Zelda's screen can't be reached). Runs as root from cron.
# An "on" time later than the "off" time keeps it on overnight.
OPT=/opt/zelda-board
SERVER=$(cat "$OPT/server" 2>/dev/null)
ON_AT="08:00"; OFF_AT="20:00"

TIMES=$(curl -fsS --max-time 10 "$SERVER/api/contacts" 2>/dev/null | python3 -c '
import json, re, sys
s = json.load(sys.stdin).get("screen", {})
ok = lambda t: isinstance(t, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t)
print(s["on"] if ok(s.get("on")) else "", s["off"] if ok(s.get("off")) else "")' 2>/dev/null)
read -r ON OFF <<<"$TIMES"
[ -n "$ON" ] && ON_AT=$ON
[ -n "$OFF" ] && OFF_AT=$OFF
# Remember the last good times in case the network is down later.
if [ -n "$ON" ]; then echo "$ON_AT $OFF_AT" > "$OPT/times"; elif [ -r "$OPT/times" ]; then read -r ON_AT OFF_AT < "$OPT/times"; fi

NOW=$(date +%H:%M)
if [[ "$ON_AT" == "$OFF_AT" ]]; then WANT=on
elif [[ "$ON_AT" < "$OFF_AT" ]]; then
  if [[ ! "$NOW" < "$ON_AT" && "$NOW" < "$OFF_AT" ]]; then WANT=on; else WANT=off; fi
else
  if [[ ! "$NOW" < "$ON_AT" || "$NOW" < "$OFF_AT" ]]; then WANT=on; else WANT=off; fi
fi

# 1. Touchscreens with a backlight control (the official Raspberry Pi display).
done_any=0
for bl in /sys/class/backlight/*/bl_power; do
  [ -e "$bl" ] || continue
  [ "$WANT" = on ] && echo 0 > "$bl" || echo 1 > "$bl"
  done_any=1
done

# 2. Otherwise switch the display output off through the desktop session.
if [ "$done_any" = 0 ]; then
  U=$(getent passwd 1000 | cut -d: -f1)
  UID1=$(id -u "$U")
  RT=/run/user/$UID1
  WL=$(ls "$RT" 2>/dev/null | grep -m1 '^wayland-[0-9]*$')
  if [ -n "$WL" ]; then
    OUT=$(sudo -u "$U" XDG_RUNTIME_DIR=$RT WAYLAND_DISPLAY=$WL wlr-randr | awk '/^[A-Za-z]/{print $1; exit}')
    sudo -u "$U" XDG_RUNTIME_DIR=$RT WAYLAND_DISPLAY=$WL wlr-randr --output "$OUT" --"$WANT"
  elif [ -e /tmp/.X11-unix/X0 ]; then
    [ "$WANT" = on ] && ARG=on || ARG=off
    sudo -u "$U" DISPLAY=:0 xset dpms force "$ARG"
  fi
fi
# The board page also goes black at night by itself, as a backup.

#!/bin/bash
# Turns the magnet board's screen on and off with the times from the admin page
# (08:00 / 20:00 if Zelda's screen can't be reached), or keeps it always on or
# off if the admin page says so. Runs as root from cron.
# An "on" time later than the "off" time keeps it on overnight.
OPT=/opt/zelda-board
SERVER=$(cat "$OPT/server" 2>/dev/null)
ON_AT="08:00"; OFF_AT="20:00"

TIMES=$(curl -fsS --max-time 10 "$SERVER/api/contacts" 2>/dev/null | python3 -c '
import json, re, sys
s = json.load(sys.stdin).get("screen", {})
ok = lambda t: isinstance(t, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t)
print(s["on"] if ok(s.get("on")) else "", s["off"] if ok(s.get("off")) else "",
      s.get("board") if s.get("board") in ("same", "on", "off") else "")' 2>/dev/null)
read -r ON OFF MODE <<<"$TIMES"
[ -n "$ON" ] && ON_AT=$ON
[ -n "$OFF" ] && OFF_AT=$OFF
# Remember the last good times in case the network is down later.
if [ -n "$ON" ]; then echo "$ON_AT $OFF_AT $MODE" > "$OPT/times"; elif [ -r "$OPT/times" ]; then read -r ON_AT OFF_AT MODE < "$OPT/times"; fi

# Tinny says goodnight on screen for half an hour before its screen goes dark.
if grep -qs '/tinny' "$OPT/link" && [[ "$ON_AT" != "$OFF_AT" ]]; then
  m=$(( (10#${OFF_AT%:*} * 60 + 10#${OFF_AT#*:} + 30) % 1440 ))
  LATER=$(printf '%02d:%02d' $((m / 60)) $((m % 60)))
  # Only push "off" later if that doesn't run past the morning "on" time.
  if [[ "$ON_AT" > "$OFF_AT" ]] || [[ "$LATER" < "$ON_AT" ]] || [[ "$LATER" > "$OFF_AT" ]]; then OFF_AT=$LATER; fi
fi
NOW=$(date +%H:%M)
if [[ "$MODE" == on || "$MODE" == off ]]; then WANT=$MODE   # "Always on" / "Off" on the admin page
elif [[ "$ON_AT" == "$OFF_AT" ]]; then WANT=on
elif [[ "$ON_AT" < "$OFF_AT" ]]; then
  if [[ ! "$NOW" < "$ON_AT" && "$NOW" < "$OFF_AT" ]]; then WANT=on; else WANT=off; fi
else
  if [[ ! "$NOW" < "$ON_AT" || "$NOW" < "$OFF_AT" ]]; then WANT=on; else WANT=off; fi
fi

# All three, every time: a backlight switch can be there and not be wired to the light
# (the 3.5" ILI9486 boards have one that does nothing), so it can't be the only one.
# 1. Touchscreens with a backlight control (the official Raspberry Pi display).
for bl in /sys/class/backlight/*/bl_power; do
  [ -e "$bl" ] || continue
  [ "$WANT" = on ] && echo 0 > "$bl" || echo 1 > "$bl"
done

# 2. The little SPI screen: blank its framebuffer (turns the backlight off on
#    boards that wire it up; otherwise the page itself goes black).
if [ -e /sys/class/graphics/fb1/blank ]; then
  [ "$WANT" = on ] && echo 0 > /sys/class/graphics/fb1/blank || echo 1 > /sys/class/graphics/fb1/blank
fi

# 3. And through the X or Wayland session, for screens that listen to that.
U=$(getent passwd 1000 | cut -d: -f1)
H=$(getent passwd 1000 | cut -d: -f6)
UID1=$(id -u "$U")
RT=/run/user/$UID1
WL=$(ls "$RT" 2>/dev/null | grep -m1 '^wayland-[0-9]*$')
if [ -n "$WL" ]; then
  OUT=$(sudo -u "$U" XDG_RUNTIME_DIR=$RT WAYLAND_DISPLAY=$WL wlr-randr | awk '/^[A-Za-z]/{print $1; exit}')
  sudo -u "$U" XDG_RUNTIME_DIR=$RT WAYLAND_DISPLAY=$WL wlr-randr --output "$OUT" --"$WANT"
elif [ -e /tmp/.X11-unix/X0 ]; then
  if [ "$WANT" = on ]; then
    sudo -u "$U" DISPLAY=:0 XAUTHORITY="$H/.Xauthority" xset dpms force on
    sudo -u "$U" DISPLAY=:0 XAUTHORITY="$H/.Xauthority" xset -dpms
  else
    sudo -u "$U" DISPLAY=:0 XAUTHORITY="$H/.Xauthority" xset +dpms
    sudo -u "$U" DISPLAY=:0 XAUTHORITY="$H/.Xauthority" xset dpms force off
  fi
fi
# The board page also goes black at night by itself, as a backup.

#!/bin/bash
# Runs at every boot, before the screen logs in: points X at the little screen's
# framebuffer. It comes up as fb0 or fb1 depending on what the Pi finds, and X
# wants the real /dev/fbN (it won't take a symlink), so the number is written in.
CONF=/etc/X11/xorg.conf.d/99-zelda-board.conf
for _ in $(seq 1 30); do            # the SPI screen's driver can take a few seconds to appear
  for fb in /sys/class/graphics/fb*; do
    grep -qs '^fb_' "$fb/name" || continue      # fb_ili9486 and the other SPI drivers
    dev=/dev/$(basename "$fb")
    grep -q "\"fbdev\" \"$dev\"" "$CONF" || sed -i "s|\"fbdev\" \"/dev/[^\"]*\"|\"fbdev\" \"$dev\"|" "$CONF"
    exit 0
  done
  sleep 1
done
echo "fbconf: no SPI screen found" >&2

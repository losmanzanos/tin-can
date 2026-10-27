# Tin Can screen

A little screen next to Zelda's Tin Can kids' phone. A Raspberry Pi Zero W on
an HDMI monitor shows three screens, and you change everything from your phone.

- **Today** (the home screen): her picture calendar, after her wooden
  Lovevery board. Each day has its color, the sentence "Today is Tuesday, a
  weekday.", and morning / midday / evening tiles. The current part of the
  day is highlighted, and past days fold away in the week strip.
- **Phone book**: who she can call and how. That's the top button or a
  2-digit code. Up to 21 contacts, 7 per page, and Zelda's own number on
  every page.
- **Postcards**: photos, drawings and notes from family. A new one pops up
  full screen, and a "new postcards" tag stays on the other screens until
  she looks.

**Her controller** (any small USB keyboard or macro pad) plugs into the Pi's
inner micro-USB port, marked **USB** (not PWR), through an OTG adapter:

- ◀ / ▶ move between days, pages or postcards.
- ● switches screens. The Today screen draws these as dark arrows at the
  ends of the week and a yellow 📞 button. Put a yellow sticker on the
  middle key to match. When postcards are waiting, the yellow spot turns
  into a mail tag, and pressing it opens them.
- After two minutes without a press, it goes back to Today.

You pick which button does what on the admin page.

## The pages

| Address | What it is |
|---|---|
| `http://zelda-phone.local:8080/admin` | Admin page (PIN): Week, Contacts, Postcards, Settings |
| `http://zelda-phone.local:8080/` | **Zelda's view**: the same three screens on any phone or laptop. Swipe or use the arrow keys, and "Add to Home Screen" for an app icon. |

## How it works

The Pi Zero runs Raspberry Pi OS **Lite** (no desktop, no browser), so:

| Piece | Job |
|---|---|
| `server.py` (service `zelda-phone-web`, runs as your user) | Admin page, Zelda's view, a small JSON API on port 8080. Checks the postcard inbox and sends ntfy alerts. |
| `page/render.js` | Draws the phone book and calendar on a canvas. After each save, the admin page in **your phone's browser** draws every screen at the Pi's exact size and uploads the pixels. |
| `postcards.py` | Postcards can arrive when no browser is open, so the Pi draws those itself with Pillow (`python3-pil`). |
| `paint.py` (service `zelda-phone`, runs as root) | Copies pictures to the screen (`/dev/fb0`), handles the buttons, picks the right day and part of the day, and turns HDMI off at night. |

Everything private lives in `~/zelda-phone-data/`, outside this repo:
contacts, photos, postcards, the hashed PIN, and the email and ntfy
settings. `git pull` never touches it. **Never commit any of it: this repo
is public.**

## Setup over SSH

1. **Connect** from your laptop on the same Wi-Fi:
   ```sh
   ssh chad@zelda-phone.local        # or: ssh chad@192.168.68.130
   ```
2. **Get the code and install it** on the Pi:
   ```sh
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/losmanzanos/tin-can.git ~/tin-can
   sudo bash ~/tin-can/install.sh
   ```
   The first run asks for Zelda's number, an admin PIN, and whether to set up
   Tailscale. Running it again is safe: existing data is kept.
3. **On your phone**, open `http://zelda-phone.local:8080/admin` and enter
   the PIN. The first visit draws all the screens.

### Updating
```sh
ssh chad@zelda-phone.local
bash ~/tin-can/update.sh       # git pull + reinstall + restart
```

## Optional extras

### Away from home: Tailscale
Install Tailscale on the Pi (`install.sh` offers to), then run `sudo tailscale up`
and open the link it prints. With Tailscale on your phone, signed in to the
same account, these work from anywhere:

- `http://zelda-phone:8080/` (Zelda's view)
- `http://zelda-phone:8080/admin` (the admin page)

Nothing is opened on your router.

### The magnet board (touchscreen)
A small touchscreen where Zelda drags magnets onto her day herself. It works
on a Pi with Raspberry Pi OS **with desktop** and a working touchscreen, on
the same Wi-Fi. A Pi 2 needs a USB Wi-Fi adapter.

1. On the admin page, go to **Settings → Magnet board → Make a board link**.
2. On the touchscreen Pi:
   ```sh
   git clone https://github.com/losmanzanos/tin-can.git ~/tin-can
   sudo bash ~/tin-can/board/setup.sh 'http://zelda-phone.local:8080/board#key=...'
   ```
It opens full screen and turns on and off with the same times as her main
screen (08:00–20:00 unless changed). Its link can only move magnets.
Changes show on her big screen about 10 seconds after she stops.

### Postcards by text or email
1. Make a new Gmail just for this. Turn on 2-Step Verification and create an
   [app password](https://myaccount.google.com/apppasswords).
2. On the admin page, open **Settings → Postcards by text or email**. Enter
   the address and app password, then tap **Test**.
3. On **Contacts**, add the mobile number and/or email for each family member
   who should be able to send. Mail from anyone else is ignored and listed
   under "Not shown."
4. Tap **Contact card for family** and send the card to them. They pick
   "Zelda's Postcards" and text a photo (picture texts to an email address
   arrive as email), or email it.

Messages that can't be verified wait for your OK. Turn on "Ask me before…"
to approve every postcard.

### Alerts: ntfy
Install the ntfy app on your phone. Then, under **Settings → Alerts**:

1. Tap **Make a random topic** and save.
2. Subscribe to that topic in the app.
3. Tap **Send a test**.

You'll get "New postcard for Zelda from Nana", and tapping it opens Zelda's
view. The topic works like a password, and only text goes through ntfy.

## Handy commands on the Pi

```sh
python3 ~/tin-can/server.py set-pin            # change the admin PIN (logs out all phones)
journalctl -u zelda-phone -f                   # screen log: shows key codes as you press buttons
journalctl -u zelda-phone-web -f               # admin page, inbox and alert log
sudo systemctl restart zelda-phone zelda-phone-web
```

The admin page is only reachable on your home network or your Tailscale
network. Don't forward port 8080 to the internet.

## Unattended first-boot setup (optional)

1. Copy this folder to the SD card as `/boot/firmware/zelda-phone/`.
2. Optionally add a `setup.env` there (never commit it) with
   `ZELDA_NUMBER="..."` and `ZELDA_PIN="..."`.
3. Run `sudo bash /boot/firmware/zelda-phone/setup.sh` once. It copies the
   folder to `~/tin-can`, installs and reboots, and logs to
   `/var/log/zelda-phone-setup.log`.

A copied folder isn't a git clone, so switch to a `git clone` before using
`update.sh`.

Fonts: Andika, SIL Open Font License (`page/fonts/OFL.txt`). The calendar
tiles use emoji to start. Swap in photos of her real magnets on the Week
tab; those photos stay on the Pi.

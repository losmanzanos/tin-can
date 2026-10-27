# Tin Can phone book

A "phone book" screen for Zelda's Tin Can kids' phone. A Raspberry Pi Zero W on
an HDMI monitor shows who she can call and how to dial them, and you can
change it from your phone.

- **Screen**: up to 21 contacts, 7 per page. Zelda's own number is always in
  the last box. With more than one page, it flips every 20 seconds.
- **Admin page** (`http://zelda-phone.local:8080/admin`, PIN protected): add,
  edit, reorder and delete contacts, add photos, change Zelda's number and set
  the screen's on/off times. The screen updates a couple of seconds after you
  save.
- **Optional page keys**: plug a small USB keyboard (such as a 2-key macro
  pad) into the Pi's inner micro-USB port (marked **USB**, not PWR) through
  an OTG adapter. Left/Up/PageUp/Backspace go back a page and any other key
  goes forward. A key press pauses the auto-flip for a minute.

## How it works

The Pi Zero runs Raspberry Pi OS **Lite**, which has no desktop or browser, so:

| Piece | What it does |
|---|---|
| `server.py` (service `zelda-phone-web`, runs as your user) | Serves the admin page, the preview at `/`, and a small JSON API on port 8080. Python standard library only. |
| `page/render.js` | Draws the phone book onto a canvas. After each save, the admin page in **your phone's browser** draws it at the Pi's exact screen size and uploads the pixels. |
| `paint.py` (service `zelda-phone`, runs as root) | Copies those pixels to the screen (`/dev/fb0`), flips pages, reads the keys, and turns HDMI off at night. |

Photos are cropped to a square and shrunk to 400 px on your phone before they
are uploaded, so the Pi never handles big or HEIC images.

Contacts, photos and the PIN (hashed) live in `~/zelda-phone-data/`, outside
this repo, so `git pull` never touches them. **Never commit them: this repo is public.**

## Setup over SSH

1. **SSH must be on.** If you didn't turn it on in Raspberry Pi Imager when you
   wrote the SD card, put an empty file named `ssh` on the card's boot
   partition and boot the Pi.
2. **Connect** from your laptop (same Wi-Fi):
   ```sh
   ssh chad@zelda-phone.local        # or: ssh chad@192.168.68.130
   ```
   The first time, type `yes` to trust the Pi, then enter its password.
3. **Get the code and install it** (on the Pi):
   ```sh
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/losmanzanos/tin-can.git ~/tin-can
   sudo bash ~/tin-can/install.sh
   ```
   The first run asks for Zelda's phone number and an admin PIN. Running it
   again is safe: existing contacts, photos and the PIN are kept.
4. **On your phone**, open `http://zelda-phone.local:8080/admin` (or
   `http://192.168.68.130:8080/admin`) and enter the PIN. The first visit
   draws the screen.

`http://zelda-phone.local:8080/` shows a live preview of the screen in any browser.

## Updating

```sh
ssh chad@zelda-phone.local
bash ~/tin-can/update.sh       # git pull + reinstall + restart both services
```

## Handy commands (on the Pi)

```sh
python3 ~/tin-can/server.py set-pin            # change the admin PIN (logs out all phones)
journalctl -u zelda-phone -f                   # screen log: shows key codes as you press keys
journalctl -u zelda-phone-web -f               # admin page log
sudo systemctl restart zelda-phone zelda-phone-web
```

The admin page only works on your home network. Don't forward port 8080 to
the internet.

## Unattended first-boot setup (optional)

Copy this folder to the SD card as `/boot/firmware/zelda-phone/`, and
optionally add a `setup.env` there (never commit it) with
`ZELDA_NUMBER="..."` and `ZELDA_PIN="..."`. Then run
`sudo bash /boot/firmware/zelda-phone/setup.sh` once. It copies the folder to
`~/tin-can`, installs and reboots, and logs to `/var/log/zelda-phone-setup.log`.
A folder copied this way isn't a git clone, so to use `update.sh` later,
switch to a `git clone` as above.

Fonts: Andika, SIL Open Font License (`page/fonts/OFL.txt`).

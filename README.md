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

You pick which button does what on the admin page. A button can also open
one screen directly, like a 📬 postcards button; pressed again, it shows the
next postcard.

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
A little touchscreen where Zelda drags magnets onto her day herself. Built
for a **Raspberry Pi 2 Model B** with a **3.5" 480×320 SPI touchscreen**
(ILI9486 display, XPT2046 touch) plugged onto its pins. It also works on any
Pi with that screen.

**1. Make the SD card** in Raspberry Pi Imager:
- Device: *Raspberry Pi 2*.
- OS: *Raspberry Pi OS (other) → Raspberry Pi OS Lite (32-bit)*.
- Edit settings:
  - hostname `zelda-board`
  - your username and password
  - your Wi-Fi and Wireless LAN country
  - your time zone
  - Services: turn on SSH

**2. Plug in the Wi-Fi stick.** Use the Edimax. The Pi 2 has no Wi-Fi of its
own; Ethernet works too. Press the screen onto the pins: it covers the first
26 pins, at the end by the SD card. Then boot it. The screen stays white
until setup is done.

**3. Make the board's link** on the admin page: **Settings → Magnet board →
Make a board link**.

**4. Set it up**, from your laptop:
```sh
ssh chad@zelda-board.local
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/losmanzanos/tin-can.git ~/tin-can
sudo bash ~/tin-can/board/setup.sh 'http://zelda-phone.local:8080/board#key=...'
```
On a Chromebook or phone, just open the board link: it works at any hour, so
you can set up tomorrow at night. On the touchscreen Pi, it reboots into the
board. It turns on and off with the same times as her
main screen, or pick **Always on** or **Off** under Settings → Magnet board
(it switches within a minute). Its link can only move magnets. Her big screen picks up her
changes about 20 seconds after she stops.

**If it isn't right the first time:**
- **"Off" still glows a little:** most of these 3.5" boards wire the backlight
  straight to the Pi's power pins, so software can only make the picture black,
  not turn the light behind it off. The driver may still list a switch
  (`/sys/class/backlight/fb_ili9486`) that isn't wired to anything. To test it:
  `echo 1 | sudo tee /sys/class/backlight/fb_ili9486/bl_power` (and `echo 0` to undo).
  If the light stays on, only cutting the power makes it fully dark.
- **Upside down:** run setup again with `ROTATE=270 sudo -E bash ~/tin-can/board/setup.sh '...'`.
- **Taps land in the wrong place:** tell Claude where you tapped and where it
  landed. The fix is one line in `/etc/X11/xorg.conf.d/99-zelda-touch.conf`.
- **Screen stays black after a restart, Tinny never appears:** the little screen can come up as
  fb0 or fb1; `zelda-fbconf.service` points X at whichever it is before the screen logs in.
  `cat /etc/X11/xorg.conf.d/99-zelda-board.conf` shows which one it picked.
- **Screen stays white:** the driver didn't load. Try
  `OVERLAY=waveshare35a sudo -E bash ~/tin-can/board/setup.sh '...'`.

### Tinny, the pet on the little screen
A pixel tin-can pet for the 3.5" touchscreen Pi, at `/tinny`. It holds up new
postcards until she looks, dresses for what's on her calendar right now
(backpack for school, goggles for swimming, a helmet for bike rides) and for the
weather (umbrella, scarf, sunglasses, snow), says good morning, suggests calling
someone she hasn't heard from now and then, gives hugs you send from the admin
page, and says goodnight for half an hour after her big screen turns off. It
grows a little over its first weeks.

Zelda can play with it too. Every target is big, because the touchscreen is
small and rough: tapping Tinny's body pets it, tapping its head tries on a hat,
and a tap anywhere else opens the play menu, four tiles a quarter of the screen
each. Nothing ever goes wrong for Tinny: it only gets a bit hungry or dusty and
asks (a thought bubble, the "Tap to play" strip glows, and so does that tile).

- **🍎 Eat:** she picks one of three snacks, with its word under it; Tinny munches, then has one more. Hungry each morning, and four hours after a meal.
- **🛁 Bath:** every tap scrubs and makes bubbles; six and it's squeaky clean. Dusty again after ten hours.
- **⚽ Ball:** every tap bounces the ball off its head, and it counts to ten on a ten-frame.
- **🎲 Games:** three little games, taking turns: *Find red!* (balloons), *How many?* (it counts them with her if she gets it wrong), and peekaboo. Three rounds, then a star.
- **Dress up:** tap its head to try on the next hat (party hat, crown, beret, bow, flower, cowboy hat, helmet, glasses, sunglasses). It keeps it until the next part of the day.
- **Bedtime:** for the last 20 minutes before her big screen turns off, it asks for help getting ready: brush its teeth, pajamas on, then turn out the lamp, and it falls asleep.
- **Stars:** finishing a game, ten bounces, a meal, a bath or bedtime earns a star, shown top left.
- 🏠 goes back, and a game she walks away from ends after 25 seconds.
- **Checking the touch:** Settings → Tinny → **Show where taps land** puts a red dot,
  with its position, wherever the little screen feels a tap. If the dots land away
  from her finger, the touch needs calibrating.

What Tinny remembers (meals, baths, stars, its hat) is kept in the little screen's browser.

Set it up under **Settings → Tinny**: pick a town for the weather, and choose
**Tinny's screen**: the same hours as her big screen, always on, or off (it
switches within a minute). Then on the little Pi run
```sh
sudo bash ~/tin-can/board/setup.sh 'http://zelda-phone.local:8080/tinny'
```
The weather comes from Open-Meteo (free, no account).

### Family links (postcards from anywhere)
Family away from home get their own send-only page: photo, drawing, message,
or a hug for Tinny. Each person has a personal link (no password to remember),
and the postcard is labeled with their name. They can't see or change anything
else.

1. **Put it online, once.** On the Pi: `sudo tailscale funnel --bg 8081`. If
   it prints a link, open it and click **Enable** (it turns on HTTPS and Funnel
   for your tailnet), then run the command again. `install.sh` does this too.
   Only the family page goes online. It runs on its own port and can't reach
   the admin page, contacts or settings, which stay home-only.
2. **Make links** on the admin page: **Postcards → Family links**. Type a name
   and tap **Make a link**. It's copied, ready to text. **Turn off** stops a
   link right away.

Each link can send up to 12 postcards an hour. "Ask me before a postcard goes
on her screen" applies to these too.

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

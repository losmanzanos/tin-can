#!/usr/bin/env python3
"""Shows Zelda's screen and turns it off at night.

Three screens, like rooms: Today (her picture calendar, the home screen), the
phone book, and postcards. Her little USB controller moves around:
  back / next  - days on Today, pages in the phone book, postcards in Postcards
  switch       - Today -> Phone book -> Postcards -> Today
After IDLE_SECONDS without a press it drifts back to Today.

Runs as root at boot (zelda-phone.service) and only copies pictures to the
framebuffer. The admin page draws the phone book and calendar; server.py draws
postcards. Standard library only.

Pictures live in <data>/frames: <group>.json says what's there, each picture
is a gzipped framebuffer dump (<name>.raw.gz).
"""
import array
import fcntl
import glob
import gzip
import json
import os
import pwd
import re
import select
import struct
import tempfile
import time

ON_AT, OFF_AT = "08:00", "20:00"   # used when contacts.json has no times
PAGE_SECONDS = 20                  # phone book pages flip on their own this often
HOLD_SECONDS = 60                  # ...but not for this long after a button press
IDLE_SECONDS = 120                 # then go back to Today
TAKEOVER_SECONDS = 60              # a new postcard shows full screen this long
REFRESH_SECONDS = 30               # repaint anyway, in case the console drew over it
MORNING_ENDS, MIDDAY_ENDS = "11:00", "16:00"

DIR = os.path.dirname(os.path.abspath(__file__))
FB = os.environ.get("ZELDA_FB", "/sys/class/graphics/fb0")
FBDEV = os.environ.get("ZELDA_FBDEV", "/dev/fb0")
try:
    DATA = os.environ.get("ZELDA_DATA") or os.path.join(pwd.getpwuid(1000).pw_dir, "zelda-phone-data")
except KeyError:
    DATA = "/home/pi/zelda-phone-data"
FRAMES = os.path.join(DATA, "frames")
PAPER = (0xFB, 0xF5, 0xE8)
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
SCREENS = ("today", "book", "postcards")

# Linux key codes (linux/input-event-codes.h). Used until buttons are set up
# on the admin page. Macro pads often send letters, numbers or media keys.
DEFAULT_BACK = {105, 103, 104, 14, 165, 114, 30, 2}    # Left Up PageUp Backspace PrevTrack Vol- A 1
DEFAULT_SWITCH = {28, 57, 164, 113, 48, 3}             # Enter Space Play/Pause Mute B 2
MODIFIERS = {29, 42, 54, 56, 97, 100, 125, 126}        # Ctrl Shift Alt Meta: not buttons on their own
EV_KEY = 1
EVENT = struct.Struct("llHHi")        # struct input_event: timeval, type, code, value
EVIOCGRAB = 0x40044590                # keep key presses away from the text console


def log(*a):
    print(*a, flush=True)


def sysfs(name):
    with open(f"{FB}/{name}") as f:
        return f.read().strip()


def set_blank(level):
    # 0 = screen on, 4 = HDMI powered down
    try:
        with open(f"{FB}/blank", "w") as f:
            f.write(str(level))
    except OSError:
        pass


def mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".paint-")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


# ------------------------------------------------------------- settings

class Settings:
    """contacts.json, re-read only when it changes."""

    def __init__(self):
        self.key, self.on, self.off, self.flip = None, ON_AT, OFF_AT, False
        self.back, self.next, self.switch = set(), set(), set()

    def refresh(self):
        path = f"{DATA}/contacts.json"
        if mtime(path) == self.key:
            return self
        self.key = mtime(path)
        doc = read_json(path) or {}
        s = doc.get("screen") or {}
        self.on = s.get("on") if TIME_RE.match(str(s.get("on"))) else ON_AT
        self.off = s.get("off") if TIME_RE.match(str(s.get("off"))) else OFF_AT
        self.flip = bool(s.get("flip"))
        k = doc.get("keys") or {}
        self.back, self.next, self.switch = (set(int(c) for c in k.get(n) or [] if str(c).isdigit())
                                             for n in ("back", "next", "switch"))
        return self

    def action(self, code):
        if code in self.back:
            return "back"
        if code in self.switch:
            return "switch"
        if code in self.next:
            return "next"
        if code in DEFAULT_BACK:
            return "back"
        if code in DEFAULT_SWITCH:
            return "switch"
        return "next"


def is_daytime(on, off, now):
    if on == off:
        return True
    if on < off:                 # e.g. on 08:00, off 20:00
        return on <= now < off
    return now >= on or now < off  # e.g. on 20:00, off 08:00 (overnight)


def period(now):
    return 0 if now < MORNING_ENDS else 1 if now < MIDDAY_ENDS else 2


# ------------------------------------------------------------- pictures

def frame_file(name):
    """(w, h, bpp, path) for a picture, or None if it isn't there."""
    group = "book" if name.startswith("book-") else "cards" if name.startswith(("card-", "badge-")) else "days"
    meta = read_json(f"{FRAMES}/{group}.json")
    path = f"{FRAMES}/{name}.raw.gz"
    if not meta or not os.path.exists(path):
        return None
    return int(meta["w"]), int(meta["h"]), int(meta["bpp"]), path


def legacy_frame(w, h, bpp, page):
    """Pictures from before the calendar: screen-<n>.raw, then the very first
    pre-drawn renders/ folder."""
    meta = read_json(f"{DATA}/screen.json")
    if meta and int(meta.get("bpp", 0)) == bpp:
        pages = int(meta.get("pages", 1))
        path = f"{DATA}/screen-{page % pages}.raw"
        if os.path.exists(path):
            return int(meta["w"]), int(meta["h"]), bpp, path
    tag = "16" if bpp == 16 else "32"
    best = None
    for f in glob.glob(f"{DIR}/renders/*.{tag}.gz"):
        try:
            rw, rh = map(int, os.path.basename(f).split(".")[0].split("x"))
        except ValueError:
            continue
        if rw > w or rh > h:
            continue
        score = (abs(rw / rh - w / h), -(rw * rh))
        if best is None or score < best[0]:
            best = (score, rw, rh, f)
    return (best[1], best[2], bpp, best[3]) if best else None


def read_picture(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        return f.read()


def fill_pixel(bpp):
    r, g, b = PAPER
    if bpp == 16:
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return bytes([v & 0xFF, v >> 8])
    return bytes([b, g, r, 0])


def overlay(img, rw, bpp, badge, bw, bh):
    """Put the "new postcards" tag in the top right corner of a picture."""
    Bpp = bpp // 8
    out = bytearray(img)
    margin = max(4, bh // 3)
    x = rw - bw - margin
    for row in range(bh):
        o = ((margin + row) * rw + x) * Bpp
        out[o:o + bw * Bpp] = badge[row * bw * Bpp:(row + 1) * bw * Bpp]
    return bytes(out)


def compose(img, rw, rh, w, h, bpp, stride, flip):
    """Center the picture on the screen (cropping if it's bigger), pad each
    row to the framebuffer's stride, and turn it 180 degrees if asked."""
    Bpp = bpp // 8
    if flip:
        a = array.array("H" if bpp == 16 else "I")
        a.frombytes(img)
        a.reverse()
        img = a.tobytes()
    px = fill_pixel(bpp)
    tail = bytes(stride - w * Bpp)
    blank_row = px * w + tail
    left, top = (w - rw) // 2, (h - rh) // 2
    sx, dx = max(0, -left), max(0, left)        # crop / pad sideways
    cw = min(rw - sx, w - dx)
    pad_l, pad_r = px * dx, px * (w - dx - cw) + tail
    out = bytearray()
    for y in range(h):
        iy = y - top
        if 0 <= iy < rh:
            o = (iy * rw + sx) * Bpp
            out += pad_l + img[o:o + cw * Bpp] + pad_r
        else:
            out += blank_row
    return bytes(out)


# ------------------------------------------------------------- the screen

class Screen:
    def __init__(self):
        self.screen = "today"
        self.offset = 0               # days away from today
        self.page = 0                 # phone book page
        self.card = 0                 # postcard, newest first
        self.pages = 1
        self.pressed = 0.0            # last button press
        self.turned = time.time()     # last phone book page change
        self.painted = None           # what's on screen now
        self.painted_at = 0.0
        self.cache = (None, None)
        seen = read_json(f"{DATA}/postcards-seen.json") or {}
        self.seen_ts = int(seen.get("ts", 0))
        cards = self.cards()
        self.newest = cards[0]["ts"] if cards else 0
        self.takeover = False         # a new postcard is waiting to be shown

    def cards(self):
        meta = read_json(f"{FRAMES}/cards.json") or {}
        return meta.get("cards") or []

    def mark_seen(self, cards):
        if cards and cards[0]["ts"] > self.seen_ts:
            self.seen_ts = cards[0]["ts"]
            try:
                write_json(f"{DATA}/postcards-seen.json", {"ts": self.seen_ts})
            except OSError:
                pass

    def press(self, action):
        now = time.time()
        self.pressed = now
        self.takeover = False
        if action == "switch":
            self.screen = SCREENS[(SCREENS.index(self.screen) + 1) % len(SCREENS)]
            self.offset, self.card = 0, 0
        else:
            by = -1 if action == "back" else 1
            if self.screen == "today":
                self.offset = max(-6, min(6, self.offset + by))
            elif self.screen == "book":
                self.page = (self.page + by) % self.pages
                self.turned = now
            else:
                self.card += by
        if self.screen == "postcards":
            self.mark_seen(self.cards())

    def want(self, now_hm, w, h, bpp):
        """Which picture to show, plus how many postcards are new."""
        cards = self.cards()
        unseen = sum(1 for c in cards if c["ts"] > self.seen_ts)
        if self.screen == "postcards":
            if not cards:
                return "card-empty", 0
            self.card %= len(cards)
            return "card-" + cards[self.card]["id"], 0
        if self.screen == "book":
            meta = read_json(f"{FRAMES}/book.json") or {}
            self.pages = max(1, len(meta.get("names") or [None]))
            self.page %= self.pages
            return f"book-{self.page}", unseen
        wd = time.localtime().tm_wday
        if self.offset == 0:
            return f"today-{wd}-{period(now_hm)}", unseen
        return f"day-{(wd + self.offset) % 7}", unseen

    def tick(self, settings, daytime, now_hm):
        now = time.time()
        cards = self.cards()
        if cards and cards[0]["ts"] > self.newest:     # a postcard just arrived
            self.newest = cards[0]["ts"]
            self.takeover = True
        if not daytime:
            set_blank(4)
            self.painted = None
            return
        if self.takeover:                               # show it now (or first thing in the morning)
            self.takeover = False
            self.screen, self.card = "postcards", 0
            self.pressed = now - IDLE_SECONDS + TAKEOVER_SECONDS
        if self.screen != "today" and now - self.pressed >= IDLE_SECONDS:
            self.screen, self.offset = "today", 0
        if self.screen == "today" and self.offset and now - self.pressed >= IDLE_SECONDS:
            self.offset = 0
        if (self.screen == "book" and self.pages > 1 and now - self.turned >= PAGE_SECONDS
                and now - self.pressed >= HOLD_SECONDS):
            self.page = (self.page + 1) % self.pages
            self.turned = now

        w, h = map(int, sysfs("virtual_size").split(","))
        bpp = int(sysfs("bits_per_pixel"))
        stride = int(sysfs("stride"))
        name, unseen = self.want(now_hm, w, h, bpp)
        src = frame_file(name)
        if not src or src[2] != bpp:
            src = frame_file("book-0") if self.screen != "book" else None
            if not src or src[2] != bpp:
                src = legacy_frame(w, h, bpp, self.page)
        if not src:
            return
        rw, rh, _, path = src
        badge = frame_file(f"badge-{min(unseen, 10)}") if unseen else None
        key = (path, mtime(path), w, h, bpp, stride, settings.flip, badge and badge[3])
        if key != self.cache[0]:
            img = read_picture(path)
            if badge and badge[2] == bpp:
                meta = read_json(f"{FRAMES}/cards.json") or {}
                bw, bh = (meta.get("badges") or {}).get(str(min(unseen, 10)), (0, 0))
                if bw and bw < rw and bh < rh // 3:
                    img = overlay(img, rw, bpp, read_picture(badge[3]), bw, bh)
            self.cache = (key, compose(img, rw, rh, w, h, bpp, stride, settings.flip))
            log(f"showing {os.path.basename(path)} on {w}x{h} {bpp}bpp" + (f", {unseen} new postcards" if unseen else ""))
        if key != self.painted or now - self.painted_at >= REFRESH_SECONDS:
            set_blank(0)
            with open(FBDEV, "wb") as fb:
                fb.write(self.cache[1])
            self.painted, self.painted_at = key, now


# ------------------------------------------------------------- keyboard

class Keys:
    """Every USB keyboard plugged in. Re-scanned so it can be plugged in later."""

    def __init__(self):
        self.devs = {}
        self.scanned = 0.0
        self.recent = (read_json(f"{DATA}/keys-seen.json") or {}).get("recent", [])

    def scan(self):
        if time.time() - self.scanned < 10:
            return
        self.scanned = time.time()
        for path in glob.glob("/dev/input/event*"):
            if path in self.devs:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError:
                pass
            self.devs[path] = fd
            log("listening for keys on", path)

    def remember(self, code):
        """So the admin page can show which codes her buttons send."""
        self.recent = [{"code": code, "at": int(time.time())}] + [k for k in self.recent if k["code"] != code]
        self.recent = self.recent[:8]
        try:
            write_json(f"{DATA}/keys-seen.json", {"recent": self.recent})
        except OSError:
            pass

    def wait(self, timeout):
        """Sleep up to timeout seconds; return the key codes pressed."""
        self.scan()
        if not self.devs:
            time.sleep(timeout)
            return []
        ready, _, _ = select.select(list(self.devs.values()), [], [], timeout)
        codes = []
        for fd in ready:
            try:
                data = os.read(fd, EVENT.size * 64)
            except BlockingIOError:
                continue
            except OSError:           # unplugged
                path = next(p for p, f in self.devs.items() if f == fd)
                del self.devs[path]
                os.close(fd)
                continue
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, typ, code, value = EVENT.unpack_from(data, i)
                if typ == EV_KEY and value == 1 and code not in MODIFIERS:
                    log("key", code)
                    self.remember(code)
                    codes.append(code)
        return codes


def main():
    screen, keys, settings = Screen(), Keys(), Settings()
    while True:
        try:
            settings.refresh()
            now_hm = time.strftime("%H:%M")
            screen.tick(settings, is_daytime(settings.on, settings.off, now_hm), now_hm)
        except Exception as e:
            log("error:", e)
        for code in keys.wait(2):
            screen.press(settings.action(code))


if __name__ == "__main__":
    main()

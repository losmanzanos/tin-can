#!/usr/bin/env python3
"""Shows Zelda's phone book full screen and turns the screen off at night.

Runs as root at boot (zelda-phone.service). It only copies pictures to the
framebuffer; the admin page draws them. Standard library only.

- Pictures: <data>/screen.json + screen-<n>.raw, uploaded from the admin page.
  Until the first upload it falls back to the old pre-drawn renders/ folder.
- On/off times and "flip upside down" come from <data>/contacts.json.
- More than one page: flips every PAGE_SECONDS. A USB keyboard (e.g. a little
  two-key macro pad) turns pages: back on BACK_KEYS, forward on anything else.
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
import time

ON_AT, OFF_AT = "08:00", "20:00"   # used when contacts.json has no times
PAGE_SECONDS = 20                  # auto-flip between pages
HOLD_SECONDS = 60                  # after a key press, wait this long before auto-flipping again
REFRESH_SECONDS = 30               # repaint even if nothing changed (in case the console drew over it)

DIR = os.path.dirname(os.path.abspath(__file__))
FB = os.environ.get("ZELDA_FB", "/sys/class/graphics/fb0")
FBDEV = os.environ.get("ZELDA_FBDEV", "/dev/fb0")
try:
    DATA = os.environ.get("ZELDA_DATA") or os.path.join(pwd.getpwuid(1000).pw_dir, "zelda-phone-data")
except KeyError:
    DATA = "/home/pi/zelda-phone-data"
PAPER = (0xFB, 0xF5, 0xE8)
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

# Linux key codes (linux/input-event-codes.h)
BACK_KEYS = {105, 103, 104, 14, 30}   # Left, Up, PageUp, Backspace, A
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


# ------------------------------------------------------------- settings

_settings = {"key": None, "value": (ON_AT, OFF_AT, False)}


def settings():
    path = f"{DATA}/contacts.json"
    key = mtime(path)
    if key != _settings["key"]:
        on, off, flip = ON_AT, OFF_AT, False
        try:
            with open(path) as f:
                s = json.load(f).get("screen") or {}
            on = s.get("on") if TIME_RE.match(str(s.get("on"))) else ON_AT
            off = s.get("off") if TIME_RE.match(str(s.get("off"))) else OFF_AT
            flip = bool(s.get("flip"))
        except (OSError, ValueError, AttributeError):
            pass
        _settings.update(key=key, value=(on, off, flip))
    return _settings["value"]


def is_daytime(on, off):
    now = time.strftime("%H:%M")
    if on == off:
        return True
    if on < off:                 # e.g. on 08:00, off 20:00
        return on <= now < off
    return now >= on or now < off  # e.g. on 20:00, off 08:00 (overnight)


# ------------------------------------------------------------- pictures

def uploaded(bpp):
    """The admin page's pictures, if they match the screen's pixel format."""
    try:
        with open(f"{DATA}/screen.json") as f:
            meta = json.load(f)
        w, h, pages = int(meta["w"]), int(meta["h"]), int(meta.get("pages", 1))
        if int(meta["bpp"]) != bpp or not 1 <= pages <= 3:
            return None
        files = [f"{DATA}/screen-{n}.raw" for n in range(pages)]
        if any(os.path.getsize(p) != w * h * bpp // 8 for p in files):
            return None
        return w, h, files
    except (OSError, ValueError, KeyError, TypeError):
        return None


def fallback(w, h, bpp):
    """The old pre-drawn renders: the closest shape that fits."""
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
    return (best[1], best[2], [best[3]]) if best else None


def read_picture(path):
    if path.endswith(".gz"):
        with gzip.open(path) as z:
            return z.read()
    with open(path, "rb") as f:
        return f.read()


def fill_pixel(bpp):
    r, g, b = PAPER
    if bpp == 16:
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return bytes([v & 0xFF, v >> 8])
    return bytes([b, g, r, 0])


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


class Screen:
    def __init__(self):
        self.page = 0
        self.pages = 1
        self.turned = time.time()     # last page change
        self.held = 0.0               # last key press
        self.painted = None           # what's on screen now
        self.painted_at = 0.0
        self.cache = {}

    def frame(self, flip):
        w, h = map(int, sysfs("virtual_size").split(","))
        bpp = int(sysfs("bits_per_pixel"))
        stride = int(sysfs("stride"))
        src = uploaded(bpp) or fallback(w, h, bpp)
        if not src:
            return None, None
        rw, rh, files = src
        self.pages = len(files)
        self.page %= self.pages
        path = files[self.page]
        key = (path, mtime(path), w, h, bpp, stride, flip)
        if key not in self.cache:
            # Forget pictures whose file has been replaced since.
            self.cache = {k: v for k, v in self.cache.items() if k[1] == mtime(k[0])}
            self.cache[key] = compose(read_picture(path), rw, rh, w, h, bpp, stride, flip)
            log(f"screen {w}x{h} {bpp}bpp, page {self.page + 1}/{self.pages} from {os.path.basename(path)}")
        return key, self.cache[key]

    def turn(self, by):
        self.page += by          # wrapped in frame()
        if self.page < 0:
            self.page += self.pages
        self.turned = time.time()

    def tick(self):
        on, off, flip = settings()
        if not is_daytime(on, off):
            set_blank(4)
            self.painted = None
            return
        now = time.time()
        if self.pages > 1 and now - self.turned >= PAGE_SECONDS and now - self.held >= HOLD_SECONDS:
            self.turn(1)
        key, data = self.frame(flip)
        if data is None:
            return
        if key != self.painted or now - self.painted_at >= REFRESH_SECONDS:
            set_blank(0)
            with open(FBDEV, "wb") as fb:
                fb.write(data)
            self.painted, self.painted_at = key, now


# ------------------------------------------------------------- keyboard

class Keys:
    """Every USB keyboard plugged in. Re-scanned so it can be plugged in later."""

    def __init__(self):
        self.devs = {}
        self.scanned = 0.0

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

    def wait(self, timeout):
        """Sleep up to timeout seconds; return +1/-1 for each page key pressed."""
        self.scan()
        if not self.devs:
            time.sleep(timeout)
            return []
        ready, _, _ = select.select(list(self.devs.values()), [], [], timeout)
        turns = []
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
                if typ == EV_KEY and value == 1:
                    log("key", code)
                    turns.append(-1 if code in BACK_KEYS else 1)
        return turns


def main():
    screen, keys = Screen(), Keys()
    while True:
        try:
            screen.tick()
        except Exception as e:
            log("error:", e)
        for by in keys.wait(2):
            screen.turn(by)
            screen.held = time.time()


if __name__ == "__main__":
    main()

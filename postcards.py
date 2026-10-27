"""Postcards for Zelda: drawing them for the screen, checking the email inbox,
and ntfy pushes. Used by server.py.

Postcards can arrive when nobody has a browser open (by email), so unlike the
phone book and the calendar, the Pi draws these itself. That needs Pillow
(`sudo apt-get install python3-pil`, which install.sh does). Everything else
here is standard library.
"""
import email
import email.policy
import gzip
import imaplib
import io
import json
import re
import threading
import time
import urllib.request
from email.utils import parseaddr
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:  # the server still runs; postcards just can't be drawn
    Image = None

FONT = Path(__file__).resolve().parent / "page" / "fonts" / "andika-latin-700-normal.ttf"
PAPER, INK, MUSTARD, CARD = (0xFB, 0xF5, 0xE8), (0x16, 0x18, 0x1F), (0xE9, 0xAD, 0x1C), (0xFF, 0xFD, 0xF7)
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_SIDE = 1600

# Andika has no emoji; drop anything it can't draw rather than show boxes.
NOT_IN_FONT = re.compile("[^ -ɏ‘-‟…–—]")


def available():
    return Image is not None


# ------------------------------------------------------------- pictures

def clean_photo(data):
    """Any photo in -> upright JPEG, at most MAX_SIDE px. Raises ValueError."""
    if not available():
        raise ValueError("python3-pil isn't installed on the Pi")
    try:
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        if im.mode != "RGB":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.convert("RGBA").split()[-1])
            im = bg
    except Exception as e:  # Pillow raises many kinds; HEIC is the usual one
        raise ValueError(f"couldn't read that picture ({e.__class__.__name__})")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=86)
    return out.getvalue()


def to_framebuffer(im, bpp):
    """RGB image -> the bytes /dev/fb0 wants (B,G,R,X or RGB565 little-endian)."""
    if bpp == 32:
        r, g, b = im.split()
        return Image.merge("RGBA", (b, g, r, Image.new("L", im.size, 0))).tobytes()
    # RGB565 without a slow per-pixel loop: build the low and high bytes as two
    # grayscale images (their bits never overlap, so adding is the same as OR),
    # then interleave them.
    from PIL import ImageChops
    r, g, b = im.split()
    low = ImageChops.add(g.point(lambda v: ((v >> 2) & 7) << 5), b.point(lambda v: v >> 3))
    high = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    return Image.merge("LA", (low, high)).tobytes()


def _font(px):
    return ImageFont.truetype(str(FONT), max(8, int(px)))


def _fit(draw, text, px, max_w):
    text = NOT_IN_FONT.sub("", text).strip()
    size = px
    while size > px * 0.6 and draw.textlength(text, font=_font(size)) > max_w:
        size -= px * 0.05
    f = _font(size)
    while text and draw.textlength(text + "…", font=f) > max_w and draw.textlength(text, font=f) > max_w:
        text = text[:-1]
    return text if draw.textlength(text, font=f) <= max_w else text + "…", f


def render_card(card, photo_path, w, h, bpp, index=0, total=1):
    """One postcard, full screen. Drawn at 2x and shrunk for smooth edges."""
    S = 2
    W, H = w * S, h * S
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    u = min(W, H) / 100  # layout unit
    d.rectangle((0, 0, int(W * 0.014), H), fill=MUSTARD)

    # The card, with the same hard ink shadow as the phone book.
    x0, y0, x1, y1 = int(W * 0.06), int(u * 5), int(W * 0.94), int(H - u * 16)
    sh, r = int(u * 1.2), int(u * 4)
    d.rounded_rectangle((x0 + sh, y0 + sh, x1 + sh, y1 + sh), r, fill=INK)
    d.rounded_rectangle((x0, y0, x1, y1), r, fill=CARD, outline=INK, width=int(u * 0.6))

    caption = (card.get("caption") or "").strip()
    pad = int(u * 3)
    ix0, iy0, ix1, iy1 = x0 + pad, y0 + pad, x1 - pad, y1 - pad
    if caption and photo_path:
        iy1 -= int(u * 11)
    if photo_path:
        ph = Image.open(photo_path).convert("RGB")
        ph.thumbnail((ix1 - ix0, iy1 - iy0), Image.LANCZOS)
        if ph.width < (ix1 - ix0) and ph.height < (iy1 - iy0):  # small photo: scale up
            k = min((ix1 - ix0) / ph.width, (iy1 - iy0) / ph.height)
            ph = ph.resize((int(ph.width * k), int(ph.height * k)), Image.LANCZOS)
        im.paste(ph, (ix0 + (ix1 - ix0 - ph.width) // 2, iy0 + (iy1 - iy0 - ph.height) // 2))
        if caption:
            t, f = _fit(d, caption, u * 7, ix1 - ix0)
            d.text(((ix0 + ix1) / 2, iy1 + u * 5.5), t, font=f, fill=INK, anchor="mm")
    elif caption:
        # A text-only message: big words on the card.
        words, lines, size = caption.split(), [], u * 12
        f = _font(size)
        while True:
            lines, line = [], ""
            for wd in words:
                test = (line + " " + wd).strip()
                if d.textlength(NOT_IN_FONT.sub("", test), font=f) <= ix1 - ix0 or not line:
                    line = test
                else:
                    lines.append(line)
                    line = wd
            lines.append(line)
            if len(lines) * size * 1.25 <= iy1 - iy0 or size < u * 5:
                break
            size *= 0.9
            f = _font(size)
        top = (iy0 + iy1) / 2 - len(lines) * size * 1.25 / 2 + size * 0.6
        for i, ln in enumerate(lines):
            d.text(((ix0 + ix1) / 2, top + i * size * 1.25), NOT_IN_FONT.sub("", ln), font=f, fill=INK, anchor="mm")

    # "From Nana" stamp and the day it came, under the card.
    who = "From " + (card.get("from") or "someone")
    t, f = _fit(d, who, u * 8, W * 0.5)
    tw = d.textlength(t, font=f)
    bx0, by0 = x0, int(y1 + u * 3.2)
    bx1, by1 = int(bx0 + tw + u * 6), int(by0 + u * 9.5)
    d.rounded_rectangle((bx0 + sh, by0 + sh, bx1 + sh, by1 + sh), int(u * 4.75), fill=INK)
    d.rounded_rectangle((bx0, by0, bx1, by1), int(u * 4.75), fill=MUSTARD)
    d.text(((bx0 + bx1) / 2, (by0 + by1) / 2), t, font=f, fill=INK, anchor="mm")
    day = DAYS[time.localtime(card.get("ts", time.time())).tm_wday]
    d.text((x1, (by0 + by1) / 2), day, font=_font(u * 5.5), fill=(0x8A, 0x81, 0x69), anchor="rm")

    # Page dots.
    if total > 1:
        dr, gap = u * 1.1, u * 3.4
        cx = (x0 + x1) / 2 - (min(total, 12) - 1) * gap / 2
        for i in range(min(total, 12)):
            fill = MUSTARD if i == min(index, 11) else (0xD8, 0xCD, 0xB4)
            d.ellipse((cx + i * gap - dr, (by0 + by1) / 2 - dr, cx + i * gap + dr, (by0 + by1) / 2 + dr), fill=fill)
    return to_framebuffer(im.resize((w, h), Image.LANCZOS), bpp)


def render_empty(w, h, bpp):
    return render_card({"caption": "No postcards yet. Ask Nana to send one!", "from": "the mailbox"}, None, w, h, bpp)


def render_badge(count, size, bpp):
    """The "2 new postcards" tag paint.py puts over the yellow-button hint on
    Today. size: the screen's shorter side."""
    S, u = 2, size / 100
    text = f"{count if count < 10 else '9+'} new postcard{'s' if count != 1 else ''}"
    f = _font(u * 5.2 * S)
    tw = ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=f)
    W, H = int(tw + u * 15 * S), int(u * 12 * S)
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, W - 1, H - 1), H // 2, fill=INK)
    # envelope
    ex0, ey0 = int(u * 3 * S), int(H * 0.28)
    ex1, ey1 = int(ex0 + u * 6 * S), int(H * 0.72)
    d.rectangle((ex0, ey0, ex1, ey1), fill=MUSTARD)
    d.line((ex0, ey0, (ex0 + ex1) // 2, (ey0 + ey1) // 2, ex1, ey0), fill=INK, width=max(2, int(u * 0.5 * S)))
    d.text((ex1 + u * 2.5 * S, H / 2), text, font=f, fill=(255, 255, 255), anchor="lm")
    im = im.resize((W // S, H // S), Image.LANCZOS)
    return im.width, im.height, to_framebuffer(im, bpp)


def gz(data):
    return gzip.compress(data, compresslevel=3)


# ------------------------------------------------------------- ntfy

def notify(cfg, title, message, click=""):
    """Push to the ntfy topic, in the background. Never raises."""
    topic = (cfg or {}).get("topic")
    if not topic:
        return
    server = ((cfg or {}).get("server") or "https://ntfy.sh").rstrip("/")

    def send():
        headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Tags": "postbox"}
        if click:
            headers["Click"] = click
        req = urllib.request.Request(f"{server}/{topic}", data=message.encode(), headers=headers, method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            print("ntfy failed:", e, flush=True)

    threading.Thread(target=send, daemon=True).start()


# ------------------------------------------------------------- email

def digits(s):
    return re.sub(r"\D", "", s or "")


def match_sender(addr, people):
    """Find the contact who sent this. Matches their email, or their phone
    number for picture texts sent to email (e.g. 5551234567@vzwpix.com)."""
    addr = (addr or "").strip().lower()
    local = addr.split("@")[0]
    for p in people:
        if p.get("email") and p["email"].strip().lower() == addr:
            return p
        ph = digits(p.get("phone"))[-10:]
        if len(ph) >= 10 and digits(local)[-10:] == ph:
            return p
    return None


def authenticated(msg):
    """Did the receiving mail server check the sender is real (SPF/DKIM/DMARC)?"""
    ar = " ".join(str(v) for v in msg.get_all("Authentication-Results", [])).lower()
    return "dmarc=pass" in ar or "dkim=pass" in ar or "spf=pass" in ar


def read_message(raw):
    """-> (sender address, caption, [image bytes])"""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(msg.get("From", ""))[1]
    images, caption = [], ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype.startswith("image/"):
            data = part.get_payload(decode=True)
            if data:
                images.append(data)
        elif ctype == "text/plain" and not caption and part.get_content_disposition() != "attachment":
            try:
                text = part.get_content()
            except Exception:
                text = ""
            # Keep the first real line; carriers add footers further down.
            for line in text.splitlines():
                line = line.strip()
                if line and not line.lower().startswith(("sent from", "this message", "--")):
                    caption = line[:120]
                    break
    if not caption:
        subj = str(msg.get("Subject", "")).strip()
        if subj and subj.lower() not in ("", "multimedia message", "mms", "(no subject)"):
            caption = subj[:120]
    return sender, caption, images, authenticated(msg)


def check_inbox(cfg, on_message):
    """Fetch unread mail once. on_message(sender, caption, images, authed)."""
    m = imaplib.IMAP4_SSL(cfg.get("host") or "imap.gmail.com", timeout=30)
    try:
        m.login(cfg["user"], cfg["password"])
        m.select("INBOX")
        _, ids = m.search(None, "UNSEEN")
        for num in (ids[0].split() if ids and ids[0] else [])[:20]:
            _, data = m.fetch(num, "(BODY.PEEK[])")
            raw = next((x[1] for x in data if isinstance(x, tuple)), None)
            if raw:
                try:
                    on_message(*read_message(raw))
                except Exception as e:
                    print("postcard email failed:", e, flush=True)
            m.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            m.logout()
        except Exception:
            pass


def test_login(cfg):
    m = imaplib.IMAP4_SSL(cfg.get("host") or "imap.gmail.com", timeout=20)
    try:
        m.login(cfg["user"], cfg["password"])
    finally:
        try:
            m.logout()
        except Exception:
            pass


class InboxPoller(threading.Thread):
    """Checks the postcard inbox every couple of minutes. The Pi reaches out;
    nothing on the internet ever connects to it."""

    def __init__(self, get_cfg, on_message, every=120):
        super().__init__(daemon=True)
        self.get_cfg, self.on_message, self.every = get_cfg, on_message, every
        self.status = {"ok": None, "at": 0, "error": ""}
        self.wake = threading.Event()

    def run(self):
        while True:
            cfg = self.get_cfg()
            if cfg and cfg.get("user") and cfg.get("password"):
                try:
                    check_inbox(cfg, self.on_message)
                    self.status = {"ok": True, "at": time.time(), "error": ""}
                except Exception as e:
                    self.status = {"ok": False, "at": time.time(), "error": str(e)[:200]}
                    print("inbox check failed:", e, flush=True)
            self.wake.wait(self.every)
            self.wake.clear()


def dumps(o):
    return json.dumps(o, indent=1).encode()

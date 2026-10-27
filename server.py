#!/usr/bin/env python3
"""Zelda's screen: phone book, picture calendar and postcards, plus the admin
page and a small JSON API.

Python 3 standard library, plus Pillow (python3-pil from apt) for drawing
postcards that arrive by email. Runs fine on a Pi Zero W.

  python3 server.py serve [--port 8080] [--data ~/zelda-phone-data]
  python3 server.py init  [--number "(555) 555-0100"]   seed data if missing
  python3 server.py set-pin                             set the admin PIN

All the family data (contacts.json, photos, postcards, the PIN hash, email and
ntfy settings) lives in the data directory, outside the git checkout, so
`git pull` never touches it.
"""
import argparse
import getpass
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import postcards as pc

APP = Path(__file__).resolve().parent
PAGE = APP / "page"
DEFAULT_DATA = Path(os.environ.get("ZELDA_DATA", Path.home() / "zelda-phone-data"))

MAX_PEOPLE = 21  # 7 per page; the screen flips through up to 3 pages
MAX_TILES = 60
MAX_CARDS = 30   # postcards kept; older ones are let go
CARDS_LOOK = 2   # bump when postcards.py draws differently, so the Pi redraws them
MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_POSTCARD_BYTES = 6 * 1024 * 1024
MAX_FRAME_BYTES = 4096 * 4096 * 4
FB = Path(os.environ.get("ZELDA_FB", "/sys/class/graphics/fb0"))
PADS = ("bolt", "star", "smile", "lips")
COOKIE = "zp_session"
SESSION_DAYS = 60
PIN_ITERATIONS = 60_000  # a Pi Zero takes about half a second for this

# The original hard-coded list. Zelda's number is never in here: it is passed
# to `init` at install time and only ever stored in the data directory.
SEED_PEOPLE = [
    {"name": "Mom",     "pad": "star",  "tone": "#5c8a6c"},
    {"name": "Dad",     "pad": "bolt",  "tone": "#2f4f8f"},
    {"name": "Grandma", "pad": "smile", "tone": "#a3473a"},
    {"name": "Grandpa", "code": "56",   "tone": "#6b6f7c"},
    {"name": "Nana",    "code": "44",   "tone": "#b7792f"},
    {"name": "Aunt Vi", "pad": "lips",  "tone": "#9a6b4b", "letter": "V"},
    {"name": "Chris",   "code": "77",   "tone": "#4d7a86"},
]

# Starter tiles, after the magnets on her wooden calendar. Swap any of them for
# a photo of the real magnet from the admin page.
SEED_TILES = [
    ("school", "School", "emoji", "🎒"), ("playdate", "Playdate", "emoji", "👫"),
    ("art", "Art", "emoji", "🎨"), ("bike", "Bike ride", "emoji", "🚲"),
    ("home", "Home", "emoji", "🏠"), ("icecream", "Ice cream", "emoji", "🍦"),
    ("movie", "Movie night", "emoji", "🍿"), ("dance", "Dance", "emoji", "💃"),
    ("swim", "Swimming", "emoji", "🏊"), ("special", "Something special", "emoji", "⭐"),
    ("park", "Park", "emoji", "🌳"), ("grandma", "Grandma's", "emoji", "👵"),
    ("bath", "Bath", "emoji", "🛁"), ("bed", "Bedtime", "emoji", "🌙"),
    ("face-green", "Green face", "face", "#5aa64b"), ("face-blue", "Blue face", "face", "#2b8fb0"),
    ("face-orange", "Orange face", "face", "#ec7a2a"), ("blank", "Free time", "blank", ""),
]
# Monday first; morning, midday, evening. Copied from the photo of her board.
SEED_WEEK = [
    [["school"], [], []],
    [["school"], ["face-green", "face-blue"], ["movie"]],
    [["playdate"], ["blank"], ["art"]],
    [["school"], ["bike"], ["home"]],
    [["school"], ["art"], ["icecream"]],
    [["dance"], ["face-orange"], ["blank"]],
    [["bike"], ["swim"], ["special"]],
]

TONE_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CODE_RE = re.compile(r"^[0-9]{2}$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
PHOTO_RE = re.compile(r"^[0-9a-f]{20}\.jpg$")
ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FRAME_GROUPS = {"book": re.compile(r"^book-[0-2]$"), "days": re.compile(r"^(today-[0-6]-[0-2]|day-[0-6])$")}

# Changes every time the server starts, so the display reloads itself after an
# update and picks up new page code, not just new data.
BOOT = secrets.token_hex(4)


class BadRequest(Exception):
    pass


# ---------------------------------------------------------------- storage

PRIVATE = ("pin.json", "session.key", "secrets.json")


def write_atomic(path, data):
    """Write to a temp file in the same directory, then rename over the target."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600 if path.name in PRIVATE else 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_json(path, default=None):
    try:
        with open(path, "rb") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def new_id():
    return secrets.token_hex(4)


def seed_tiles():
    out = []
    for tid, label, kind, art in SEED_TILES:
        t = {"id": tid, "label": label, "kind": kind}
        if kind == "emoji":
            t["emoji"] = art
        elif kind == "face":
            t["color"] = art
        out.append(t)
    return out


def with_defaults(doc):
    """Fill in anything an older contacts.json doesn't have yet."""
    doc.setdefault("people", [])
    doc.setdefault("number", "")
    doc.setdefault("screen", {})
    doc["screen"].setdefault("on", "08:00")
    doc["screen"].setdefault("off", "20:00")
    doc["screen"].setdefault("flip", False)
    if "tiles" not in doc:
        doc["tiles"] = seed_tiles()
        doc["week"] = json.loads(json.dumps(SEED_WEEK))
    doc.setdefault("week", [[[], [], []] for _ in range(7)])
    doc.setdefault("keys", {"back": [], "next": [], "switch": []})
    doc.setdefault("postcards", {"approve": False})
    return doc


def seed_doc(number=""):
    people = [{"id": new_id(), "photo": "", "letter": "", **p} for p in SEED_PEOPLE]
    return with_defaults({"rev": 1, "people": people, "number": number})


def clean_doc(doc):
    """Validate a document from the admin page. Returns a normalised copy."""
    if not isinstance(doc, dict):
        raise BadRequest("expected an object")
    people = doc.get("people")
    if not isinstance(people, list):
        raise BadRequest("people must be a list")
    if len(people) > MAX_PEOPLE:
        raise BadRequest(f"at most {MAX_PEOPLE} contacts (3 pages of 7)")
    out, pads, codes, ids = [], set(), set(), set()
    for p in people:
        if not isinstance(p, dict):
            raise BadRequest("bad contact")
        name = str(p.get("name", "")).strip()
        if not name or len(name) > 24:
            raise BadRequest("each contact needs a name (24 characters max)")
        tone = str(p.get("tone", ""))
        if not TONE_RE.match(tone):
            raise BadRequest(f"{name}: color must look like #a3473a")
        letter = str(p.get("letter", "") or "").strip()
        if len(letter) > 2:
            raise BadRequest(f"{name}: letter is 1 or 2 characters")
        photo = str(p.get("photo", "") or "")
        if photo and not PHOTO_RE.match(photo):
            raise BadRequest(f"{name}: bad photo name")
        mail = str(p.get("email", "") or "").strip()
        if mail and (len(mail) > 120 or not EMAIL_RE.match(mail)):
            raise BadRequest(f"{name}: that email address doesn't look right")
        phone = str(p.get("phone", "") or "").strip()
        if phone and (len(phone) > 30 or len(pc.digits(phone)) < 10):
            raise BadRequest(f"{name}: phone number needs at least 10 digits")
        pid = str(p.get("id", "") or "") or new_id()
        if not ID_RE.match(pid) or pid in ids:
            pid = new_id()
        ids.add(pid)
        c = {"id": pid, "name": name, "tone": tone.lower(), "letter": letter, "photo": photo,
             "email": mail, "phone": phone}
        pad, code = p.get("pad"), p.get("code")
        if pad:
            if pad not in PADS:
                raise BadRequest(f"{name}: unknown speed-dial button")
            if pad in pads:
                raise BadRequest(f"two contacts can't share the {pad} button")
            pads.add(pad)
            c["pad"] = pad
        elif code is not None and code != "":
            code = str(code)
            if not CODE_RE.match(code):
                raise BadRequest(f"{name}: code must be two digits")
            if code in codes:
                raise BadRequest(f"two contacts can't share code {code}")
            codes.add(code)
            c["code"] = code
        else:
            raise BadRequest(f"{name}: pick a button or a two-digit code")
        out.append(c)

    number = str(doc.get("number", "") or "").strip()
    if len(number) > 30:
        raise BadRequest("phone number is too long")
    screen = doc.get("screen") or {}
    on, off = str(screen.get("on", "08:00")), str(screen.get("off", "20:00"))
    if not TIME_RE.match(on) or not TIME_RE.match(off):
        raise BadRequest("screen times must be HH:MM")

    # Calendar tiles and the week.
    tiles, tile_ids = [], set()
    for t in (doc.get("tiles") or [])[:MAX_TILES]:
        if not isinstance(t, dict):
            raise BadRequest("bad tile")
        tid = str(t.get("id", "")) or new_id()
        if not ID_RE.match(tid) or tid in tile_ids:
            tid = new_id()
        label = str(t.get("label", "")).strip()[:24]
        kind = t.get("kind")
        c = {"id": tid, "label": label, "kind": kind}
        if kind == "emoji":
            c["emoji"] = str(t.get("emoji", "")).strip()[:16]
            if not c["emoji"]:
                raise BadRequest(f"{label or 'a tile'}: pick an emoji")
        elif kind == "face":
            if not TONE_RE.match(str(t.get("color", ""))):
                raise BadRequest(f"{label or 'a tile'}: bad face color")
            c["color"] = t["color"].lower()
        elif kind == "photo":
            if not PHOTO_RE.match(str(t.get("photo", ""))):
                raise BadRequest(f"{label or 'a tile'}: add a photo")
            c["photo"] = t["photo"]
        elif kind != "blank":
            raise BadRequest("unknown tile type")
        tile_ids.add(tid)
        tiles.append(c)
    week = doc.get("week") or []
    if not isinstance(week, list) or len(week) != 7:
        raise BadRequest("the week needs 7 days")
    clean_week = []
    for day in week:
        if not isinstance(day, list) or len(day) != 3:
            raise BadRequest("each day needs morning, midday and evening")
        clean_week.append([[str(x) for x in (slot or []) if str(x) in tile_ids][:2] for slot in day])

    keys = doc.get("keys") or {}
    clean_keys = {}
    for k in ("back", "next", "switch"):
        vals = keys.get(k) or []
        clean_keys[k] = sorted({int(v) for v in vals if str(v).isdigit() and 0 < int(v) < 768})[:8]

    home = screen.get("home") if screen.get("home") in ("today", "book") else "today"
    return {"people": out, "number": number,
            "screen": {"on": on, "off": off, "flip": bool(screen.get("flip", False)), "home": home},
            "tiles": tiles, "week": clean_week, "keys": clean_keys,
            "postcards": {"approve": bool((doc.get("postcards") or {}).get("approve", False))}}


def public_doc(doc):
    """What the display and Zelda's view need: no family emails or phone numbers."""
    doc = dict(doc)
    doc["people"] = [{k: v for k, v in p.items() if k not in ("email", "phone")} for p in doc.get("people", [])]
    doc.pop("keys", None)
    return doc


def fb_geometry():
    """Size and pixel format of the Pi's screen. Falls back to a common small
    HDMI panel when there is no framebuffer (e.g. testing on a laptop)."""
    try:
        w, h = map(int, (FB / "virtual_size").read_text().strip().split(","))
        bpp = int((FB / "bits_per_pixel").read_text().strip())
        if w > 0 and h > 0 and bpp in (16, 32):
            return {"w": w, "h": h, "bpp": bpp, "real": True}
    except (OSError, ValueError):
        pass
    return {"w": 1024, "h": 600, "bpp": 32, "real": False}


class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir).expanduser()
        self.photos = self.dir / "photos"
        self.frames = self.dir / "frames"
        self.cards_dir = self.dir / "postcards"
        self.contacts = self.dir / "contacts.json"
        self.cards_file = self.dir / "postcards.json"
        self.secrets_file = self.dir / "secrets.json"
        self.pin_file = self.dir / "pin.json"
        self.key_file = self.dir / "session.key"
        self.lock = threading.Lock()        # contacts.json and frames
        self.cards_lock = threading.RLock()  # postcards.json and card frames
        self.render_lock = threading.Lock()
        self.cards_rev = 0

    def ensure(self, number=""):
        for d in (self.dir, self.photos, self.frames, self.cards_dir):
            d.mkdir(parents=True, exist_ok=True)
        created = False
        if not self.contacts.exists():
            write_atomic(self.contacts, json.dumps(seed_doc(number), indent=2).encode())
            created = True
        if not self.key_file.exists():
            write_atomic(self.key_file, secrets.token_bytes(32))
        return created

    def load(self):
        with open(self.contacts, "rb") as f:
            return with_defaults(json.load(f))

    def save(self, doc, expect_rev=None):
        with self.lock:
            cur = self.load()
            rev = int(cur.get("rev", 0))
            if expect_rev is not None and int(expect_rev) != rev:
                return None
            doc = dict(doc, rev=rev + 1)
            write_atomic(self.contacts, json.dumps(doc, indent=2).encode())
            self.prune_photos(doc)
            return doc

    def prune_photos(self, doc):
        """Delete photos nothing uses. Leaves fresh uploads alone for a day
        in case someone is still filling in the form."""
        used = {p.get("photo") for p in doc["people"]} | {t.get("photo") for t in doc.get("tiles", [])}
        cutoff = time.time() - 86400
        for f in self.photos.glob("*.jpg"):
            if f.name not in used and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass

    # -- screens drawn by the admin page: the phone book and the calendar.
    # Each group is a set of gzipped framebuffer pictures plus <group>.json,
    # written last, which is what paint.py goes by.
    def frames_info(self):
        return {g: read_json(self.frames / f"{g}.json") for g in FRAME_GROUPS}

    def save_frame(self, group, name, data, gzipped):
        if gzipped:
            write_atomic(self.frames / f"{group}-staging-{name}.raw.gz", data)
        else:
            write_atomic(self.frames / f"{group}-staging-{name}.raw.gz", gzip.compress(data, 3))

    def commit_frames(self, group, meta):
        """Swap the staged pictures in, then write the group's json."""
        with self.lock:
            for name in meta["names"]:
                staged = self.frames / f"{group}-staging-{name}.raw.gz"
                if not staged.exists():
                    raise BadRequest(f"picture {name} is missing")
            for name in meta["names"]:
                os.replace(self.frames / f"{group}-staging-{name}.raw.gz", self.frames / f"{name}.raw.gz")
            write_atomic(self.frames / f"{group}.json", json.dumps(meta).encode())
            for f in self.frames.glob("*.raw.gz"):
                n = f.name[:-len(".raw.gz")]
                if FRAME_GROUPS[group].match(n) and n not in meta["names"]:
                    f.unlink()

    # -- secrets: email inbox and ntfy. Never sent back in full.
    def secrets(self):
        s = read_json(self.secrets_file, {}) or {}
        s.setdefault("imap", {})
        s.setdefault("ntfy", {})
        s.setdefault("viewUrl", f"http://{socket.gethostname()}:8080/")
        return s

    def save_secrets(self, s):
        write_atomic(self.secrets_file, json.dumps(s, indent=1).encode())

    # -- postcards
    def cards(self):
        return read_json(self.cards_file, None) or {"cards": [], "ignored": []}

    def save_cards(self, data):
        write_atomic(self.cards_file, json.dumps(data, indent=1).encode())
        self.cards_rev += 1

    def add_card(self, sender, caption, jpeg, via, status):
        cid = secrets.token_hex(6)
        with self.cards_lock:
            if jpeg:
                write_atomic(self.cards_dir / f"{cid}.jpg", jpeg)
            data = self.cards()
            data["cards"].insert(0, {"id": cid, "ts": int(time.time()), "from": sender[:40],
                                     "caption": (caption or "")[:120], "photo": bool(jpeg),
                                     "via": via, "status": status})
            for old in data["cards"][MAX_CARDS:]:
                self._forget_card(old["id"])
            data["cards"] = data["cards"][:MAX_CARDS]
            self.save_cards(data)
        return cid

    def _forget_card(self, cid):
        for f in (self.cards_dir / f"{cid}.jpg", self.frames / f"card-{cid}.raw.gz"):
            try:
                f.unlink()
            except FileNotFoundError:
                pass

    def update_card(self, cid, status=None, delete=False):
        with self.cards_lock:
            data = self.cards()
            for c in data["cards"]:
                if c["id"] == cid:
                    if delete:
                        data["cards"].remove(c)
                        self._forget_card(cid)
                    elif status:
                        c["status"] = status
                    self.save_cards(data)
                    return c
        return None

    def note_ignored(self, sender, why):
        with self.cards_lock:
            data = self.cards()
            data["ignored"] = ([{"from": sender[:80], "why": why, "ts": int(time.time())}] + data.get("ignored", []))[:10]
            self.save_cards(data)

    def render_cards(self):
        """Draw any postcard the screen doesn't have a picture of yet, then
        write frames/cards.json for paint.py. Redraws everything if the screen
        size changed."""
        if not pc.available():
            return
        with self.render_lock:
            fb = fb_geometry()
            geo = [fb["w"], fb["h"], fb["bpp"]]
            meta = read_json(self.frames / "cards.json", {}) or {}
            fresh = meta.get("geometry") != geo or meta.get("v") != CARDS_LOOK  # new look: redraw all
            with self.cards_lock:
                shown = [c for c in self.cards()["cards"] if c["status"] == "shown"]
            for c in shown:
                path = self.frames / f"card-{c['id']}.raw.gz"
                if fresh or not path.exists():
                    photo = self.cards_dir / f"{c['id']}.jpg"
                    try:
                        data = pc.render_card(c, photo if c.get("photo") and photo.exists() else None, *geo)
                        write_atomic(path, pc.gz(data))
                    except Exception as e:
                        print("couldn't draw postcard", c["id"], e, flush=True)
            badges = meta.get("badges") or {}
            if fresh or not (self.frames / "card-empty.raw.gz").exists():
                write_atomic(self.frames / "card-empty.raw.gz", pc.gz(pc.render_empty(*geo)))
                badges = {}
                for n in range(1, 11):
                    bw, bh, data = pc.render_badge(n, min(fb["w"], fb["h"]), fb["bpp"])
                    write_atomic(self.frames / f"badge-{n}.raw.gz", pc.gz(data))
                    badges[str(n)] = [bw, bh]
            cards = [{"id": c["id"], "ts": c["ts"], "from": c["from"]} for c in shown
                     if (self.frames / f"card-{c['id']}.raw.gz").exists()]
            write_atomic(self.frames / "cards.json", json.dumps(
                {"w": fb["w"], "h": fb["h"], "bpp": fb["bpp"], "geometry": geo, "v": CARDS_LOOK,
                 "cards": cards, "badges": badges}).encode())

    def render_cards_soon(self):
        threading.Thread(target=self.render_cards, daemon=True).start()

    # -- PIN and sessions
    def has_pin(self):
        return self.pin_file.exists()

    def set_pin(self, pin):
        salt = secrets.token_bytes(16)
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, PIN_ITERATIONS)
        rec = {"alg": "pbkdf2_sha256", "iter": PIN_ITERATIONS, "salt": salt.hex(), "hash": h.hex()}
        self.dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.pin_file, json.dumps(rec).encode())
        # A new PIN logs out every phone that was signed in with the old one.
        write_atomic(self.key_file, secrets.token_bytes(32))

    def check_pin(self, pin):
        try:
            rec = json.loads(self.pin_file.read_text())
        except (OSError, ValueError):
            return False
        h = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(rec["salt"]), int(rec["iter"]))
        return hmac.compare_digest(h.hex(), rec["hash"])

    def _key(self):
        return self.key_file.read_bytes()

    def make_session(self):
        exp = str(int(time.time()) + SESSION_DAYS * 86400)
        sig = hmac.new(self._key(), exp.encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{sig}"

    def check_session(self, token):
        if not token or "." not in token:
            return False
        exp, sig = token.split(".", 1)
        if not exp.isdigit() or int(exp) < time.time():
            return False
        try:
            good = hmac.new(self._key(), exp.encode(), hashlib.sha256).hexdigest()
        except OSError:
            return False
        return hmac.compare_digest(good, sig)


# ---------------------------------------------------------------- postcards in

class Mailroom:
    """Where postcards come in: the admin page and the email inbox."""

    def __init__(self, store):
        self.store = store
        self.poller = pc.InboxPoller(lambda: store.secrets().get("imap"), self.from_email)

    def accept(self, sender, caption, jpeg, via, trusted):
        doc = self.store.load()
        # Parents sending from the admin page never need approving.
        approve_first = via != "admin" and (doc["postcards"]["approve"] or not trusted)
        status = "pending" if approve_first else "shown"
        cid = self.store.add_card(sender, caption, jpeg, via, status)
        self.store.render_cards_soon()
        s = self.store.secrets()
        url = s.get("viewUrl", "")
        if status == "pending":
            pc.notify(s.get("ntfy"), f"Postcard for Zelda from {sender}",
                      "Waiting for your OK before it goes on her screen.", url.rstrip("/") + "/admin#postcards")
        elif via != "admin":
            pc.notify(s.get("ntfy"), f"New postcard for Zelda from {sender}", "Tap to see it.", url + "#postcards")
        return cid, status

    def from_email(self, sender, caption, images, authed):
        person = pc.match_sender(sender, self.store.load()["people"])
        if not person:
            self.store.note_ignored(sender, "not in the phone book")
            return
        if not images and not caption:
            return
        for raw in images[:4] or [None]:
            jpeg = None
            if raw:
                try:
                    jpeg = pc.clean_photo(raw)
                except ValueError as e:
                    self.store.note_ignored(sender, str(e))
                    continue
            self.accept(person["name"], caption, jpeg, "email", trusted=authed)


# ---------------------------------------------------------------- http

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".woff2": "font/woff2",
                ".jpg": "image/jpeg", ".css": "text/css", ".js": "text/javascript",
                ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json"}


class Failures:
    """Slow down PIN guessing: after 5 wrong tries, wait a minute."""
    def __init__(self):
        self.count, self.until, self.lock = 0, 0.0, threading.Lock()


def make_handler(store, mailroom):
    failures = Failures()

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZeldaPhone/2"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            # Keep the journal quiet: the views poll every 10 seconds.
            if self.path.startswith("/api/version"):
                return
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        # helpers
        def send(self, status, body=b"", ctype="application/json", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def error(self, status, msg):
            self.send(status, {"error": msg})

        def file(self, path, cache=False):
            try:
                data = path.read_bytes()
            except OSError:
                return self.error(HTTPStatus.NOT_FOUND, "not found")
            ctype = STATIC_TYPES.get(path.suffix, "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def body(self, limit):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise BadRequest("too big")
            return self.rfile.read(n)

        def json_body(self):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise BadRequest("expected JSON")
            try:
                return json.loads(self.body(512 * 1024) or b"null")
            except ValueError:
                raise BadRequest("bad JSON")

        def authed(self):
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return COOKIE in c and store.check_session(c[COOKIE].value)

        def board_ok(self):
            """The touchscreen magnet board: a key that can only move tiles
            around the week (and redraw the calendar for the screen)."""
            key = self.headers.get("X-Board-Key") or ""
            good = store.secrets().get("board") or ""
            return bool(key and good and hmac.compare_digest(key, good))

        def same_origin(self):
            # Belt and braces next to the SameSite cookie: refuse writes that a
            # page on some other site sent.
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlsplit(origin).netloc == (self.headers.get("Host") or "")

        def admin_state(self):
            s = store.secrets()
            cards = store.cards()
            return {
                "doc": store.load(),
                "fb": fb_geometry(),
                "frames": store.frames_info(),
                "postcards": cards["cards"],
                "ignored": cards.get("ignored", []),
                "keysSeen": read_json(store.dir / "keys-seen.json", {}) or {},
                "pil": pc.available(),
                "settings": {
                    "imap": {"host": s["imap"].get("host", "imap.gmail.com"), "user": s["imap"].get("user", ""),
                             "passwordSet": bool(s["imap"].get("password"))},
                    "ntfy": {"server": s["ntfy"].get("server", "https://ntfy.sh"), "topic": s["ntfy"].get("topic", "")},
                    "viewUrl": s["viewUrl"],
                    "inbox": mailroom.poller.status,
                    "board": s.get("board", ""),
                },
            }

        # routes
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                return self.file(PAGE / "index.html")
            if path in ("/admin", "/admin/"):
                return self.file(PAGE / ("admin.html" if self.authed() else "login.html"))
            if path in ("/board", "/board/"):
                return self.file(PAGE / "board.html")
            if path == "/api/board":
                if not (self.board_ok() or self.authed()):
                    return self.error(HTTPStatus.UNAUTHORIZED, "This board's link is out of date. Make a new one on the admin page.")
                return self.send(HTTPStatus.OK, {"fb": fb_geometry(), "days": store.frames_info()["days"]})
            if path == "/manifest.json":
                return self.file(PAGE / "manifest.json")
            if path == "/api/version":
                rev = store.load().get("rev", 0)
                return self.send(HTTPStatus.OK, {"version": f"{BOOT}-{rev}-{store.cards_rev}", "boot": BOOT,
                                                 "rev": rev, "cards": store.cards_rev})
            if path == "/api/contacts":
                return self.send(HTTPStatus.OK, public_doc(store.load()))
            if path == "/api/postcards":
                shown = [{k: c[k] for k in ("id", "ts", "from", "caption", "photo")}
                         for c in store.cards()["cards"] if c["status"] == "shown"]
                return self.send(HTTPStatus.OK, shown)
            if path == "/api/session":
                return self.send(HTTPStatus.OK, {"authed": bool(self.authed()), "hasPin": store.has_pin()})
            if path == "/api/admin":
                if not self.authed():
                    return self.error(HTTPStatus.UNAUTHORIZED, "Please log in")
                return self.send(HTTPStatus.OK, self.admin_state())
            if path == "/render.js":
                return self.file(PAGE / "render.js")
            if path.startswith("/photos/"):
                name = path[len("/photos/"):]
                if PHOTO_RE.match(name):
                    return self.file(store.photos / name, cache=True)
            if path.startswith("/postcards/"):
                m = re.match(r"^/postcards/([0-9a-f]{12})\.jpg$", path)
                if m:
                    c = next((c for c in store.cards()["cards"] if c["id"] == m.group(1)), None)
                    # Pending postcards are only for the grown-ups until approved.
                    if c and (c["status"] == "shown" or self.authed()):
                        return self.file(store.cards_dir / f"{c['id']}.jpg", cache=True)
            if path.startswith("/fonts/"):
                name = path[len("/fonts/"):]
                if re.match(r"^[a-z0-9-]+\.woff2$", name):
                    return self.file(PAGE / "fonts" / name, cache=True)
            if path.startswith("/icons/"):
                name = path[len("/icons/"):]
                if re.match(r"^[a-z0-9-]+\.(png|svg)$", name):
                    return self.file(PAGE / "icons" / name, cache=True)
            self.error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self):
            try:
                self.route_write()
            except BadRequest as e:
                self.error(HTTPStatus.BAD_REQUEST, str(e))

        def do_PUT(self):
            self.do_POST()

        def route_write(self):
            path = urlsplit(self.path).path
            if not self.same_origin():
                return self.error(HTTPStatus.FORBIDDEN, "wrong origin")

            if path == "/api/login" and self.command == "POST":
                with failures.lock:
                    wait = failures.until - time.time()
                if wait > 0:
                    return self.error(HTTPStatus.TOO_MANY_REQUESTS, f"Too many tries. Wait {int(wait) + 1} seconds.")
                pin = str((self.json_body() or {}).get("pin", ""))
                if not store.has_pin():
                    return self.error(HTTPStatus.FORBIDDEN, "No PIN is set yet. Run install.sh or `server.py set-pin` on the Pi.")
                if not store.check_pin(pin):
                    with failures.lock:
                        failures.count += 1
                        if failures.count >= 5:
                            failures.count, failures.until = 0, time.time() + 60
                    return self.error(HTTPStatus.UNAUTHORIZED, "Wrong PIN")
                with failures.lock:
                    failures.count = 0
                cookie = (f"{COOKIE}={store.make_session()}; Path=/; HttpOnly; SameSite=Strict; "
                          f"Max-Age={SESSION_DAYS * 86400}")
                return self.send(HTTPStatus.OK, {"ok": True}, headers={"Set-Cookie": cookie})

            if path == "/api/logout" and self.command == "POST":
                return self.send(HTTPStatus.OK, {"ok": True},
                                 headers={"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; SameSite=Strict"})

            board_paths = path == "/api/board/week" or path.startswith("/api/frames/days/")
            if not (self.authed() or (board_paths and self.board_ok())):
                return self.error(HTTPStatus.UNAUTHORIZED, "Please log in")

            if path == "/api/board/week" and self.command == "POST":
                body = self.json_body() or {}
                cur = store.load()
                doc = clean_doc(dict(cur, week=body.get("week")))
                saved = store.save(doc, expect_rev=body.get("rev"))
                if saved is None:
                    return self.send(HTTPStatus.CONFLICT, {"error": "changed elsewhere", "doc": public_doc(store.load())})
                return self.send(HTTPStatus.OK, public_doc(saved))

            if path == "/api/remote" and self.command == "POST":
                action = str((self.json_body() or {}).get("action", ""))
                if action not in ("back", "next", "switch", "goto:today", "goto:book", "goto:postcards"):
                    raise BadRequest("unknown button")
                write_atomic(store.dir / "remote.json", json.dumps({"seq": time.time_ns(), "action": action}).encode())
                return self.send(HTTPStatus.OK, {"ok": True})

            if path == "/api/board/key" and self.command == "POST":
                s = store.secrets()
                s["board"] = secrets.token_urlsafe(18)
                store.save_secrets(s)
                return self.send(HTTPStatus.OK, {"board": s["board"]})

            if path == "/api/contacts" and self.command == "PUT":
                incoming = self.json_body()
                doc = clean_doc(incoming)
                for p in doc["people"]:
                    if p["photo"] and not (store.photos / p["photo"]).exists():
                        p["photo"] = ""
                saved = store.save(doc, expect_rev=incoming.get("rev"))
                if saved is None:
                    return self.send(HTTPStatus.CONFLICT, {"error": "Changed on another phone. Reloaded.", "doc": store.load()})
                return self.send(HTTPStatus.OK, saved)

            m = re.match(r"^/api/frames/(book|days)/([a-z0-9-]+)$", path)
            if m and self.command == "POST":
                group, name = m.groups()
                if name == "commit":
                    meta = self.json_body() or {}
                    names = meta.get("names") or []
                    if not names or not all(isinstance(n, str) and FRAME_GROUPS[group].match(n) for n in names):
                        raise BadRequest("bad picture names")
                    try:
                        clean = {"w": int(meta["w"]), "h": int(meta["h"]), "bpp": int(meta["bpp"]),
                                 "key": str(meta.get("key", ""))[:64], "names": names}
                    except (KeyError, ValueError, TypeError):
                        raise BadRequest("missing screen size")
                    store.commit_frames(group, clean)
                    return self.send(HTTPStatus.OK, {"ok": True})
                if not FRAME_GROUPS[group].match(name):
                    raise BadRequest("bad picture name")
                try:
                    w, h, bpp = (int(self.headers.get(k, "")) for k in ("X-Width", "X-Height", "X-Bpp"))
                except ValueError:
                    raise BadRequest("missing size headers")
                if not (0 < w <= 4096 and 0 < h <= 4096 and bpp in (16, 32)):
                    raise BadRequest("bad screen size")
                data = self.body(MAX_FRAME_BYTES)
                gz = self.headers.get("X-Gzip") == "1"
                size = len(gzip.decompress(data)) if gz else len(data)
                if size != w * h * bpp // 8:
                    raise BadRequest("picture is the wrong size")
                store.save_frame(group, name, data, gz)
                return self.send(HTTPStatus.OK, {"ok": True})

            if path == "/api/photo" and self.command == "POST":
                if (self.headers.get("Content-Type") or "") != "image/jpeg":
                    raise BadRequest("send a JPEG")
                data = self.body(MAX_PHOTO_BYTES)
                if not data.startswith(b"\xff\xd8\xff"):
                    raise BadRequest("that isn't a JPEG")
                name = secrets.token_hex(10) + ".jpg"
                store.photos.mkdir(parents=True, exist_ok=True)
                write_atomic(store.photos / name, data)
                return self.send(HTTPStatus.OK, {"photo": name})

            if path == "/api/postcards" and self.command == "POST":
                ctype = self.headers.get("Content-Type") or ""
                sender = unquote(self.headers.get("X-From", "")).strip()[:40] or "Mom & Dad"
                caption = unquote(self.headers.get("X-Caption", "")).strip()[:120]
                jpeg = None
                if ctype == "image/jpeg":
                    jpeg = self.body(MAX_POSTCARD_BYTES)
                    if not jpeg.startswith(b"\xff\xd8\xff"):
                        raise BadRequest("that isn't a JPEG")
                elif not caption:
                    raise BadRequest("add a photo, a drawing or a message")
                if not pc.available():
                    raise BadRequest("The Pi needs python3-pil to draw postcards. Run install.sh again.")
                cid, status = mailroom.accept(sender, caption, jpeg, "admin", trusted=True)
                return self.send(HTTPStatus.OK, {"id": cid, "status": status})

            m = re.match(r"^/api/postcards/([0-9a-f]{12})/(approve|delete)$", path)
            if m and self.command == "POST":
                cid, action = m.groups()
                c = store.update_card(cid, status="shown" if action == "approve" else None, delete=action == "delete")
                if not c:
                    return self.error(HTTPStatus.NOT_FOUND, "no such postcard")
                store.render_cards_soon()
                return self.send(HTTPStatus.OK, {"ok": True})

            if path == "/api/settings" and self.command == "PUT":
                body = self.json_body() or {}
                s = store.secrets()
                imap = body.get("imap") or {}
                user = str(imap.get("user", "")).strip()
                if user and not EMAIL_RE.match(user):
                    raise BadRequest("the postcard email address doesn't look right")
                s["imap"]["host"] = str(imap.get("host") or "imap.gmail.com").strip()[:100]
                s["imap"]["user"] = user
                pw = str(imap.get("password", "")).replace(" ", "")
                if pw:
                    s["imap"]["password"] = pw
                if imap.get("clearPassword") or not user:
                    s["imap"].pop("password", None)
                ntfy = body.get("ntfy") or {}
                topic = str(ntfy.get("topic", "")).strip()
                if topic and not re.match(r"^[A-Za-z0-9_-]{8,64}$", topic):
                    raise BadRequest("ntfy topic: 8 to 64 letters, numbers, - or _")
                s["ntfy"] = {"server": str(ntfy.get("server") or "https://ntfy.sh").strip()[:100], "topic": topic}
                view = str(body.get("viewUrl", "")).strip()
                if view:
                    if not re.match(r"^https?://[^\s]+$", view):
                        raise BadRequest("Zelda's view address should start with http://")
                    s["viewUrl"] = view if view.endswith("/") else view + "/"
                store.save_secrets(s)
                mailroom.poller.wake.set()
                return self.send(HTTPStatus.OK, self.admin_state()["settings"])

            if path == "/api/test/ntfy" and self.command == "POST":
                s = store.secrets()
                if not s["ntfy"].get("topic"):
                    raise BadRequest("Save an ntfy topic first")
                pc.notify(s["ntfy"], "Hello from Zelda's screen", "Postcard alerts will show up like this.", s["viewUrl"])
                return self.send(HTTPStatus.OK, {"ok": True})

            if path == "/api/test/email" and self.command == "POST":
                cfg = store.secrets()["imap"]
                if not cfg.get("user") or not cfg.get("password"):
                    raise BadRequest("Save the email address and app password first")
                try:
                    pc.test_login(cfg)
                except Exception as e:
                    raise BadRequest(f"Couldn't log in: {e}")
                mailroom.poller.wake.set()
                return self.send(HTTPStatus.OK, {"ok": True})

            self.error(HTTPStatus.NOT_FOUND, "not found")

    return Handler


# ---------------------------------------------------------------- cli

def ask_pin():
    pin = os.environ.get("ZELDA_PIN")
    if pin:
        return pin
    while True:
        pin = getpass.getpass("Admin PIN (4+ digits or letters): ")
        if len(pin) < 4:
            print("Use at least 4 characters.")
            continue
        if getpass.getpass("Same PIN again: ") == pin:
            return pin
        print("Those didn't match, try again.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="data directory")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--host", default="0.0.0.0")
    i = sub.add_parser("init")
    i.add_argument("--number", default=os.environ.get("ZELDA_NUMBER", ""))
    sub.add_parser("set-pin")
    sub.add_parser("has-pin")
    args = ap.parse_args()
    store = Store(args.data)

    if args.cmd == "init":
        created = store.ensure(args.number.strip())
        print(("Seeded " if created else "Kept existing ") + str(store.contacts))
        return
    if args.cmd == "set-pin":
        store.ensure()
        store.set_pin(ask_pin())
        print("PIN saved (hashed) in " + str(store.pin_file))
        return
    if args.cmd == "has-pin":
        sys.exit(0 if store.has_pin() else 1)

    port = getattr(args, "port", 8080)
    host = getattr(args, "host", "0.0.0.0")
    store.ensure()
    mailroom = Mailroom(store)
    mailroom.poller.start()
    store.render_cards_soon()
    if not pc.available():
        print("python3-pil is missing: postcards can't be drawn. Run install.sh again.", flush=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(store, mailroom))
    httpd.daemon_threads = True
    print(f"Zelda's screen on http://{host}:{port}/  (data in {store.dir})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

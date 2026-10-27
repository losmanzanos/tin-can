#!/usr/bin/env python3
"""Zelda's phone book: display page, admin page and a tiny JSON API.

Python 3 standard library only, so it runs on a Pi Zero W with nothing extra.

  python3 server.py serve [--port 8080] [--data ~/zelda-phone-data]
  python3 server.py init  [--number "(555) 555-0100"]   seed data if missing
  python3 server.py set-pin                             set the admin PIN

All the family data (contacts.json, photos/, the PIN hash) lives in the data
directory, outside the git checkout, so `git pull` never touches it.
"""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

APP = Path(__file__).resolve().parent
PAGE = APP / "page"
DEFAULT_DATA = Path(os.environ.get("ZELDA_DATA", Path.home() / "zelda-phone-data"))

MAX_PEOPLE = 21  # 7 per page; the screen flips through up to 3 pages
MAX_PAGES = 3
MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_SCREEN_BYTES = 4096 * 4096 * 4
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

TONE_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CODE_RE = re.compile(r"^[0-9]{2}$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
PHOTO_RE = re.compile(r"^[0-9a-f]{20}\.jpg$")
ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,32}$")

# Changes every time the server starts, so the display reloads itself after an
# update and picks up new page code, not just new data.
BOOT = secrets.token_hex(4)


class BadRequest(Exception):
    pass


# ---------------------------------------------------------------- storage

def write_atomic(path, data):
    """Write to a temp file in the same directory, then rename over the target."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600 if path.name.endswith(".key") or path.name == "pin.json" else 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def new_id():
    return secrets.token_hex(4)


def seed_doc(number=""):
    people = []
    for p in SEED_PEOPLE:
        people.append({"id": new_id(), "photo": "", "letter": "", **p})
    return {"rev": 1, "people": people, "number": number,
            "screen": {"on": "08:00", "off": "20:00", "flip": False}}


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
        pid = str(p.get("id", "") or "") or new_id()
        if not ID_RE.match(pid) or pid in ids:
            pid = new_id()
        ids.add(pid)
        c = {"id": pid, "name": name, "tone": tone.lower(), "letter": letter, "photo": photo}
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
    flip = bool(screen.get("flip", False))
    return {"people": out, "number": number, "screen": {"on": on, "off": off, "flip": flip}}


class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir).expanduser()
        self.photos = self.dir / "photos"
        self.contacts = self.dir / "contacts.json"
        self.pin_file = self.dir / "pin.json"
        self.key_file = self.dir / "session.key"
        self.screen_meta = self.dir / "screen.json"
        self.lock = threading.Lock()

    def ensure(self, number=""):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.photos.mkdir(exist_ok=True)
        created = False
        if not self.contacts.exists():
            write_atomic(self.contacts, json.dumps(seed_doc(number), indent=2).encode())
            created = True
        if not self.key_file.exists():
            write_atomic(self.key_file, secrets.token_bytes(32))
        return created

    def load(self):
        with open(self.contacts, "rb") as f:
            return json.load(f)

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
        """Delete photos no contact uses. Leaves fresh uploads alone for a day
        in case someone is still filling in the form."""
        used = {p.get("photo") for p in doc["people"]}
        cutoff = time.time() - 86400
        for f in self.photos.glob("*.jpg"):
            if f.name not in used and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass

    # The picture paint.py copies to the screen, drawn by the admin page.
    def screen_info(self):
        try:
            return json.loads(self.screen_meta.read_text())
        except (OSError, ValueError):
            return None

    def screen_page(self, n):
        return self.dir / f"screen-{n}.raw"

    def save_screen(self, w, h, bpp, rev, page, pages, data):
        """Pages arrive one at a time; paint.py switches over once the last one is in."""
        with self.lock:
            write_atomic(self.screen_page(page), data)
            if page == pages - 1:
                write_atomic(self.screen_meta, json.dumps(
                    {"w": w, "h": h, "bpp": bpp, "rev": rev, "pages": pages}).encode())
                for n in range(pages, MAX_PAGES + 1):
                    try:
                        self.screen_page(n).unlink()
                    except FileNotFoundError:
                        pass

    # PIN and sessions
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


# ---------------------------------------------------------------- http

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


STATIC_TYPES = {".html": "text/html; charset=utf-8", ".woff2": "font/woff2",
                ".jpg": "image/jpeg", ".css": "text/css", ".js": "text/javascript",
                ".svg": "image/svg+xml", ".png": "image/png"}


class Failures:
    """Slow down PIN guessing: after 5 wrong tries, wait a minute."""
    def __init__(self):
        self.count, self.until, self.lock = 0, 0.0, threading.Lock()


def make_handler(store):
    failures = Failures()

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZeldaPhone/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            # Keep the journal quiet: the display polls every 10 seconds.
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
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if cache else {}
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", headers.get("Cache-Control", "no-cache"))
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
                return json.loads(self.body(256 * 1024) or b"null")
            except ValueError:
                raise BadRequest("bad JSON")

        def authed(self):
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return COOKIE in c and store.check_session(c[COOKIE].value)

        def same_origin(self):
            # Belt and braces next to the SameSite cookie: refuse writes that a
            # page on some other site sent.
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlsplit(origin).netloc == (self.headers.get("Host") or "")

        # routes
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                return self.file(PAGE / "index.html")
            if path in ("/admin", "/admin/"):
                return self.file(PAGE / ("admin.html" if self.authed() else "login.html"))
            if path == "/api/version":
                rev = store.load().get("rev", 0)
                return self.send(HTTPStatus.OK, {"version": f"{BOOT}-{rev}", "boot": BOOT, "rev": rev})
            if path == "/api/contacts":
                return self.send(HTTPStatus.OK, store.load())
            if path == "/api/session":
                return self.send(HTTPStatus.OK, {"authed": bool(self.authed()), "hasPin": store.has_pin()})
            if path == "/api/screen":
                return self.send(HTTPStatus.OK, {"fb": fb_geometry(), "drawn": store.screen_info()})
            if path == "/render.js":
                return self.file(PAGE / "render.js")
            if path.startswith("/photos/"):
                name = path[len("/photos/"):]
                if PHOTO_RE.match(name):
                    return self.file(store.photos / name, cache=True)
            if path.startswith("/fonts/"):
                name = path[len("/fonts/"):]
                if re.match(r"^[a-z0-9-]+\.woff2$", name):
                    return self.file(PAGE / "fonts" / name, cache=True)
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

            if not self.authed():
                return self.error(HTTPStatus.UNAUTHORIZED, "Please log in")

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

            if path == "/api/screen" and self.command == "POST":
                if (self.headers.get("Content-Type") or "") != "application/octet-stream":
                    raise BadRequest("send raw pixels")
                try:
                    w, h, bpp, rev, page, pages = (int(self.headers.get(k, "")) for k in
                                                   ("X-Width", "X-Height", "X-Bpp", "X-Rev", "X-Page", "X-Pages"))
                except ValueError:
                    raise BadRequest("missing size headers")
                if not (0 < w <= 4096 and 0 < h <= 4096 and bpp in (16, 32)):
                    raise BadRequest("bad screen size")
                if not (1 <= pages <= MAX_PAGES and 0 <= page < pages):
                    raise BadRequest("bad page number")
                data = self.body(MAX_SCREEN_BYTES)
                if len(data) != w * h * bpp // 8:
                    raise BadRequest("picture is the wrong size")
                store.save_screen(w, h, bpp, rev, page, pages, data)
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
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    httpd.daemon_threads = True
    print(f"Zelda's phone book on http://{host}:{port}/  (data in {store.dir})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

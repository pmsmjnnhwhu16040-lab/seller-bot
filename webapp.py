#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webapp.py — bot.py এর সাথে জোড়া ওয়েব সার্ভার (শুধু Python stdlib, নতুন প্যাকেজ লাগে না)

কী কী করে:
  • /                    → miniapp.html (Mini App / ড্যাশবোর্ড)
  • Telegram অটো লগইন   → Mini App খুললে initData ভেরিফাই করে সরাসরি লগইন (পাসওয়ার্ড লাগে না)
  • ব্রাউজার লগইন       → Telegram ID / @username + বটে /password দিয়ে সেট করা পাসওয়ার্ড
  • /upload, /api/*      → সাইট ডিপ্লয়, আপডেট (ভার্সন হিস্ট্রিসহ), রিনেম, ডিলিট
  • /s/<slug>/…          → ইউজারদের সাইট হোস্টিং  (SITES_DOMAIN থাকলে <slug>.domain)
  • /t/<tid>/…           → টেমপ্লেট প্রিভিউ
  • /health              → UptimeRobot / cron পিং

সব ডাটা (ইউজার, ব্যালেন্স, সাইট) bot.py এর DB-ই ব্যবহার করে — বট ও ওয়েব একই অ্যাকাউন্ট/ওয়ালেট।
bot.py থেকে webapp.start(bot_module) কল হয়; এই ফাইল bot.py ইমপোর্ট করে না।
"""

import asyncio
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
import zipfile
from email.parser import BytesParser
from email.policy import HTTP as HTTP_POLICY
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, quote, unquote, urlsplit

log = logging.getLogger("hosting.web")

B = None  # bot module (start() এ সেট হয়)
HERE = Path(__file__).resolve().parent
APP_HTML = HERE / "miniapp.html"

COOKIE = "ffx_s"
MAX_VERSIONS = 5                      # প্রতি সাইটে সর্বোচ্চ কতটা পুরনো ভার্সন রাখা হবে
MAX_JSON_BYTES = 64 * 1024
INIT_MAX_AGE = int(os.getenv("INIT_MAX_AGE") or 3600)   # initData কতক্ষণ বৈধ (সেকেন্ড)
SESSION_TTL = 7 * 86400               # Telegram লগইনের কুকি
REMEMBER_TTL = 30 * 86400             # ব্রাউজারে "Save Me" চাপলে
SHORT_TTL = 86400                     # "Save Me" ছাড়া

_SECRET = b""
_FAILS: dict = {}                     # ip -> [timestamps]  (লগইন ব্রুট-ফোর্স ঠেকাতে)
_GATE_CACHE: dict = {}                # uid -> (time, [])   (চ্যানেল-লক চেক ক্যাশ)
_HTML_CACHE = {"mtime": 0, "data": b""}

TEXT_TYPES = ("text/", "application/javascript", "application/json", "image/svg+xml", "application/xml")
SANDBOX_TYPES = ("text/html", "image/svg+xml", "application/xml", "text/xml", "application/xhtml+xml")
SANDBOX_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox "
    "allow-modals allow-downloads allow-pointer-lock allow-presentation"
)


# ═══════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════
def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:40]


def make_token(uid: int, ttl: int) -> str:
    payload = f"{uid}.{int(time.time()) + ttl}"
    return f"{payload}.{_sign(payload)}"


def read_token(tok: str):
    try:
        uid, exp, sig = tok.split(".")
        if not hmac.compare_digest(sig, _sign(f"{uid}.{exp}")) or int(exp) < time.time():
            return None
        return int(uid)
    except Exception:
        return None


def verify_init_data(raw: str):
    """Telegram Mini App initData যাচাই (HMAC-SHA256)। সঠিক হলে user dict, নইলে None।"""
    if not raw or len(raw) > 8192:
        return None
    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", None)
    if not got:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    key = hmac.new(b"WebAppData", B.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        age = time.time() - int(pairs.get("auth_date", "0"))
        if age > INIT_MAX_AGE or age < -300:
            return None
        user = json.loads(pairs["user"])
        int(user["id"])
        return user
    except Exception:
        return None


def ensure_user(tg: dict) -> int:
    uid = int(tg["id"])
    name = " ".join(x for x in (tg.get("first_name"), tg.get("last_name")) if x) or "User"
    B.get_user(SimpleNamespace(id=uid, full_name=name, username=tg.get("username")))
    return uid


def display_name(uid: int) -> str:
    u = B.DB["users"].get(str(uid), {})
    return ("@" + u["username"]) if u.get("username") else (u.get("name") or str(uid))


def client_ip(h) -> str:
    xff = h.headers.get("X-Forwarded-For")
    return (xff.split(",")[0].strip() if xff else h.client_address[0]) or "?"


def find_uids(ident: str):
    ident = (ident or "").strip().lstrip("@").lower()
    if not ident:
        return []
    out = []
    for u in B.DB["users"].values():
        if (ident.isdigit() and str(u.get("id")) == ident) or (u.get("username") or "").lower() == ident:
            out.append(int(u["id"]))
    return out


def channel_missing(uid: int):
    """চ্যানেল-লক থাকলে যে চ্যানেলে জয়েন করা বাকি তার লিস্ট (বটের gate এর মতোই)।"""
    if uid == B.ADMIN_ID or not B.DB.get("channel_lock"):
        return []
    c = _GATE_CACHE.get(uid)
    if c and time.time() - c[0] < 60:
        return []
    if not (B.APP and B.LOOP):
        return []
    try:
        miss = asyncio.run_coroutine_threadsafe(B.missing_channels(B.APP.bot, uid), B.LOOP).result(timeout=15)
    except Exception as e:  # চেক ব্যর্থ হলে আটকাই না
        log.warning("channel check failed: %s", e)
        return []
    if not miss:
        _GATE_CACHE[uid] = (time.time(), [])
    return miss


# ═══════════════════════════════════════════════════════════════
#  SITE HELPERS
# ═══════════════════════════════════════════════════════════════
def versions_dir() -> Path:
    return B.DATA_DIR / "versions"


def norm_slug(raw: str) -> str:
    """ফ্রন্টএন্ডের প্রিভিউয়ের মতোই: স্পেস → '-', বাকি অবৈধ অক্ষর বাদ।"""
    s = re.sub(r"\s+", "-", (raw or "").strip().lower())
    return B.clean_slug(re.sub(r"[^a-z0-9-]", "", s))


def plain(msg: str) -> str:
    return re.sub(r"<[^>]+>", "", msg or "").replace("&lt;", "<").replace("&gt;", ">").strip()


def find_site(uid: int, slug: str):
    for sid, s in B.DB["sites"].items():
        if s["slug"] == slug and (s["owner"] == uid or uid == B.ADMIN_ID):
            return sid, s
    return None, None


def list_versions(sid: str):
    vd = versions_dir() / sid
    if not vd.exists():
        return []
    return sorted((int(p.stem) for p in vd.glob("*.zip") if p.stem.isdigit()), reverse=True)


def snapshot(sid: str) -> None:
    d = B.site_dir(sid)
    if not d.exists() or B.dir_size(d) > 20 * 1024 * 1024:
        return
    vd = versions_dir() / sid
    vd.mkdir(parents=True, exist_ok=True)
    nums = sorted(int(p.stem) for p in vd.glob("*.zip") if p.stem.isdigit())
    n = (nums[-1] if nums else 0) + 1
    with zipfile.ZipFile(vd / f"{n}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in d.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(d).as_posix())
    nums.append(n)
    for old in nums[:-MAX_VERSIONS]:
        (vd / f"{old}.zip").unlink(missing_ok=True)


def project_json(sid: str, s: dict) -> dict:
    return {
        "name": s["slug"],
        "url": B.site_url(s["slug"]),
        "versions": list_versions(sid),
        "status": s.get("status", "online"),
        "views": s.get("views", 0),
    }


def parse_multipart(ctype: str, body: bytes):
    msg = BytesParser(policy=HTTP_POLICY).parsebytes(
        b"Content-Type: " + ctype.encode("latin-1", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    fields, files = {}, {}
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        fn = part.get_filename()
        if fn is not None:
            files[name] = (fn, data)
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


# ═══════════════════════════════════════════════════════════════
#  HTTP HANDLER
# ═══════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FFXHost"
    timeout = 60
    _head = False

    def log_message(self, *a):
        pass

    # ── low level ──
    def _out(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=()):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if not self._head and code != 304:
            self.wfile.write(body)

    def _json(self, obj, code=200, headers=()):
        self._out(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", headers)

    def _err(self, code, msg):
        self._out(code, msg, headers=(("Cache-Control", "no-store"),))

    def _cookie(self, tok, ttl=None):
        https = B.WEBAPP_URL.startswith("https://")
        c = f"{COOKIE}={tok}; Path=/; HttpOnly; " + ("SameSite=None; Secure" if https else "SameSite=Lax")
        if ttl is not None:
            c += f"; Max-Age={ttl}"
        return ("Set-Cookie", c)

    def _body(self, limit):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            self.close_connection = True
            return None
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        raw = self._body(MAX_JSON_BYTES)
        if raw is None:
            return None
        try:
            d = json.loads(raw.decode("utf-8") or "{}")
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _origin_ok(self) -> bool:
        """অন্য ওরিজিন (যেমন ইউজারের হোস্ট করা সাইট) থেকে API কল ঠেকায়।"""
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != (self.headers.get("Host") or ""):
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def _auth(self, allow_query=False):
        """লগইন করা ইউজারের uid অথবা None। প্রথমে Telegram initData, তারপর কুকি।"""
        raw = self.headers.get("X-Tg-Init-Data")
        if not raw and allow_query:
            raw = dict(parse_qsl(urlsplit(self.path).query)).get("tg")
        if raw:
            tg = verify_init_data(raw)
            if tg:
                return ensure_user(tg), True
        ck = SimpleCookie(self.headers.get("Cookie") or "")
        if COOKIE in ck:
            uid = read_token(ck[COOKIE].value)
            if uid and str(uid) in B.DB["users"]:
                return uid, False
        return None, False

    # ── verbs ──
    def do_HEAD(self):
        self._head = True
        self._dispatch("GET")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):
        host_site = self._site_host() is not None or urlsplit(self.path).path.startswith(("/s/", "/t/"))
        if host_site:
            return self._out(204, b"", headers=(("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Methods", "GET, HEAD")))
        self._out(204)  # CORS হেডার নেই → অন্য ওরিজিন ব্লক

    def _site_host(self):
        if not B.SITES_DOMAIN:
            return None
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host.endswith("." + B.SITES_DOMAIN):
            slug = host[: -len(B.SITES_DOMAIN) - 1]
            if slug and "." not in slug:
                return slug
        return None

    def _dispatch(self, method):
        try:
            slug = self._site_host()
            path = urlsplit(self.path).path
            if slug is not None:
                return self._serve_site(slug, path)
            if method == "GET":
                if path in ("/", "/index.html"):
                    return self._serve_app()
                if path in ("/health", "/healthz", "/ping"):
                    return self._out(200, "OK - hosting platform is running")
                if path.startswith("/s/"):
                    return self._route_site(path)
                if path.startswith("/t/"):
                    return self._route_tpl(path)
                if path == "/api/user":
                    return self._api_user()
                if path == "/api/download-version":
                    return self._api_download()
                if path == "/logout":
                    return self._logout()
            elif method == "POST":
                if path == "/login":
                    return self._login()
                if path == "/register":
                    return self._register()
                if path == "/upload":
                    return self._upload()
                if path == "/api/delete":
                    return self._api_delete()
                if path == "/api/rename-project":
                    return self._api_rename()
            self._err(404, "Not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("request error: %s", self.path)
            try:
                self._err(500, "Server error")
            except Exception:
                pass

    # ── mini app page ──
    def _serve_app(self):
        if not APP_HTML.exists():
            return self._err(500, "miniapp.html পাওয়া যায়নি — bot.py এর পাশে রাখুন।")
        m = APP_HTML.stat().st_mtime
        if _HTML_CACHE["mtime"] != m:
            _HTML_CACHE["data"] = APP_HTML.read_bytes()
            _HTML_CACHE["mtime"] = m
        cfg = json.dumps({"siteUrlTpl": B.site_url("{slug}")})
        data = _HTML_CACHE["data"].replace(b"__FFX_CFG__", cfg.encode())
        self._out(200, data, "text/html; charset=utf-8", (("Cache-Control", "no-store"),))

    # ── auth endpoints ──
    def _login(self):
        if not self._origin_ok():
            return self._err(403, "Forbidden")
        d = self._json_body()
        if d is None:
            return self._err(413, "Too large")
        ip = client_ip(self)
        recent = [t for t in _FAILS.get(ip, []) if time.time() - t < 600]
        if len(recent) >= 10:
            return self._err(429, "Too many attempts. Try again in 10 minutes.")
        ident, pw = str(d.get("username") or ""), str(d.get("password") or "")
        for uid in find_uids(ident):
            if pw and B.web_check_password(uid, pw):
                _FAILS.pop(ip, None)
                ttl = REMEMBER_TTL if d.get("remember") == "yes" else SHORT_TTL
                tok = make_token(uid, ttl)
                return self._json({"ok": True}, headers=(self._cookie(tok, ttl if d.get("remember") == "yes" else None),))
        recent.append(time.time())
        _FAILS[ip] = recent
        self._err(401, "Wrong ID/username or password. Set your password in the Telegram bot with /password first.")

    def _register(self):
        self._err(
            403,
            "Accounts are created through the Telegram bot. Open the bot and send /password YOUR_PASSWORD, "
            "then log in here with your Telegram ID or @username. "
            "(Inside Telegram you are logged in automatically.)",
        )

    def _logout(self):
        self._out(200, "ok", headers=(self._cookie("", 0), ("Cache-Control", "no-store")))

    # ── api ──
    def _api_user(self):
        if not self._origin_ok():
            return self._err(403, "Forbidden")
        uid, via_tg = self._auth()
        if not uid:
            return self._err(401, "Login required")
        u = B.DB["users"].get(str(uid), {})
        projects = [project_json(sid, s) for sid, s in B.user_sites(uid)]
        projects.sort(key=lambda p: p["name"])
        hdrs = [("Cache-Control", "no-store")]
        if via_tg:  # Telegram থেকে এলে কুকিও বসাই — ডাউনলোড লিংক ইত্যাদির জন্য
            hdrs.append(self._cookie(make_token(uid, SESSION_TTL), SESSION_TTL))
        self._json(
            {
                "username": display_name(uid),
                "telegram": via_tg,
                "balance": u.get("balance", 0),
                "price": B.DB["settings"]["global_price"],
                "projects": projects,
            },
            headers=hdrs,
        )

    def _upload(self):
        if not self._origin_ok():
            return self._err(403, "Forbidden")
        uid, _ = self._auth()
        if not uid:
            return self._err(401, "Login required")
        ctype = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ctype:
            return self._err(400, "Bad request")
        body = self._body(B.MAX_ZIP_BYTES + 1024 * 1024)
        if body is None:
            return self._err(413, "File too large")
        fields, files = parse_multipart(ctype, body)
        fn, data = files.get("siteFile", (None, None))
        if not data:
            return self._err(400, "No file received")
        slug = norm_slug(fields.get("siteName"))
        is_update = fields.get("isUpdate") == "true"

        is_zip = fn.lower().endswith(".zip")
        limit = B.MAX_ZIP_BYTES if is_zip else B.MAX_HTML_BYTES
        if len(data) > limit:
            return self._err(413, f"File too large (max {limit // 1024 // 1024} MB)")
        if not is_zip and (b"<" not in data or b">" not in data):
            return self._err(400, "This does not look like HTML code.")

        B.TMP_DIR.mkdir(exist_ok=True)
        tmp = B.TMP_DIR / f"web_{uid}_{int(time.time() * 1000)}{'.zip' if is_zip else '.html'}"
        tmp.write_bytes(data)
        try:
            if is_zip:
                bad = B.inspect_zip(tmp)
                if bad:
                    return self._err(400, bad)
            if is_update:
                return self._do_update(uid, slug, tmp)
            return self._do_create(uid, slug, tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def _do_create(self, uid, slug, tmp):
        err = B.slug_error(slug)
        if err:
            return self._err(400, plain(err))
        miss = channel_missing(uid)
        if miss:
            return self._err(403, "Join these channels first: " + ", ".join(miss) + " — then open the bot and press Verify.")
        price = B.DB["settings"]["global_price"]
        ok, res = B.deploy_site(uid, slug, tmp, "custom", None, price)
        if not ok:
            if res == "insufficient":
                bal = B.DB["users"][str(uid)]["balance"]
                return self._err(
                    402,
                    f"Insufficient balance. Hosting costs {B.money(price)}, your balance is {B.money(bal)}. "
                    "Add money from the Telegram bot (💳 এড মানি).",
                )
            return self._err(400, plain(res))
        self._json({"ok": True, "name": slug, "url": B.site_url(slug), "charged": price})

    def _do_update(self, uid, slug, tmp):
        sid, s = find_site(uid, slug)
        if not s:
            return self._err(404, "Project not found")
        with B._SAVE_LOCK:
            try:
                snapshot(sid)
            except Exception as e:
                log.warning("snapshot failed: %s", e)
            ok, e = B.apply_package(tmp, B.site_dir(sid))
            if not ok:
                return self._err(500, f"Update failed: {e}")
            s["updated"] = B.now()
            B.save_data()
        self._json({"ok": True, "name": slug, "url": B.site_url(slug)})

    def _api_delete(self):
        if not self._origin_ok():
            return self._err(403, "Forbidden")
        uid, _ = self._auth()
        if not uid:
            return self._err(401, "Login required")
        d = self._json_body() or {}
        sid, s = find_site(uid, norm_slug(d.get("siteName")))
        if not s:
            return self._err(404, "Project not found")
        B.delete_site(sid)
        shutil.rmtree(versions_dir() / sid, ignore_errors=True)
        self._json({"ok": True})

    def _api_rename(self):
        if not self._origin_ok():
            return self._err(403, "Forbidden")
        uid, _ = self._auth()
        if not uid:
            return self._err(401, "Login required")
        d = self._json_body() or {}
        sid, s = find_site(uid, norm_slug(d.get("oldName")))
        if not s:
            return self._err(404, "Project not found")
        new = norm_slug(d.get("newName"))
        with B._SAVE_LOCK:
            err = B.slug_error(new, ignore_sid=sid)
            if err:
                return self._err(400, plain(err))
            s["slug"], s["updated"] = new, B.now()
            B.save_data()
        self._json({"ok": True, "name": new, "url": B.site_url(new)})

    def _api_download(self):
        if not self._origin_ok():
            return self._err(403, "Forbidden")
        uid, _ = self._auth(allow_query=True)
        if not uid:
            return self._err(401, "Login required")
        q = dict(parse_qsl(urlsplit(self.path).query))
        sid, s = find_site(uid, norm_slug(q.get("project")))
        v = q.get("v", "")
        if not s or not v.isdigit():
            return self._err(404, "Version not found")
        f = versions_dir() / sid / f"{int(v)}.zip"
        if not f.is_file():
            return self._err(404, "Version not found")
        with zipfile.ZipFile(f) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if names == ["index.html"]:
                data, ext, ct = z.read("index.html"), "html", "text/html; charset=utf-8"
            else:
                data, ext, ct = f.read_bytes(), "zip", "application/zip"
        fname = f"{s['slug']}-v{int(v)}.{ext}"
        self._out(200, data, ct, (("Content-Disposition", f'attachment; filename="{fname}"'), ("Cache-Control", "no-store")))

    # ── site hosting ──
    def _route_site(self, path):
        if B.SITES_DOMAIN:  # সাবডোমেইন মোডে /s/ বন্ধ (একই ওরিজিনে ইউজারের কোড চালানো নিরাপদ নয়)
            parts = path.split("/", 3)
            slug = parts[2] if len(parts) > 2 else ""
            rest = parts[3] if len(parts) > 3 else ""
            return self._out(302, b"", headers=(("Location", B.site_url(slug) + rest),))
        parts = path.split("/", 3)  # ['', 's', slug, rest]
        slug = parts[2] if len(parts) > 2 else ""
        if len(parts) == 3:
            return self._out(302, b"", headers=(("Location", f"/s/{quote(slug)}/"),))
        self._serve_site(slug, "/" + parts[3], sandbox=True)

    def _serve_site(self, slug, rel, sandbox=False):
        sid = next((i for i, s in B.DB["sites"].items() if s["slug"] == slug), None)
        if not sid or not B.site_dir(sid).exists():
            return self._out(404, _PAGE.format(t="Site not found", m="এই নামে কোনো সাইট নেই।"), "text/html; charset=utf-8")
        if B.DB["sites"][sid].get("status") != "online":
            return self._out(503, _PAGE.format(t="Site offline", m="এই সাইটটি এখন অফলাইন।"), "text/html; charset=utf-8")
        sandbox = sandbox or (not B.SITES_DOMAIN)
        if os.getenv("SITES_SANDBOX") == "0":
            sandbox = False
        self._serve_dir(B.site_dir(sid), rel, sid, sandbox)

    def _route_tpl(self, path):
        parts = path.split("/", 3)
        tid = parts[2] if len(parts) > 2 else ""
        if tid not in B.DB["templates"] or not B.tdir(tid).exists():
            return self._out(404, _PAGE.format(t="Not found", m="টেমপ্লেট পাওয়া যায়নি।"), "text/html; charset=utf-8")
        if len(parts) == 3:
            return self._out(302, b"", headers=(("Location", f"/t/{quote(tid)}/"),))
        self._serve_dir(B.tdir(tid), "/" + parts[3], None, True)

    def _serve_dir(self, base: Path, rel: str, sid, sandbox: bool):
        rel = unquote(rel).replace("\\", "/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if "\x00" in rel or ".." in parts:
            return self._err(404, "Not found")
        try:
            root = base.resolve()
            target = root.joinpath(*parts).resolve()
        except Exception:
            return self._err(404, "Not found")
        if target != root and root not in target.parents:
            return self._err(404, "Not found")
        if target.is_dir():
            target = target / "index.html"
        status = 200
        if not target.is_file():
            nf = root / "404.html"
            if nf.is_file():
                target, status = nf, 404
            else:
                return self._out(404, _PAGE.format(t="404", m="পেজ পাওয়া যায়নি।"), "text/html; charset=utf-8")
        if sid and status == 200 and target.parent == root and target.name == "index.html" and not self._head:
            B.DB["sites"][sid]["views"] = B.DB["sites"][sid].get("views", 0) + 1
            B.VIEWS["dirty"] = True

        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith(TEXT_TYPES):
            ctype += "; charset=utf-8"
        st = target.stat()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        hdrs = [
            ("ETag", etag),
            ("Access-Control-Allow-Origin", "*"),
            ("Cache-Control", "public, max-age=0, must-revalidate" if ctype.startswith("text/html") else "public, max-age=300"),
        ]
        if sandbox and ctype.split(";")[0] in SANDBOX_TYPES:
            hdrs.append(("Content-Security-Policy", SANDBOX_CSP))
        if status == 200 and self.headers.get("If-None-Match") == etag:
            return self._out(304, b"", ctype, hdrs)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(st.st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in hdrs:
            self.send_header(k, v)
        self.end_headers()
        if not self._head:
            with open(target, "rb") as f:
                shutil.copyfileobj(f, self.wfile)


_PAGE = (
    '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>{t}</title><body style=\"margin:0;min-height:100vh;display:grid;place-items:center;"
    'background:#09090b;color:#fafafa;font-family:system-ui,sans-serif;text-align:center">'
    "<div><h1 style=\"margin:0 0 8px\">{t}</h1><p style=\"color:#a1a1aa\">{m}</p></div>"
)


# ═══════════════════════════════════════════════════════════════
#  START
# ═══════════════════════════════════════════════════════════════
def start(bot_module) -> bool:
    """bot.py এর start_web_server() থেকে কল হয়। সফল হলে True।"""
    global B, _SECRET
    B = bot_module
    port = os.getenv("PORT")
    if not port:
        return False
    _SECRET = hashlib.sha256(b"ffx-session:" + B.BOT_TOKEN.encode()).digest()
    versions_dir().mkdir(parents=True, exist_ok=True)
    if not APP_HTML.exists():
        log.warning("miniapp.html পাওয়া যায়নি (%s) — / খালি দেখাবে", APP_HTML)
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("🌐 Web app + hosting server listening on port %s", port)
    return True

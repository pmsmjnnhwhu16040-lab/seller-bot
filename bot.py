#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║        TELEGRAM WEBSITE HOSTING PLATFORM  —  bot.py          ║
║        python-telegram-bot v20+ (async)                      ║
╚══════════════════════════════════════════════════════════════╝

ইউজার HTML কোড (বা .html / .zip ফাইল) দিলে বট ওয়েবসাইট হোস্ট করে ডোমেইন/লিংক দেয়।

Run:
    pip install -r requirements.txt
    export BOT_TOKEN="123456:ABC..."
    python bot.py

Folders (auto-created):
    sites/      -> ইউজারদের ওয়েবসাইট   (sites/{site_id}/index.html ...)
    templates/  -> মার্কেটের টেমপ্লেট     (templates/{tpl_id}/index.html ...)
    data.json   -> সব ডাটা

Render Free প্ল্যানে ডিস্ক স্থায়ী নয় — তাই AUTO-BACKUP আছে:
    ডাটা + সাইট জিপ করে এডমিনের প্রাইভেট চ্যাটে পাঠিয়ে পিন করে রাখা হয়,
    রিস্টার্টের পর ডিস্ক খালি থাকলে সেখান থেকে নিজে নিজে রিস্টোর হয়।
"""

import asyncio
import copy
import hashlib
import hmac
import html
import json
import logging
import os
import random
import re
import shutil
import string
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:  # tap-to-copy button (PTB >= 21.7)
    from telegram import CopyTextButton
except ImportError:  # pragma: no cover
    CopyTextButton = None

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
BOT_TOKEN = os.getenv("BOT_TOKEN") or "PUT_YOUR_PLATFORM_BOT_TOKEN_HERE"
ADMIN_ID = int(os.getenv("ADMIN_ID") or 8933985337)  # এই আইডিই বট ও ওয়েব অ্যাপ দুই জায়গার এডমিন

# Mini App এর HTTPS লিংক — Render এ RENDER_EXTERNAL_URL অটো সেট থাকে, নইলে WEBAPP_URL দাও
WEBAPP_URL = (os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")

# নিজের ডোমেইন থাকলে (ওয়াইল্ডকার্ড *.yourdomain.com Render এ যোগ করা থাকলে) এখানে দাও:
#   SITES_DOMAIN=yourdomain.com  →  ইউজারের সাইট হবে  https://নাম.yourdomain.com
# না দিলে লিংক হবে  https://your-app.onrender.com/s/নাম/
SITES_DOMAIN = re.sub(r"^https?://", "", (os.getenv("SITES_DOMAIN") or "").strip().lower()).strip("/.")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR") or BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "data.json"
SITES_DIR = DATA_DIR / "sites"
TEMPLATES_DIR = DATA_DIR / "templates"
TMP_DIR = DATA_DIR / "tmp"

MAX_HTML_BYTES = 3 * 1024 * 1024        # একটা HTML কোড/ফাইল
MAX_ZIP_BYTES = 10 * 1024 * 1024        # ZIP ফাইল
MAX_UNZIPPED_BYTES = 15 * 1024 * 1024   # ZIP খুললে মোট সাইজ
MAX_ZIP_FILES = 600
BACKUP_MAX_BYTES = 19 * 1024 * 1024     # Telegram বট ডাউনলোড লিমিট ২০MB
BACKUP_INTERVAL = 300                   # সেকেন্ড — পরিবর্তন হলে সর্বোচ্চ ৫ মিনিটে একবার ব্যাকআপ

LINE = "━━━━━━━━━━━━━━━━━━"

# যে স্টেটগুলোতে কোড (ফাইল অথবা লেখা মেসেজ) গ্রহণ করা হয়
CODE_STATES = ("upload_code", "edit_code", "adm_tpl_file", "adm_tpl_edit")
ADMIN_CODE_STATES = ("adm_tpl_file", "adm_tpl_edit")

# Reply keyboard labels
BTN_ADD = "➕ সাইট এড"
BTN_SITES = "🌐 আমার সাইট"
BTN_MARKET = "🛒 টেমপ্লেট মার্কেট"
BTN_WALLET = "💰 ওয়ালেট"
BTN_ADDMONEY = "💳 এড মানি"
BTN_SUPPORT = "📞 সাপোর্ট"
BTN_APP = "📱 মিনি অ্যাপ"
BTN_ADMIN = "👑 এডমিন প্যানেল"
MENU_LABELS = {BTN_ADD, BTN_SITES, BTN_MARKET, BTN_WALLET, BTN_ADDMONEY, BTN_SUPPORT, BTN_ADMIN}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("hosting")

_SAVE_LOCK = threading.RLock()  # বট (async) ও ওয়েব (thread) একসাথে সেভ করলে ফাইল নষ্ট হওয়া ঠেকায়
VIEWS = {"dirty": False}        # ভিজিটর কাউন্ট বদলালে webapp.py এটা True করে
BACKUP = {"dirty": False, "mid": None, "last": 0.0, "warned": False}
APP = None   # post_init এ সেট হয়
LOOP = None  # post_init এ সেট হয় (webapp থেকে async কাজ চালাতে)


# ═══════════════════════════════════════════════════════════════
#  AUTO RESTORE (Render Free এ ডিস্ক খালি হয়ে গেলে)
# ═══════════════════════════════════════════════════════════════
def _tg_api(method: str, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json.dumps(params).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("result")


def _safe_extract_backup(z: zipfile.ZipFile) -> int:
    root = DATA_DIR.resolve()
    n = 0
    for info in z.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        if not parts or ".." in parts or name.startswith("/"):
            continue
        if parts[0] not in ("data.json", "sites", "templates"):
            continue
        target = (DATA_DIR / name).resolve()
        if root not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        n += 1
    return n


def restore_from_telegram() -> None:
    """data.json না থাকলে এডমিনের চ্যাটে পিন করা সর্বশেষ ব্যাকআপ থেকে সব ফিরিয়ে আনে।"""
    if DATA_FILE.exists() or BOT_TOKEN.startswith("PUT_YOUR"):
        return
    try:
        chat = _tg_api("getChat", chat_id=ADMIN_ID) or {}
        pinned = chat.get("pinned_message") or {}
        doc = pinned.get("document") or {}
        if not str(doc.get("file_name", "")).startswith("backup_"):
            log.info("কোনো ব্যাকআপ পাওয়া যায়নি — নতুন করে শুরু হচ্ছে")
            return
        info = _tg_api("getFile", file_id=doc["file_id"])
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info['file_path']}"
        tmp = DATA_DIR / "restore.zip"
        with urllib.request.urlopen(url, timeout=90) as r, open(tmp, "wb") as out:
            shutil.copyfileobj(r, out)
        with zipfile.ZipFile(tmp) as z:
            n = _safe_extract_backup(z)
        tmp.unlink(missing_ok=True)
        BACKUP["mid"] = pinned.get("message_id")
        log.info("♻️ ব্যাকআপ থেকে রিস্টোর হয়েছে (%d ফাইল)", n)
    except Exception as e:
        log.warning("রিস্টোর ব্যর্থ: %s", e)


restore_from_telegram()

# ═══════════════════════════════════════════════════════════════
#  DATABASE (data.json)
# ═══════════════════════════════════════════════════════════════
DEFAULT_DATA = {
    "users": {},            # uid -> {id,name,username,balance,joined}
    "sites": {},            # sid -> {owner,slug,status,source,template,views,created,updated}
    "templates": {},        # tid -> {name,price,desc,created}   (ফাইল: templates/{tid}/)
    "payment_codes": {},    # code -> {uid,status,amount,trx,created,...}
    "payment_numbers": [],  # [{id,method,number}]
    "channel_lock": [],     # ["@channel"]
    "settings": {"global_price": 50, "support": "@support"},
    "counters": {"site": 0, "tpl": 0, "pay": 0},
}


def load_data() -> dict:
    data = None
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            shutil.copy(DATA_FILE, DATA_FILE.with_suffix(f".broken.{int(time.time())}"))
            log.error("data.json corrupted — backup created, starting fresh")
    if not isinstance(data, dict):
        data = {}
    for k, v in DEFAULT_DATA.items():
        data.setdefault(k, copy.deepcopy(v))
    for k, v in DEFAULT_DATA["settings"].items():
        data["settings"].setdefault(k, v)
    for k, v in DEFAULT_DATA["counters"].items():
        data["counters"].setdefault(k, v)

    for key in ("users", "sites", "templates", "payment_codes"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    for key in ("payment_numbers", "channel_lock"):
        if not isinstance(data.get(key), list):
            data[key] = []

    for uid, u in list(data["users"].items()):
        if not isinstance(u, dict):
            u = {}
            data["users"][uid] = u
        u.setdefault("id", int(uid) if str(uid).isdigit() else 0)
        u.setdefault("name", "User")
        u.setdefault("username", None)
        try:
            u["balance"] = float(u.get("balance") or 0)
        except (TypeError, ValueError):
            u["balance"] = 0.0
        u.setdefault("joined", int(time.time()))

    for sid, s in list(data["sites"].items()):
        if not isinstance(s, dict) or not s.get("slug"):
            data["sites"].pop(sid, None)
            continue
        s.setdefault("owner", 0)
        s.setdefault("status", "online")
        s.setdefault("source", "custom")
        s.setdefault("template", None)
        s.setdefault("views", 0)
        s.setdefault("created", int(time.time()))
        s.setdefault("updated", s["created"])
    return data


DB = load_data()


def save_data(mark: bool = True) -> None:
    with _SAVE_LOCK:
        payload = None
        for _ in range(5):
            try:
                payload = json.dumps(DB, ensure_ascii=False, indent=2)
                break
            except RuntimeError:  # অন্য থ্রেড ডাটা বদলাচ্ছিল — আবার চেষ্টা
                time.sleep(0.05)
        if payload is None:
            return
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, DATA_FILE)
        if mark:
            BACKUP["dirty"] = True


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def now() -> int:
    return int(time.time())


def esc(s) -> str:
    return html.escape(str(s))


def money(x) -> str:
    s = f"{float(x):,.2f}".rstrip("0").rstrip(".")
    return f"৳{s}"


def parse_num(text: str):
    try:
        v = float(text.translate(BN_DIGITS).replace(",", "").replace("৳", "").strip())
        return v if v >= 0 else None
    except Exception:
        return None


def next_id(kind: str, prefix: str) -> str:
    with _SAVE_LOCK:
        DB["counters"][kind] += 1
        save_data()
        return f"{prefix}{DB['counters'][kind]}"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def get_user(tg_user) -> dict:
    uid = str(tg_user.id)
    u = DB["users"].get(uid)
    if not u:
        u = {
            "id": tg_user.id,
            "name": tg_user.full_name,
            "username": tg_user.username,
            "balance": 0.0,
            "joined": now(),
        }
        DB["users"][uid] = u
        save_data()
    else:
        changed = False
        for k, v in (("id", tg_user.id), ("balance", 0.0), ("joined", now())):
            if k not in u:
                u[k] = v
                changed = True
        if u.get("name") != tg_user.full_name or u.get("username") != tg_user.username:
            u["name"], u["username"] = tg_user.full_name, tg_user.username
            changed = True
        if changed:
            save_data()
    return u


def user_sites(uid: int):
    return [(sid, s) for sid, s in DB["sites"].items() if s["owner"] == uid]


# ── ওয়েব (ব্রাউজার) লগইনের পাসওয়ার্ড — Telegram এ অটো লগইন হয়, এটা শুধু ব্রাউজারের জন্য ──
def _pw_hash(pw: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 120_000).hex()


def web_set_password(uid: int, pw: str) -> None:
    salt = os.urandom(16)
    with _SAVE_LOCK:
        DB.setdefault("web_pw", {})[str(uid)] = {"salt": salt.hex(), "hash": _pw_hash(pw, salt)}
        save_data()


def web_check_password(uid: int, pw: str) -> bool:
    rec = (DB.get("web_pw") or {}).get(str(uid))
    if not rec:
        return False
    try:
        return hmac.compare_digest(_pw_hash(pw, bytes.fromhex(rec["salt"])), rec["hash"])
    except Exception:
        return False


def ib(text, cb=None, url=None, style=None, copy_text=None, web_app=None):
    """InlineKeyboardButton helper — style: primary(নীল) / success(সবুজ) / danger(লাল)."""
    extra = {"api_kwargs": {"style": style}} if style else {}
    if copy_text is not None:
        if CopyTextButton is None:
            return None
        return InlineKeyboardButton(text, copy_text=CopyTextButton(text=str(copy_text)[:256]), **extra)
    if web_app:
        return InlineKeyboardButton(text, web_app=WebAppInfo(url=web_app), **extra)
    if url:
        return InlineKeyboardButton(text, url=url, **extra)
    return InlineKeyboardButton(text, callback_data=cb, **extra)


def webapp_btn(text="🌐 প্রিমিয়াম ওয়েব অ্যাপ"):
    """Mini App খোলার বাটন (HTTPS লিংক না থাকলে None — kb() নিজে বাদ দেয়)।"""
    return ib(text, web_app=WEBAPP_URL, style="primary") if WEBAPP_URL.startswith("https://") else None


def kb(*rows) -> InlineKeyboardMarkup:
    out = []
    for r in rows:
        r = [b for b in r if b is not None]
        if r:
            out.append(r)
    return InlineKeyboardMarkup(out)


CANCEL_KB = kb([ib("❌ বাতিল করুন", cb="cancel", style="danger")])


def main_menu(uid: int) -> ReplyKeyboardMarkup:
    def k(text, style):
        return KeyboardButton(text, api_kwargs={"style": style})

    rows = [
        [k(BTN_ADD, "primary"), k(BTN_MARKET, "success")],
        [k(BTN_SITES, "primary"), k(BTN_WALLET, "success")],
        [k(BTN_ADDMONEY, "primary"), k(BTN_SUPPORT, "danger")],
    ]
    last = []
    if WEBAPP_URL.startswith("https://"):  # কিবোর্ড থেকেই মিনি অ্যাপ খোলার বাটন
        last.append(KeyboardButton(BTN_APP, web_app=WebAppInfo(url=WEBAPP_URL), api_kwargs={"style": "success"}))
    if uid == ADMIN_ID:
        last.append(KeyboardButton(BTN_ADMIN))
    if last:
        rows.append(last)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


async def send(update: Update, text: str, markup=None):
    return await update.effective_chat.send_message(
        text, parse_mode=ParseMode.HTML, reply_markup=markup
    )


async def edit(q, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        await q.message.chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ═══════════════════════════════════════════════════════════════
#  CHANNEL LOCK
# ═══════════════════════════════════════════════════════════════
async def missing_channels(bot, uid: int):
    missing = []
    for ch in DB["channel_lock"]:
        try:
            m = await bot.get_chat_member(ch, uid)
            if m.status in ("left", "kicked") or (
                m.status == "restricted" and not getattr(m, "is_member", True)
            ):
                missing.append(ch)
        except TelegramError as e:
            log.warning("Channel check failed for %s: %s", ch, e)
    return missing


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if uid == ADMIN_ID or not DB["channel_lock"]:
        return True
    missing = await missing_channels(context.bot, uid)
    if not missing:
        return True
    rows = [
        [ib(f"🔵 Join Channel {ch}", url=f"https://t.me/{ch.lstrip('@')}", style="primary")]
        for ch in missing
    ]
    rows.append([ib("🟢 Verify", cb="verify", style="success")])
    await send(
        update,
        f"🔒 <b>চ্যানেল জয়েন বাধ্যতামূলক!</b>\n{LINE}\n\n"
        "বটটি ব্যবহার করতে নিচের চ্যানেলে জয়েন করুন, তারপর "
        "<b>🟢 Verify</b> বাটনে ক্লিক করুন।",
        kb(*rows),
    )
    return False


# ═══════════════════════════════════════════════════════════════
#  SITE STORAGE  (সাইট ফাইল, ZIP, ডোমেইন/স্লাগ)
# ═══════════════════════════════════════════════════════════════
RESERVED_SLUGS = {
    "www", "app", "api", "admin", "s", "t", "static", "assets", "index", "health", "healthz",
    "mail", "ftp", "root", "support", "dashboard", "login", "bot", "telegram", "cdn",
}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")


def site_dir(sid: str) -> Path:
    return SITES_DIR / sid


def tdir(tid: str) -> Path:
    return TEMPLATES_DIR / tid


def clean_slug(raw) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"^https?://", "", s).rstrip("/")
    if "/" in s:
        s = s.split("/")[-1]
    if SITES_DOMAIN and s.endswith("." + SITES_DOMAIN):
        s = s[: -len(SITES_DOMAIN) - 1]
    return s


def slug_taken(slug: str, ignore_sid=None) -> bool:
    return any(s["slug"] == slug and sid != ignore_sid for sid, s in DB["sites"].items())


def slug_error(slug: str, ignore_sid=None):
    if not SLUG_RE.match(slug or ""):
        return (
            "❌ <b>নামটি সঠিক নয়!</b>\n"
            "৩–৩২ অক্ষর, শুধু ইংরেজি ছোট হাতের অক্ষর (a-z), সংখ্যা (0-9) ও হাইফেন (-)। "
            "শুরু ও শেষে হাইফেন চলবে না।\nযেমন: <code>my-shop</code>"
        )
    if slug in RESERVED_SLUGS:
        return "❌ এই নামটি সিস্টেমের জন্য সংরক্ষিত — অন্য নাম দিন।"
    if slug_taken(slug, ignore_sid):
        return "⚠️ <b>এই নামটি আগেই নেওয়া হয়ে গেছে!</b> অন্য নাম দিন।"
    return None


def random_slug() -> str:
    while True:
        s = "site-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        if not slug_taken(s):
            return s


def site_url(slug: str) -> str:
    if SITES_DOMAIN:
        return f"https://{slug}.{SITES_DOMAIN}/"
    return f"{WEBAPP_URL}/s/{slug}/"


def tpl_preview_url(tid: str) -> str:
    return f"{WEBAPP_URL}/t/{tid}/"


def link_ok(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def _zip_plan(zf: zipfile.ZipFile):
    """ZIP এর ফাইলগুলো নিরাপদ ভাবে যাচাই করে [(info, নতুন_নাম)] রিটার্ন করে; সমস্যা হলে ValueError।"""
    entries = []
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/").lstrip("/")
        parts = PurePosixPath(name).parts
        if not parts or ".." in parts or ":" in parts[0]:
            raise ValueError("ZIP এর ভেতরে অনিরাপদ ফাইল পাথ আছে")
        if parts[0] == "__MACOSX" or parts[-1] == ".DS_Store" or parts[-1].startswith("._"):
            continue
        total += info.file_size
        if total > MAX_UNZIPPED_BYTES:
            raise ValueError(f"ZIP খুললে মোট সাইজ {MAX_UNZIPPED_BYTES // 1024 // 1024}MB এর বেশি")
        entries.append((info, "/".join(parts)))
        if len(entries) > MAX_ZIP_FILES:
            raise ValueError(f"ZIP এ {MAX_ZIP_FILES} টির বেশি ফাইল")
    if not entries:
        raise ValueError("ZIP ফাঁকা")
    names = {n for _, n in entries}
    prefix = ""
    if "index.html" not in names:
        tops = {n.split("/")[0] for n in names if "/" in n}
        if len(tops) == 1 and all(n.startswith(next(iter(tops)) + "/") for n in names):
            cand = next(iter(tops)) + "/"
            if cand + "index.html" in names:
                prefix = cand
        if not prefix:
            raise ValueError("ZIP এর মূল ফোল্ডারে <index.html> পাওয়া যায়নি")
    return [(i, n[len(prefix):]) for i, n in entries if n.startswith(prefix)]


def inspect_zip(path: Path):
    """ঠিক থাকলে None, নইলে সমস্যার বিবরণ (টেক্সট)।"""
    try:
        if not zipfile.is_zipfile(path):
            return "এটি সঠিক ZIP ফাইল নয়"
        with zipfile.ZipFile(path) as zf:
            _zip_plan(zf)
        return None
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"ZIP পড়া যায়নি ({e})"


def _extract_zip(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(src) as zf:
        plan = _zip_plan(zf)
        root = dest.resolve()
        total = 0
        for info, name in plan:
            target = (dest / name).resolve()
            if root not in target.parents:
                raise ValueError("অনিরাপদ ফাইল পাথ")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as fin, open(target, "wb") as fout:
                while True:
                    chunk = fin.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UNZIPPED_BYTES:
                        raise ValueError("ZIP খুললে সাইজ অনেক বড়")
                    fout.write(chunk)


def install_package(src: Path, dest: Path):
    """ZIP বা ফোল্ডার থেকে dest পুরো রিপ্লেস করে। (ok, error)"""
    newd = dest.with_name(dest.name + ".new")
    shutil.rmtree(newd, ignore_errors=True)
    newd.mkdir(parents=True, exist_ok=True)
    try:
        if src.is_dir():
            shutil.copytree(src, newd, dirs_exist_ok=True)
        else:
            _extract_zip(src, newd)
    except Exception as e:
        shutil.rmtree(newd, ignore_errors=True)
        return False, str(e)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(newd, dest)
    return True, None


def apply_package(src: Path, dest: Path):
    """.zip / ফোল্ডার → পুরো সাইট রিপ্লেস।  .html → শুধু index.html রিপ্লেস।"""
    if src.is_dir() or src.suffix.lower() == ".zip":
        return install_package(src, dest)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest / "index.html")
        return True, None
    except Exception as e:
        return False, str(e)


def export_dir(d: Path, name: str) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    files = [p for p in d.rglob("*") if p.is_file()]
    stamp = f"{now()}_{random.randint(100, 999)}"
    if len(files) == 1 and files[0] == d / "index.html":
        out = TMP_DIR / f"{name}_{stamp}.html"
        shutil.copyfile(files[0], out)
        return out
    out = TMP_DIR / f"{name}_{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(d).as_posix())
    return out


def deploy_site(uid: int, slug: str, src: Path, source: str, tid, price: float):
    """ব্যালেন্স কেটে সাইট পাবলিশ করে। (True, sid) অথবা (False, কারণ)।
    বট (async) ও ওয়েব অ্যাপ (thread) — দুই জায়গা থেকেই এটা ব্যবহার হয়।"""
    with _SAVE_LOCK:
        user = DB["users"].get(str(uid))
        if not user:
            return False, "ইউজার পাওয়া যায়নি"
        if user["balance"] < price:
            return False, "insufficient"
        err = slug_error(slug)
        if err:
            return False, re.sub(r"<[^>]+>", "", err)
        sid = next_id("site", "s")
        ok, e = apply_package(src, site_dir(sid))
        if not ok:
            shutil.rmtree(site_dir(sid), ignore_errors=True)
            return False, f"সাইট তৈরি করা যায়নি: {e}"
        user["balance"] = round(user["balance"] - price, 2)
        DB["sites"][sid] = {
            "owner": uid,
            "slug": slug,
            "status": "online",
            "source": source,
            "template": tid,
            "views": 0,
            "created": now(),
            "updated": now(),
        }
        save_data()
        return True, sid


def delete_site(sid: str) -> None:
    with _SAVE_LOCK:
        shutil.rmtree(site_dir(sid), ignore_errors=True)
        DB["sites"].pop(sid, None)
        save_data()


# ═══════════════════════════════════════════════════════════════
#  AUTO BACKUP  (এডমিনের চ্যাটে জিপ পাঠিয়ে পিন)
# ═══════════════════════════════════════════════════════════════
def build_backup() -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    save_data(mark=False)
    out = TMP_DIR / f"backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if DATA_FILE.exists():
            z.write(DATA_FILE, "data.json")
        for base in (SITES_DIR, TEMPLATES_DIR):
            if base.exists():
                for p in base.rglob("*"):
                    if p.is_file():
                        z.write(p, p.relative_to(DATA_DIR).as_posix())
    return out


async def do_backup(bot):
    """(ok, বার্তা)"""
    path = await asyncio.to_thread(build_backup)
    try:
        size = path.stat().st_size
        if size > BACKUP_MAX_BYTES:
            if not BACKUP["warned"]:
                BACKUP["warned"] = True
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ <b>ব্যাকআপ অনেক বড় ({human_size(size)})!</b>\n"
                        "Telegram এর লিমিটের (২০MB) কারণে অটো-ব্যাকআপ বন্ধ। "
                        "পুরনো/বড় সাইট মুছুন অথবা Render এ Persistent Disk ব্যবহার করুন।",
                        parse_mode=ParseMode.HTML,
                    )
                except TelegramError:
                    pass
            return False, f"ব্যাকআপ সাইজ {human_size(size)} — লিমিট পার"
        BACKUP["warned"] = False
        with open(path, "rb") as f:
            msg = await bot.send_document(
                ADMIN_ID,
                f,
                filename=path.name,
                caption=f"💾 AUTO-BACKUP • {time.strftime('%Y-%m-%d %H:%M')}\n"
                        f"👥 {len(DB['users'])} ইউজার • 🌐 {len(DB['sites'])} সাইট",
                disable_notification=True,
            )
        try:
            await bot.pin_chat_message(ADMIN_ID, msg.message_id, disable_notification=True)
        except TelegramError as e:
            log.warning("backup pin failed: %s", e)
        old = BACKUP["mid"]
        BACKUP["mid"] = msg.message_id
        BACKUP["dirty"] = False
        if old and old != msg.message_id:
            try:
                await bot.delete_message(ADMIN_ID, old)
            except TelegramError:
                pass
        return True, f"ব্যাকআপ সম্পূর্ণ ({human_size(size)})"
    except TelegramError as e:
        log.warning("backup failed: %s", e)
        return False, f"ব্যাকআপ পাঠানো যায়নি: {e}"
    finally:
        path.unlink(missing_ok=True)


async def maintenance_loop(app: Application):
    while True:
        await asyncio.sleep(30)
        try:
            if VIEWS["dirty"]:
                VIEWS["dirty"] = False
                save_data(mark=False)
            if BACKUP["dirty"] and time.time() - BACKUP["last"] >= BACKUP_INTERVAL:
                BACKUP["last"] = time.time()
                await do_backup(app.bot)
        except Exception:
            log.exception("maintenance error")


# ═══════════════════════════════════════════════════════════════
#  FILE / CODE INTAKE
# ═══════════════════════════════════════════════════════════════
def tmp_path(uid: int, ext: str) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    return TMP_DIR / f"{uid}_{now()}_{random.randint(100, 999)}{ext}"


def clean_code_text(text: str) -> str:
    """মেসেজে পেস্ট করা কোড থেকে ```html ... ``` মার্কডাউন ফেন্স সরায়।"""
    t = text.strip()
    m = re.match(r"^```[A-Za-z0-9_+-]*[ \t]*\n(.*?)\n?[ \t]*```$", t, re.S)
    if m:
        t = m.group(1)
    return t.strip("\n") + "\n"


async def fetch_code_file(context, uid: int, doc):
    fname = (doc.file_name or "").lower()
    ext = Path(fname).suffix
    if ext not in (".html", ".htm", ".zip"):
        return False, "❌ শুধু <b>.html</b> অথবা <b>.zip</b> ফাইল গ্রহণযোগ্য।"
    limit = MAX_ZIP_BYTES if ext == ".zip" else MAX_HTML_BYTES
    if doc.file_size and doc.file_size > limit:
        return False, f"❌ ফাইল অনেক বড়! সর্বোচ্চ <b>{limit // 1024 // 1024} MB</b>।"
    tmp = tmp_path(uid, ".zip" if ext == ".zip" else ".html")
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(custom_path=tmp)
    if ext == ".zip":
        err = await asyncio.to_thread(inspect_zip, tmp)
        if err:
            tmp.unlink(missing_ok=True)
            return False, f"❌ <b>ZIP সমস্যা:</b> {esc(err)}"
        return True, tmp
    try:
        src = tmp.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        tmp.unlink(missing_ok=True)
        return False, "❌ ফাইল অবশ্যই <b>UTF-8</b> এনকোডিং-এ হতে হবে।"
    if "<" not in src or ">" not in src:
        tmp.unlink(missing_ok=True)
        return False, "❌ এটি HTML ফাইল মনে হচ্ছে না।"
    tmp.write_text(src, encoding="utf-8")
    return True, tmp


async def code_from_text(uid: int, text: str):
    src = clean_code_text(text)
    if len(src.encode("utf-8")) > MAX_HTML_BYTES:
        return False, f"❌ কোড অনেক বড়! সর্বোচ্চ <b>{MAX_HTML_BYTES // 1024 // 1024} MB</b>।"
    if "<" not in src or ">" not in src:
        return False, "❌ এটি HTML কোড মনে হচ্ছে না।"
    tmp = tmp_path(uid, ".html")
    tmp.write_text(src, encoding="utf-8")
    return True, tmp


# ═══════════════════════════════════════════════════════════════
#  USER SCREENS
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user)
    context.user_data.pop("state", None)
    if not await gate(update, context):
        return
    await send_welcome(update)


async def send_welcome(update: Update):
    user = update.effective_user
    u = get_user(user)
    sites = user_sites(user.id)
    live = sum(1 for _, s in sites if s["status"] == "online")
    await send(
        update,
        f"👋 <b>স্বাগতম, {esc(user.first_name)}!</b>\n\n"
        f"🚀 <b>Premium Website Hosting</b>\n{LINE}\n\n"
        f"💰 <b>ব্যালেন্স:</b> <code>{money(u['balance'])}</code>\n"
        f"🌐 <b>সাইট:</b> <code>{len(sites)}</code>   🟢 <b>অনলাইন:</b> <code>{live}</code>\n\n"
        "✨ নিজের HTML কোড দিন — সাথে সাথে ডোমেইনসহ লাইভ ওয়েবসাইট!\n"
        "📦 একটা HTML ফাইল বা CSS/JS/ছবিসহ পুরো <b>.zip</b> — দুটোই চলবে।\n\n"
        "👇 <b>নিচের মেনু থেকে বেছে নিন</b>",
        main_menu(user.id),
    )
    wb = webapp_btn()
    if wb:
        await send(
            update,
            f"🌐 <b>Premium Web App</b>\n{LINE}\n\n"
            "ড্যাশবোর্ড, সাইট ম্যানেজমেন্ট, কোড এডিটর ও টেমপ্লেট মার্কেট — সব এক জায়গায়।\n"
            "🔐 <i>আপনার Telegram আইডি দিয়ে অটো লগইন — পাসওয়ার্ড লাগবে না।</i>",
            kb([wb]),
        )


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    wb = webapp_btn()
    await send(update, "🌐 <b>Premium Web App</b> খুলতে নিচের বাটনে চাপুন 👇" if wb
               else "⚠️ ওয়েব অ্যাপের লিংক সেট করা হয়নি (WEBAPP_URL)।", kb([wb]) if wb else None)


async def cmd_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/password নতুন_পাসওয়ার্ড — ব্রাউজারে (Telegram এর বাইরে) লগইনের পাসওয়ার্ড সেট করে।"""
    if update.effective_chat.type != "private":
        return
    user = update.effective_user
    get_user(user)
    pw = " ".join(context.args).strip() if context.args else ""
    if not pw:
        await send(
            update,
            "🔑 <b>ওয়েব পাসওয়ার্ড সেট</b>\n" + LINE + "\n\n"
            "Telegram এর ভেতরে Mini App খুললে পাসওয়ার্ড ছাড়াই অটো লগইন হয়। "
            "ব্রাউজারে লগইন করতে চাইলে পাসওয়ার্ড সেট করুন:\n\n"
            "<code>/password আপনার_পাসওয়ার্ড</code>\n\n"
            f"📌 লগইন আইডি: <code>{user.id}</code>"
            + (f" অথবা <code>@{esc(user.username)}</code>" if user.username else ""),
        )
        return
    try:  # পাসওয়ার্ড লেখা মেসেজটা চ্যাট থেকে মুছে ফেলা
        await update.message.delete()
    except TelegramError:
        pass
    if not 6 <= len(pw) <= 64:
        await send(update, "❌ পাসওয়ার্ড ৬–৬৪ অক্ষরের হতে হবে।")
        return
    await asyncio.to_thread(web_set_password, user.id, pw)
    await send(
        update,
        "✅ <b>ওয়েব পাসওয়ার্ড সেট হয়েছে!</b>\n(নিরাপত্তার জন্য আপনার মেসেজটা মুছে দেওয়া হয়েছে)\n\n"
        f"📌 ব্রাউজারে লগইন আইডি: <code>{user.id}</code>"
        + (f" অথবা <code>@{esc(user.username)}</code>" if user.username else "")
        + "\n🔑 পাসওয়ার্ড: আপনি যেটা দিয়েছেন।",
    )


async def show_add_site(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user)
    price = DB["settings"]["global_price"]
    if u["balance"] < price:
        await send(
            update,
            f"➕ <b>সাইট এড</b>\n{LINE}\n\n"
            f"💸 <b>সাইট হোস্টিং চার্জ:</b> <code>{money(price)}</code>\n"
            f"💰 <b>আপনার ব্যালেন্স:</b> <code>{money(u['balance'])}</code>\n\n"
            "⚠️ <b>ব্যালেন্স যথেষ্ট নয়!</b> আগে <b>💳 এড মানি</b> করুন।",
            kb([ib("💳 এড মানি", cb="am:new", style="primary")]),
        )
        return
    context.user_data["state"] = {"name": "upload_code"}
    await send(
        update,
        f"➕ <b>নতুন ওয়েবসাইট</b>\n{LINE}\n\n"
        f"💸 <b>চার্জ:</b> <code>{money(price)}</code>  |  💰 <b>ব্যালেন্স:</b> <code>{money(u['balance'])}</code>\n\n"
        "📂 <b>ধাপ ১:</b> সাইটের কোড তিনভাবে দিতে পারেন:\n\n"
        "1️⃣ <b>.html ফাইল</b> পাঠান (Document হিসেবে)\n"
        "2️⃣ <b>.zip ফাইল</b> পাঠান — ভেতরে <code>index.html</code> + css/js/ছবি ফোল্ডার\n"
        "3️⃣ <b>HTML কোড লিখে/পেস্ট করে</b> মেসেজ পাঠান — একাধিক মেসেজে হলে সবগুলো পাঠিয়ে "
        "শেষে <b>✅ কোড সম্পূর্ণ</b> বাটন চাপুন।\n\n"
        "💡 <i>কোড বড় হলে ফাইল পাঠানোই ভালো।</i>",
        CANCEL_KB,
    )


def wallet_text(uid: int) -> str:
    u = DB["users"][str(uid)]
    sites = user_sites(uid)
    on = [s for _, s in sites if s["status"] == "online"]
    off = [s for _, s in sites if s["status"] != "online"]
    t = (
        f"💰 <b>আমার ওয়ালেট</b>\n{LINE}\n\n"
        f"💵 <b>বর্তমান ব্যালেন্স:</b> <code>{money(u['balance'])}</code>\n"
        f"🌐 <b>মোট সাইট:</b> <code>{len(sites)}</code>\n"
        f"🟢 <b>অনলাইন:</b> <code>{len(on)}</code>   🔴 <b>অফলাইন:</b> <code>{len(off)}</code>\n"
    )
    if on:
        t += "\n🟢 <b>অনলাইন সাইট:</b>\n" + "\n".join(f"   ▫️ {esc(s['slug'])}" for s in on) + "\n"
    if off:
        t += "\n🔴 <b>অফলাইন সাইট:</b>\n" + "\n".join(f"   ▫️ {esc(s['slug'])}" for s in off) + "\n"
    if not sites:
        t += "\n📭 <i>আপনার কোনো সাইট নেই।</i>\n"
    else:
        t += "\n👇 <i>ম্যানেজ করতে সাইটে ক্লিক করুন</i>"
    return t


def wallet_markup(uid: int):
    btns = [
        ib(("🟢 " if s["status"] == "online" else "🔴 ") + s["slug"], cb=f"sm:{sid}")
        for sid, s in user_sites(uid)
    ]
    rows = [btns[i: i + 2] for i in range(0, len(btns), 2)]
    rows.append([ib("➕ সাইট এড", cb="addsite", style="primary"), ib("💳 এড মানি", cb="am:new", style="success")])
    rows.append([webapp_btn()])
    return kb(*rows)


async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    uid = update.effective_user.id
    await send(update, wallet_text(uid), wallet_markup(uid))


async def show_sites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    uid = update.effective_user.id
    sites = user_sites(uid)
    if not sites:
        await send(
            update,
            f"🌐 <b>আমার সাইট</b>\n{LINE}\n\n📭 <i>আপনার কোনো সাইট নেই।</i>",
            kb([ib("➕ সাইট এড", cb="addsite", style="primary")]),
        )
        return
    lines = "\n".join(
        f"{'🟢' if s['status'] == 'online' else '🔴'} <b>{esc(s['slug'])}</b>\n   🔗 <code>{esc(site_url(s['slug']))}</code>"
        for _, s in sites
    )
    await send(update, f"🌐 <b>আমার সাইট ({len(sites)})</b>\n{LINE}\n\n{lines}\n\n👇 <i>ম্যানেজ করতে সাইটে ক্লিক করুন</i>",
               wallet_markup(uid))


async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = (DB["settings"].get("support") or "").strip()
    url = None
    if s.startswith("@"):
        url = f"https://t.me/{s[1:]}"
    elif s.startswith("http"):
        url = s
    elif s.startswith("t.me"):
        url = "https://" + s
    text = f"📞 <b>সাপোর্ট</b>\n{LINE}\n\n"
    text += f"💬 যেকোনো সমস্যায় যোগাযোগ করুন:\n👉 <b>{esc(s)}</b>" if s else "⚠️ সাপোর্ট এখনো সেট করা হয়নি।"
    await send(update, text, kb([ib("💬 সাপোর্টে যোগাযোগ", url=url, style="danger")]) if url else None)


# ───────────────────────── Site management ─────────────────────────
STATUS_TXT = {"online": "🟢 Online", "offline": "🔴 Offline"}


def site_view(sid: str, viewer: int):
    s = DB["sites"][sid]
    online = s["status"] == "online"
    url = site_url(s["slug"])
    src = "🛒 Template" if s["source"] == "market" else "📂 Custom Code"
    owner_line = ""
    if viewer == ADMIN_ID and s["owner"] != ADMIN_ID:
        ou = DB["users"].get(str(s["owner"]), {})
        owner_line = f"\n👤 <b>Owner:</b> {esc(ou.get('name', '?'))} (<code>{s['owner']}</code>)"
    text = (
        f"🌐 <b>{esc(s['slug'])}</b>\n{LINE}\n\n"
        f"📊 <b>Status:</b> {STATUS_TXT.get(s['status'], s['status'])}\n"
        f"🔗 <b>লিংক:</b> <code>{esc(url)}</code>\n"
        f"👁 <b>ভিজিট:</b> <code>{s.get('views', 0)}</code>\n"
        f"📦 <b>Source:</b> {src}\n"
        f"💾 <b>সাইজ:</b> <code>{human_size(dir_size(site_dir(sid)))}</code>\n"
        f"🕒 <b>Created:</b> <code>{time.strftime('%Y-%m-%d %H:%M', time.localtime(s['created']))}</code>"
        f"{owner_line}"
    )
    open_row = []
    if link_ok(url) and online:
        open_row.append(ib("🔗 সাইট ওপেন", url=url, style="success"))
        if url.startswith("https://"):
            open_row.append(ib("📱 অ্যাপে খুলুন", web_app=url, style="primary"))
    back = "adm:sites" if (viewer == ADMIN_ID and s["owner"] != ADMIN_ID) else "wallet"
    markup = kb(
        open_row,
        [ib("📋 লিংক কপি", copy_text=url) if link_ok(url) else None],
        [
            ib("⏹ অফলাইন করুন", cb=f"sa:off:{sid}", style="danger")
            if online
            else ib("▶️ অনলাইন করুন", cb=f"sa:on:{sid}", style="success"),
            ib("🏷 নাম বদলান", cb=f"sa:rename:{sid}", style="primary"),
        ],
        [ib("✏️ Edit Code", cb=f"sa:edit:{sid}"), ib("📥 কোড ডাউনলোড", cb=f"sa:dl:{sid}")],
        [ib("🗑 Delete", cb=f"sa:delask:{sid}", style="danger")],
        [ib("⬅️ Back", cb=back)],
    )
    return text, markup


async def site_action(update, context, q, uid, action, sid):
    s = DB["sites"].get(sid)
    if not s or (s["owner"] != uid and uid != ADMIN_ID):
        await q.answer("❌ সাইট পাওয়া যায়নি", show_alert=True)
        return
    if action in ("on", "off"):
        s["status"] = "online" if action == "on" else "offline"
        s["updated"] = now()
        save_data()
        await q.answer("🟢 অনলাইন হয়েছে" if action == "on" else "⏹ অফলাইন করা হয়েছে")
    elif action == "edit":
        await q.answer()
        context.user_data["state"] = {"name": "edit_code", "sid": sid}
        await q.message.chat.send_message(
            f"✏️ <b>কোড এডিট — {esc(s['slug'])}</b>\n{LINE}\n\n"
            "📂 নতুন কোড পাঠান:\n"
            "• <b>.html ফাইল / লেখা কোড</b> → শুধু <code>index.html</code> বদলাবে\n"
            "• <b>.zip ফাইল</b> → পুরো সাইট বদলে যাবে\n\n"
            "✍️ লিখে পাঠালে শেষে <b>✅ কোড সম্পূর্ণ</b> চাপুন।\n"
            "💸 <i>এডিট সম্পূর্ণ ফ্রি।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=CANCEL_KB,
        )
        return
    elif action == "rename":
        await q.answer()
        context.user_data["state"] = {"name": "rename_site", "sid": sid}
        await q.message.chat.send_message(
            f"🏷 <b>নতুন নাম লিখুন</b>\n{LINE}\n\n"
            f"বর্তমান: <code>{esc(s['slug'])}</code>\n"
            f"নতুন লিংক হবে: <code>{esc(site_url('NAME'))}</code>\n\n"
            "⚠️ <i>নাম বদলালে পুরনো লিংক আর কাজ করবে না।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=CANCEL_KB,
        )
        return
    elif action == "dl":
        await q.answer("⏳ ফাইল তৈরি হচ্ছে...")
        d = site_dir(sid)
        if not d.exists():
            await q.message.chat.send_message("❌ সাইটের ফাইল পাওয়া যায়নি।")
            return
        out = await asyncio.to_thread(export_dir, d, re.sub(r"[^a-z0-9-]", "", s["slug"]) or sid)
        try:
            with open(out, "rb") as f:
                await q.message.chat.send_document(f, filename=out.name.rsplit("_", 2)[0] + out.suffix,
                                                   caption=f"📥 {s['slug']} — বর্তমান কোড")
        finally:
            out.unlink(missing_ok=True)
        return
    elif action == "delask":
        await q.answer()
        await edit(
            q,
            f"🗑 <b>{esc(s['slug'])}</b> মুছে ফেলবেন?\n\n⚠️ <i>এটি আর ফেরত আনা যাবে না এবং টাকা রিফান্ড হবে না।</i>",
            kb([ib("✅ হ্যাঁ, মুছুন", cb=f"sa:del:{sid}", style="danger"), ib("❌ না", cb=f"sm:{sid}", style="success")]),
        )
        return
    elif action == "del":
        delete_site(sid)
        await q.answer("🗑 মুছে ফেলা হয়েছে")
        if uid == ADMIN_ID and s["owner"] != uid:
            t, m = render_sites_admin()
            await edit(q, t, m)
        else:
            await edit(q, wallet_text(uid), wallet_markup(uid))
        return
    text, markup = site_view(sid, uid)
    await edit(q, text, markup)


# ───────────────────────── Market ─────────────────────────
async def show_market(update: Update, context: ContextTypes.DEFAULT_TYPE, q=None):
    tpls = DB["templates"]
    if not tpls:
        text, markup = f"🛒 <b>টেমপ্লেট মার্কেট</b>\n{LINE}\n\n📭 এখনো কোনো টেমপ্লেট যোগ করা হয়নি। পরে আবার দেখুন!", None
    else:
        text = (
            f"🛒 <b>টেমপ্লেট মার্কেট</b>\n{LINE}\n\n"
            "🔥 রেডি ওয়েবসাইট — শুধু একটা <b>নাম (ডোমেইন)</b> দিলেই লাইভ!\n\n👇 একটি টেমপ্লেট সিলেক্ট করুন:"
        )
        markup = kb(*[[ib(f"🌐 {t['name']}  •  {money(t['price'])}", cb=f"mk:{tid}", style="success")] for tid, t in tpls.items()])
    if q:
        await edit(q, text, markup)
    else:
        await send(update, text, markup)


async def market_item(q, tid: str, uid: int):
    t = DB["templates"].get(tid)
    if not t:
        await q.answer("❌ টেমপ্লেট পাওয়া যায়নি", show_alert=True)
        return
    u = DB["users"][str(uid)]
    prev = tpl_preview_url(tid)
    await edit(
        q,
        f"🌐 <b>{esc(t['name'])}</b>\n{LINE}\n\n"
        f"📝 {esc(t.get('desc') or 'কোনো বিবরণ নেই')}\n\n"
        f"💸 <b>দাম:</b> <code>{money(t['price'])}</code>\n"
        f"💰 <b>আপনার ব্যালেন্স:</b> <code>{money(u['balance'])}</code>",
        kb(
            [ib("👁 লাইভ প্রিভিউ", url=prev, style="primary") if link_ok(prev) else None],
            [ib("🛒 কিনুন ও পাবলিশ করুন", cb=f"mkbuy:{tid}", style="success")],
            [ib("⬅️ Back", cb="market")],
        ),
    )


# ───────────────────────── Add Money ─────────────────────────
METHOD_EMOJI = {"bKash": "🟣", "Nagad": "🟠", "Rocket": "🟪", "Crypto": "🪙"}


def gen_pay_code() -> str:
    for _ in range(2000):
        code = f"PAY-{random.randint(1000, 9999)}"
        if code not in DB["payment_codes"]:
            return code
    while True:
        code = f"PAY-{random.randint(10000, 99999)}"
        if code not in DB["payment_codes"]:
            return code


async def show_add_money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    get_user(update.effective_user)
    nums = DB["payment_numbers"]
    if not nums:
        await send(update, f"💳 <b>এড মানি</b>\n{LINE}\n\n⚠️ এখনো পেমেন্ট নাম্বার সেট করা হয়নি। এডমিনের সাথে যোগাযোগ করুন।")
        return
    for c in DB["payment_codes"].values():  # পুরনো অব্যবহৃত কোড এক্সপায়ার
        if c["uid"] == uid and c["status"] == "created":
            c["status"] = "expired"
    code = gen_pay_code()
    DB["payment_codes"][code] = {"uid": uid, "status": "created", "amount": None, "trx": None, "created": now()}
    save_data()
    lines = "\n".join(
        f"{METHOD_EMOJI.get(n['method'], '💠')} <b>{esc(n['method'])}:</b> <code>{esc(n['number'])}</code>" for n in nums
    )
    rows = [[ib(f"📋 কপি: {n['method']}", copy_text=n["number"], style="primary")] for n in nums[:6]]
    rows.append([ib("📋 কপি: Reference কোড", copy_text=code, style="primary")])
    rows.append([ib("✅ I Paid", cb=f"am:paid:{code}", style="success"), ib("❌ Cancel", cb=f"am:cancel:{code}", style="danger")])
    await send(
        update,
        f"💳 <b>এড মানি</b>\n{LINE}\n\n"
        f"🔖 <b>আপনার Reference কোড:</b>\n<code>{code}</code>\n\n"
        f"📲 <b>এই নাম্বারে টাকা পাঠান:</b>\n{lines}\n\n"
        f"📝 Reference এ লিখুন: <code>{code}</code>\n\n"
        "👆 <i>নাম্বার/কোডের উপর ট্যাপ করলে কপি হবে।</i>\n"
        "✅ টাকা পাঠানো শেষে <b>I Paid</b> বাটনে ক্লিক করুন।\n"
        "⚠️ <i>এই কোড শুধু একবারই ব্যবহার করা যাবে।</i>",
        kb(*rows),
    )


async def pay_callback(update, context, q, uid, parts):
    act, code = parts[1], parts[2]
    pc = DB["payment_codes"].get(code)
    if not pc or pc["uid"] != uid:
        await q.answer("❌ কোড পাওয়া যায়নি", show_alert=True)
        return
    if pc["status"] != "created":
        await q.answer("⚠️ এই কোড আগেই ব্যবহার/এক্সপায়ার হয়েছে!", show_alert=True)
        return
    if act == "cancel":
        pc["status"] = "cancelled"
        save_data()
        await q.answer()
        await edit(q, "❌ <b>পেমেন্ট রিকোয়েস্ট বাতিল করা হয়েছে।</b>")
    elif act == "paid":
        await q.answer()
        context.user_data["state"] = {"name": "pay_amount", "code": code}
        await q.message.chat.send_message(
            "💵 <b>কত টাকা পাঠিয়েছেন?</b>\n\nশুধু সংখ্যা লিখুন (যেমন: <code>100</code>)",
            parse_mode=ParseMode.HTML,
            reply_markup=CANCEL_KB,
        )


async def approval(q, context, code: str, ok: bool):
    with _SAVE_LOCK:
        pc = DB["payment_codes"].get(code)
        if not pc or pc["status"] != "submitted":
            processed = True
        else:
            processed = False
            u = DB["users"].get(str(pc["uid"]))
            if ok:
                pc["status"] = "approved"
                u["balance"] = round(u["balance"] + pc["amount"], 2)
            else:
                pc["status"] = "rejected"
            pc["resolved"] = now()
            save_data()
    if processed:
        await q.answer("⚠️ আগেই প্রসেস করা হয়েছে!", show_alert=True)
        return
    await q.answer("✅ Approved" if ok else "❌ Rejected")
    await edit(q, q.message.text_html + f"\n\n{'✅ <b>APPROVED</b>' if ok else '❌ <b>REJECTED</b>'}")
    try:
        if ok:
            await context.bot.send_message(
                pc["uid"],
                f"✅ <b>পেমেন্ট অ্যাপ্রুভড!</b>\n{LINE}\n\n💵 <b>যোগ হয়েছে:</b> <code>{money(pc['amount'])}</code>\n"
                f"💰 <b>নতুন ব্যালেন্স:</b> <code>{money(u['balance'])}</code>\n🔖 <code>{code}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await context.bot.send_message(
                pc["uid"],
                f"❌ <b>পেমেন্ট রিজেক্ট করা হয়েছে</b>\n{LINE}\n\n🔖 <code>{code}</code>\n"
                "সমস্যা হলে সাপোর্টে যোগাযোগ করুন।",
                parse_mode=ParseMode.HTML,
            )
    except TelegramError:
        pass


def pay_request_text(code: str) -> str:
    pc = DB["payment_codes"][code]
    u = DB["users"].get(str(pc["uid"]), {})
    uname = f"@{u['username']}" if u.get("username") else "—"
    return (
        f"💳 <b>নতুন পেমেন্ট রিকোয়েস্ট</b>\n{LINE}\n\n"
        f"👤 <b>User:</b> {esc(u.get('name', '?'))} ({esc(uname)})\n"
        f"🆔 <b>ID:</b> <code>{pc['uid']}</code>\n"
        f"🔖 <b>Code:</b> <code>{code}</code>\n"
        f"💵 <b>Amount:</b> <code>{money(pc['amount'])}</code>\n"
        f"🧾 <b>TrxID:</b> <code>{esc(pc['trx'])}</code>"
    )


def pay_request_markup(code: str):
    return kb([ib("✅ Approve", cb=f"ap:ok:{code}", style="success"), ib("❌ Reject", cb=f"ap:no:{code}", style="danger")])


# ═══════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ═══════════════════════════════════════════════════════════════
def admin_home():
    pend = sum(1 for c in DB["payment_codes"].values() if c["status"] == "submitted")
    text = f"👑 <b>এডমিন প্যানেল</b>\n{LINE}\n\n👇 একটি অপশন বেছে নিন:"
    markup = kb(
        [ib("💳 পেমেন্ট নাম্বার", cb="adm:pay", style="primary"), ib("💲 দাম ঠিক করা", cb="adm:price", style="success")],
        [ib("🛍 টেমপ্লেট মার্কেট", cb="adm:mk", style="primary"), ib("➕ মার্কেটে টেমপ্লেট এড", cb="adm:tplnew", style="success")],
        [ib("🌐 সব সাইট", cb="adm:sites", style="primary"), ib("🔒 চ্যানেল লক", cb="adm:ch", style="success")],
        [ib(f"⏳ Pending Payments ({pend})", cb="adm:pending", style="danger")],
        [ib("📞 সাপোর্ট সেট", cb="adm:support"), ib("📊 স্ট্যাটস", cb="adm:stats")],
        [ib("💾 এখনই ব্যাকআপ", cb="adm:backup")],
    )
    return text, markup


async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    t, m = admin_home()
    await send(update, t, m)


def render_pay_admin():
    nums = DB["payment_numbers"]
    text = f"💳 <b>পেমেন্ট নাম্বার</b>\n{LINE}\n\n"
    text += (
        "\n".join(f"{METHOD_EMOJI.get(n['method'], '💠')} <b>{esc(n['method'])}:</b> <code>{esc(n['number'])}</code>" for n in nums)
        if nums
        else "📭 কোনো নাম্বার নেই।"
    )
    rows = [[ib(f"🗑 {n['method']} • {n['number']}", cb=f"adm:payd:{n['id']}", style="danger")] for n in nums]
    rows.append([ib("➕ নাম্বার এড করুন", cb="adm:payadd", style="success")])
    rows.append([ib("⬅️ Back", cb="adm:home")])
    return text, kb(*rows)


def render_price_admin():
    text = (
        f"💲 <b>দাম ঠিক করা</b>\n{LINE}\n\n"
        f"📂 <b>Global Price</b> (নিজের কোড দিয়ে সাইট পাবলিশ): <code>{money(DB['settings']['global_price'])}</code>\n\n"
        "🛒 <b>মার্কেট টেমপ্লেটের দাম:</b>\n"
    )
    rows = [[ib(f"✏️ Global Price: {money(DB['settings']['global_price'])}", cb="adm:gprice", style="success")]]
    if DB["templates"]:
        for tid, t in DB["templates"].items():
            rows.append([ib(f"🌐 {t['name']} • {money(t['price'])}", cb=f"adm:tp:{tid}", style="primary")])
    else:
        text += "   📭 মার্কেটে কোনো টেমপ্লেট নেই\n"
    rows.append([ib("⬅️ Back", cb="adm:home")])
    return text, kb(*rows)


def render_market_admin():
    tpls = DB["templates"]
    text = f"🛍 <b>টেমপ্লেট মার্কেট (এডমিন)</b>\n{LINE}\n\n"
    if tpls:
        text += f"🌐 <b>মোট টেমপ্লেট:</b> <code>{len(tpls)}</code>\n\n👇 কোড এডিট / ডিলিট করতে সিলেক্ট করুন:"
    else:
        text += "📭 মার্কেটে কোনো টেমপ্লেট নেই।"
    rows = [[ib(f"🌐 {t['name']}  •  {money(t['price'])}", cb=f"adm:mki:{tid}", style="primary")] for tid, t in tpls.items()]
    rows.append([ib("➕ নতুন টেমপ্লেট এড", cb="adm:tplnew", style="success")])
    rows.append([ib("⬅️ Back", cb="adm:home")])
    return text, kb(*rows)


def render_market_item(tid: str):
    t = DB["templates"][tid]
    sold = sum(1 for s in DB["sites"].values() if s.get("template") == tid)
    prev = tpl_preview_url(tid)
    text = (
        f"🌐 <b>{esc(t['name'])}</b>\n{LINE}\n\n"
        f"💸 <b>দাম:</b> <code>{money(t['price'])}</code>\n"
        f"🛒 <b>বিক্রি:</b> <code>{sold}</code>\n"
        f"📝 {esc(t.get('desc') or '-')}"
    )
    markup = kb(
        [ib("👁 প্রিভিউ", url=prev) if link_ok(prev) else None],
        [ib("✏️ কোড এডিট", cb=f"adm:mkedit:{tid}", style="primary"), ib("📥 বর্তমান কোড", cb=f"adm:mkget:{tid}")],
        [ib("💲 দাম পরিবর্তন", cb=f"adm:tpprice:{tid}", style="success")],
        [ib("🗑 ডিলিট", cb=f"adm:mkdelask:{tid}", style="danger")],
        [ib("⬅️ Back", cb="adm:mk")],
    )
    return text, markup


def render_channels_admin():
    chs = DB["channel_lock"]
    text = f"🔒 <b>চ্যানেল জয়েন লক</b>\n{LINE}\n\n"
    text += "\n".join(f"📢 {esc(c)}" for c in chs) if chs else "🔓 কোনো লক সেট নেই।"
    text += "\n\n⚠️ <i>বটকে অবশ্যই চ্যানেলের Admin বানাতে হবে।</i>"
    rows = [[ib(f"🗑 {c}", cb=f"adm:chdel:{i}", style="danger")] for i, c in enumerate(chs)]
    rows.append([ib("➕ চ্যানেল এড করুন", cb="adm:chadd", style="success")])
    rows.append([ib("⬅️ Back", cb="adm:home")])
    return text, kb(*rows)


def render_sites_admin():
    items = sorted(DB["sites"].items(), key=lambda kv: kv[1]["created"], reverse=True)
    text = f"🌐 <b>সব সাইট (এডমিন)</b>\n{LINE}\n\n"
    if items:
        text += f"📦 <b>মোট:</b> <code>{len(items)}</code>  (সর্বশেষ ৪০টি দেখানো হচ্ছে)\n\n👇 ম্যানেজ করতে সিলেক্ট করুন:"
    else:
        text += "📭 কোনো সাইট নেই।"
    rows = [
        [ib(f"{'🟢' if s['status'] == 'online' else '🔴'} {s['slug']} • {s['owner']}", cb=f"sm:{sid}")]
        for sid, s in items[:40]
    ]
    rows.append([ib("⬅️ Back", cb="adm:home")])
    return text, kb(*rows)


async def admin_callback(update, context, q, data):
    parts = data.split(":")
    act = parts[1]
    await q.answer()

    if act == "home":
        context.user_data.pop("state", None)
        t, m = admin_home()
        await edit(q, t, m)

    elif act == "pay":
        t, m = render_pay_admin()
        await edit(q, t, m)
    elif act == "payadd":
        rows = [[ib(f"{METHOD_EMOJI[m]} {m}", cb=f"adm:payam:{m}", style="primary")] for m in METHOD_EMOJI]
        rows.append([ib("⬅️ Back", cb="adm:pay")])
        await edit(q, "➕ <b>পেমেন্ট মেথড সিলেক্ট করুন:</b>", kb(*rows))
    elif act == "payam":
        method = parts[2]
        context.user_data["state"] = {"name": "adm_pay_num", "method": method}
        await edit(q, f"📲 <b>{esc(method)}</b> এর নাম্বার / ওয়ালেট এড্রেস পাঠান:", CANCEL_KB)
    elif act == "payd":
        DB["payment_numbers"] = [n for n in DB["payment_numbers"] if n["id"] != parts[2]]
        save_data()
        t, m = render_pay_admin()
        await edit(q, t, m)

    elif act == "price":
        t, m = render_price_admin()
        await edit(q, t, m)
    elif act == "gprice":
        context.user_data["state"] = {"name": "adm_global_price"}
        await edit(q, "💲 <b>Global Price</b> লিখুন (নিজের কোড দিয়ে সাইট পাবলিশ করার চার্জ):", CANCEL_KB)
    elif act == "tp":
        tid = parts[2]
        t = DB["templates"].get(tid)
        if not t:
            return
        await edit(
            q,
            f"🌐 <b>{esc(t['name'])}</b>\n{LINE}\n\n💸 <b>দাম:</b> <code>{money(t['price'])}</code>\n📝 {esc(t.get('desc') or '-')}",
            kb(
                [ib("✏️ দাম পরিবর্তন", cb=f"adm:tpprice:{tid}", style="success")],
                [ib("🗑 মার্কেট থেকে মুছুন", cb=f"adm:tpdel:{tid}", style="danger")],
                [ib("⬅️ Back", cb="adm:price")],
            ),
        )
    elif act == "tpprice":
        context.user_data["state"] = {"name": "adm_tpl_price_edit", "tid": parts[2]}
        await edit(q, "💲 এই টেমপ্লেটের নতুন দাম লিখুন:", CANCEL_KB)
    elif act == "tpdel":
        t = DB["templates"].pop(parts[2], None)
        if t:
            shutil.rmtree(tdir(parts[2]), ignore_errors=True)
            save_data()
        t, m = render_price_admin()
        await edit(q, t, m)

    elif act == "mk":
        context.user_data.pop("state", None)
        t, m = render_market_admin()
        await edit(q, t, m)
    elif act == "mki":
        if parts[2] not in DB["templates"]:
            t, m = render_market_admin()
            await edit(q, t, m)
            return
        t, m = render_market_item(parts[2])
        await edit(q, t, m)
    elif act == "mkedit":
        t = DB["templates"].get(parts[2])
        if not t:
            return
        context.user_data["state"] = {"name": "adm_tpl_edit", "tid": parts[2]}
        await edit(
            q,
            f"✏️ <b>কোড এডিট — {esc(t['name'])}</b>\n{LINE}\n\n"
            "📂 নতুন কোড পাঠান:\n"
            "• <b>.html ফাইল / লেখা কোড</b> → শুধু <code>index.html</code> বদলাবে\n"
            "• <b>.zip ফাইল</b> → পুরো টেমপ্লেট বদলে যাবে\n"
            "(লিখে পাঠালে শেষে <b>✅ কোড সম্পূর্ণ</b> চাপুন)\n\n"
            "ℹ️ <i>নতুন কোড শুধু ভবিষ্যতে কেনা সাইটে যাবে; আগে কেনা সাইট অপরিবর্তিত থাকবে।</i>",
            CANCEL_KB,
        )
    elif act == "mkget":
        t = DB["templates"].get(parts[2])
        if not t or not tdir(parts[2]).exists():
            await q.message.chat.send_message("❌ টেমপ্লেটের ফাইল পাওয়া যায়নি।")
            return
        out = await asyncio.to_thread(export_dir, tdir(parts[2]), parts[2])
        try:
            fname = re.sub(r"[^A-Za-z0-9_-]", "_", t["name"]) or parts[2]
            with open(out, "rb") as f:
                await q.message.chat.send_document(f, filename=fname + out.suffix, caption=f"📥 {t['name']} — বর্তমান কোড")
        finally:
            out.unlink(missing_ok=True)
    elif act == "mkdelask":
        t = DB["templates"].get(parts[2])
        if not t:
            return
        await edit(
            q,
            f"🗑 <b>{esc(t['name'])}</b> মার্কেট থেকে ডিলিট করবেন?\n\n"
            "⚠️ <i>টেমপ্লেটের ফাইল মুছে যাবে। যারা আগে কিনেছে তাদের সাইট অপরিবর্তিত থাকবে।</i>",
            kb([ib("✅ হ্যাঁ, ডিলিট", cb=f"adm:mkdel:{parts[2]}", style="danger"), ib("❌ না", cb=f"adm:mki:{parts[2]}", style="success")]),
        )
    elif act == "mkdel":
        t = DB["templates"].pop(parts[2], None)
        if t:
            shutil.rmtree(tdir(parts[2]), ignore_errors=True)
            save_data()
        t, m = render_market_admin()
        await edit(q, "✅ <b>টেমপ্লেট ডিলিট হয়েছে।</b>\n\n" + t, m)

    elif act == "tplnew":
        context.user_data["state"] = {"name": "adm_tpl_name"}
        await edit(q, f"➕ <b>মার্কেটে নতুন টেমপ্লেট</b>\n{LINE}\n\n<b>ধাপ ১/৪:</b> টেমপ্লেটের <b>নাম</b> লিখুন:", CANCEL_KB)

    elif act == "sites":
        context.user_data.pop("state", None)
        t, m = render_sites_admin()
        await edit(q, t, m)

    elif act == "ch":
        t, m = render_channels_admin()
        await edit(q, t, m)
    elif act == "chadd":
        context.user_data["state"] = {"name": "adm_ch_add"}
        await edit(q, "📢 চ্যানেলের <b>@username</b> পাঠান (যেমন: <code>@mychannel</code>):", CANCEL_KB)
    elif act == "chdel":
        i = int(parts[2])
        if 0 <= i < len(DB["channel_lock"]):
            DB["channel_lock"].pop(i)
            save_data()
        t, m = render_channels_admin()
        await edit(q, t, m)

    elif act == "pending":
        pend = [c for c, v in DB["payment_codes"].items() if v["status"] == "submitted"]
        if not pend:
            await edit(q, "✅ <b>কোনো Pending পেমেন্ট নেই।</b>", kb([ib("⬅️ Back", cb="adm:home")]))
            return
        await edit(q, f"⏳ <b>{len(pend)}টি Pending পেমেন্ট</b> নিচে পাঠানো হলো 👇", kb([ib("⬅️ Back", cb="adm:home")]))
        for code in pend[:15]:
            await q.message.chat.send_message(
                pay_request_text(code), parse_mode=ParseMode.HTML, reply_markup=pay_request_markup(code)
            )

    elif act == "support":
        context.user_data["state"] = {"name": "adm_support"}
        await edit(q, "📞 সাপোর্ট <b>@username</b> বা লিংক পাঠান:", CANCEL_KB)

    elif act == "backup":
        wait = await q.message.chat.send_message("⏳ <b>ব্যাকআপ তৈরি হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        ok, msg = await do_backup(context.bot)
        await wait.edit_text(("✅ " if ok else "❌ ") + esc(msg) + ("\n📌 <i>চ্যাটে পিন করা আছে।</i>" if ok else ""),
                             parse_mode=ParseMode.HTML)

    elif act == "stats":
        total_bal = sum(u["balance"] for u in DB["users"].values())
        online = sum(1 for s in DB["sites"].values() if s["status"] == "online")
        views = sum(s.get("views", 0) for s in DB["sites"].values())
        pend = sum(1 for c in DB["payment_codes"].values() if c["status"] == "submitted")
        dep = sum(c["amount"] or 0 for c in DB["payment_codes"].values() if c["status"] == "approved")
        store = await asyncio.to_thread(lambda: dir_size(SITES_DIR) + dir_size(TEMPLATES_DIR))
        await edit(
            q,
            f"📊 <b>স্ট্যাটস</b>\n{LINE}\n\n"
            f"👥 <b>Users:</b> <code>{len(DB['users'])}</code>\n"
            f"🌐 <b>Total Sites:</b> <code>{len(DB['sites'])}</code>\n"
            f"🟢 <b>Online:</b> <code>{online}</code>\n"
            f"👁 <b>মোট ভিজিট:</b> <code>{views}</code>\n"
            f"🛒 <b>Market Templates:</b> <code>{len(DB['templates'])}</code>\n"
            f"💾 <b>স্টোরেজ:</b> <code>{human_size(store)}</code>\n"
            f"💰 <b>Users' Total Balance:</b> <code>{money(total_bal)}</code>\n"
            f"📈 <b>মোট ডিপোজিট:</b> <code>{money(dep)}</code>\n"
            f"⏳ <b>Pending Payments:</b> <code>{pend}</code>",
            kb([ib("⬅️ Back", cb="adm:home")]),
        )


# ═══════════════════════════════════════════════════════════════
#  SITE NAME → DEPLOY
# ═══════════════════════════════════════════════════════════════
def name_prompt_text(extra: str = "") -> str:
    return (
        f"{extra}🏷 <b>ধাপ ২:</b> সাইটের <b>নাম (ডোমেইন)</b> লিখুন\n{LINE}\n\n"
        "শুধু ইংরেজি ছোট অক্ষর, সংখ্যা ও হাইফেন — যেমন: <code>my-shop</code>\n"
        f"🔗 লিংক হবে: <code>{esc(site_url('my-shop'))}</code>"
    )


NAME_KB = kb(
    [ib("🎲 র‍্যান্ডম নাম দিন", cb="rndname", style="primary")],
    [ib("❌ বাতিল করুন", cb="cancel", style="danger")],
)


async def process_site_name(update: Update, context: ContextTypes.DEFAULT_TYPE, st: dict, text: str):
    slug = clean_slug(text)
    err = slug_error(slug)
    if err:
        await send(update, err, NAME_KB)
        return
    wait = await send(update, "🚀 <b>সাইট পাবলিশ হচ্ছে...</b>")
    await run_deploy(context, update.effective_user.id, st, slug, wait)


async def run_deploy(context, uid: int, st: dict, slug: str, wait):
    if st["name"] == "site_name":
        src, price, source, tid = Path(st["file"]), DB["settings"]["global_price"], "custom", None
        if not src.exists():
            context.user_data.pop("state", None)
            await wait.edit_text("❌ কোড ফাইল পাওয়া যায়নি। আবার শুরু করুন।")
            return
    else:
        tid = st["tid"]
        tpl = DB["templates"].get(tid)
        if not tpl or not tdir(tid).exists():
            context.user_data.pop("state", None)
            await wait.edit_text("❌ এই টেমপ্লেট আর মার্কেটে নেই।")
            return
        src, price, source = tdir(tid), tpl["price"], "market"

    ok, res = await asyncio.to_thread(deploy_site, uid, slug, src, source, tid, price)
    if not ok:
        if res == "insufficient":
            context.user_data.pop("state", None)
            if source == "custom":
                src.unlink(missing_ok=True)
            u = DB["users"][str(uid)]
            await wait.edit_text(
                f"⚠️ <b>ব্যালেন্স যথেষ্ট নয়!</b>\n💸 প্রয়োজন: <code>{money(price)}</code> | 💰 আছে: <code>{money(u['balance'])}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb([ib("💳 এড মানি", cb="am:new", style="primary")]),
            )
        else:  # নাম/অন্য সমস্যা — স্টেট রেখে আবার চেষ্টা করতে দেওয়া
            await wait.edit_text(f"❌ {esc(res)}\n\nঅন্য নাম লিখে পাঠান।", parse_mode=ParseMode.HTML, reply_markup=NAME_KB)
        return

    context.user_data.pop("state", None)
    if source == "custom":
        src.unlink(missing_ok=True)
    u = DB["users"][str(uid)]
    url = site_url(slug)
    await wait.edit_text(
        f"✅ <b>ওয়েবসাইট লাইভ হয়েছে!</b>\n{LINE}\n\n"
        f"🌐 <b>লিংক:</b> <code>{esc(url)}</code>\n"
        f"💸 <b>চার্জ:</b> <code>{money(price)}</code>\n"
        f"💰 <b>বাকি ব্যালেন্স:</b> <code>{money(u['balance'])}</code>\n"
        f"🟢 <b>Status:</b> Online\n\n"
        "ℹ️ কোড বদলাতে/অফলাইন করতে <b>⚙️ ম্যানেজ</b> বাটন ব্যবহার করুন।",
        parse_mode=ParseMode.HTML,
        reply_markup=kb(
            [ib("🔗 সাইট ওপেন", url=url, style="success") if link_ok(url) else None,
             ib("📱 অ্যাপে খুলুন", web_app=url, style="primary") if url.startswith("https://") else None],
            [ib("📋 লিংক কপি", copy_text=url) if link_ok(url) else None],
            [ib("⚙️ ম্যানেজ", cb=f"sm:{res}", style="primary")],
        ),
    )


# ═══════════════════════════════════════════════════════════════
#  MESSAGE HANDLERS
# ═══════════════════════════════════════════════════════════════
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    get_user(user)
    text = (update.message.text or "").strip()

    # ── Main menu buttons ──
    if text in MENU_LABELS:
        context.user_data.pop("state", None)
        if text == BTN_ADMIN and uid != ADMIN_ID:
            return
        if not await gate(update, context):
            return
        await MENU_ACTIONS[text](update, context)
        return

    st = context.user_data.get("state")
    if not st:
        return
    if not await gate(update, context):
        return
    name = st["name"]

    if name in ("site_name", "market_name"):
        await process_site_name(update, context, st, text)

    elif name == "rename_site":
        sid = st["sid"]
        s = DB["sites"].get(sid)
        if not s or (s["owner"] != uid and uid != ADMIN_ID):
            context.user_data.pop("state", None)
            await send(update, "❌ সাইট পাওয়া যায়নি।")
            return
        slug = clean_slug(text)
        err = slug_error(slug, ignore_sid=sid)
        if err:
            await send(update, err, CANCEL_KB)
            return
        with _SAVE_LOCK:
            if slug_taken(slug, ignore_sid=sid):
                await send(update, "⚠️ এই নামটি আগেই নেওয়া হয়ে গেছে!", CANCEL_KB)
                return
            s["slug"], s["updated"] = slug, now()
            save_data()
        context.user_data.pop("state", None)
        t, m = site_view(sid, uid)
        await send(update, "✅ <b>নাম বদলানো হয়েছে!</b>\n\n" + t, m)

    elif name in CODE_STATES:
        if name in ADMIN_CODE_STATES and uid != ADMIN_ID:
            return
        await collect_code(update, context, st)

    # ── Add money ──
    elif name == "pay_amount":
        amt = parse_num(text)
        if not amt or amt <= 0:
            await send(update, "❌ সঠিক সংখ্যা লিখুন (যেমন: <code>100</code>)", CANCEL_KB)
            return
        context.user_data["state"] = {"name": "pay_trx", "code": st["code"], "amount": amt}
        await send(update, "🧾 আপনার পেমেন্টের <b>Transaction ID (TrxID)</b> লিখুন:", CANCEL_KB)

    elif name == "pay_trx":
        trx = text.upper()[:40]
        code = st["code"]
        pc = DB["payment_codes"].get(code)
        if not pc or pc["status"] != "created":
            context.user_data.pop("state", None)
            await send(update, "⚠️ এই কোড আগেই ব্যবহার হয়েছে বা এক্সপায়ার!")
            return
        if len(trx) < 4:
            await send(update, "❌ সঠিক TrxID দিন।", CANCEL_KB)
            return
        if any(c.get("trx") == trx and c["status"] in ("submitted", "approved") for c in DB["payment_codes"].values()):
            await send(update, "🚫 <b>এই TrxID আগেই ব্যবহার করা হয়েছে!</b>", CANCEL_KB)
            return
        pc.update({"status": "submitted", "amount": st["amount"], "trx": trx, "submitted": now()})
        save_data()
        context.user_data.pop("state", None)
        await send(
            update,
            f"✅ <b>রিকোয়েস্ট জমা হয়েছে!</b>\n{LINE}\n\n🔖 <code>{code}</code>\n💵 <code>{money(pc['amount'])}</code>\n\n"
            "⏳ এডমিন যাচাই করে ব্যালেন্স যোগ করবেন। অনুগ্রহ করে অপেক্ষা করুন।",
        )
        try:
            await context.bot.send_message(
                ADMIN_ID, pay_request_text(code), parse_mode=ParseMode.HTML, reply_markup=pay_request_markup(code)
            )
        except TelegramError as e:
            log.error("Admin notify failed: %s", e)

    # ── Admin states ──
    elif uid == ADMIN_ID:
        await admin_text_state(update, context, st, text)


CODE_DONE_KB = kb(
    [ib("✅ কোড সম্পূর্ণ", cb="codedone", style="success")],
    [ib("❌ বাতিল করুন", cb="cancel", style="danger")],
)


async def collect_code(update: Update, context: ContextTypes.DEFAULT_TYPE, st: dict):
    """লেখা কোডের অংশ জমা করে; ইউজার '✅ কোড সম্পূর্ণ' চাপলে প্রসেস হয়।"""
    msg = update.message
    buf = st.setdefault("buf", {})
    buf[msg.message_id] = msg.text or ""  # message_id দিয়ে সাজানো হয় — ক্রম ঠিক থাকে
    total = sum(len(v) for v in buf.values())
    old = st.get("status_mid")
    status = await send(
        update,
        f"📝 <b>কোড গ্রহণ হচ্ছে...</b>\n{LINE}\n\n"
        f"📨 <b>অংশ:</b> <code>{len(buf)}</code>  |  🔤 <b>অক্ষর:</b> <code>{total}</code>\n\n"
        "আরও অংশ থাকলে পাঠান। কোড শেষ হলে <b>✅ কোড সম্পূর্ণ</b> চাপুন।",
        CODE_DONE_KB,
    )
    st["status_mid"] = status.message_id
    if old:
        try:
            await context.bot.delete_message(update.effective_chat.id, old)
        except TelegramError:
            pass


async def code_done(update, context, q, uid):
    st = context.user_data.get("state")
    if not st or st["name"] not in CODE_STATES:
        await q.answer("⚠️ কোনো কাজ চলছে না। মেনু থেকে আবার শুরু করুন।", show_alert=True)
        return
    if st["name"] in ADMIN_CODE_STATES and uid != ADMIN_ID:
        await q.answer("⛔ Access denied", show_alert=True)
        return
    buf = st.get("buf") or {}
    if not buf:
        await q.answer("⚠️ আগে কোড লিখে পাঠান।", show_alert=True)
        return
    await q.answer()
    text = "\n".join(buf[k] for k in sorted(buf))
    wait = await q.message.chat.send_message("⏳ <b>কোড যাচাই করা হচ্ছে...</b>", parse_mode=ParseMode.HTML)
    ok, res = await code_from_text(uid, text)
    if not ok:
        st["buf"], st["status_mid"] = {}, None  # ভুল কোড বাদ — নতুন করে পাঠাতে হবে
        await wait.edit_text(res + "\n\nসঠিক কোড আবার পাঠান।", parse_mode=ParseMode.HTML, reply_markup=CANCEL_KB)
        return
    await after_code(context, uid, st, res, wait)


async def admin_text_state(update, context, st, text):
    name = st["name"]
    back = kb([ib("⬅️ এডমিন প্যানেল", cb="adm:home")])

    if name == "adm_pay_num":
        DB["payment_numbers"].append({"id": next_id("pay", "p"), "method": st["method"], "number": text[:120]})
        save_data()
        context.user_data.pop("state", None)
        t, m = render_pay_admin()
        await send(update, "✅ <b>নাম্বার এড হয়েছে!</b>\n\n" + t, m)

    elif name == "adm_global_price":
        v = parse_num(text)
        if v is None:
            await send(update, "❌ সঠিক সংখ্যা লিখুন।", CANCEL_KB)
            return
        DB["settings"]["global_price"] = v
        save_data()
        context.user_data.pop("state", None)
        t, m = render_price_admin()
        await send(update, "✅ <b>Global Price আপডেট হয়েছে!</b>\n\n" + t, m)

    elif name == "adm_tpl_price_edit":
        v = parse_num(text)
        t = DB["templates"].get(st["tid"])
        if v is None or not t:
            await send(update, "❌ সঠিক সংখ্যা লিখুন।", CANCEL_KB)
            return
        t["price"] = v
        save_data()
        context.user_data.pop("state", None)
        tt, m = render_price_admin()
        await send(update, "✅ <b>দাম আপডেট হয়েছে!</b>\n\n" + tt, m)

    elif name == "adm_tpl_name":
        context.user_data["state"] = {"name": "adm_tpl_price", "tname": text[:50]}
        await send(update, "<b>ধাপ ২/৪:</b> এই টেমপ্লেটের <b>দাম</b> লিখুন (যেমন: <code>30</code>):", CANCEL_KB)

    elif name == "adm_tpl_price":
        v = parse_num(text)
        if v is None:
            await send(update, "❌ সঠিক সংখ্যা লিখুন।", CANCEL_KB)
            return
        context.user_data["state"] = {"name": "adm_tpl_desc", "tname": st["tname"], "price": v}
        await send(update, "<b>ধাপ ৩/৪:</b> টেমপ্লেটের <b>বিবরণ</b> লিখুন (স্কিপ করতে <code>-</code> দিন):", CANCEL_KB)

    elif name == "adm_tpl_desc":
        desc = "" if text == "-" else text[:400]
        context.user_data["state"] = {"name": "adm_tpl_file", "tname": st["tname"], "price": st["price"], "desc": desc}
        await send(update, "<b>ধাপ ৪/৪:</b> টেমপ্লেটের কোড <b>.html / .zip ফাইল</b> হিসেবে পাঠান, অথবা "
                           "<b>HTML কোড লিখে/পেস্ট করে</b> মেসেজ পাঠান (শেষে <b>✅ কোড সম্পূর্ণ</b> চাপুন)।", CANCEL_KB)

    elif name == "adm_ch_add":
        ch = text.strip()
        if "t.me/" in ch:
            ch = "@" + ch.rstrip("/").split("/")[-1]
        if not ch.startswith("@"):
            ch = "@" + ch
        if not re.match(r"^@[A-Za-z][A-Za-z0-9_]{4,31}$", ch):
            await send(update, "❌ সঠিক @username দিন।", CANCEL_KB)
            return
        if ch in DB["channel_lock"]:
            await send(update, "⚠️ এই চ্যানেল আগেই এড করা আছে।", CANCEL_KB)
            return
        try:
            await context.bot.get_chat(ch)
            me = await context.bot.get_chat_member(ch, context.bot.id)
            if me.status not in ("administrator", "creator"):
                raise TelegramError("not admin")
        except TelegramError:
            await send(update, "❌ চ্যানেল পাওয়া যায়নি, অথবা বট ঐ চ্যানেলের <b>Admin</b> নয়। বটকে Admin বানিয়ে আবার পাঠান।", CANCEL_KB)
            return
        DB["channel_lock"].append(ch)
        save_data()
        context.user_data.pop("state", None)
        t, m = render_channels_admin()
        await send(update, "✅ <b>চ্যানেল লক এড হয়েছে!</b>\n\n" + t, m)

    elif name == "adm_support":
        DB["settings"]["support"] = text[:200]
        save_data()
        context.user_data.pop("state", None)
        await send(update, f"✅ সাপোর্ট সেট হয়েছে: <b>{esc(text[:200])}</b>", back)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    get_user(user)
    st = context.user_data.get("state")
    if not st or st["name"] not in CODE_STATES:
        await send(update, "ℹ️ ফাইল পাঠানোর আগে মেনু থেকে <b>➕ সাইট এড</b> চাপুন।")
        return
    if st["name"] in ADMIN_CODE_STATES and uid != ADMIN_ID:
        return
    if not await gate(update, context):
        return

    wait = await send(update, "⏳ <b>ফাইল যাচাই করা হচ্ছে...</b>")
    ok, res = await fetch_code_file(context, uid, update.message.document)
    if not ok:
        await wait.edit_text(res + "\n\nআবার সঠিক ফাইল পাঠান।", parse_mode=ParseMode.HTML, reply_markup=CANCEL_KB)
        return
    await after_code(context, uid, st, res, wait)


async def after_code(context, uid: int, st: dict, path: Path, wait):
    """ফাইল বা লেখা — যেভাবেই কোড আসুক, যাচাইয়ের পর এখান থেকে পরের ধাপ চলে।"""
    kind = "📦 ZIP" if path.suffix.lower() == ".zip" else "📄 HTML"

    if st["name"] == "upload_code":
        context.user_data["state"] = {"name": "site_name", "file": str(path)}
        await wait.edit_text(
            name_prompt_text(f"✅ <b>কোড গ্রহণ করা হয়েছে!</b> ({kind})\n\n"),
            parse_mode=ParseMode.HTML,
            reply_markup=NAME_KB,
        )

    elif st["name"] == "edit_code":
        sid = st["sid"]
        s = DB["sites"].get(sid)
        context.user_data.pop("state", None)
        if not s or (s["owner"] != uid and uid != ADMIN_ID):
            path.unlink(missing_ok=True)
            await wait.edit_text("❌ সাইট পাওয়া যায়নি।")
            return
        ok, err = await asyncio.to_thread(apply_package, path, site_dir(sid))
        path.unlink(missing_ok=True)
        if not ok:
            await wait.edit_text(f"❌ আপডেট ব্যর্থ: {esc(err)}", parse_mode=ParseMode.HTML)
            return
        s["updated"] = now()
        save_data()
        url = site_url(s["slug"])
        await wait.edit_text(
            f"✅ <b>কোড আপডেট হয়েছে!</b> ({kind})\n🌐 <code>{esc(s['slug'])}</code>\n\n"
            "ℹ️ <i>ব্রাউজারে না বদলালে রিফ্রেশ (Ctrl+F5) করুন।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb(
                [ib("🔗 সাইট ওপেন", url=url, style="success") if link_ok(url) and s["status"] == "online" else None],
                [ib("⚙️ ম্যানেজ", cb=f"sm:{sid}", style="primary")],
            ),
        )

    elif st["name"] == "adm_tpl_edit":
        tid = st["tid"]
        t = DB["templates"].get(tid)
        context.user_data.pop("state", None)
        if not t:
            path.unlink(missing_ok=True)
            await wait.edit_text("❌ এই টেমপ্লেট আর মার্কেটে নেই।")
            return
        ok, err = await asyncio.to_thread(apply_package, path, tdir(tid))
        path.unlink(missing_ok=True)
        if not ok:
            await wait.edit_text(f"❌ আপডেট ব্যর্থ: {esc(err)}", parse_mode=ParseMode.HTML)
            return
        t["updated"] = now()
        save_data()
        await wait.edit_text(
            f"✅ <b>টেমপ্লেটের কোড আপডেট হয়েছে!</b> ({kind})\n🌐 <b>{esc(t['name'])}</b>\n\n"
            "ℹ️ <i>নতুন কোড শুধু ভবিষ্যতে কেনা সাইটে যাবে।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb([ib("⬅️ টেমপ্লেট মার্কেট", cb=f"adm:mki:{tid}", style="primary")]),
        )

    elif st["name"] == "adm_tpl_file":
        tid = next_id("tpl", "t")
        ok, err = await asyncio.to_thread(apply_package, path, tdir(tid))
        path.unlink(missing_ok=True)
        if not ok:
            context.user_data.pop("state", None)
            await wait.edit_text(f"❌ যোগ করা যায়নি: {esc(err)}", parse_mode=ParseMode.HTML)
            return
        DB["templates"][tid] = {
            "name": st["tname"],
            "price": st["price"],
            "desc": st["desc"],
            "created": now(),
        }
        save_data()
        context.user_data.pop("state", None)
        prev = tpl_preview_url(tid)
        await wait.edit_text(
            f"✅ <b>মার্কেটে টেমপ্লেট যোগ হয়েছে!</b> ({kind})\n{LINE}\n\n🌐 <b>{esc(st['tname'])}</b>\n💸 <code>{money(st['price'])}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb(
                [ib("👁 প্রিভিউ", url=prev) if link_ok(prev) else None],
                [ib("🛍 টেমপ্লেট মার্কেট", cb="adm:mk", style="primary")],
                [ib("⬅️ এডমিন প্যানেল", cb="adm:home")],
            ),
        )


# ═══════════════════════════════════════════════════════════════
#  CALLBACK ROUTER
# ═══════════════════════════════════════════════════════════════
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    uid = user.id
    data = q.data or ""
    get_user(user)

    # ── Channel verify ──
    if data == "verify":
        missing = await missing_channels(context.bot, uid) if uid != ADMIN_ID else []
        if missing:
            await q.answer("❌ আপনি এখনো সব চ্যানেলে জয়েন করেননি!", show_alert=True)
            return
        await q.answer("✅ ভেরিফাইড!")
        try:
            await q.message.delete()
        except TelegramError:
            pass
        await send_welcome(update)
        return

    if data == "cancel":
        st = context.user_data.pop("state", None)
        if st and st.get("file"):
            Path(st["file"]).unlink(missing_ok=True)
        await q.answer("বাতিল")
        await edit(q, "❌ <b>বাতিল করা হয়েছে।</b>")
        return

    # Admin callbacks (no channel gate)
    if data.startswith("adm:"):
        if uid != ADMIN_ID:
            await q.answer("⛔ Access denied", show_alert=True)
            return
        await admin_callback(update, context, q, data)
        return
    if data.startswith("ap:"):
        if uid != ADMIN_ID:
            await q.answer("⛔ Access denied", show_alert=True)
            return
        _, act, code = data.split(":", 2)
        await approval(q, context, code, act == "ok")
        return

    # ── User callbacks (channel lock enforced) ──
    if uid != ADMIN_ID and DB["channel_lock"]:
        if await missing_channels(context.bot, uid):
            await q.answer()
            await gate(update, context)
            return

    parts = data.split(":")
    head = parts[0]

    if head == "wallet":
        await q.answer()
        await edit(q, wallet_text(uid), wallet_markup(uid))
    elif head == "addsite":
        await q.answer()
        await show_add_site(update, context)
    elif head == "market":
        await q.answer()
        await show_market(update, context, q)
    elif head == "mk":
        await q.answer()
        await market_item(q, parts[1], uid)
    elif head == "mkbuy":
        tid = parts[1]
        t = DB["templates"].get(tid)
        if not t:
            await q.answer("❌ টেমপ্লেট পাওয়া যায়নি", show_alert=True)
            return
        if DB["users"][str(uid)]["balance"] < t["price"]:
            await q.answer("⚠️ ব্যালেন্স যথেষ্ট নয়! আগে এড মানি করুন।", show_alert=True)
            return
        await q.answer()
        context.user_data["state"] = {"name": "market_name", "tid": tid}
        await q.message.chat.send_message(
            f"🌐 <b>{esc(t['name'])}</b>\n{LINE}\n\n"
            + name_prompt_text().replace("ধাপ ২:", "")
            + f"\n\n💸 চার্জ: <code>{money(t['price'])}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=NAME_KB,
        )
    elif head == "rndname":
        st = context.user_data.get("state")
        if not st or st.get("name") not in ("site_name", "market_name"):
            await q.answer("⚠️ কোনো কাজ চলছে না। মেনু থেকে আবার শুরু করুন।", show_alert=True)
            return
        await q.answer("🎲")
        await edit(q, "🚀 <b>সাইট পাবলিশ হচ্ছে...</b>")
        await run_deploy(context, uid, st, random_slug(), q.message)
    elif head == "codedone":
        await code_done(update, context, q, uid)
    elif head == "sm":
        sid = parts[1]
        s = DB["sites"].get(sid)
        if not s or (s["owner"] != uid and uid != ADMIN_ID):
            await q.answer("❌ সাইট পাওয়া যায়নি", show_alert=True)
            return
        await q.answer()
        t, m = site_view(sid, uid)
        await edit(q, t, m)
    elif head == "sa":
        await site_action(update, context, q, uid, parts[1], parts[2])
    elif head == "am":
        if parts[1] == "new":
            await q.answer()
            await show_add_money(update, context)
        else:
            await pay_callback(update, context, q, uid, parts)
    else:
        await q.answer()


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Update error: %s", context.error, exc_info=context.error)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = context.user_data.pop("state", None)
    if st and st.get("file"):
        Path(st["file"]).unlink(missing_ok=True)
    await send(update, "❌ <b>বাতিল করা হয়েছে।</b>", main_menu(update.effective_user.id))


MENU_ACTIONS = {
    BTN_ADD: show_add_site,
    BTN_SITES: show_sites,
    BTN_MARKET: show_market,
    BTN_WALLET: show_wallet,
    BTN_ADDMONEY: show_add_money,
    BTN_SUPPORT: show_support,
    BTN_ADMIN: show_admin,
}


# ═══════════════════════════════════════════════════════════════
#  LIFECYCLE
# ═══════════════════════════════════════════════════════════════
async def post_init(app: Application):
    global APP, LOOP
    APP, LOOP = app, asyncio.get_running_loop()
    for d in (SITES_DIR, TEMPLATES_DIR, TMP_DIR):
        d.mkdir(exist_ok=True)
    for f in TMP_DIR.glob("*"):  # পুরনো টেম্প ফাইল ক্লিন
        try:
            f.unlink() if f.is_file() else shutil.rmtree(f, ignore_errors=True)
        except Exception:
            pass

    if WEBAPP_URL.startswith("https://"):  # চ্যাটের নিচে সবার জন্য "অ্যাপ" বাটন
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="🚀 অ্যাপ", web_app=WebAppInfo(url=WEBAPP_URL))
            )
        except TelegramError as e:
            log.warning("menu button failed: %s", e)
    else:
        log.warning("WEBAPP_URL নেই — Mini App বাটন ও সাইট লিংক ঠিকমতো দেখাবে না")

    app.bot_data["maintenance"] = asyncio.create_task(maintenance_loop(app))
    log.info("Sites: %d | Templates: %d | Users: %d", len(DB["sites"]), len(DB["templates"]), len(DB["users"]))


async def post_shutdown(app: Application):
    task = app.bot_data.get("maintenance")
    if task:
        task.cancel()
    if BACKUP["dirty"]:  # বন্ধ হওয়ার আগে শেষবার ব্যাকআপ (Render রিস্টার্ট/ডিপ্লয়)
        try:
            await asyncio.wait_for(do_backup(app.bot), timeout=20)
        except Exception as e:
            log.warning("final backup failed: %s", e)
    save_data(mark=False)


def start_web_server() -> None:
    """Render Web Service এর জন্য HTTP সার্ভার (PORT env থাকলেই চালু হয়)।
    webapp.py: Mini App + API + সাইট হোস্টিং + health — সব এখান থেকেই সার্ভ হয়।
    UptimeRobot/cron-job.org দিয়ে লিংক পিং করলে সার্ভিস ঘুমিয়ে পড়ে না।"""
    port = os.getenv("PORT")
    if not port:
        return
    try:
        import webapp

        if webapp.start(sys.modules[__name__]):
            return
    except Exception as e:
        log.warning("webapp.py লোড হয়নি (%s) — শুধু health সার্ভার চলছে", e)
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Health(BaseHTTPRequestHandler):
        def _ok(self, body: bool):
            data = b"OK - hosting platform is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)

        def do_GET(self):
            self._ok(True)

        def do_HEAD(self):
            self._ok(False)

        def log_message(self, *args):
            pass

    try:
        server = ThreadingHTTPServer(("0.0.0.0", int(port)), Health)
    except Exception as e:
        log.warning("Web server start failed: %s", e)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("🌐 Health server listening on port %s", port)


def main():
    if BOT_TOKEN.startswith("PUT_YOUR"):
        sys.exit("❌ BOT_TOKEN সেট করুন (bot.py এর উপরে অথবা env variable)।")
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("admin", show_admin))
    app.add_handler(CommandHandler("app", cmd_app))
    app.add_handler(CommandHandler("password", cmd_password))
    app.add_handler(CommandHandler("sites", show_sites))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))
    app.add_error_handler(on_error)
    start_web_server()
    log.info("🚀 Website hosting platform started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

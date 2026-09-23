# ═══════════════════════════════════════════════════════════
#  ⚙️  CONFIG
# ═══════════════════════════════════════════════════════════
BOT_TOKEN = "8933523621:AAE6IS2nIS5OaflvAEVyQK4mEXzj0V-boK4"
API_ID    = "20346550"
API_HASH  = "bc79c3bea7a626887bdc0871eecf0327"
OWNER_ID  = 8460497291

MONGO_URI = "mongodb+srv://alexaditya:alexaditya950@cluster0.7j1hfjk.mongodb.net/?appName=Cluster0"
DB_NAME   = "course_uploader_bot"

COURSE_ID       = "41"
FALLBACK_USERID = "464995"
AKAMAI_HOST     = "armathsapi.akamai.net.in"
# ═══════════════════════════════════════════════════════════

import os
import re
import time
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, unquote
import requests
import jwt
import yt_dlp
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ═══════════════════════════════════════════════════════════
#  🗄️ MONGODB
# ═══════════════════════════════════════════════════════════
users_col = captions_col = settings_col = cookies_col = None
MONGO_OK = False

try:
    from pymongo import MongoClient
    _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    _client.server_info()
    _db = _client[DB_NAME]
    users_col    = _db["users"]
    captions_col = _db["captions"]
    settings_col = _db["settings"]
    cookies_col  = _db["cookies"]
    MONGO_OK = True
    print("✅ MongoDB connected:", DB_NAME)
except Exception as e:
    print(f"❌ MongoDB fail: {e}")

# ═══════════════════════════════════════════════════════════
#  🔧 LOCAL BOT API SERVER
# ═══════════════════════════════════════════════════════════
LOCAL_API_PORT = 8081
LOCAL_API_DIR  = "/tmp/tg-bot-api"
os.makedirs(LOCAL_API_DIR, exist_ok=True)


def auto_logout_public_api():
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/logOut", timeout=10)
        if r.json().get("ok"):
            print("✅ Public API logout")
    except Exception as e:
        print(f"⚠️ Logout fail: {e}")


def start_local_api_server():
    print("🚀 Local Bot API Server start...")
    auto_logout_public_api()
    for binary in ["/usr/local/bin/telegram-bot-api", "telegram-bot-api"]:
        try:
            subprocess.Popen([
                binary, f"--api-id={API_ID}", f"--api-hash={API_HASH}",
                "--local", f"--http-port={LOCAL_API_PORT}", f"--dir={LOCAL_API_DIR}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(6)
            try:
                r = requests.get(f"http://localhost:{LOCAL_API_PORT}/bot{BOT_TOKEN}/getMe", timeout=5)
                if r.status_code == 200:
                    print("✅ Local API ready — 4GB mode")
                    return True
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️ {binary} fail: {e}")
    print("⚠️ Local API nahi chala — 50MB mode")
    return False


LOCAL_API_OK = start_local_api_server()
USE_LOCAL_API = LOCAL_API_OK
LOCAL_API_BASE  = f"http://localhost:{LOCAL_API_PORT}/bot" if LOCAL_API_OK else "https://api.telegram.org/bot"
LOCAL_FILE_BASE = f"http://localhost:{LOCAL_API_PORT}/file/bot" if LOCAL_API_OK else "https://api.telegram.org/file/bot"
MAX_UPLOAD_MB   = 4000 if LOCAL_API_OK else 50

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
THUMB_DIR    = Path("thumbs")
THUMB_DIR.mkdir(exist_ok=True)
COOKIES_FILE = Path("cookies.txt")

WAITING_TXT  = {}
WAITING_CAPTION = {}
STOP_FLAGS   = {}
CURRENT_TASK = {}

MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv",
              ".mp3", ".m4a", ".pdf", ".zip", ".rar", ".ts", ".apk")

DEFAULT_CAPTION = (
    "[📁] File_ID : {file_index}\n\n"
    "NAME  : {file_name}\n\n"
    "💼  Size : {file_size}\n\n"
    "📚 BATCH NAME : {batch_name}\n\n"
    "DOWNLOADED BY : {downloaded_by} ❤️"
)

# ═══════════════════════════════════════════════════════════
#  🍪 COOKIES (MongoDB-backed)
# ═══════════════════════════════════════════════════════════
def save_cookies_to_db(content: bytes, filename: str = "cookies.txt") -> bool:
    if not MONGO_OK: return False
    try:
        cookies_col.update_one(
            {"_id": "primary"},
            {"$set": {
                "content": content,
                "filename": filename,
                "updated_at": datetime.utcnow(),
                "size": len(content),
            }},
            upsert=True)
        return True
    except Exception as e:
        print(f"⚠️ save cookies: {e}")
        return False


def load_cookies_from_db() -> dict:
    if not MONGO_OK: return {}
    try:
        return cookies_col.find_one({"_id": "primary"}) or {}
    except Exception:
        return {}


def delete_cookies_from_db() -> bool:
    if not MONGO_OK: return False
    try:
        return cookies_col.delete_one({"_id": "primary"}).deleted_count > 0
    except Exception:
        return False


def ensure_cookies_file() -> bool:
    data = load_cookies_from_db()
    content = data.get("content")
    if not content:
        return False
    try:
        COOKIES_FILE.write_bytes(content)
        return True
    except Exception as e:
        print(f"⚠️ write cookies: {e}")
        return False


if MONGO_OK:
    if ensure_cookies_file():
        print("✅ Cookies loaded from MongoDB")

# ═══════════════════════════════════════════════════════════
#  🗄️ DB HELPERS
# ═══════════════════════════════════════════════════════════
def is_premium(user_id):
    if not MONGO_OK: return False, 0
    try:
        u = users_col.find_one({"_id": str(user_id)})
        if not u: return False, 0
        exp = u.get("expires")
        if not exp: return False, 0
        now = datetime.utcnow()
        if exp < now: return False, 0
        return True, (exp - now).days
    except Exception:
        return False, 0


def add_premium(user_id, days, added_by):
    if not MONGO_OK: return "DB error"
    try:
        uid = str(user_id); now = datetime.utcnow()
        u = users_col.find_one({"_id": uid})
        base = u["expires"] if (u and u.get("expires") and u["expires"] > now) else now
        new_exp = base + timedelta(days=days)
        users_col.update_one({"_id": uid},
            {"$set": {"expires": new_exp, "added_by": str(added_by), "added_at": now}},
            upsert=True)
        return new_exp.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return "DB error"


def remove_premium(user_id):
    if not MONGO_OK: return False
    try: return users_col.delete_one({"_id": str(user_id)}).deleted_count > 0
    except Exception: return False


def list_premium_users():
    if not MONGO_OK: return []
    try: return list(users_col.find({}))
    except Exception: return []


def is_owner(user_id):
    return int(user_id) == int(OWNER_ID)


def get_user_caption(uid):
    default = {"enabled": True, "template": DEFAULT_CAPTION}
    if not MONGO_OK: return default
    try:
        c = captions_col.find_one({"_id": str(uid)})
        if not c: return default
        return {"enabled": c.get("enabled", True),
                "template": c.get("template", DEFAULT_CAPTION)}
    except Exception:
        return default


def set_caption_enabled(uid, enabled):
    if not MONGO_OK: return
    try: captions_col.update_one({"_id": str(uid)}, {"$set": {"enabled": enabled}}, upsert=True)
    except Exception: pass


def set_caption_template(uid, template):
    if not MONGO_OK: return
    try: captions_col.update_one({"_id": str(uid)}, {"$set": {"template": template}}, upsert=True)
    except Exception: pass


def reset_caption(uid):
    if not MONGO_OK: return
    try: captions_col.update_one({"_id": str(uid)}, {"$set": {"template": DEFAULT_CAPTION}}, upsert=True)
    except Exception: pass


def render_caption(template, values):
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out

# ═══════════════════════════════════════════════════════════
#  🎯 URL HANDLING
# ═══════════════════════════════════════════════════════════
def is_direct_media_url(url):
    low = url.lower().split("?")[0]
    return any(low.endswith(e) for e in MEDIA_EXTS)


def is_api_url(url):
    low = url.lower()
    return "fetch_video" in low or ("video_id=" in low and "token=" in low)


def build_candidate_urls(video_id, course_id, token, userid):
    vid, cid = str(video_id), str(course_id)
    return [
        f"https://{AKAMAI_HOST}/{cid}/{vid}.mp4",
        f"https://{AKAMAI_HOST}/videos/{cid}/{vid}.mp4",
        f"https://{AKAMAI_HOST}/video/{cid}/{vid}.mp4",
        f"https://{AKAMAI_HOST}/media/{cid}/{vid}.mp4",
        f"https://{AKAMAI_HOST}/hls/{cid}/{vid}.m3u8",
    ]


def is_url_live(url, timeout=3):
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code in (200, 206)
    except Exception:
        return False


def find_working_url(video_id, course_id, token, userid, user_id=None):
    for url in build_candidate_urls(video_id, course_id, token, userid):
        if user_id and is_stopped(user_id):
            raise yt_dlp.utils.DownloadError("User stopped")
        if is_url_live(url):
            return url
    raise ValueError("Koi URL pattern kaam nahi kiya")


def resolve_url(url, user_id=None):
    if is_direct_media_url(url): return url
    if is_api_url(url):
        p = urlparse(url); qs = parse_qs(p.query)
        video_id  = qs.get("video_id",  [""])[0]
        token     = qs.get("token",     [""])[0]
        userid    = qs.get("userid",    [FALLBACK_USERID])[0]
        course_id = qs.get("course_id", [COURSE_ID])[0]
        if video_id and token:
            return find_working_url(video_id, course_id, token, userid, user_id)
    return url

# ═══════════════════════════════════════════════════════════
#  🖼️ THUMBNAIL + DURATION
# ═══════════════════════════════════════════════════════════
def generate_thumbnail(video_path, out_path, seek_sec=5):
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", str(seek_sec), "-i", str(video_path),
                        "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "5",
                        str(out_path)], capture_output=True, timeout=30)
        if out_path.exists() and out_path.stat().st_size > 0: return True
        subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-vframes", "1",
                        "-vf", "scale=320:-1", "-q:v", "5", str(out_path)],
                       capture_output=True, timeout=30)
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception:
        return False


def get_media_info(path):
    info = {"duration": 0.0, "width": 0, "height": 0}
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                           capture_output=True, timeout=20, text=True)
        try: info["duration"] = float(r.stdout.strip())
        except Exception: pass
        if info["duration"] <= 0:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=duration",
                                "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                               capture_output=True, timeout=20, text=True)
            try: info["duration"] = float(r.stdout.strip())
            except Exception: pass
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=s=x:p=0", str(path)],
                           capture_output=True, timeout=20, text=True)
        out = r.stdout.strip()
        if "x" in out:
            p = out.split("x"); info["width"] = int(p[0]); info["height"] = int(p[1])
    except Exception:
        pass
    return info

# ───────── HELPERS ─────────
def fmt_size(b):
    b = float(b or 0)
    if b < 1024: return f"{b:.0f} B"
    if b < 1024**2: return f"{b/1024:.1f} KB"
    if b < 1024**3: return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"


def fmt_size_mib(b):
    b = float(b or 0)
    mib = b / (1024 * 1024)
    if mib < 1024: return f"{mib:.2f} MiB"
    return f"{mib / 1024:.2f} GiB"


def fmt_speed_mib(bps): return fmt_size_mib(bps) + "/s"


def fmt_eta_fancy(secs):
    if secs is None or secs < 0 or secs == float("inf"): return "Calculating..."
    secs = int(secs)
    if secs < 60: return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60: return f"{m}m, {s}s"
    h, m = divmod(m, 60)
    return f"{h}h, {m}m"


def fmt_duration(secs):
    if not secs or secs <= 0: return "0:00"
    secs = int(secs)
    h, rem = divmod(secs, 3600); m, s = divmod(rem, 60)
    if h: return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def make_bar(pct, width=20):
    filled = int(width * pct / 100); filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def build_fancy_progress(phase, done, total, speed, header=""):
    pct = (done / total * 100) if total else 0.0
    bar = make_bar(pct, 20)
    eta = fmt_eta_fancy((total - done) / speed if speed and total > done else None)
    lines = []
    if header:
        lines.append(header); lines.append("")
    lines.append(f"┌─「 {phase} 」─○"); lines.append("│")
    lines.append(f"│ » Progress:- {pct:.2f}%"); lines.append("│")
    lines.append(f"│ » {bar}"); lines.append("│")
    lines.append(f"│ » «{fmt_size_mib(done)} of {fmt_size_mib(total)}»"); lines.append("│")
    lines.append(f"│ » Speed:- {fmt_speed_mib(speed)}"); lines.append("│")
    lines.append(f"│ » ETA:- {eta}"); lines.append("│")
    lines.append("└────────────────────○")
    return "\n".join(lines)


async def safe_edit(bot, chat_id, msg_id, text, keyboard=None):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
                                    text=text[:4000], reply_markup=keyboard)
    except Exception:
        pass


def is_stopped(user_id): return STOP_FLAGS.get(user_id, False)

# ───────── TXT PARSER ─────────
URL_RE   = re.compile(r"(https?://\S+)")
TYPE_RE  = re.compile(r"\[(VIDEO|PDF)\]", re.I)
SHORT_RE = re.compile(
    r"^\s*(?P<vid>\d+)\s*[\|,]\s*(?P<tok>eyJ[\w\-\.]+)"
    r"\s*(?:[\|,]\s*(?P<uid>\d+))?\s*$")


def url_basename(url):
    clean = url.split("?")[0].split("#")[0]
    name = unquote(clean.rstrip("/").split("/")[-1])
    return name or "file"


def parse_txt(text):
    items = []; pending_name = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line: continue
        um = URL_RE.search(line)
        if um:
            url = um.group(1).rstrip(").,;\"'")
            before = line[:um.start()].strip().rstrip(":").strip()
            tm = TYPE_RE.search(before)
            ftype = tm.group(1).upper() if tm else "VIDEO"
            name_part = TYPE_RE.sub("", before).strip()
            name = name_part if name_part else (pending_name if pending_name else url_basename(url))
            name = name.rstrip(":").strip() or url_basename(url)
            items.append({"group": "", "type": ftype, "name": name, "url": url, "mode": "url"})
            pending_name = None; continue
        sm = SHORT_RE.match(line)
        if sm:
            items.append({"group": "", "type": "VIDEO",
                          "name": f"Video {sm.group('vid')}",
                          "video_id": sm.group("vid"), "token": sm.group("tok"),
                          "userid": sm.group("uid"), "mode": "short"})
            pending_name = None; continue
        pending_name = line.rstrip(":").strip()
    return items

# ═══════════════════════════════════════════════════════════
#  📥 DOWNLOAD — yt-dlp (YouTube fix + cookies)
# ═══════════════════════════════════════════════════════════
def download_file(url, out_dir, base, info, user_id=None):
    def hook(d):
        if d.get("status") == "downloading":
            info["done"]  = d.get("downloaded_bytes", 0)
            info["total"] = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            t = time.time()
            if info.get("_t"):
                dt = t - info["_t"]
                if dt >= 0.5:
                    info["speed"] = (info["done"] - info["_b"]) / dt
                    info["_t"], info["_b"] = t, info["done"]
            else:
                info["_t"], info["_b"] = t, info["done"]
            if user_id and is_stopped(user_id):
                raise yt_dlp.utils.DownloadError("User stopped")

    ensure_cookies_file()
    has_cookies = COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0
    if has_cookies:
        print(f"🍪 Using cookies ({COOKIES_FILE.stat().st_size} bytes)")
    else:
        print("⚠️ Cookies nahi")

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://{urlparse(url).netloc}/",
    }

    opts = {
        "outtmpl": str(out_dir / f"{base}.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "progress_hooks": [hook],
        "retries": 5, "fragment_retries": 5,
        "socket_timeout": 30,
        "http_headers": browser_headers,
        "cookiefile": str(COOKIES_FILE) if has_cookies else None,
        # 🔥 YOUTUBE FIX — naye clients + PO token bypass
        "extractor_args": {
            "youtube": {
                "player_client": [
                    "web_safari",
                    "web_creator",
                    "tv_embedded",
                    "ios",
                    "android",
                ],
                "player_skip": ["webpage", "configs"],
                "skip": ["hls", "dash"],
            }
        },
        "nocheckcertificate": True,
        "geo_bypass": True,
    }
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])

    files = [f for f in out_dir.glob(f"{base}.*") if f.is_file()]
    if not files:
        raise FileNotFoundError("Downloaded file missing")
    f = files[0]
    size = f.stat().st_size
    if size < 10 * 1024:
        try:
            head = f.read_bytes()[:200].lower()
            if b"<html" in head or b"<!doctype" in head:
                f.unlink()
                raise ValueError("HTML page — ye video nahi hai")
        except Exception: pass
        f.unlink()
        raise ValueError(f"File bahut chhota ({size} bytes)")

    ext = f.suffix.lower()
    if ext not in MEDIA_EXTS and ext != "":
        try:
            head = f.read_bytes()[:16]
            if head[4:8] == b"ftyp":
                new = f.with_suffix(".mp4"); f.rename(new); f = new
            elif head[:4] == b"%PDF":
                new = f.with_suffix(".pdf"); f.rename(new); f = new
        except Exception: pass
    return f

# ───────── UPLOAD PROGRESS ─────────
class ProgressFile:
    def __init__(self, path, info):
        self._f = open(path, "rb")
        self._info = info
        self._total = os.path.getsize(path)
        self._done = 0; self._last_t = time.time(); self._last_b = 0
        info["total"], info["done"] = self._total, 0
    def read(self, size=-1):
        chunk = self._f.read(size)
        self._done += len(chunk); self._info["done"] = self._done
        t = time.time(); dt = t - self._last_t
        if dt >= 0.5:
            self._info["speed"] = (self._done - self._last_b) / dt
            self._last_t, self._last_b = t, self._done
        return chunk
    def __len__(self): return self._total
    def seek(self, o, w=0): return self._f.seek(o, w)
    def tell(self): return self._f.tell()
    def close(self): return self._f.close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

# ═══════════════════════════════════════════════════════════
#  🎯 MAIN PROCESSOR
# ═══════════════════════════════════════════════════════════
async def process_items(update, ctx, items, batch_label=""):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    loop = asyncio.get_event_loop()
    total = len(items)
    mode = "4GB" if USE_LOCAL_API else "50MB"

    u = update.effective_user
    downloaded_by = u.first_name or u.username or str(user_id)
    if u.last_name: downloaded_by += f" {u.last_name}"

    STOP_FLAGS[user_id] = False
    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel All Downloads",
                              callback_data=f"cancel:{user_id}")]
    ])

    status = await ctx.bot.send_message(
        chat_id,
        f"🎬 *Sequential Mode ({mode})*\n"
        f"Batch: {batch_label or 'Direct'}\n"
        f"Total: {total} item(s)\n\n"
        f"`/stop` ya button se rok sakte ho.",
        parse_mode="Markdown", reply_markup=cancel_kb)

    ok = 0; failed = []; stopped = False

    for idx, item in enumerate(items, 1):
        if is_stopped(user_id):
            stopped = True; break
        name = item["name"][:100]
        ftype = item["type"]
        header = f"📦 Item {idx}/{total}\n🎬 {name}\n🔖 {ftype}"

        await safe_edit(ctx.bot, chat_id, status.message_id,
                        f"{header}\n\n🔐 Step 1/3 — Verifying...", cancel_kb)
        if is_stopped(user_id):
            stopped = True; break
        try:
            final_url = await loop.run_in_executor(None, resolve_url, item["url"], user_id)
        except PermissionError as e:
            failed.append((idx, name, "verify", f"auth: {str(e)[:120]}")); continue
        except Exception as e:
            failed.append((idx, name, "verify", f"url: {str(e)[:120]}")); continue
        if is_stopped(user_id):
            stopped = True; break

        base = f"{user_id}_{idx}"
        dl_info = {"done": 0, "total": 0, "speed": 0}
        dl_task = loop.run_in_executor(None, download_file, final_url,
                                       DOWNLOAD_DIR, base, dl_info, user_id)

        while not dl_task.done():
            await asyncio.wait({dl_task}, timeout=3)
            if is_stopped(user_id):
                stopped = True; break
            txt = build_fancy_progress("Downloading", dl_info["done"],
                                       dl_info["total"], dl_info["speed"], header)
            await safe_edit(ctx.bot, chat_id, status.message_id, txt, cancel_kb)

        if stopped:
            async def _cleanup_dl(t=dl_task):
                try:
                    p = await t
                    try: os.remove(p)
                    except: pass
                except: pass
            asyncio.create_task(_cleanup_dl())
            break

        try:
            out_path = await dl_task
        except Exception as e:
            failed.append((idx, name, "download", str(e)[:120])); continue

        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            failed.append((idx, name, "download", f"{size_mb:.0f}MB > {MAX_UPLOAD_MB}MB"))
            try: os.remove(out_path)
            except: pass
            continue
        if is_stopped(user_id):
            try: os.remove(out_path)
            except: pass
            stopped = True; break

        thumb_path = None
        media_info = {"duration": 0.0, "width": 0, "height": 0}
        is_video = out_path.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov",
                                               ".m4v", ".avi", ".flv", ".ts")
        if is_video:
            t_path = THUMB_DIR / f"{base}.jpg"
            ok_thumb = await loop.run_in_executor(None, generate_thumbnail, out_path, t_path)
            if ok_thumb: thumb_path = t_path
            media_info = await loop.run_in_executor(None, get_media_info, out_path)

        cap = get_user_caption(user_id)
        ext = out_path.suffix.lower().lstrip(".")
        name_clean = name
        if name_clean.lower().endswith(f".{ext.lower()}"):
            name_clean = name_clean[:-(len(ext) + 1)]
        display_name = f"{name_clean[:60]}.{ext}" if ext else f"{name_clean[:60]}"

        if cap["enabled"]:
            values = {
                "file_name": name_clean,
                "file_size": fmt_size(os.path.getsize(out_path)),
                "file_extension": ext or "file",
                "file_duration": fmt_duration(media_info["duration"]),
                "file_url": item["url"], "file_index": idx,
                "batch_name": batch_label or "Direct",
                "downloaded_by": downloaded_by,
            }
            caption = render_caption(cap["template"], values)[:1024]
        else:
            caption = None

        up_info = {"done": 0, "total": os.path.getsize(out_path), "speed": 0}
        wrapper = ProgressFile(str(out_path), up_info)
        thumb_fh = open(thumb_path, "rb") if thumb_path else None
        try:
            if is_video:
                coro = ctx.bot.send_video(
                    chat_id=chat_id, video=wrapper, filename=display_name,
                    caption=caption, thumbnail=thumb_fh,
                    duration=int(media_info["duration"]) if media_info["duration"] else 0,
                    width=media_info["width"] or None,
                    height=media_info["height"] or None,
                    supports_streaming=True,
                    read_timeout=7200, write_timeout=7200)
            else:
                coro = ctx.bot.send_document(
                    chat_id=chat_id, document=wrapper, filename=display_name,
                    caption=caption, thumbnail=thumb_fh,
                    read_timeout=7200, write_timeout=7200)
        except Exception as e:
            wrapper.close()
            if thumb_fh: thumb_fh.close()
            try: os.remove(out_path)
            except: pass
            failed.append((idx, name, "upload", str(e)[:120])); continue

        send_task = asyncio.create_task(coro)
        while not send_task.done():
            await asyncio.wait({send_task}, timeout=3)
            if is_stopped(user_id):
                send_task.cancel(); stopped = True; break
            txt = build_fancy_progress("Uploading", up_info["done"],
                                       up_info["total"], up_info["speed"], header)
            await safe_edit(ctx.bot, chat_id, status.message_id, txt, cancel_kb)

        try:
            if not stopped:
                await send_task; ok += 1
        except asyncio.CancelledError: pass
        except Exception as e:
            failed.append((idx, name, "upload", str(e)[:120]))
        finally:
            wrapper.close()
            if thumb_fh: thumb_fh.close()
            try: os.remove(out_path)
            except: pass
            if thumb_path and thumb_path.exists():
                try: thumb_path.unlink()
                except: pass

        if stopped: break

    if stopped:
        final = f"🛑 *STOPPED*\n\n✔️ Uploaded: {ok}/{total}\n❌ Failed: {len(failed)}"
    else:
        final = (f"✅ *DONE ({mode})*\n\n✔️ Uploaded: {ok}/{total}\n"
                 f"❌ Failed: {len(failed)}/{total}")
    if failed:
        final += "\n\n*Failed Items:*\n"
        for idx, n, stage, err in failed[:25]:
            final += f"• #{idx} `{n[:35]}` → {stage}: {err[:60]}\n"
        if len(failed) > 25:
            final += f"\n_...aur {len(failed)-25} fail_"
    await safe_edit(ctx.bot, chat_id, status.message_id, final)
    STOP_FLAGS.pop(user_id, None)


async def start_batch(update, ctx, items, batch_label=""):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        ok_p, _ = is_premium(user_id)
        if not ok_p:
            await update.message.reply_text(
                f"🚫 *Access Denied*\n\nAapki ID: `{user_id}`",
                parse_mode="Markdown")
            return
    if user_id in CURRENT_TASK and not CURRENT_TASK[user_id].done():
        await update.message.reply_text("⚠️ Ek batch chal rahi. /stop bhejo.")
        return
    task = asyncio.create_task(process_items(update, ctx, items, batch_label))
    CURRENT_TASK[user_id] = task
    def _done(t):
        if CURRENT_TASK.get(user_id) is t: CURRENT_TASK.pop(user_id, None)
        STOP_FLAGS.pop(user_id, None)
    task.add_done_callback(_done)

# ═══════════════════════════════════════════════════════════
#  🍪 COOKIES COMMANDS
# ═══════════════════════════════════════════════════════════
async def setcookies_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return

    doc = None
    if update.message.reply_to_message and update.message.reply_to_message.document:
        doc = update.message.reply_to_message.document
    elif update.message.document:
        doc = update.message.document

    if not doc:
        await update.message.reply_text(
            "📄 *Cookies Setup*\n\n"
            "1️⃣ Browser pe YouTube/Instagram login karo\n"
            "2️⃣ Chrome extension *'Get cookies.txt LOCALLY'* install karo\n"
            "3️⃣ Export karo → `cookies.txt`\n"
            "4️⃣ Yahan bhejo:\n"
            "   `/setcookies` bhejo aur reply me file attach karo\n"
            "   YA seedha `cookies.txt` file bhejo",
            parse_mode="Markdown")
        return

    if not (doc.file_name or "").lower().endswith(".txt"):
        await update.message.reply_text("❌ .txt file bhejo (cookies.txt)"); return

    msg = await update.message.reply_text("📥 Cookies save kar raha...")
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
        content = bytes(data)
        if len(content) < 50:
            await msg.edit_text("❌ File bahut chhoti — galat cookies.txt"); return
        if save_cookies_to_db(content, doc.file_name or "cookies.txt"):
            COOKIES_FILE.write_bytes(content)
            await msg.edit_text(
                f"✅ *Cookies Saved!*\n\n"
                f"📁 `{doc.file_name}`\n"
                f"💾 {len(content)} bytes\n\n"
                f"Ab YouTube, Instagram sab download hoga.",
                parse_mode="Markdown")
        else:
            await msg.edit_text("❌ MongoDB save fail")
    except Exception as e:
        await msg.edit_text(f"❌ Cookies save fail: {str(e)[:200]}")


async def getcookies_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    data = load_cookies_from_db()
    if not data:
        await update.message.reply_text("📭 Koi cookies nahi."); return
    content = data.get("content")
    updated = data.get("updated_at")
    size = data.get("size", len(content) if content else 0)
    when = updated.strftime("%d %b %Y, %I:%M %p") if updated else "?"
    await update.message.reply_text(
        f"🍪 *Cookies Status*\n\n"
        f"📁 `{data.get('filename', 'cookies.txt')}`\n"
        f"💾 {size} bytes\n"
        f"⏰ Updated: {when}",
        parse_mode="Markdown")


async def delcookies_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    if delete_cookies_from_db():
        try: COOKIES_FILE.unlink()
        except: pass
        await update.message.reply_text("✅ Cookies deleted.")
    else:
        await update.message.reply_text("ℹ️ Koi cookies nahi thi.")


async def handle_doc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    doc = update.message.document
    fname = (doc.file_name or "").lower()
    caption = (update.message.caption or "").lower()

    if fname == "cookies.txt" or "cookies" in fname or "/setcookies" in caption:
        if is_owner(uid):
            await setcookies_cmd(update, ctx)
            return

    if WAITING_TXT.get(uid):
        WAITING_TXT.pop(uid, None)
        await handle_txt_doc(update, ctx, doc)


async def handle_txt_doc(update, ctx, doc):
    if not (doc.file_name or "").lower().endswith(".txt"):
        await update.message.reply_text("❌ .txt file bhejo"); return
    msg = await update.message.reply_text("📖 TXT padh raha hoon...")
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
        items = parse_txt(data.decode("utf-8", errors="ignore"))
    except Exception as e:
        await msg.edit_text(f"❌ TXT error: {str(e)[:150]}"); return
    if not items:
        await msg.edit_text("❌ Koi valid item nahi."); return
    batch_name = (doc.file_name or "TXT")[:60]
    await msg.edit_text(f"✅ {len(items)} items. Start...")
    await start_batch(update, ctx, items, batch_label=batch_name)

# ═══════════════════════════════════════════════════════════
#  🎨 CAPTION
# ═══════════════════════════════════════════════════════════
def build_caption_menu(uid):
    cap = get_user_caption(uid)
    status = "Enabled" if cap["enabled"] else "Disabled"
    return (
        "Set Caption\n\n➤ Available Variables 📌\n\n"
        "🎙 Name : {file_name}\n📦 Size : {file_size}\n"
        "⚙️ Extension : {file_extension}\n⏱ Duration : {file_duration}\n"
        "🔗 Link : {file_url}\n🔢 Index : {file_index}\n"
        "📚 Batch Name : {batch_name}\n👤 Downloaded By : {downloaded_by}\n\n"
        "═══════════════════════\n\n➤ Current:\n"
        f"{cap['template']}\n\n═══════════════════════\n\n➤ Default:\n"
        f"{DEFAULT_CAPTION}\n\n➤ Status: {status}"
    )


def caption_keyboard(uid):
    cap = get_user_caption(uid)
    t = "🔴 DISABLE" if cap["enabled"] else "🟢 ENABLE"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t, callback_data="cap:toggle")],
        [InlineKeyboardButton("✏️ UPDATE CAPTION", callback_data="cap:update")],
        [InlineKeyboardButton("↺ RESET DEFAULT", callback_data="cap:reset")],
        [InlineKeyboardButton("🔙 BACK", callback_data="cap:back")],
    ])


async def caption_cmd(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid):
        ok_p, _ = is_premium(uid)
        if not ok_p:
            await update.message.reply_text(f"🚫 Premium chahiye. ID: `{uid}`",
                                            parse_mode="Markdown"); return
    await update.message.reply_text(build_caption_menu(uid),
                                    reply_markup=caption_keyboard(uid))


async def caption_callback(update, ctx):
    q = update.callback_query; uid = q.from_user.id
    action = q.data.split(":", 1)[1]
    if action == "toggle":
        cap = get_user_caption(uid); set_caption_enabled(uid, not cap["enabled"])
        await q.answer("✅"); await q.edit_message_text(build_caption_menu(uid),
                                                        reply_markup=caption_keyboard(uid))
    elif action == "update":
        WAITING_CAPTION[uid] = True; await q.answer("Send template")
        await q.edit_message_text(
            "✏️ *Caption Update*\n\nVariables: `{file_name}` `{file_size}` "
            "`{file_extension}` `{file_duration}` `{file_url}` `{file_index}` "
            "`{batch_name}` `{downloaded_by}`\n\n/cancel se rok do.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 BACK", callback_data="cap:back")]]))
    elif action == "reset":
        reset_caption(uid); await q.answer("✅")
        await q.edit_message_text(build_caption_menu(uid),
                                  reply_markup=caption_keyboard(uid))
    elif action == "back":
        await q.answer()
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass


async def cancel_caption_cmd(update, ctx):
    uid = update.effective_user.id
    if WAITING_CAPTION.get(uid):
        WAITING_CAPTION.pop(uid, None)
        await update.message.reply_text("❌ Cancelled.")
    else:
        await update.message.reply_text("ℹ️ Kuch cancel nahi.")

# ═══════════════════════════════════════════════════════════
#  👑 OWNER
# ═══════════════════════════════════════════════════════════
async def add_cmd(update, ctx):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("Usage: `/add <user_id> <days>`",
                                        parse_mode="Markdown"); return
    try: target = int(ctx.args[0]); days = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Numbers hone chahiye."); return
    if days <= 0: await update.message.reply_text("❌ Days > 0."); return
    exp_str = add_premium(target, days, update.effective_user.id)
    await update.message.reply_text(
        f"✅ *Premium Added*\n\n👤 `{target}`\n📅 +{days} days\n⏰ {exp_str}",
        parse_mode="Markdown")
    try:
        await ctx.bot.send_message(target,
            f"🎉 *Premium Activated!*\n\n+{days} days\nExpires: {exp_str}",
            parse_mode="Markdown")
    except: pass


async def remove_cmd(update, ctx):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    if not ctx.args:
        await update.message.reply_text("Usage: /remove <user_id>"); return
    try: target = int(ctx.args[0])
    except: await update.message.reply_text("❌ user_id number."); return
    if remove_premium(target):
        await update.message.reply_text(f"✅ `{target}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ `{target}` nahi tha.", parse_mode="Markdown")


async def list_cmd(update, ctx):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    users = list_premium_users()
    if not users:
        await update.message.reply_text("📭 Koi user nahi."); return
    now = datetime.utcnow(); lines = ["👥 Premium Users\n"]; a = e = 0
    for u in users:
        exp = u.get("expires")
        if not exp: continue
        if exp > now:
            lines.append(f"✅ `{u['_id']}` → {(exp-now).days} din"); a += 1
        else:
            lines.append(f"❌ `{u['_id']}` → expire"); e += 1
    lines.append(f"\nTotal: {len(users)} | ✅ {a} | ❌ {e}")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode="Markdown")


async def myid_cmd(update, ctx):
    await update.message.reply_text(f"🆔 `{update.effective_user.id}`", parse_mode="Markdown")


async def premium_cmd(update, ctx):
    uid = update.effective_user.id
    if is_owner(uid):
        await update.message.reply_text("👑 Owner — unlimited."); return
    ok_p, days = is_premium(uid)
    if ok_p: await update.message.reply_text(f"✅ Active — {days} din.")
    else: await update.message.reply_text(f"🚫 Premium nahi.\nID: `{uid}`", parse_mode="Markdown")

# ───────── GENERAL ─────────
async def start(update, ctx):
    uid = update.effective_user.id
    mode = "🚀 4GB" if USE_LOCAL_API else "⚠️ 50MB"
    db = "✅" if MONGO_OK else "❌"
    ck = "✅" if (COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0) else "❌"
    if is_owner(uid): access = "👑 Owner"
    else:
        ok_p, days = is_premium(uid)
        access = f"✅ Premium ({days}d)" if ok_p else "🚫 No Premium"
    await update.message.reply_text(
        f"👋 *Course Uploader Bot*\n━━━━━━━━━━━━━━━\n"
        f"🔧 Mode: {mode}\n📦 Max: {MAX_UPLOAD_MB} MB\n"
        f"🎫 {access}\n"
        f"🗄️ DB: {db} | 🍪 Cookies: {ck}\n\n"
        f"⚡ *Commands:*\n"
        f"• /start /help — Info\n• /txt — TXT batch\n"
        f"• /stop — Rok do\n• /caption — Custom caption\n"
        f"• /premium — Status\n• /myid — ID",
        parse_mode="Markdown")


async def help_cmd(update, ctx): await start(update, ctx)


async def stop_cmd(update, ctx):
    uid = update.effective_user.id
    STOP_FLAGS[uid] = True
    task = CURRENT_TASK.get(uid)
    if task and not task.done(): await update.message.reply_text("🛑 Stop requested!")
    else: await update.message.reply_text("ℹ️ Koi active batch nahi.")


async def cancel_cb(update, ctx):
    q = update.callback_query; await q.answer("Cancelling...")
    try: _, uid_str = q.data.split(":"); uid = int(uid_str)
    except: return
    STOP_FLAGS[uid] = True
    try: await q.edit_message_reply_markup(reply_markup=None)
    except: pass


async def txt_cmd(update, ctx):
    uid = update.effective_user.id
    if not is_owner(uid):
        ok_p, _ = is_premium(uid)
        if not ok_p:
            await update.message.reply_text("🚫 Premium chahiye."); return
    if update.message.reply_to_message and update.message.reply_to_message.document:
        await handle_txt_doc(update, ctx, update.message.reply_to_message.document)
        return
    WAITING_TXT[uid] = True
    await update.message.reply_text("📄 Ab .txt file bhejo.")


async def handle_text(update, ctx):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"): return

    if WAITING_CAPTION.get(uid):
        WAITING_CAPTION.pop(uid, None)
        if len(text) > 900:
            await update.message.reply_text("❌ Max 900 chars."); return
        set_caption_template(uid, text)
        await update.message.reply_text("✅ Caption updated!")
        await update.message.reply_text(build_caption_menu(uid),
                                        reply_markup=caption_keyboard(uid))
        return

    if not URL_RE.search(text):
        await update.message.reply_text("❓ URL bhejo ya /txt se TXT upload karo.")
        return
    items = parse_txt(text)
    if not items:
        await update.message.reply_text("❌ Koi valid URL nahi mila."); return
    await update.message.reply_text(f"✅ {len(items)} item(s). Shuru...")
    await start_batch(update, ctx, items, batch_label="Direct URL")

# ═══════════════════════════════════════════════════════════
#  🎯 POST INIT — MENU SETUP
# ═══════════════════════════════════════════════════════════
async def post_init(app):
    general_commands = [
        BotCommand("start", "Bot info"),
        BotCommand("help", "Help"),
        BotCommand("txt", "TXT batch upload"),
        BotCommand("stop", "Stop current batch"),
        BotCommand("caption", "Custom caption setup"),
        BotCommand("premium", "Check premium status"),
        BotCommand("myid", "Get your Telegram ID"),
    ]
    owner_commands = general_commands + [
        BotCommand("add", "Add premium (owner)"),
        BotCommand("remove", "Remove premium (owner)"),
        BotCommand("list", "List premium users (owner)"),
        BotCommand("setcookies", "Upload cookies (owner)"),
        BotCommand("getcookies", "Cookies status (owner)"),
        BotCommand("delcookies", "Delete cookies (owner)"),
    ]
    try:
        await app.bot.set_my_commands(general_commands, scope=BotCommandScopeDefault())
        print("✅ Default menu set")
    except Exception as e:
        print(f"⚠️ Default menu fail: {e}")
    try:
        await app.bot.set_my_commands(owner_commands,
            scope=BotCommandScopeChat(chat_id=int(OWNER_ID)))
        print("✅ Owner menu set")
    except Exception as e:
        print(f"⚠️ Owner menu fail: {e}")

# ───────── MAIN ─────────
def main():
    builder = (Application.builder()
               .token(BOT_TOKEN)
               .concurrent_updates(16)
               .post_init(post_init))
    if USE_LOCAL_API:
        builder = (builder.base_url(LOCAL_API_BASE)
                          .base_file_url(LOCAL_FILE_BASE)
                          .local_mode(True))
    app = builder.build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("txt", txt_cmd))
    app.add_handler(CommandHandler("caption", caption_cmd))
    app.add_handler(CommandHandler("cancel", cancel_caption_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("setcookies", setcookies_cmd))
    app.add_handler(CommandHandler("getcookies", getcookies_cmd))
    app.add_handler(CommandHandler("delcookies", delcookies_cmd))

    app.add_handler(CallbackQueryHandler(caption_callback, pattern=r"^cap:"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel:"))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"🤖 Bot chalu... Mode: {'4GB' if USE_LOCAL_API else '50MB'} | "
          f"MongoDB: {'✅' if MONGO_OK else '❌'} | Cookies: {'✅' if COOKIES_FILE.exists() else '❌'}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

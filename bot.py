# ═══════════════════════════════════════════════════════════
#  ⚙️  CONFIG
# ═══════════════════════════════════════════════════════════
BOT_TOKEN = "8933523621:AAE6IS2nIS5OaflvAEVyQK4mEXzj0V-boK4"
API_ID    = "20346550"
API_HASH  = "bc79c3bea7a626887bdc0871eecf0327"
OWNER_ID  = 8460497291

COURSE_ID       = "41"
FALLBACK_USERID = "464995"
AKAMAI_HOST     = "armathsapi.akamai.net.in"
# ═══════════════════════════════════════════════════════════

import os
import re
import json
import time
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import requests
import jwt
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ═══════════════════════════════════════════════════════════
#  🔧 LOCAL BOT API SERVER
# ═══════════════════════════════════════════════════════════
LOCAL_API_PORT = 8081
LOCAL_API_DIR  = "/tmp/tg-bot-api"
os.makedirs(LOCAL_API_DIR, exist_ok=True)


def auto_logout_public_api():
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/logOut", timeout=10)
        data = r.json()
        if data.get("ok"):
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
                r = requests.get(
                    f"http://localhost:{LOCAL_API_PORT}/bot{BOT_TOKEN}/getMe",
                    timeout=5)
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
DATA_FILE    = Path("users.json")

WAITING_TXT  = {}
STOP_FLAGS   = {}
CURRENT_TASK = {}

MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv",
              ".mp3", ".m4a", ".pdf", ".zip", ".rar", ".ts")

# ═══════════════════════════════════════════════════════════
#  💾 PREMIUM STORAGE
# ═══════════════════════════════════════════════════════════
def load_users() -> dict:
    if not DATA_FILE.exists(): return {}
    try: return json.loads(DATA_FILE.read_text())
    except Exception: return {}


def save_users(data: dict):
    try: DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e: print(f"⚠️ Save fail: {e}")


def is_premium(user_id) -> tuple:
    users = load_users()
    u = users.get(str(user_id))
    if not u: return False, 0
    try: exp = datetime.fromisoformat(u["expires"])
    except Exception: return False, 0
    now = datetime.now()
    if exp < now: return False, 0
    return True, (exp - now).days


def add_premium(user_id, days: int, added_by: int) -> str:
    users = load_users()
    now = datetime.now()
    current = users.get(str(user_id))
    if current:
        try:
            base = datetime.fromisoformat(current["expires"])
            if base < now: base = now
        except Exception: base = now
    else: base = now
    new_exp = base + timedelta(days=days)
    users[str(user_id)] = {
        "expires": new_exp.isoformat(),
        "added_by": added_by, "added_at": now.isoformat(),
    }
    save_users(users)
    return new_exp.strftime("%d %b %Y, %I:%M %p")


def remove_premium(user_id) -> bool:
    users = load_users()
    if str(user_id) in users:
        del users[str(user_id)]; save_users(users); return True
    return False


def is_owner(user_id) -> bool:
    return int(user_id) == int(OWNER_ID)

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
    if is_direct_media_url(url):
        return url
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
#  🖼️ THUMBNAIL GENERATOR
# ═══════════════════════════════════════════════════════════
def generate_thumbnail(video_path: Path, out_path: Path, seek_sec: int = 5) -> bool:
    try:
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(seek_sec), "-i", str(video_path),
            "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "5",
            str(out_path)
        ], capture_output=True, timeout=30)
        if out_path.exists() and out_path.stat().st_size > 0:
            return True
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "5",
            str(out_path)
        ], capture_output=True, timeout=30)
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        print(f"⚠️ Thumb fail: {e}")
        return False

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


def fmt_speed_mib(bps):
    return fmt_size_mib(bps) + "/s"


def fmt_eta_fancy(secs):
    if secs is None or secs < 0 or secs == float("inf"):
        return "Calculating..."
    secs = int(secs)
    if secs < 60: return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60: return f"{m}m, {s}s"
    h, m = divmod(m, 60)
    return f"{h}h, {m}m"


def make_bar(pct, width=20):
    filled = int(width * pct / 100)
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def build_fancy_progress(phase, done, total, speed, header=""):
    pct = (done / total * 100) if total else 0.0
    bar = make_bar(pct, 20)
    eta = fmt_eta_fancy((total - done) / speed if speed and total > done else None)
    lines = []
    if header:
        lines.append(header); lines.append("")
    lines.append(f"┌─「 {phase} 」─○")
    lines.append("│")
    lines.append(f"│ » Progress:- {pct:.2f}%")
    lines.append("│")
    lines.append(f"│ » {bar}")
    lines.append("│")
    lines.append(f"│ » «{fmt_size_mib(done)} of {fmt_size_mib(total)}»")
    lines.append("│")
    lines.append(f"│ » Speed:- {fmt_speed_mib(speed)}")
    lines.append("│")
    lines.append(f"│ » ETA:- {eta}")
    lines.append("│")
    lines.append("└────────────────────○")
    return "\n".join(lines)


async def safe_edit(bot, chat_id, msg_id, text, keyboard=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id, text=text[:4000],
            reply_markup=keyboard)
    except Exception:
        pass


def is_stopped(user_id):
    return STOP_FLAGS.get(user_id, False)

# ───────── TXT PARSER ─────────
URL_RE   = re.compile(r"(https?://\S+)")
TYPE_RE  = re.compile(r"\[(VIDEO|PDF)\]", re.I)
SHORT_RE = re.compile(
    r"^\s*(?P<vid>\d+)\s*[\|,]\s*(?P<tok>eyJ[\w\-\.]+)"
    r"\s*(?:[\|,]\s*(?P<uid>\d+))?\s*$")


def parse_txt(text):
    items, group = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line: continue
        um = URL_RE.search(line)
        if um:
            url = um.group(1).rstrip(").,;\"'")
            before = line[:um.start()].strip().rstrip(":").strip()
            tm = TYPE_RE.search(before)
            ftype = tm.group(1).upper() if tm else "VIDEO"
            name = TYPE_RE.sub("", before).strip()
            if group and name.startswith(group):
                name = name[len(group):].strip()
            name = name.rstrip(":").strip() or f"Item {len(items)+1}"
            items.append({"group": group or "", "type": ftype,
                          "name": name, "url": url, "mode": "url"})
            continue
        sm = SHORT_RE.match(line)
        if sm:
            items.append({"group": group or "", "type": "VIDEO",
                          "name": f"Video {sm.group('vid')}",
                          "video_id": sm.group("vid"),
                          "token": sm.group("tok"),
                          "userid": sm.group("uid"), "mode": "short"})
            continue
        group = line.strip().strip(":").strip()
    return items

# ═══════════════════════════════════════════════════════════
#  📥 DOWNLOAD (with strict validation)
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

    opts = {
        "outtmpl": str(out_dir / f"{base}.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 16,
        "http_chunk_size": 10485760,
        "buffersize": 1024 * 1024,
        "retries": 5, "fragment_retries": 5,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": f"https://{AKAMAI_HOST}/",
        },
    }
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])

    # ✅ Strict validation — HTML page ya chhota file reject karo
    files = [f for f in out_dir.glob(f"{base}.*") if f.is_file()]
    if not files:
        raise FileNotFoundError("Downloaded file missing")
    f = files[0]
    size = f.stat().st_size
    if size < 10 * 1024:  # 10KB se chhota → kaam nahi karega
        # HTML check
        try:
            head = f.read_bytes()[:200].lower()
            if b"<html" in head or b"<!doctype" in head:
                f.unlink()
                raise ValueError("HTML page download hua — real file nahi")
        except Exception: pass
        f.unlink()
        raise ValueError(f"File bahut chhota ({size} bytes)")

    # extension check (bahut zaroori)
    ext = f.suffix.lower()
    if ext not in MEDIA_EXTS and ext != "":
        # Kabhi kabhi extension nahi hota — content-type check
        try:
            head = f.read_bytes()[:16]
            if head[4:8] == b"ftyp":  # MP4 signature
                new = f.with_suffix(".mp4")
                f.rename(new); f = new
            elif head[:4] == b"%PDF":
                new = f.with_suffix(".pdf")
                f.rename(new); f = new
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
        parse_mode="Markdown",
        reply_markup=cancel_kb,
    )

    ok = 0
    failed = []  # (idx, name, stage, error)
    stopped = False

    for idx, item in enumerate(items, 1):
        if is_stopped(user_id):
            stopped = True; break

        name = item["name"][:100]
        ftype = item["type"]
        group = item["group"][:80]
        header = (f"📦 Item {idx}/{total}\n"
                  f"📁 {group}\n🎬 {name}\n🔖 {ftype}")

        # ─── STEP 1: VERIFY ───
        await safe_edit(ctx.bot, chat_id, status.message_id,
                        f"{header}\n\n🔐 Step 1/3 — Verifying...", cancel_kb)
        if is_stopped(user_id):
            stopped = True; break
        try:
            final_url = await loop.run_in_executor(
                None, resolve_url, item["url"], user_id)
        except PermissionError as e:
            failed.append((idx, name, "verify", f"auth: {str(e)[:120]}")); continue
        except Exception as e:
            failed.append((idx, name, "verify", f"url: {str(e)[:120]}")); continue
        if is_stopped(user_id):
            stopped = True; break

        # ─── STEP 2: DOWNLOAD ───
        base = f"{user_id}_{idx}"
        dl_info = {"done": 0, "total": 0, "speed": 0}
        dl_task = loop.run_in_executor(
            None, download_file, final_url, DOWNLOAD_DIR, base, dl_info, user_id)

        while not dl_task.done():
            await asyncio.wait({dl_task}, timeout=3)
            if is_stopped(user_id):
                stopped = True; break
            txt = build_fancy_progress("Downloading",
                dl_info["done"], dl_info["total"], dl_info["speed"], header)
            await safe_edit(ctx.bot, chat_id, status.message_id, txt, cancel_kb)

        if stopped:
            # 🔥 FIX: dono await nahi, cleanup fire-and-forget
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
            failed.append((idx, name, "download",
                           f"{size_mb:.0f}MB > {MAX_UPLOAD_MB}MB"))
            try: os.remove(out_path)
            except: pass
            continue
        if is_stopped(user_id):
            try: os.remove(out_path)
            except: pass
            stopped = True; break

        # ─── THUMBNAIL ───
        thumb_path = None
        if out_path.suffix.lower() not in (".pdf", ".mp3", ".m4a", ".zip", ".rar"):
            t_path = THUMB_DIR / f"{base}.jpg"
            ok_thumb = await loop.run_in_executor(
                None, generate_thumbnail, out_path, t_path)
            if ok_thumb: thumb_path = t_path

        # ─── STEP 3: UPLOAD ───
        up_info = {"done": 0, "total": os.path.getsize(out_path), "speed": 0}
        wrapper = ProgressFile(str(out_path), up_info)

        ext = out_path.suffix.lower()
        display_name = f"{name[:60]}{ext}" if ext else f"{name[:60]}.mp4"

        caption = (
            f"[📁] File_ID : {idx}\n"
            f"NAME : {display_name}\n\n"
            f"📁 {group}\n🎬 {name}\n🔖 {ftype}"
        )[:1024]

        thumb_fh = open(thumb_path, "rb") if thumb_path else None
        try:
            if ext == ".pdf" or ftype == "PDF":
                coro = ctx.bot.send_document(
                    chat_id=chat_id, document=wrapper,
                    filename=display_name, caption=caption,
                    thumbnail=thumb_fh,
                    read_timeout=7200, write_timeout=7200)
            else:
                coro = ctx.bot.send_video(
                    chat_id=chat_id, video=wrapper,
                    filename=display_name, caption=caption,
                    thumbnail=thumb_fh,
                    supports_streaming=True,
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
            txt = build_fancy_progress("Uploading",
                up_info["done"], up_info["total"], up_info["speed"], header)
            await safe_edit(ctx.bot, chat_id, status.message_id, txt, cancel_kb)

        try:
            if not stopped:
                await send_task; ok += 1
        except asyncio.CancelledError:
            pass
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

    # ─── SUMMARY ───
    if stopped:
        final = (f"🛑 *STOPPED*\n\n"
                 f"✔️ Uploaded: {ok}/{total}\n"
                 f"❌ Failed: {len(failed)}")
    else:
        final = (f"✅ *DONE ({mode})*\n\n"
                 f"✔️ Uploaded: {ok}/{total}\n"
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
    """🔥 Fire-and-forget — handler block nahi hoga, doosre user kaam kar sakte hain."""
    user_id = update.effective_user.id

    if not is_owner(user_id):
        ok_p, days = is_premium(user_id)
        if not ok_p:
            await update.message.reply_text(
                f"🚫 *Access Denied*\n\n"
                f"Aapki ID: `{user_id}`\n"
                f"Owner se contact karo.",
                parse_mode="Markdown")
            return

    # Same user ka purana task chal raha ho to skip
    if user_id in CURRENT_TASK and not CURRENT_TASK[user_id].done():
        await update.message.reply_text(
            "⚠️ Aapki ek batch chal rahi hai. /stop bhejo pehle.")
        return

    # 🔥 Task create karo — await NAHI karo
    task = asyncio.create_task(process_items(update, ctx, items, batch_label))
    CURRENT_TASK[user_id] = task

    # Done hone pe dict se hata do
    def _done(t):
        if CURRENT_TASK.get(user_id) is t:
            CURRENT_TASK.pop(user_id, None)
        STOP_FLAGS.pop(user_id, None)
    task.add_done_callback(_done)

# ═══════════════════════════════════════════════════════════
#  👑 OWNER COMMANDS
# ═══════════════════════════════════════════════════════════
async def add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("Usage: `/add <user_id> <days>`",
                                        parse_mode="Markdown"); return
    try: target = int(ctx.args[0]); days = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Numbers hone chahiye."); return
    if days <= 0:
        await update.message.reply_text("❌ Days > 0 hone chahiye."); return
    exp_str = add_premium(target, days, update.effective_user.id)
    await update.message.reply_text(
        f"✅ *Premium Added*\n\n"
        f"👤 `{target}`\n📅 +{days} days\n⏰ {exp_str}",
        parse_mode="Markdown")
    try:
        await ctx.bot.send_message(target,
            f"🎉 *Premium Activated!*\n\n+{days} days\nExpires: {exp_str}",
            parse_mode="Markdown")
    except Exception: pass


async def remove_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    if not ctx.args:
        await update.message.reply_text("Usage: /remove <user_id>"); return
    try: target = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id number."); return
    if remove_premium(target):
        await update.message.reply_text(f"✅ `{target}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ `{target}` premium me nahi tha.",
                                        parse_mode="Markdown")


async def list_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🚫 Sirf owner."); return
    users = load_users()
    if not users:
        await update.message.reply_text("📭 Koi premium user nahi."); return
    now = datetime.now(); lines = ["👥 Premium Users\n"]; a = e = 0
    for uid, u in users.items():
        try: exp = datetime.fromisoformat(u["expires"])
        except Exception: continue
        if exp > now:
            lines.append(f"✅ {uid} → {(exp-now).days} din")
            a += 1
        else:
            lines.append(f"❌ {uid} → expire"); e += 1
    lines.append(f"\nTotal: {len(users)} | ✅ {a} | ❌ {e}")
    await update.message.reply_text("\n".join(lines)[:4000])


async def myid_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆔 `{update.effective_user.id}`", parse_mode="Markdown")


async def premium_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_owner(uid):
        await update.message.reply_text("👑 Owner — unlimited."); return
    ok_p, days = is_premium(uid)
    if ok_p:
        await update.message.reply_text(f"✅ Active — {days} din bache.")
    else:
        await update.message.reply_text(
            f"🚫 Premium nahi.\nAapki ID: `{uid}`", parse_mode="Markdown")

# ───────── GENERAL ─────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mode = "🚀 4GB (Local API)" if USE_LOCAL_API else "⚠️ 50MB (Public API)"
    if is_owner(uid):
        access = "👑 Owner"
    else:
        ok_p, days = is_premium(uid)
        access = f"✅ Premium ({days}d)" if ok_p else "🚫 No Premium"
    await update.message.reply_text(
        f"👋 *Course Uploader Bot*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔧 Mode: {mode}\n"
        f"📦 Max: {MAX_UPLOAD_MB} MB\n"
        f"🎫 {access}\n\n"
        f"• URL bhejo → upload\n"
        f"• /txt → TXT batch\n"
        f"• /stop → rok do\n"
        f"• /premium → status\n"
        f"• /myid → ID",
        parse_mode="Markdown")


async def help_cmd(update, ctx): await start(update, ctx)


async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    STOP_FLAGS[uid] = True
    task = CURRENT_TASK.get(uid)
    if task and not task.done():
        await update.message.reply_text(
            "🛑 Stop requested! Current item ke baad ruk jayega...")
    else:
        await update.message.reply_text("ℹ️ Koi active batch nahi.")


async def cancel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Cancelling...")
    try:
        _, uid_str = q.data.split(":"); uid = int(uid_str)
    except Exception: return
    STOP_FLAGS[uid] = True
    try: await q.edit_message_reply_markup(reply_markup=None)
    except Exception: pass


async def txt_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        ok_p, _ = is_premium(uid)
        if not ok_p:
            await update.message.reply_text("🚫 Premium chahiye. /premium dekho.")
            return
    if update.message.reply_to_message and update.message.reply_to_message.document:
        await handle_txt_doc(update, ctx, update.message.reply_to_message.document)
        return
    WAITING_TXT[uid] = True
    await update.message.reply_text("📄 Ab .txt file bhejo.")


async def handle_doc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if WAITING_TXT.get(uid):
        WAITING_TXT.pop(uid, None)
        await handle_txt_doc(update, ctx, update.message.document)


async def handle_txt_doc(update, ctx, doc):
    if not (doc.file_name or "").lower().endswith(".txt"):
        await update.message.reply_text("❌ .txt file bhejo"); return
    msg = await update.message.reply_text("📖 TXT padh raha hoon...")
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
        items = parse_txt(data.decode("utf-8", errors="ignore"))
    except Exception as e:
        await msg.edit_text(f"❌ TXT padhne me error: {str(e)[:150]}"); return
    if not items:
        await msg.edit_text("❌ Koi valid item nahi."); return
    batch_name = (doc.file_name or "TXT")[:60]
    await msg.edit_text(f"✅ {len(items)} items. Start...")
    await start_batch(update, ctx, items, batch_label=batch_name)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"): return
    if not URL_RE.search(text):
        await update.message.reply_text("❓ URL bhejo ya /txt se TXT upload karo.")
        return
    items = parse_txt(text)
    if not items:
        await update.message.reply_text("❌ Koi valid URL nahi mila."); return
    await update.message.reply_text(f"✅ {len(items)} item(s). Shuru...")
    await start_batch(update, ctx, items, batch_label="Direct URL")

# ───────── MAIN ─────────
def main():
    builder = (Application.builder()
               .token(BOT_TOKEN)
               .concurrent_updates(16))  # 🔥 multiple users ek saath
    if USE_LOCAL_API:
        builder = (builder.base_url(LOCAL_API_BASE)
                          .base_file_url(LOCAL_FILE_BASE)
                          .local_mode(True))
    app = builder.build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("txt", txt_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("list", list_cmd))

    app.add_handler(CallbackQueryHandler(cancel_cb, pattern=r"^cancel:"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"🤖 Bot chalu... Mode: {'4GB' if USE_LOCAL_API else '50MB'} | Concurrent: 16")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

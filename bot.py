# ═══════════════════════════════════════════════
#  ⚙️  CONFIG — SIRF YAHAN BADLO
# ═══════════════════════════════════════════════
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"   # ← BotFather se

# 🔹 Fixed API (pehle message wala)
API_ENDPOINT = "https://appx-sign-urls-g-483856624945.herokuapp.com/fetch_video"
API_HOST     = "armathsapi.akamai.net.in"
COURSE_ID    = "41"
# Agar TXT me userid nahi mile to ye use hoga
FALLBACK_USERID = "464995"

# 🔹 2GB upload chahiye? (Local Bot API Server alag Railway service)
#   false = normal (50MB), true = 2GB
USE_LOCAL_API   = False
LOCAL_API_BASE  = "https://YOUR-BOT-API.up.railway.app/bot"
LOCAL_FILE_BASE = "https://YOUR-BOT-API.up.railway.app/file/bot"
# ═══════════════════════════════════════════════

import os
import re
import time
import asyncio
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests
import yt_dlp
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
WAITING_TXT = set()

# ───────── HELPERS ─────────
def fmt_size(b):
    b = float(b or 0)
    if b < 1024: return f"{b:.0f} B"
    if b < 1024**2: return f"{b/1024:.1f} KB"
    if b < 1024**3: return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"

def fmt_speed(bps): return fmt_size(bps) + "/s"

def fmt_eta(done, total, speed):
    if not speed or not total or done >= total: return "--:--"
    rem = (total - done) / speed
    if rem < 60: return f"{int(rem)}s"
    return f"{int(rem//60)}m {int(rem%60)}s"

async def safe_edit(bot, chat_id, msg_id, text):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text[:4000])
    except Exception:
        pass

# ───────── API CALL + TOKEN VERIFY ─────────
def call_api(course_id, video_id, token, userid):
    """API call karo → signed URL. Token galat → PermissionError."""
    params = {
        "api_base": API_HOST,
        "course_id": str(course_id),
        "video_id": str(video_id),
        "token": token,
        "userid": str(userid),
    }
    r = requests.get(API_ENDPOINT, params=params, timeout=30)
    if r.status_code in (401, 403):
        raise PermissionError(f"Token invalid/expire (HTTP {r.status_code})")
    r.raise_for_status()
    try:
        data = r.json()
    except Exception:
        txt = r.text.strip()
        if txt.startswith("http"): return txt
        raise ValueError(f"Bad response: {txt[:150]}")

    for k in ("video_url", "url", "link", "videoUrl", "file", "data"):
        if k in data:
            v = data[k]
            if isinstance(v, dict):
                v = v.get("url") or v.get("link") or v.get("video_url")
            if v: return v
    if data.get("status") in ("error", "fail", False):
        raise PermissionError(f"API error: {str(data)[:150]}")
    raise ValueError(f"URL nahi mila: {str(data)[:150]}")

# ───────── TXT PARSER ─────────
URL_RE   = re.compile(r"(https?://\S+)")
TYPE_RE  = re.compile(r"\[(VIDEO|PDF)\]", re.I)
SHORT_RE = re.compile(r"^\s*(?P<vid>\d+)\s*[\|,]\s*(?P<tok>eyJ[\w\-\.]+)\s*(?:[\|,]\s*(?P<uid>\d+))?\s*$")

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

# ───────── RESOLVE SIGNED URL (VERIFY + FETCH) ─────────
def resolve_signed_url(item):
    if item["mode"] == "url":
        p = urlparse(item["url"])
        qs = parse_qs(p.query)
        endpoint = f"{p.scheme}://{p.netloc}{p.path}"
        params = {k: v[0] for k, v in qs.items()}
        params.setdefault("api_base", API_HOST)
        params.setdefault("course_id", COURSE_ID)
        params.setdefault("userid", FALLBACK_USERID)

        r = requests.get(endpoint, params=params, timeout=30)
        if r.status_code in (401, 403):
            raise PermissionError(f"Token invalid/expire (HTTP {r.status_code})")
        r.raise_for_status()
        data = r.json()
        for k in ("video_url", "url", "link", "videoUrl", "file", "data"):
            if k in data:
                v = data[k]
                if isinstance(v, dict):
                    v = v.get("url") or v.get("link") or v.get("video_url")
                if v: return v
        raise ValueError(f"URL nahi mila: {str(data)[:150]}")

    uid = item.get("userid") or FALLBACK_USERID
    return call_api(COURSE_ID, item["video_id"], item["token"], uid)

# ───────── DOWNLOAD ─────────
def download_file(url, out_dir, base, info):
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

    opts = {
        "outtmpl": str(out_dir / f"{base}.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "progress_hooks": [hook],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://appx.akamai.net.in/",
        },
    }
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    files = [f for f in out_dir.glob(f"{base}.*") if f.is_file()]
    if not files: raise FileNotFoundError("Downloaded file missing")
    return files[0]

# ───────── UPLOAD PROGRESS WRAPPER ─────────
class ProgressFile:
    def __init__(self, path, info):
        self._f = open(path, "rb")
        self._info = info
        self._total = os.path.getsize(path)
        self._done = 0
        self._last_t = time.time()
        self._last_b = 0
        info["total"], info["done"] = self._total, 0

    def read(self, size=-1):
        chunk = self._f.read(size)
        self._done += len(chunk)
        self._info["done"] = self._done
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

# ───────── MAIN PROCESSOR ─────────
async def process_items(update, ctx, items):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    total = len(items)
    loop = asyncio.get_event_loop()

    status = await ctx.bot.send_message(chat_id, f"📋 {total} items. API verify...")
    ok, failed = 0, []

    for idx, item in enumerate(items, 1):
        name = item["name"][:100]
        ftype = item["type"]
        group = item["group"][:80]
        header = f"📦 Item {idx}/{total}\n📁 {group}\n🎬 {name}\n🔖 Type: {ftype}"

        # 1) API verify + signed URL
        await safe_edit(ctx.bot, chat_id, status.message_id,
                        header + "\n\n🔐 API verify + signed URL...")
        try:
            signed = await loop.run_in_executor(None, resolve_signed_url, item)
            if not signed or not str(signed).startswith("http"):
                raise ValueError("Invalid signed URL")
        except PermissionError as e:
            failed.append((name, f"auth: {str(e)[:100]}")); continue
        except Exception as e:
            failed.append((name, f"api: {str(e)[:100]}")); continue

        # 2) Download with live speed
        base = f"{user_id}_{idx}"
        dl_info = {"done": 0, "total": 0, "speed": 0}
        dl_task = loop.run_in_executor(
            None, download_file, signed, DOWNLOAD_DIR, base, dl_info
        )
        while not dl_task.done():
            await asyncio.wait({dl_task}, timeout=3)
            pct = (dl_info["done"]/dl_info["total"]*100) if dl_info["total"] else 0
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n📥 Downloading: {pct:.1f}%\n"
                f"   {fmt_size(dl_info['done'])} / {fmt_size(dl_info['total'])}\n"
                f"⚡ Speed: {fmt_speed(dl_info['speed'])}\n"
                f"⏱ ETA: {fmt_eta(dl_info['done'], dl_info['total'], dl_info['speed'])}")
        try:
            out_path = await dl_task
        except Exception as e:
            failed.append((name, f"download: {str(e)[:100]}")); continue

        size = os.path.getsize(out_path)
        up_info = {"done": 0, "total": size, "speed": 0}
        wrapper = ProgressFile(str(out_path), up_info)
        caption = f"📁 {group}\n🎬 {name}\n🔖 {ftype}"[:1000]

        # 3) Upload with live speed
        try:
            if ftype == "PDF":
                coro = ctx.bot.send_document(
                    chat_id=chat_id, document=wrapper,
                    filename=f"{name[:60]}.pdf", caption=caption,
                    read_timeout=1800, write_timeout=1800)
            else:
                coro = ctx.bot.send_video(
                    chat_id=chat_id, video=wrapper,
                    filename=f"{name[:60]}.mp4", caption=caption,
                    supports_streaming=True,
                    read_timeout=1800, write_timeout=1800)
        except Exception as e:
            wrapper.close()
            try: os.remove(out_path)
            except: pass
            failed.append((name, f"upload-setup: {str(e)[:100]}")); continue

        send_task = asyncio.create_task(coro)
        while not send_task.done():
            await asyncio.wait({send_task}, timeout=3)
            pct = (up_info["done"]/up_info["total"]*100) if up_info["total"] else 0
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n📤 Uploading: {pct:.1f}%\n"
                f"   {fmt_size(up_info['done'])} / {fmt_size(up_info['total'])}\n"
                f"⚡ Speed: {fmt_speed(up_info['speed'])}\n"
                f"⏱ ETA: {fmt_eta(up_info['done'], up_info['total'], up_info['speed'])}")

        try:
            await send_task; ok += 1
        except Exception as e:
            failed.append((name, f"upload: {str(e)[:100]}"))
        finally:
            wrapper.close()
            try: os.remove(out_path)
            except: pass

    summary = f"✅ *Complete!*\n\n✔️ Success: {ok}/{total}\n"
    if failed:
        summary += f"\n❌ Failed: {len(failed)}\n"
        for n, e in failed[:15]:
            summary += f"• {n[:40]}: {str(e)[:80]}\n"
    await safe_edit(ctx.bot, chat_id, status.message_id, summary)

# ───────── HANDLERS ─────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Course Uploader Bot\n\n"
        "📄 TXT Format:\n\n"
        "① Full URL:\n"
        "GROUP\n"
        "GROUP [VIDEO] Lesson 01 : https://appx-sign-urls...&token=...&userid=...\n\n"
        "② Short:\n"
        "GROUP\n"
        "GROUP [VIDEO] Lesson 01 : 30224|eyJhbGci...|464995\n\n"
        "Use: /txt → .txt file bhejo → auto verify + download + upload"
    )

async def txt_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message and update.message.reply_to_message.document:
        await handle_txt_doc(update, ctx, update.message.reply_to_message.document)
        return
    WAITING_TXT.add(update.effective_user.id)
    await update.message.reply_text("📄 Ab .txt file bhejo (document).")

async def handle_doc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in WAITING_TXT:
        WAITING_TXT.discard(update.effective_user.id)
        await handle_txt_doc(update, ctx, update.message.document)

async def handle_txt_doc(update, ctx, doc):
    if not (doc.file_name or "").lower().endswith(".txt"):
        await update.message.reply_text("❌ Sirf .txt file bhejo"); return
    msg = await update.message.reply_text("📖 TXT padh raha hoon...")
    tg_file = await ctx.bot.get_file(doc.file_id)
    data = await tg_file.download_as_bytearray()
    items = parse_txt(data.decode("utf-8", errors="ignore"))
    if not items:
        await msg.edit_text("❌ Koi valid URL / video_id nahi mila."); return
    await msg.edit_text(f"✅ {len(items)} items. API verify start...")
    await process_items(update, ctx, items)

# ───────── MAIN ─────────
def main():
    builder = Application.builder().token(BOT_TOKEN)
    if USE_LOCAL_API:
        builder = (builder.base_url(LOCAL_API_BASE)
                          .base_file_url(LOCAL_FILE_BASE)
                          .local_mode(True))
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("txt", txt_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
    print("🤖 Bot chalu...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

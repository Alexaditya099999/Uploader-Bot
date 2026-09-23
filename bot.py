# ═══════════════════════════════════════════════════════════
#  ⚙️  CONFIG — SIRF YAHAN BADLO
# ═══════════════════════════════════════════════════════════
BOT_TOKEN = "8933523621:AAHzHsW765IJ5Fl1rPSpdYsOI-Oc0m-03GE"
API_ID    = "20346550"
API_HASH  = "bc79c3bea7a626887bdc0871eecf0327"

COURSE_ID        = "41"
FALLBACK_USERID  = "464995"
AKAMAI_HOST      = "armathsapi.akamai.net.in"

JWT_SECRET       = ""   # optional
AKAMAI_SIGN_KEY  = ""   # optional
# ═══════════════════════════════════════════════════════════

import os
import re
import time
import hmac
import hashlib
import asyncio
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests
import jwt
import yt_dlp
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)

# ───────── LOCAL BOT API SERVER (2GB) ─────────
LOCAL_API_PORT = 8081
LOCAL_API_DIR  = "/tmp/tg-bot-api"
os.makedirs(LOCAL_API_DIR, exist_ok=True)

def start_local_api_server():
    print("🚀 Local Bot API Server start...")
    for binary in ["/usr/local/bin/telegram-bot-api", "telegram-bot-api"]:
        try:
            subprocess.Popen([
                binary, f"--api-id={API_ID}", f"--api-hash={API_HASH}",
                "--local", f"--http-port={LOCAL_API_PORT}", f"--dir={LOCAL_API_DIR}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(5)
            try:
                r = requests.get(
                    f"http://localhost:{LOCAL_API_PORT}/bot{BOT_TOKEN}/getMe",
                    timeout=5)
                if r.status_code == 200:
                    print("✅ Local API ready — 2GB mode")
                    return True
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️ {binary} fail: {e}")
    print("⚠️ Local API nahi chala — 50MB mode")
    return False

LOCAL_API_OK    = start_local_api_server()
USE_LOCAL_API   = LOCAL_API_OK
LOCAL_API_BASE  = f"http://localhost:{LOCAL_API_PORT}/bot" if LOCAL_API_OK else "https://api.telegram.org/bot"
LOCAL_FILE_BASE = f"http://localhost:{LOCAL_API_PORT}/file/bot" if LOCAL_API_OK else "https://api.telegram.org/file/bot"
MAX_UPLOAD_MB   = 2000 if LOCAL_API_OK else 50

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
WAITING_TXT = set()

# ═══════════════════════════════════════════════════════════
#  🔥 BUILT-IN API
# ═══════════════════════════════════════════════════════════
def verify_jwt_token(token: str, userid: str) -> dict:
    if not JWT_SECRET:
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
        except Exception as e:
            raise PermissionError(f"Token decode fail: {str(e)[:100]}")
    else:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise PermissionError("Token expire ho gaya")
        except jwt.InvalidTokenError as e:
            raise PermissionError(f"Token invalid: {str(e)[:100]}")
    if userid and str(payload.get("id", "")) != str(userid):
        raise PermissionError(f"Userid mismatch")
    return payload


def generate_signed_url(video_id: str, userid: str) -> str:
    path = f"/videos/{COURSE_ID}/{video_id}.mp4"
    if not AKAMAI_SIGN_KEY:
        return f"https://{AKAMAI_HOST}{path}"
    expires = int(time.time()) + 3600
    acl = f"/*/videos/{COURSE_ID}/{video_id}*"
    data = f"exp={expires}~acl={acl}~hmac="
    hash_val = hmac.new(AKAMAI_SIGN_KEY.encode(), data.encode(),
                        hashlib.sha256).hexdigest()
    token = f"exp={expires}~acl={acl}~hmac={hash_val}"
    return f"https://{AKAMAI_HOST}{path}?hdnts={token}"


def builtin_api_fetch(course_id, video_id, token, userid):
    verify_jwt_token(token, userid)
    return generate_signed_url(video_id, userid)


def resolve_item_to_url(item: dict) -> str:
    if item["mode"] == "url":
        p = urlparse(item["url"])
        qs = parse_qs(p.query)
        video_id  = qs.get("video_id",  [""])[0]
        token     = qs.get("token",     [""])[0]
        userid    = qs.get("userid",    [FALLBACK_USERID])[0]
        course_id = qs.get("course_id", [COURSE_ID])[0]
        if not (video_id and token):
            raise ValueError("URL me video_id/token missing")
        return builtin_api_fetch(course_id, video_id, token, userid)
    return builtin_api_fetch(
        COURSE_ID, item["video_id"], item["token"],
        item.get("userid") or FALLBACK_USERID)

# ═══════════════════════════════════════════════════════════

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
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
                                    text=text[:4000])
    except Exception:
        pass

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
            "Referer": f"https://{AKAMAI_HOST}/",
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

# ═══════════════════════════════════════════════════════════
#  🎯 MAIN PROCESSOR — SEQUENTIAL (Verify → Download → Upload)
# ═══════════════════════════════════════════════════════════
async def process_items(update, ctx, items):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    loop = asyncio.get_event_loop()

    total = len(items)
    mode = "🚀 2GB (Local API)" if USE_LOCAL_API else "⚠️ 50MB (Public API)"

    status = await ctx.bot.send_message(
        chat_id,
        f"🎬 *START — Sequential Mode*\n"
        f"Mode: {mode}\n"
        f"Total items: {total}\n\n"
        f"Har item pehle verify hoga, phir download, phir upload.",
        parse_mode="Markdown"
    )

    ok, fail_verify, fail_dl, fail_up = 0, [], [], []

    for idx, item in enumerate(items, 1):
        name = item["name"][:100]
        ftype = item["type"]
        group = item["group"][:80]

        header = (f"📦 *Item {idx}/{total}*\n"
                  f"📁 {group}\n"
                  f"🎬 {name}\n"
                  f"🔖 Type: {ftype}")

        # ─── STEP 1: VERIFY ───
        await safe_edit(ctx.bot, chat_id, status.message_id,
                        f"{header}\n\n🔐 *Step 1/3 — Verify...*")
        try:
            signed = await loop.run_in_executor(None, resolve_item_to_url, item)
            if not signed or not str(signed).startswith("http"):
                raise ValueError("Invalid signed URL")
        except PermissionError as e:
            fail_verify.append((name, f"auth: {str(e)[:200]}"))
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n❌ *Verify FAIL*\n`{str(e)[:250]}`\n\n"
                f"➡️ Agla item...")
            await asyncio.sleep(1.5)
            continue
        except Exception as e:
            fail_verify.append((name, f"api: {str(e)[:200]}"))
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n❌ *Verify FAIL*\n`{str(e)[:250]}`\n\n"
                f"➡️ Agla item...")
            await asyncio.sleep(1.5)
            continue

        # ─── STEP 2: DOWNLOAD ───
        base = f"{user_id}_{idx}"
        dl_info = {"done": 0, "total": 0, "speed": 0}
        dl_task = loop.run_in_executor(
            None, download_file, signed, DOWNLOAD_DIR, base, dl_info
        )
        while not dl_task.done():
            await asyncio.wait({dl_task}, timeout=3)
            pct = (dl_info["done"]/dl_info["total"]*100) if dl_info["total"] else 0
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n✅ Verify done\n"
                f"📥 *Step 2/3 — Downloading: {pct:.1f}%*\n"
                f"   {fmt_size(dl_info['done'])} / {fmt_size(dl_info['total'])}\n"
                f"⚡ Speed: {fmt_speed(dl_info['speed'])}\n"
                f"⏱ ETA: {fmt_eta(dl_info['done'], dl_info['total'], dl_info['speed'])}")
        try:
            out_path = await dl_task
        except Exception as e:
            fail_dl.append((name, f"download: {str(e)[:200]}"))
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n❌ *Download FAIL*\n`{str(e)[:250]}`\n\n"
                f"➡️ Agla item...")
            await asyncio.sleep(1.5)
            continue

        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            fail_dl.append((name, f"size {size_mb:.0f}MB > {MAX_UPLOAD_MB}MB"))
            try: os.remove(out_path)
            except: pass
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n❌ *Too big:* {size_mb:.0f}MB (limit {MAX_UPLOAD_MB}MB)\n\n"
                f"➡️ Agla item...")
            await asyncio.sleep(1.5)
            continue

        # ─── STEP 3: UPLOAD ───
        up_info = {"done": 0, "total": os.path.getsize(out_path), "speed": 0}
        wrapper = ProgressFile(str(out_path), up_info)
        caption = f"📁 {group}\n🎬 {name}\n🔖 {ftype}"[:1000]

        try:
            if ftype == "PDF":
                coro = ctx.bot.send_document(
                    chat_id=chat_id, document=wrapper,
                    filename=f"{name[:60]}.pdf", caption=caption,
                    read_timeout=7200, write_timeout=7200)
            else:
                coro = ctx.bot.send_video(
                    chat_id=chat_id, video=wrapper,
                    filename=f"{name[:60]}.mp4", caption=caption,
                    supports_streaming=True,
                    read_timeout=7200, write_timeout=7200)
        except Exception as e:
            wrapper.close()
            try: os.remove(out_path)
            except: pass
            fail_up.append((name, f"upload-setup: {str(e)[:200]}"))
            await asyncio.sleep(1.5)
            continue

        send_task = asyncio.create_task(coro)
        while not send_task.done():
            await asyncio.wait({send_task}, timeout=3)
            pct = (up_info["done"]/up_info["total"]*100) if up_info["total"] else 0
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n✅ Verify done\n✅ Download done\n"
                f"📤 *Step 3/3 — Uploading: {pct:.1f}%*\n"
                f"   {fmt_size(up_info['done'])} / {fmt_size(up_info['total'])}\n"
                f"⚡ Speed: {fmt_speed(up_info['speed'])}\n"
                f"⏱ ETA: {fmt_eta(up_info['done'], up_info['total'], up_info['speed'])}")

        try:
            await send_task
            ok += 1
            # Small success flash
            await safe_edit(ctx.bot, chat_id, status.message_id,
                f"{header}\n\n✅ *Success!*\n\n➡️ Agla item...")
            await asyncio.sleep(1)
        except Exception as e:
            fail_up.append((name, f"upload: {str(e)[:200]}"))
            await asyncio.sleep(1.5)
        finally:
            wrapper.close()
            try: os.remove(out_path)
            except: pass

    # ─── FINAL SUMMARY ───
    final = (
        f"✅ *ALL DONE* ({mode})\n\n"
        f"📊 *Summary*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✔️ Uploaded:      {ok}/{total}\n"
        f"❌ Verify fail:   {len(fail_verify)}\n"
        f"❌ Download fail: {len(fail_dl)}\n"
        f"❌ Upload fail:   {len(fail_up)}\n"
    )
    if fail_verify:
        final += "\n*Verify Failures (pehle 5):*\n"
        for n, e in fail_verify[:5]:
            final += f"• `{n[:35]}` → {e[:80]}\n"
    if fail_dl:
        final += "\n*Download Failures (pehle 5):*\n"
        for n, e in fail_dl[:5]:
            final += f"• `{n[:35]}` → {e[:80]}\n"
    if fail_up:
        final += "\n*Upload Failures (pehle 5):*\n"
        for n, e in fail_up[:5]:
            final += f"• `{n[:35]}` → {e[:80]}\n"

    await safe_edit(ctx.bot, chat_id, status.message_id, final)

# ───────── HANDLERS ─────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mode = "🚀 2GB (Local API)" if USE_LOCAL_API else "⚠️ 50MB (Public API)"
    await update.message.reply_text(
        f"🎬 Course Uploader Bot\n"
        f"Mode: {mode}\n\n"
        f"⚙️ *Sequential Process:*\n"
        f"Har item pehle verify hoga → phir download → phir upload.\n"
        f"Phir agla item same tarah.\n\n"
        "📄 TXT Format:\n"
        "GROUP\n"
        "GROUP [VIDEO] Lesson 01 : https://...&token=...&userid=...\n"
        "ya short:\n"
        "GROUP [VIDEO] Lesson 01 : 30224|eyJhbGci...|464995\n\n"
        "Use: /txt → .txt file bhejo",
        parse_mode="Markdown"
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
    await msg.edit_text(f"✅ {len(items)} items mile. Sequential process start...")
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
    print(f"🤖 Bot chalu... Mode: {'2GB' if USE_LOCAL_API else '50MB'}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

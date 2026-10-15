import os
import re
import json
import asyncio
import logging
import threading
import subprocess
import shutil
import tempfile
from flask import Flask
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)
from pytgcalls import PyTgCalls, StreamType, idle
from pytgcalls.types import (
    Update,
    AudioPiped,
    AudioVideoPiped,
    HighQualityAudio,
    HighQualityVideo,
    MediumQualityVideo,
    LowQualityVideo,
)
from pytgcalls.types.stream import StreamAudioEnded

from helpers.queues import (
    QUEUE,
    add_to_queue,
    get_queue,
    clear_queue,
)
from helpers.admin_check import is_admin






# =========================================================
# 1. LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

LOGGER = logging.getLogger("MusicBot")


# =========================================================
# 2. ENVIRONMENT VARIABLES
# =========================================================
def get_required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required Render Environment Variable: {name}"
        )
    return value

API_ID = int(get_required_env("API_ID"))
API_HASH = get_required_env("API_HASH")
BOT_TOKEN = get_required_env("BOT_TOKEN")
SESSION_STRING = get_required_env("SESSION_NAME")
OWNER_ID = int(get_required_env("OWNER_ID"))


# =========================================================
# 3. FLASK HEALTH SERVER
# =========================================================

web_app = Flask(__name__)


@web_app.route("/", methods=["GET", "POST", "HEAD"])
@web_app.route("/health", methods=["GET", "HEAD"])
def health_check():
    return "Bot is alive!", 200


def run_flask():
    port = int(os.environ.get("PORT", "10000"))

    web_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# 4. PYROGRAM CLIENTS
# =========================================================

bot = Client(
    "MusicStreamBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
)

client = Client(
    "MusicUser",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    parse_mode=enums.ParseMode.HTML,
)


# =========================================================
# 5. PYTGCALLS COMPATIBILITY PATCH
# =========================================================

# py-tgcalls 0.8.6 expects get_me() to behave like a dictionary.
# Pyrogram 2.x returns a User object.
# Patch only the method shown failing in your Render logs.

from pytgcalls.mtproto.pyrogram_client import PyrogramClient


async def patched_get_id(self):
    me = await self._app.get_me()

    if hasattr(me, "id"):
        return me.id

    return me["id"]


PyrogramClient.get_id = patched_get_id


# =========================================================
# 6. PYTGCALLS INSTANCE
# =========================================================

call_app = PyTgCalls(client)

LIVE_CHATS = set()


# =========================================================
# 7. START MESSAGE
# =========================================================

START_TEXT = """
Hi <b>{}</b> 👋

I can play music and stream videos.

⏤‌★𝐊𝐊 𝐀𝐑𝐌𝐘 𝐎𝐅𝐅𝐈𝐂𝐈𝐀𝐋ꗄ➺

This is a friendship community built on love and
strong bonds. We consider our members family.

<b>Constant Admins</b>

☬ @FULLY_CRUAL_MIND
🖥 @JinnSulthan
🎭 @im_casanova
🕺 @KottayamkunjachanM88
💃 @Kappusz
💙 @L_The_Fallen_Angel

<b>Complaint Channel:</b>
<a href="https://t.me/complaint_portal_tg">
@complaint_portal_tg
</a>
"""

START_BUTTONS = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                "📨 Support",
                url="https://t.me/KanimangalamKovilakam",
            )
        ]
    ]
)



# =========================================================
# 8. HELPER FUNCTIONS
# =========================================================

import os
import re
import asyncio


# YouTube cookies file on Render
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

YOUTUBE_COOKIES_FILE = os.getenv(
    "YOUTUBE_COOKIES_FILE",
    os.path.join(BASE_DIR, "youtube-cookies.txt"),
)

async def safe_delete(message):
    """Delete a message safely."""
    try:
        await message.delete()
    except Exception:
        pass






async def extract_stream_url(link, video=False):
    """Extract a stream URL using yt-dlp and optional YouTube cookies."""
    format_selector = "bestaudio[protocol=https]/bestaudio/best"
    temp_dir = None

    try:
        command = [
            "yt-dlp",
            "--no-warnings",
            "--no-playlist",
            "--extractor-args",
            "youtube:player_client=android",
        ]

        # Check the cookies file without logging cookie values.
        if os.path.isfile(YOUTUBE_COOKIES_FILE):
            size = os.path.getsize(YOUTUBE_COOKIES_FILE)

            try:
                with open(
                    YOUTUBE_COOKIES_FILE, "r", encoding="utf-8"
                ) as cookie_file:
                    lines = cookie_file.readlines()

                cookie_lines = [
                    line for line in lines
                    if line.strip()
                    and not line.lstrip().startswith("#")
                    and len(line.rstrip().split("\t")) >= 7
                ]

                LOGGER.info(
                    "YouTube cookies check: exists=True, "
                    "size=%d bytes, cookie_records=%d",
                    size,
                    len(cookie_lines),
                )

                if not cookie_lines:
                    LOGGER.warning(
                        "Cookies file may be empty or not in "
                        "Netscape cookies.txt format."
                    )

            except (OSError, UnicodeError):
                LOGGER.exception(
                    "Could not inspect YouTube cookies file."
                )

            if size < 100:
                LOGGER.warning(
                    "YouTube cookies file looks small: %d bytes",
                    size,
                )

            # Copy cookies to a writable temporary file.
            temp_dir = tempfile.mkdtemp(prefix="ytcookies_")
            writable_cookies = os.path.join(
                temp_dir, "cookies.txt"
            )
            shutil.copyfile(
                YOUTUBE_COOKIES_FILE, writable_cookies
            )

            command.extend(["--cookies", writable_cookies])
            LOGGER.info("YouTube cookies copied to temporary file.")

        else:
            LOGGER.warning(
                "YouTube cookies file not found: %s",
                YOUTUBE_COOKIES_FILE,
            )

        command.extend([
            "--format", format_selector,
            "--get-url", link,
        ])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0 and stdout.strip():
            return True, stdout.decode(
                "utf-8", errors="replace"
            ).splitlines()[0]

        error = stderr.decode("utf-8", errors="replace")

        # Retry with a broader format if the requested format is unavailable.
        if "Requested format is not available" in error:
            retry_command = [
                "yt-dlp",
                "--no-warnings",
                "--no-playlist",
                "--extractor-args",
                "youtube:player_client=android",
            ]

            if temp_dir:
                retry_command.extend([
                    "--cookies",
                    os.path.join(temp_dir, "cookies.txt"),
                ])

            retry_command.extend([
                "--format", "best",
                "--get-url", link,
            ])

            retry = await asyncio.create_subprocess_exec(
                *retry_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            retry_stdout, retry_stderr = await retry.communicate()

            if retry.returncode == 0 and retry_stdout.strip():
                return True, retry_stdout.decode(
                    "utf-8", errors="replace"
                ).splitlines()[0]

            error = retry_stderr.decode(
                "utf-8", errors="replace"
            ) or error

        LOGGER.error(
            "yt-dlp extraction failed: %s", error[-1500:]
        )
        return False, error[-1000:] or "No stream URL returned."

    except Exception as exc:
        LOGGER.exception("Stream extraction error")
        return False, str(exc)

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

def search_youtube(query):
    """Search YouTube through yt-dlp and return the first result."""

    temp_dir = None

    try:
        command = [
            "yt-dlp",
            "--no-warnings",
            "--ignore-errors",
            "--skip-download",
            "--dump-single-json",
            "--flat-playlist",
            "--playlist-end", "1",
        ]

        if os.path.isfile(YOUTUBE_COOKIES_FILE):
            temp_dir = tempfile.mkdtemp(prefix="ytsearch_")
            writable_cookies = os.path.join(temp_dir, "cookies.txt")
            shutil.copyfile(YOUTUBE_COOKIES_FILE, writable_cookies)
            command.extend(["--cookies", writable_cookies])

        command.append(f"ytsearch1:{query}")

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )

        if result.returncode != 0 or not result.stdout.strip():
            LOGGER.error(
                "yt-dlp YouTube search failed: %s",
                (result.stderr or "No search results")[-1200:],
            )
            return None

        data = json.loads(result.stdout)
        entries = data.get("entries") or []

        if not entries:
            return None

        item = entries[0]
        video_id = item.get("id")
        link = item.get("webpage_url")

        if not link and video_id:
            link = f"https://www.youtube.com/watch?v={video_id}"

        if not link:
            return None

        duration = item.get("duration_string")

        if not duration and item.get("duration") is not None:
            seconds = int(item["duration"])
            hours, remainder = divmod(seconds, 3600)
            minutes, secs = divmod(remainder, 60)
            duration = (
                f"{hours}:{minutes:02d}:{secs:02d}"
                if hours else f"{minutes}:{secs:02d}"
            )

        thumbnails = item.get("thumbnails") or []
        thumbnail = item.get("thumbnail") or (
            thumbnails[-1].get("url") if thumbnails else ""
        )

        return {
            "title": item.get("title") or "Unknown title",
            "link": link,
            "duration": duration or "Unknown",
            "thumbnail": thumbnail,
        }

    except Exception:
        LOGGER.exception("yt-dlp YouTube search failed")
        return None

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def get_video_quality(query):
    """Extract low, mid, or high quality from the query."""

    match = re.search(
        r"(?:^|\s)(low|mid|high)(?:\s|$)",
        query,
        re.IGNORECASE,
    )

    if not match:
        return "mid", query.strip()

    quality = match.group(1).lower()

    cleaned_query = (
        query[:match.start()] + " " + query[match.end():]
    ).strip()

    return quality, cleaned_query


def make_video_quality(quality):
    """Return the matching PyTgCalls video quality."""

    if quality == "high":
        return HighQualityVideo()

    if quality == "low":
        return LowQualityVideo()

    return MediumQualityVideo()


async def stop_chat(chat_id):
    """Stop the voice chat and clear its queue."""

    try:
        await call_app.leave_group_call(chat_id)
    except Exception as exc:
        LOGGER.warning(
            "Could not leave voice chat %s: %s",
            chat_id,
            exc,
        )

    clear_queue(chat_id)
    LIVE_CHATS.discard(chat_id)


# =========================================================
# 9. QUEUE MANAGEMENT
# =========================================================

async def skip_current_song(chat_id):
    if chat_id not in QUEUE:
        return 0

    chat_queue = get_queue(chat_id)

    if not chat_queue:
        await stop_chat(chat_id)
        return 1

    # Remove the song that has just finished / was skipped.
    chat_queue.pop(0)

    if not chat_queue:
        await stop_chat(chat_id)
        return 1

    next_song = chat_queue[0]

    title = next_song[0]
    playlink = next_song[3]
    media_type = next_song[4]
    quality = next_song[5]

    try:
        if media_type == "Audio":
            stream = AudioPiped(playlink)
        else:
            stream = AudioVideoPiped(
                playlink,
                HighQualityAudio(),
                make_video_quality(quality),
            )

        await call_app.change_stream(chat_id, stream)

        LOGGER.info(
            "Now playing next queued item in %s: %s",
            chat_id,
            title,
        )

        return 2

    except Exception:
        LOGGER.exception("Could not switch to next queued item")
        await stop_chat(chat_id)
        return 0


async def skip_item(chat_id, index):
    if chat_id not in QUEUE:
        return 0

    chat_queue = get_queue(chat_id)

    try:
        index = int(index)

        if index <= 0 or index >= len(chat_queue):
            return 0

        removed = chat_queue.pop(index)
        return removed[0]

    except (ValueError, IndexError, TypeError):
        return 0


# =========================================================
# 10. PYTGCALLS EVENTS
# =========================================================

@call_app.on_stream_end()
async def on_end_handler(_, update: Update):
    if isinstance(update, StreamAudioEnded):
        await skip_current_song(update.chat_id)


@call_app.on_closed_voice_chat()
async def close_handler(_, chat_id: int):
    if chat_id in QUEUE:
        clear_queue(chat_id)

    LIVE_CHATS.discard(chat_id)

    LOGGER.info("Voice chat closed: %s", chat_id)


# =========================================================
# 11. START COMMAND
# =========================================================

@bot.on_message(filters.command("start") & filters.private)
async def start_private(_, message):
    name = message.from_user.mention if message.from_user else "there"

    await message.reply_text(
        START_TEXT.format(name),
        reply_markup=START_BUTTONS,
        disable_web_page_preview=True,
    )


@bot.on_message(filters.command("start") & filters.group)
async def start_group(_, message):
    await message.reply_text(
        "🎧 <i>Music player is running.</i>"
    )


# =========================================================
# 12. CALLBACK BUTTONS
# =========================================================

@bot.on_callback_query()
async def callbacks(_, cq: CallbackQuery):
    if not cq.message or not cq.from_user:
        return await cq.answer()

    data = cq.data
    chat_id = cq.message.chat.id
    user_id = cq.from_user.id

    if data == "close":
        try:
            await cq.message.delete()
        except Exception:
            pass

        return await cq.answer()

    # Check whether the clicking user is an admin.
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        status = str(member.status).lower().split(".")[-1]

        if status not in ("owner", "creator", "administrator"):
            return await cq.answer(
                "You aren't an admin.",
                show_alert=True,
            )

    except Exception as exc:
        LOGGER.warning("Admin check failed: %s", exc)

        return await cq.answer(
            "Could not verify your admin status.",
            show_alert=True,
        )

    if chat_id not in QUEUE:
        return await cq.answer("Nothing is playing.")

    try:
        if data == "pause":
            await call_app.pause_stream(chat_id)
            return await cq.answer("⏸️ Paused")

        if data == "resume":
            await call_app.resume_stream(chat_id)
            return await cq.answer("⏯️ Resumed")

        if data == "stop":
            await stop_chat(chat_id)
            return await cq.answer("⏹️ Stopped")

        if data == "mute":
            await call_app.mute_stream(chat_id)
            return await cq.answer("🔇 Muted")

        if data == "unmute":
            await call_app.unmute_stream(chat_id)
            return await cq.answer("🔊 Unmuted")

        if data == "skip":
            result = await skip_current_song(chat_id)

            if result == 1:
                return await cq.answer("Queue ended.")

            if result == 0:
                return await cq.answer(
                    "Unable to skip this song.",
                    show_alert=True,
                )

            return await cq.answer("⏭️ Skipped")

    except Exception:
        LOGGER.exception("Callback action failed")
        return await cq.answer(
            "Action failed.",
            show_alert=True,
        )

    await cq.answer()


# =========================================================
# 13. PLAY / VPLAY COMMAND
# =========================================================

@bot.on_message(
    filters.command(["play", "vplay"]) & filters.group
)
async def video_play(_, message):
    if not message.from_user:
        return

    if not message.text:
        return

    command_name = message.command[0].lower()

    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>Usage:</b> <code>/{command_name} song name</code>"
        )

    query = message.text.split(None, 1)[1].strip()

    if not query:
        return await message.reply_text("Please provide a song name.")

    quality, search_query = get_video_quality(query)

    if not search_query:
        search_query = query

    chat_id = message.chat.id
    is_video = command_name == "vplay"
    media_type = "Video" if is_video else "Audio"

    if chat_id in LIVE_CHATS:
        return await message.reply_text(
            "❗ Please stop the live stream before playing music."
        )

    status_message = await message.reply_text(
        "🔎 <i>Searching YouTube...</i>"
    )

    try:
        result = await asyncio.to_thread(
            search_youtube,
            search_query,
        )

        if not result:
            return await status_message.edit_text(
                "❌ No YouTube results found."
            )

        title = result["title"]
        link = result["link"]
        duration = result["duration"]
        thumbnail = result["thumbnail"]

        success, extracted = await extract_stream_url(
            link,
            video=is_video,
        )

        if not success:
            LOGGER.error("Stream URL extraction failed: %s", extracted)

            return await status_message.edit_text(
                "❌ Unable to extract this YouTube stream. "
                "Please try another song."
            )

        playlink = extracted

        if is_video:
            stream = AudioVideoPiped(
                playlink,
                HighQualityAudio(),
                make_video_quality(quality),
            )
        else:
            stream = AudioPiped(playlink)

        caption = (
            f"▶️ <b>{'Video' if is_video else 'Now playing'}:</b> "
            f'<a href="{link}">{title}</a>\n'
            f"⏳ <b>Duration:</b> {duration}"
        )

        if chat_id in QUEUE and get_queue(chat_id):
            position = add_to_queue(
                chat_id,
                title,
                duration,
                link,
                playlink,
                media_type,
                quality,
                thumbnail,
            )

            await status_message.edit_text(
                f"➕ <b>Added to queue:</b> "
                f'<a href="{link}">{title}</a>\n'
                f"🔢 <b>Position:</b> {position}\n"
                f"⏳ <b>Duration:</b> {duration}"
            )

        else:
            await call_app.join_group_call(
                chat_id,
                stream,
                stream_type=StreamType().pulse_stream,
            )

            add_to_queue(
                chat_id,
                title,
                duration,
                link,
                playlink,
                media_type,
                quality,
                thumbnail,
            )

            await status_message.edit_text(caption)

        LOGGER.info(
            "Playback request processed: %s in %s",
            title,
            chat_id,
        )

    
    except Exception:
        LOGGER.exception("PLAY COMMAND FAILED")

        try:
            await status_message.edit_text(
                "❌ Playback failed. Check Render logs "
                "for the full error."
            )
        except Exception:
            LOGGER.exception("FAILED TO SEND PLAYBACK ERROR")



# =========================================================
# 14. SKIP COMMAND
# =========================================================

@bot.on_message(filters.command("skip") & filters.group)
@is_admin
async def skip(_, message):
    await safe_delete(message)

    chat_id = message.chat.id

    if chat_id not in QUEUE or not get_queue(chat_id):
        return await bot.send_message(
            chat_id,
            "❗ Nothing is playing.",
        )

    if len(message.command) < 2:
        result = await skip_current_song(chat_id)

        if result == 1:
            await bot.send_message(
                chat_id,
                "⏹️ Queue ended.",
            )
        elif result == 0:
            await bot.send_message(
                chat_id,
                "❌ Unable to skip.",
            )
        else:
            await bot.send_message(
                chat_id,
                "⏭️ Skipped to the next song.",
            )

        return

    indexes = [
        int(value)
        for value in message.text.split(None, 1)[1].split()
        if value.isdigit()
    ]

    removed_titles = []

    # Remove highest indexes first.
    for index in sorted(set(indexes), reverse=True):
        title = await skip_item(chat_id, index)

        if title:
            removed_titles.append(f"• {title}")

    if removed_titles:
        await bot.send_message(
            chat_id,
            "<b>Removed from queue:</b>\n"
            + "\n".join(removed_titles),
        )
    else:
        await bot.send_message(
            chat_id,
            "❗ No matching queue items were removed.",
        )


# =========================================================
# 15. PLAYLIST / QUEUE COMMAND
# =========================================================

@bot.on_message(
    filters.command(["playlist", "queue"]) & filters.group
)
@is_admin
async def playlist(_, message):
    chat_id = message.chat.id

    if chat_id not in QUEUE or not get_queue(chat_id):
        return await message.reply_text("❗ Nothing is playing.")

    chat_queue = get_queue(chat_id)

    lines = ["<b>📃 Player queue</b>\n"]

    for index, item in enumerate(chat_queue):
        title = item[0]
        link = item[2]
        media_type = item[4]

        if index == 0:
            lines.append(
                f'▶️ <a href="{link}">{title}</a> '
                f"<b>(Now playing)</b> — {media_type}"
            )
        else:
            lines.append(
                f'{index}. <a href="{link}">{title}</a> '
                f"— {media_type}"
            )

    output = "\n".join(lines)

    # Telegram message length limit protection.
    await message.reply_text(
        output[:4000],
        disable_web_page_preview=True,
    )


# =========================================================
# 16. STOP COMMAND
# =========================================================

@bot.on_message(filters.command("stop") & filters.group)
@is_admin
async def end(_, message):
    await safe_delete(message)

    chat_id = message.chat.id

    if chat_id in LIVE_CHATS:
        await stop_chat(chat_id)

        return await bot.send_message(
            chat_id,
            "⏹️ Live streaming stopped.",
        )

    if chat_id in QUEUE:
        await stop_chat(chat_id)

        return await bot.send_message(
            chat_id,
            "⏹️ Playback stopped and queue cleared.",
        )

    await bot.send_message(chat_id, "❗ Nothing is playing.")


# =========================================================
# 17. PAUSE COMMAND
# =========================================================

@bot.on_message(filters.command("pause") & filters.group)
@is_admin
async def pause(_, message):
    await safe_delete(message)

    chat_id = message.chat.id

    try:
        await call_app.pause_stream(chat_id)
        await bot.send_message(chat_id, "⏸️ Paused.")
    except Exception:
        await bot.send_message(chat_id, "❗ Nothing is playing.")


# =========================================================
# 18. RESUME COMMAND
# =========================================================

@bot.on_message(filters.command("resume") & filters.group)
@is_admin
async def resume(_, message):
    await safe_delete(message)

    chat_id = message.chat.id

    try:
        await call_app.resume_stream(chat_id)
        await bot.send_message(chat_id, "⏯️ Resumed.")
    except Exception:
        await bot.send_message(chat_id, "❗ Nothing is playing.")


# =========================================================
# 19. MUTE COMMAND
# =========================================================

@bot.on_message(filters.command("mute") & filters.group)
@is_admin
async def mute(_, message):
    await safe_delete(message)

    chat_id = message.chat.id

    try:
        await call_app.mute_stream(chat_id)
        await bot.send_message(chat_id, "🔇 Muted.")
    except Exception:
        await bot.send_message(chat_id, "❗ Nothing is playing.")


# =========================================================
# 20. UNMUTE COMMAND
# =========================================================

@bot.on_message(filters.command("unmute") & filters.group)
@is_admin
async def unmute(_, message):
    await safe_delete(message)

    chat_id = message.chat.id

    try:
        await call_app.unmute_stream(chat_id)
        await bot.send_message(chat_id, "🔊 Unmuted.")
    except Exception:
        await bot.send_message(chat_id, "❗ Nothing is playing.")


# =========================================================
# 21. RESTART COMMAND
# =========================================================

@bot.on_message(filters.command("restart"))
async def restart(_, message):
    if not message.from_user:
        return

    if message.from_user.id != OWNER_ID:
        return

    await message.reply_text(
        "🛠 <i>Restarting music player...</i>"
    )

    # Render will normally restart the process after exit.
    os._exit(0)


# =========================================================
# 22. STARTUP
# =========================================================

if __name__ == "__main__":
    LOGGER.info("Starting Flask health server...")

    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    LOGGER.info("Starting PyTgCalls...")
    call_app.start()

    LOGGER.info("PyTgCalls started. Starting Telegram bot...")

    bot.run()

"""
Video downloader (..) — reply to any message containing a link and send `..`

Uses @MegaSaverBot instead of yt-dlp/ffmpeg:
  - No system packages needed (no ffmpeg on Railway)
  - No YouTube login-wall problems
  - Supports YouTube, Instagram, TikTok, Pinterest, and more
"""

import re

import megasaver
from logger import log
from telegram_layer import client

_URL_RE = re.compile(r'https?://[^\s\]\)>\"\']+', re.IGNORECASE)


def _extract_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,)>") if m else None


async def cmd_download_video(event):
    if not event.is_reply:
        await event.edit(
            "⬇️ **دانلود ویدیو**\n"
            "روی پیامی که لینک داره ریپلای کن و `..` بزن.\n"
            "یوتیوب · اینستاگرام · تیک‌تاک · پینترست پشتیبانی می‌شن."
        )
        return

    reply = await event.get_reply_message()
    if reply is None:
        await event.edit("⚠️ پیام ریپلای پیدا نشد.")
        return

    url = _extract_url(reply.raw_text or "")
    if not url:
        await event.edit("⚠️ لینکی تو پیام پیدا نشد.")
        return

    await event.edit("⬇️ در حال دانلود از MegaSaverBot…")

    video_bytes = await megasaver.download_via_megasaver(client, url)

    if video_bytes is None:
        await event.edit(
            "❌ دانلود ناموفق بود.\n"
            "ممکنه لینک خصوصی باشه، یا @MegaSaverBot موقتاً کند باشه — "
            "چند ثانیه صبر کن و دوباره امتحان کن."
        )
        return

    await event.edit("📤 در حال ارسال…")
    try:
        import io
        buf = io.BytesIO(video_bytes)
        buf.name = "video.mp4"
        await client.send_file(
            event.chat_id,
            buf,
            supports_streaming=True,
            reply_to=reply.id,
        )
        await event.delete()
        try:
            await reply.delete()
        except Exception as e:
            # Not fatal — the video already sent successfully. This just
            # means we couldn't remove the original link (e.g. it's someone
            # else's message in a group and this account isn't admin there).
            log.warn(f"Downloader: couldn't delete original link message: {e}")
    except Exception as e:
        log.error(f"Downloader send error: {e}")
        await event.edit(f"❌ خطا در ارسال: {str(e)[:200]}")

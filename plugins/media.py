"""Media plugins: turning replied-to messages into quote cards / stickers.

Heavy libraries (PIL, arabic-reshaper, bidi, imageio-ffmpeg) are imported
lazily — only when the command actually runs, not at startup. This keeps
idle RAM ~25-35 MB lower than the eager-import version, since most sessions
never call .quote or .اسکرین at all.
"""

import io

from logger import log
from telegram_layer import client
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeSticker, InputStickerSetEmpty

VIDEO_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50MB cap

# Both attributes are required for Telegram to treat the file as an actual
# sticker rather than a plain document (otherwise desktop clients open it
# via the OS default handler — typically a browser — instead of showing it
# as an in-chat sticker).
STATIC_STICKER_KWARGS = {"mime_type": "image/webp"}


async def cmd_quote(event):
    if not event.is_reply:
        await event.edit("⚠️ باید روی یک پیام ریپلای کنی و بعد دستور رو بزنی.")
        return

    reply = await event.get_reply_message()
    if reply is None:
        await event.edit("⚠️ پیام مورد ریپلای پیدا نشد.")
        return

    sender = await reply.get_sender()
    sender_name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "Unknown"

    avatar_bytes = None
    try:
        buf = io.BytesIO()
        result = await client.download_profile_photo(sender, file=buf)
        if result is not None and buf.getbuffer().nbytes > 0:
            avatar_bytes = buf.getvalue()
    except Exception as e:
        log.warn(f"Could not download avatar for quote: {e}")

    import quote  # lazy — PIL/arabic-reshaper/bidi only loaded on first .quote call
    image_bytes = quote.build_quote_image(
        sender_name, reply.raw_text or "", avatar_bytes,
        sender_id=reply.sender_id, sent_at=reply.date,
    )

    await event.delete()
    await client.send_file(
        event.chat_id,
        io.BytesIO(image_bytes),
        attributes=[DocumentAttributeFilename("quote.webp")],
        mime_type="image/webp",
        force_document=False,
    )


async def _send_static_sticker(event, image_bytes: bytes, alt: str):
    await event.delete()
    await client.send_file(
        event.chat_id,
        io.BytesIO(image_bytes),
        attributes=[
            DocumentAttributeFilename("sticker.webp"),
            DocumentAttributeSticker(alt=alt, stickerset=InputStickerSetEmpty()),
        ],
        force_document=False,
        **STATIC_STICKER_KWARGS,
    )


async def cmd_screen(event):
    """Reply to a text, photo, video, or GIF message with .اسکرین to turn
    it into a sticker.

    Text is re-rendered from scratch as a Telegram-style message bubble
    (name, avatar, colored name, timestamp) — not a screenshot, so quality
    only depends on the source image/font. Photos convert to a static
    sticker directly, with no name/avatar attached. Videos and GIFs try to
    become a real *animated* sticker (WEBM/VP9, via ffmpeg); if ffmpeg
    isn't available or the encode fails, this falls back to a static
    sticker from a thumbnail frame instead of failing outright."""
    if not event.is_reply:
        await event.edit("⚠️ باید روی یه پیام (متن، عکس، ویدیو یا گیف) ریپلای کنی و بعد دستور رو بزنی.")
        return

    reply = await event.get_reply_message()
    if reply is None:
        await event.edit("⚠️ پیام مورد ریپلای پیدا نشد.")
        return

    is_photo = bool(reply.photo)
    is_video = bool(reply.video)
    is_gif   = bool(reply.gif)
    is_text  = not reply.media and bool((reply.raw_text or "").strip())

    if not (is_photo or is_video or is_gif or is_text):
        await event.edit("⚠️ این پیام نه متن قابل‌استفاده داره نه عکس/ویدیو/گیف — نمی‌تونم ازش استیکر بسازم.")
        return

    if is_text:
        import quotlybot  # lazy
        card_bytes = await quotlybot.render_via_quotly(client, reply)

        if card_bytes is None:
            sender = await reply.get_sender()
            sender_name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "Unknown"

            avatar_bytes = None
            try:
                buf = io.BytesIO()
                result = await client.download_profile_photo(sender, file=buf)
                if result is not None and buf.getbuffer().nbytes > 0:
                    avatar_bytes = buf.getvalue()
            except Exception as e:
                log.warn(f"Could not download avatar for screen: {e}")

            try:
                import quote  # lazy
                card_bytes = quote.build_quote_image(
                    sender_name, reply.raw_text, avatar_bytes,
                    sender_id=reply.sender_id, sent_at=reply.date,
                )
            except Exception as e:
                await event.edit(f"⚠️ خطا تو ساخت استیکر: {e}")
                return

        await _send_static_sticker(event, card_bytes, alt="💬")
        return

    if is_photo:
        try:
            buf = io.BytesIO()
            await reply.download_media(file=buf)
            import quote  # lazy
            sticker_bytes = quote.image_to_sticker(buf.getvalue())
        except Exception as e:
            await event.edit(f"⚠️ خطا تو ساخت استیکر: {e}")
            return

        await _send_static_sticker(event, sticker_bytes, alt="🖼")
        return

    # Video or GIF
    import stickers  # lazy — imageio-ffmpeg only needed for video stickers
    if stickers.is_available():
        try:
            size = reply.file.size if reply.file else None
            if size is not None and size > VIDEO_MAX_DOWNLOAD_BYTES:
                await event.edit("⚠️ فایل بزرگ‌تر از حد مجازه (۵۰ مگابایت) برای تبدیل به استیکر متحرک.")
                return

            buf = io.BytesIO()
            await reply.download_media(file=buf)
            webm_bytes = stickers.video_to_sticker_webm(buf.getvalue())

            await event.delete()
            await client.send_file(
                event.chat_id,
                io.BytesIO(webm_bytes),
                attributes=[
                    DocumentAttributeFilename("sticker.webm"),
                    DocumentAttributeSticker(alt="🎬", stickerset=InputStickerSetEmpty()),
                ],
                mime_type="video/webm",
                force_document=False,
            )
            return
        except Exception as e:
            log.warn(f"Animated sticker failed, falling back to static thumbnail: {e}")

    # Fallback: static sticker from a thumbnail frame.
    try:
        buf = io.BytesIO()
        result = await client.download_media(reply, thumb=-1, file=buf)
        if result is None or buf.getbuffer().nbytes == 0:
            await event.edit("⚠️ نه ffmpeg در دسترس بود نه thumbnail قابل‌استفاده — نتونستم استیکر بسازم.")
            return
        import quote  # lazy
        sticker_bytes = quote.image_to_sticker(buf.getvalue())
    except Exception as e:
        await event.edit(f"⚠️ خطا تو ساخت استیکر: {e}")
        return

    await _send_static_sticker(event, sticker_bytes, alt="🖼")

"""Integration with @MegaSaverBot for video downloads.

Instead of running yt-dlp + ffmpeg locally (which needs system packages,
breaks on YouTube login-walls, and uses Railway CPU), we forward the
link message to @MegaSaverBot and wait for its video reply — the same
pattern used for @QuotLyBot. The bot handles YouTube, Instagram, TikTok,
Pinterest, etc.

Flow:
  1. User replies to a message containing a URL and sends ".."
  2. We forward that message to @MegaSaverBot
  3. MegaSaverBot responds with the downloaded video file
  4. We forward that file to the original chat and clean up the bot DM
"""

import asyncio

from telethon import events

from logger import log

BOT_USERNAME   = "MegaSaverBot"
RESPONSE_TIMEOUT = 90   # MegaSaverBot can be slow on large videos

_bot_entity_cache = None


async def _get_bot_entity(client):
    global _bot_entity_cache
    if _bot_entity_cache is None:
        _bot_entity_cache = await client.get_entity(BOT_USERNAME)
    return _bot_entity_cache


def _has_media(message) -> bool:
    """True if the message carries any downloadable media (video, audio,
    document, photo). MegaSaverBot may send status text messages first —
    we keep waiting until we get an actual file."""
    if not message:
        return False
    return bool(message.video or message.document or message.audio or
                (message.photo and not message.sticker))


async def download_via_megasaver(client, url_or_message) -> bytes | None:
    """Sends a URL (str) or forwards a message to @MegaSaverBot and returns
    the raw bytes of the video it sends back. Returns None on timeout or
    failure — caller should show a friendly error in that case.

    Also cleans up the DM with MegaSaverBot afterwards so it doesn't fill
    up with forwarded links and downloaded files."""
    try:
        bot_entity = await _get_bot_entity(client)
        bot_id     = bot_entity.id
    except Exception as e:
        log.warn(f"MegaSaver: could not resolve bot ({e!r})")
        return None

    queue: asyncio.Queue = asyncio.Queue()
    seen_ids: list       = []

    async def _handler(event):
        if event.message and event.message.sender_id == bot_id:
            seen_ids.append(event.message.id)
            await queue.put(event.message)

    client.add_event_handler(_handler, events.NewMessage(incoming=True))
    try:
        if isinstance(url_or_message, str):
            sent = await client.send_message(bot_entity, url_or_message)
            if sent:
                seen_ids.append(sent.id)
        else:
            fwd = await client.forward_messages(bot_entity, url_or_message)
            if fwd:
                ids = fwd if isinstance(fwd, list) else [fwd]
                seen_ids.extend(m.id for m in ids)

        deadline = asyncio.get_event_loop().time() + RESPONSE_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                log.warn("MegaSaver: timed out waiting for video")
                return None

            try:
                message = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                log.warn("MegaSaver: timed out waiting for video")
                return None

            if _has_media(message):
                return await client.download_media(message, file=bytes)
            # Text status message (e.g. "Downloading…") — keep waiting.

    except Exception as e:
        log.warn(f"MegaSaver: error ({e!r})")
        return None
    finally:
        client.remove_event_handler(_handler, events.NewMessage(incoming=True))
        if seen_ids:
            try:
                await client.delete_messages(bot_entity, seen_ids, revoke=True)
            except Exception as e:
                log.warn(f"MegaSaver: couldn't clean up DM: {e!r}")

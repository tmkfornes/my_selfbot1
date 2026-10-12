"""Integration with the public @QuotLyBot: forward a message to it and
wait for the sticker it replies with, instead of rendering the quote
bubble ourselves. QuotLyBot renders real Telegram quote cards (including
reply-chains, multiple senders, etc.), so this generally looks better
than a from-scratch re-render — this is just automating a normal chat
with a public bot, the same as a human would forward-and-wait by hand.
"""

import asyncio
import traceback

from telethon import events
from telethon.tl.types import DocumentAttributeSticker

from logger import log

QUOTLY_USERNAME = "QuotLyBot"
RESPONSE_TIMEOUT = 15  # seconds to wait for QuotLyBot's reply

_bot_entity_cache = None  # resolved once, reused after — resolving the
                           # username costs a real network round trip
                           # (a big chunk of the multi-second delay if
                           # done on every single .اسکرین call)


def _is_sticker(message) -> bool:
    if not message or not message.file:
        return False
    if message.sticker:
        return True
    attrs = getattr(message.media.document, "attributes", []) if message.media and message.media.document else []
    return any(isinstance(a, DocumentAttributeSticker) for a in attrs)


async def _get_bot_entity(client):
    global _bot_entity_cache
    if _bot_entity_cache is None:
        _bot_entity_cache = await client.get_entity(QUOTLY_USERNAME)
    return _bot_entity_cache


async def render_via_quotly(client, reply_message) -> bytes | None:
    """Forwards `reply_message` to @QuotLyBot and returns the raw bytes of
    the sticker it sends back, or None on timeout/failure (caller should
    fall back to local rendering in that case).

    NOTE: this deliberately does NOT filter the event handler with
    events.NewMessage(from_users=<entity>). If the account has never
    messaged QuotLyBot before, get_entity() can hand back a "min" entity
    (no usable access_hash) — telethon then fails to resolve that entity
    while building the from_users filter, and the handler silently never
    fires, even though QuotLyBot answers almost instantly. Instead we
    listen to ALL incoming messages and match the numeric sender id
    ourselves, which needs no extra resolution.
    """
    try:
        bot_entity = await _get_bot_entity(client)
        bot_id = bot_entity.id
    except Exception as e:
        log.warn(f"Could not resolve QuotLyBot ({e!r}) — falling back to local render")
        return None

    queue: asyncio.Queue = asyncio.Queue()
    seen_message_ids = []  # every message id we send to / receive from
                            # QuotLyBot, so we can clean the chat up after

    async def _handler(event):
        if event.message and event.message.sender_id == bot_id:
            seen_message_ids.append(event.message.id)
            await queue.put(event.message)

    client.add_event_handler(_handler, events.NewMessage(incoming=True))
    try:
        sent = await client.forward_messages(bot_entity, reply_message)
        if sent:
            sent_ids = sent if isinstance(sent, list) else [sent]
            seen_message_ids.extend(m.id for m in sent_ids)

        deadline = asyncio.get_event_loop().time() + RESPONSE_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                log.warn("QuotLyBot timed out — falling back to local render")
                return None

            try:
                message = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                log.warn("QuotLyBot timed out — falling back to local render")
                return None

            if _is_sticker(message):
                return await client.download_media(message, file=bytes)

            # QuotLyBot sometimes sends a status/error text first (e.g.
            # "processing…") before the actual sticker — keep waiting
            # instead of bailing on the first message.

    except Exception as e:
        log.warn(f"QuotLyBot integration failed ({e!r}) — falling back to local render")
        log.warn(traceback.format_exc())
        return None
    finally:
        client.remove_event_handler(_handler, events.NewMessage(incoming=True))
        if seen_message_ids:
            try:
                await client.delete_messages(bot_entity, seen_message_ids, revoke=True)
            except Exception as e:
                log.warn(f"Could not clean up QuotLyBot chat: {e!r}")

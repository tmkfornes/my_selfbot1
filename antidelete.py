"""Anti-delete / anti-edit: caches recent private-chat messages (in + out)
so that when one gets deleted OR edited, a copy can be reposted to Saved
Messages — for edits, showing both the pre-edit and post-edit text.

Telegram's delete update for normal (non-channel) chats only carries the
deleted message IDs — it does NOT tell you which chat they belonged to.
That's why we need our own rolling cache built from every private message
we see, keyed by message id, so that when a delete event arrives we can
look up what it was and where it came from. The same cache doubles as the
"pre-edit" snapshot for edit tracking: when an edit event arrives, we read
the cached entry *before* overwriting it with the new version.

Cache is kept *per chat* — each private chat gets its own 500-message
window, so a busy chat can't push another chat's messages out of the cache.

Known limitation: message IDs are only guaranteed unique *within* a single
chat. Since delete events don't tell us which chat they're for, we keep a
global msg_id -> chat_id index to route lookups. If two different private
chats both have a message with the same id cached at the same time, the
index can only point to one of them — the other would be reported
incorrectly (or missed) if it's the one that gets deleted. Rare, but not a
hard guarantee.

Also known: Telegram fires an edit event when a link preview finishes
loading for a message that contains a URL, even though the visible text
didn't change. We detect that case (old text == new text) and skip
notifying for it so Saved Messages isn't spammed with false "edits".
"""

import asyncio
import io
import json
import os
from collections import OrderedDict, deque
from datetime import datetime as _dt
from zoneinfo import ZoneInfo

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import ReadMessageContentsRequest
from telethon.tl.types import UpdateReadMessagesContents

from config import CLOCK_TIMEZONE, PREFIX
from logger import log
import group_inbox
import state

CACHE_LIMIT_PER_CHAT = 500
MEDIA_MAX_BYTES = 20 * 1024 * 1024  # 20MB — skip downloading bigger media
                                     # to keep this cheap on every message
NOTIFY_DELAY = 1.0  # seconds between forwarded notifications — a bulk
                     # delete of hundreds of messages at once would
                     # otherwise blast Saved Messages and hit FloodWait
NOTIFY_MAX_RETRIES = 3

# Per-chat antidelete mute — chats where .انسیو was run.
# Individual deletions/edits are NOT reported, but bilateral deletions are
# always saved (regardless of mute state). Persisted across restarts.
_MUTED_FILE = "antidelete_muted.json"
_muted_chats: set = set()


def _load_muted():
    global _muted_chats
    try:
        with open(_MUTED_FILE, "r", encoding="utf-8") as f:
            _muted_chats = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        _muted_chats = set()


def _save_muted():
    try:
        with open(_MUTED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(_muted_chats), f)
    except Exception as e:
        log.warn(f"antidelete: couldn't save muted chats: {e}")


def toggle_unsave(chat_id: int) -> bool:
    """Toggle per-chat antidelete mute. Returns True if now muted, False if unmuted."""
    if chat_id in _muted_chats:
        _muted_chats.discard(chat_id)
        _save_muted()
        return False
    else:
        _muted_chats.add(chat_id)
        _save_muted()
        return True


def set_muted(chat_id: int, muted: bool) -> bool:
    """Explicitly mute/unmute per-chat antidelete. Returns the new state."""
    if muted:
        _muted_chats.add(chat_id)
    else:
        _muted_chats.discard(chat_id)
    _save_muted()
    return muted


_load_muted()

_chat_caches = {}       # chat_id -> {msg_id: entry}
_chat_cache_order = {}  # chat_id -> deque([msg_id, ...]) oldest-left
_msg_index = {}         # msg_id -> chat_id, to route delete-event lookups

# Own user id — set via set_my_id() right after client.get_me() in main.py.
# Used to skip Saved Messages (the "chat with yourself") entirely: its
# chat_id equals the account's own numeric id, so a single compare is all
# that's needed. Also used in the delete handler to silently drop entries
# that originated from Saved Messages.
_my_id: int | None = None


def set_my_id(uid: int):
    global _my_id
    _my_id = uid

# Tracks (chat_id, msg_id) for messages that WERE a `.command` you sent
# yourself. Those get edited in place by our own handlers (a `.حذف همه`
# progress bar can edit the same message a dozen times) — none of that is
# real conversation worth a 500-message cache slot, so these are skipped
# from caching entirely, from the very first edit onward. Bounded so this
# bookkeeping itself can't grow forever.
_OWN_COMMAND_ID_CAP = 1000
_own_command_ids: "OrderedDict[tuple, bool]" = OrderedDict()


def _mark_own_command(chat_id, msg_id):
    key = (chat_id, msg_id)
    _own_command_ids[key] = True
    _own_command_ids.move_to_end(key)
    if len(_own_command_ids) > _OWN_COMMAND_ID_CAP:
        _own_command_ids.popitem(last=False)


def _is_own_command(chat_id, msg_id) -> bool:
    return (chat_id, msg_id) in _own_command_ids


# ── Self-destructing (timed) media ───────────────────────────────────────
# Telegram only hands over the file once the message is "opened". Opening it
# from this session would show the sender "opened" immediately, so by default
# we DON'T. Instead we remember the message, and when you open it yourself
# (on your phone/desktop) Telegram pushes UpdateReadMessagesContents to this
# session — only then do we fetch + save it. Set TTL_AUTO_OPEN=1 to open it
# right away instead (the old behavior), or use `.تایم` (reply) per message.
TTL_AUTO_OPEN = os.getenv("TTL_AUTO_OPEN", "0") == "1"
_pending_ttl: "OrderedDict[int, int]" = OrderedDict()  # msg_id -> chat_id
_PENDING_TTL_CAP = 500


def _is_ttl(msg) -> bool:
    return bool(msg and msg.media and getattr(msg.media, "ttl_seconds", None))


async def _save_ttl(client, chat_id, msg_id, unlock=False) -> bool:
    """Fetch a timed message and post it to the inbox. unlock=True opens it
    first (marks it opened for the sender)."""
    try:
        if unlock:
            await client(ReadMessageContentsRequest(id=[msg_id]))
        entry = None
        for _ in range(4):
            # Re-fetch: the message object from the original NewMessage event
            # has no file attached — only a fresh copy after opening does.
            msg = await client.get_messages(chat_id, ids=msg_id)
            if msg and msg.media:
                entry = await _build_entry(client, msg, chat_id, allow_ttl=True)
                if entry["media_bytes"]:
                    break
            await asyncio.sleep(1.0)
        if not entry or not entry["media_bytes"]:
            log.warn(f"Anti-delete: timed media {msg_id} not downloadable (already expired?)")
            return False
        await _notify_with_retry(client, entry, msg_id)
        return True
    except Exception as e:
        log.error(f"Anti-delete: timed media save failed for {msg_id}: {e}")
        return False


async def cmd_save_ttl(event):
    """`.تایم` as a reply to a timed photo/video: open + save it on demand."""
    reply = await event.get_reply_message()
    if not reply or not _is_ttl(reply):
        await event.edit("⚠️ روی یه عکس/ویدیوی تایم‌دار ریپلای کن.")
        return
    await event.edit("⏳ در حال باز کردن و سیو…")
    ok = await _save_ttl(event.client, event.chat_id, reply.id, unlock=True)
    await event.edit("✅ تایم‌دار سیو شد." if ok else "❌ نتونستم سیوش کنم (احتمالاً منقضی شده).")


_control_bot_id = None          # resolved lazily, see _resolve_control_bot_id
_control_bot_id_attempted = False

# Extra bots whose messages should never be cached or reported.
# Set via ANTIDELETE_EXCLUDE_BOTS env var (comma-separated usernames),
# e.g. ANTIDELETE_EXCLUDE_BOTS=mybotname,anotherbot
# Nothing is hardcoded here — this file must not contain personal info.
from config import ANTIDELETE_EXCLUDE_BOTS as _EXTRA_EXCLUDE_RAW

# Split into numeric IDs (added directly) and usernames (resolved lazily)
_EXTRA_EXCLUDE_USERNAMES: set = set()
_excluded_bot_ids: set = set()
for _entry in _EXTRA_EXCLUDE_RAW:
    if _entry.lstrip("-").isdigit():
        _excluded_bot_ids.add(int(_entry))   # numeric ID — no resolve needed
    else:
        _EXTRA_EXCLUDE_USERNAMES.add(_entry) # username — resolved on first message
_excluded_resolved = False


async def _resolve_control_bot_id():
    """Resolves BOT_TOKEN's own numeric user id, once, via the Bot API's
    getMe — so anti-edit can recognize edits from *this project's own*
    control bot specifically (never other bots in general). Resolved
    lazily on first use rather than at import time: antidelete.register()
    runs before the asyncio event loop exists, so there's nothing to await
    into at that point. Failure here is non-fatal: anti-edit keeps working
    for everyone else, it just can't exclude the control bot until this
    succeeds (retried again next process start, not retried mid-run)."""
    global _control_bot_id, _control_bot_id_attempted
    _control_bot_id_attempted = True
    try:
        from telegram import Bot
        from telegram.request import HTTPXRequest

        from config import BOT_TOKEN, PROXY_HOST, PROXY_PORT

        request = HTTPXRequest(proxy=f"socks5://{PROXY_HOST}:{PROXY_PORT}") if PROXY_HOST else None
        bot = Bot(token=BOT_TOKEN, request=request) if request else Bot(token=BOT_TOKEN)
        me = await bot.get_me()
        _control_bot_id = me.id
        log.ok(f"Anti-edit: resolved control bot id ({_control_bot_id}) — its edits won't post to Saved Messages")
    except Exception as e:
        log.warn(f"Anti-edit: couldn't resolve control bot id via getMe, exclusion unavailable: {e}")


async def _resolve_excluded_bots(client):
    """Resolve extra excluded bot usernames (e.g. @TMKselfbot) to their
    numeric ids so the cache handler can skip them by id — much cheaper
    than a username string-compare on every single incoming message."""
    global _excluded_resolved
    _excluded_resolved = True
    for username in _EXTRA_EXCLUDE_USERNAMES:
        try:
            entity = await client.get_entity(username)
            _excluded_bot_ids.add(entity.id)
            log.ok(f"Anti-delete: excluding messages from @{username} (id {entity.id})")
        except Exception as e:
            log.warn(f"Anti-delete: couldn't resolve @{username}: {e}")


def _remember(chat_id, msg_id, entry):
    """Add or update a cache entry.

    Uses a deque for the per-chat insertion order so eviction is O(1)
    (deque.popleft()) instead of O(n) (list.pop(0)).  Re-caching an
    already-present message (edit case) only updates the dict value and
    does NOT move the entry to the end of the deque — FIFO eviction is
    perfectly correct here and avoids the O(n) deque.remove() call that
    the old LRU-style code needed on every edit event.
    """
    cache = _chat_caches.setdefault(chat_id, {})
    order = _chat_cache_order.setdefault(chat_id, deque())

    is_new = msg_id not in cache
    cache[msg_id] = entry
    _msg_index[msg_id] = chat_id

    if is_new:
        order.append(msg_id)
        # Evict oldest entries to stay within the per-chat cap.  Skip any
        # "ghost" ids that _pop() left behind (they're no longer in cache).
        while len(order) > CACHE_LIMIT_PER_CHAT:
            oldest = order.popleft()          # O(1) with deque
            if oldest not in cache:
                continue                       # ghost — already deleted, skip
            cache.pop(oldest, None)
            if _msg_index.get(oldest) == chat_id:
                _msg_index.pop(oldest, None)


def _pop(msg_id):
    chat_id = _msg_index.pop(msg_id, None)
    if chat_id is None:
        return None
    cache = _chat_caches.get(chat_id)
    data = cache.pop(msg_id, None) if cache else None
    # Leave the deque entry as a "ghost" — it'll be skipped during the next
    # eviction pass in _remember (which checks cache membership). This avoids
    # an O(n) deque.remove() on every deleted message; ghosts are harmless
    # because they never outnumber the CACHE_LIMIT_PER_CHAT live entries.
    return data


async def _build_cache_entry(event):
    return await _build_entry(event.client, event.message, event.chat_id)


async def _build_entry(client, msg, chat_id, allow_ttl=False):
    fwd_from_name = None
    if msg.fwd_from:
        fwd_from_name = getattr(msg.fwd_from, "from_name", None) or "منبع ناشناس"

    media_bytes = None
    media_too_large = False
    media_attributes = None
    media_mime_type = None
    media_is_photo = False
    media_file_name = None
    if msg.media:
        # Self-destructing ("timed") media is NEVER opened here — opening
        # it would show the sender "opened" before you did. It is handled
        # separately (see _save_ttl): saved only when YOU open it, or when
        # you run `.تایم` on it.
        is_ttl = bool(getattr(msg.media, "ttl_seconds", None)) and not allow_ttl
        size = None
        try:
            size = msg.file.size if msg.file else None
        except Exception:
            size = None

        if is_ttl:
            pass  # leave media_bytes empty — never touch unopened timed media
        elif size is not None and size > MEDIA_MAX_BYTES:
            media_too_large = True
        else:
            try:
                buf = io.BytesIO()
                await msg.download_media(file=buf)
                media_bytes = buf.getvalue() or None
                # Re-uploading later with NO attributes/mime-type at all
                # forces Telegram to guess the file type from scratch —
                # in practice that usually means videos land as a generic
                # document with no supports_streaming flag, so they can't
                # be played inline and have to fully download first (slow
                # or outright fails to open on some phones). Keeping the
                # ORIGINAL attributes (resolution, duration, streaming
                # flag, voice/round-video flags, etc.) and passing them
                # straight through on resend fixes that completely.
                if msg.photo:
                    media_is_photo = True
                elif msg.document:
                    media_attributes = msg.document.attributes
                    media_mime_type = msg.document.mime_type
                    media_file_name = getattr(msg.file, "name", None)
            except Exception as e:
                log.warn(f"Anti-delete: couldn't cache media for msg {msg.id}: {e}")

    # Only cheap, synchronous data goes in the cache — no entity resolution
    # here, since this runs on every single private message. Names are
    # resolved later, only for the rare message that actually gets deleted.
    return {
        "chat_id": chat_id,
        "sender_id": msg.sender_id,
        "out": bool(msg.out),
        "text": msg.raw_text or "",
        "date": msg.date,
        "fwd_from_name": fwd_from_name,
        "media_bytes": media_bytes,
        "media_too_large": media_too_large,
        "media_attributes": media_attributes,
        "media_mime_type": media_mime_type,
        "media_is_photo": media_is_photo,
        "media_file_name": media_file_name,
        "ttl": getattr(msg.media, "ttl_seconds", None) if msg.media else None,
    }


async def _resolve_label(client, entity_id):
    if entity_id is None:
        return "نامشخص"
    try:
        entity = await client.get_entity(entity_id)
        return getattr(entity, "first_name", None) or getattr(entity, "title", None) or str(entity_id)
    except Exception:
        return str(entity_id)


async def _send_cached_media(client, entity, data, caption):
    """Resends cached media in a form Telegram can actually stream/preview
    properly on both desktop and mobile.

    The mobile-not-opening bug: when we pass a bare BytesIO with no name,
    Telethon has no way to know the file is a photo even when the original
    was one — so it uploads it as a generic document. Telegram then sends
    a 'file' icon, not an inline image, and the phone's viewer has to
    download the whole thing before even deciding what to do with it.
    Setting bio.name to a real image extension tells Telethon what type to
    use without needing to re-infer it from the raw bytes."""
    bio = io.BytesIO(data["media_bytes"])

    if data.get("media_is_photo"):
        bio.name = "photo.jpg"
        await client.send_file(entity, bio, caption=caption[:1024], force_document=False)
        return

    file_name = data.get("media_file_name")
    mime = data.get("media_mime_type") or ""
    if not file_name:
        if mime.startswith("video/"):
            file_name = "video.mp4"
        elif mime.startswith("audio/"):
            file_name = "audio.ogg"
        elif mime.startswith("image/"):
            ext = mime.split("/")[-1] or "jpg"
            file_name = f"image.{ext}"
    if file_name:
        bio.name = file_name

    await client.send_file(
        entity,
        bio,
        caption=caption[:1024],
        attributes=data.get("media_attributes"),
        mime_type=mime or None,
        force_document=False,
    )


async def _notify_to(client, dest, data):
    """Core send: puts one cached message into `dest` (a group id or 'me')."""
    sender_label = "خودت" if data["out"] else await _resolve_label(client, data["sender_id"])
    date_label = (
        data["date"].astimezone(ZoneInfo(CLOCK_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
        if data["date"]
        else "?"
    )
    header = f"👤 {sender_label}\n🕒 {date_label}"
    if data.get("ttl"):
        header = f"⏳ مدیای تایم‌دار\n{header}"

    if data["media_bytes"]:
        caption = header + (f"\n\n{data['text']}" if data["text"] else "")
        await _send_cached_media(client, dest, data, caption)
    elif data["media_too_large"]:
        await client.send_message(dest, header + "\n\n⚠️ رسانه بزرگ‌تر از حد مجاز بود.")
    else:
        body = header + (f"\n\n{data['text']}" if data["text"] else "")
        await client.send_message(dest, body)


async def _notify(client, data):
    try:
        dest = await group_inbox.get_inbox(client)
    except Exception:
        dest = "me"
    await _notify_to(client, dest, data)


async def _notify_with_retry(client, data, msg_id):
    """A bulk delete of hundreds of messages fires hundreds of these back to
    back. Telegram *will* FloodWait us if we blast them all at once — retry
    with the server-told wait time instead of just dropping the message."""
    for attempt in range(NOTIFY_MAX_RETRIES):
        try:
            await _notify(client, data)
            log.ok(f"Anti-delete: reposted deleted message {msg_id} to Saved Messages")
            return
        except FloodWaitError as e:
            log.warn(f"Anti-delete: flood wait {e.seconds}s, retrying message {msg_id}")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            log.error(f"Anti-delete notify error for message {msg_id}: {e}")
            return
    log.error(f"Anti-delete: gave up on message {msg_id} after {NOTIFY_MAX_RETRIES} flood-wait retries")


async def _notify_edit(client, old_entry, new_text, new_date):
    sender_label = "خودت" if old_entry["out"] else await _resolve_label(client, old_entry["sender_id"])
    date_label = (
        new_date.astimezone(ZoneInfo(CLOCK_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
        if new_date
        else "?"
    )
    old_text = old_entry.get("text") or "(بدون متن)"
    new_text_label = new_text or "(بدون متن)"

    body = (
        f"✏️ {sender_label}\n🕒 {date_label}\n\n"
        f"🔴 {old_text}\n🟢 {new_text_label}"
    )

    try:
        dest = await group_inbox.get_inbox(client)
    except Exception:
        dest = "me"

    if old_entry.get("media_bytes"):
        await _send_cached_media(client, dest, old_entry, body)
    else:
        await client.send_message(dest, body)


async def _notify_edit_with_retry(client, old_entry, new_text, new_date, msg_id):
    for attempt in range(NOTIFY_MAX_RETRIES):
        try:
            await _notify_edit(client, old_entry, new_text, new_date)
            log.ok(f"Anti-edit: reposted pre-edit version of message {msg_id} to Saved Messages")
            return
        except FloodWaitError as e:
            log.warn(f"Anti-edit: flood wait {e.seconds}s, retrying message {msg_id}")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            log.error(f"Anti-edit notify error for message {msg_id}: {e}")
            return
    log.error(f"Anti-edit: gave up on message {msg_id} after {NOTIFY_MAX_RETRIES} flood-wait retries")


async def _cache_in_background(event):
    try:
        entry = await _build_cache_entry(event)
        _remember(event.chat_id, event.message.id, entry)
    except Exception as e:
        log.error(f"Anti-delete cache error: {e}")


async def _resend_full_conversation(client, chat_id, all_entries):
    """Saves a bilateral-delete to ONE dedicated group, message by message.
    Text messages are sent as plain Telegram messages; media (photos, videos,
    files) are sent as native Telegram files — everything opens inline in
    Telegram without any external app. One group, one run, exactly once per
    bilateral-delete event (the debounce + cooldown in register() guarantees
    this function is never called twice for the same event)."""
    all_sorted = sorted(
        all_entries,
        key=lambda item: item[1].get("date") or _dt.min.replace(tzinfo=None),
    )
    chat_label = await _resolve_label(client, chat_id)
    me_entity  = await client.get_me()
    my_name    = getattr(me_entity, "first_name", None) or "من"
    now_str    = _dt.now(ZoneInfo(CLOCK_TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    count      = len(all_sorted)

    try:
        dest = await group_inbox.create_bilateral_group(client, chat_label)
    except Exception as e:
        log.error(f"Anti-delete: couldn't create bilateral group: {e}")
        try:
            dest = await group_inbox.get_inbox(client)
        except Exception:
            dest = "me"

    # ── Header ────────────────────────────────────────────────────────────
    await client.send_message(
        dest,
        f"🗑 **دیلیت دوطرفه با {chat_label}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 مخاطب: **{chat_label}**\n"
        f"📝 تعداد پیام: **{count}**\n"
        f"🕒 زمان: `{now_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━",
    )
    await asyncio.sleep(0.5)

    # ── Messages — one by one ─────────────────────────────────────────────
    for _mid, data in all_sorted:
        is_out   = data["out"]
        date_obj = data.get("date")
        try:
            ts = (date_obj.astimezone(ZoneInfo(CLOCK_TIMEZONE)).strftime("%H:%M")
                  if date_obj else "?")
        except Exception:
            ts = "?"

        arrow  = "▶️" if is_out else "◀️"
        sender = f"**{my_name}**" if is_out else f"**{chat_label}**"
        prefix = f"{arrow} {sender} · _{ts}_"
        fwd    = (f"\n↪️ _فوروارد از {data['fwd_from_name']}_"
                  if data.get("fwd_from_name") else "")
        text   = data.get("text") or ""

        try:
            if data.get("media_bytes"):
                caption = f"{prefix}{fwd}"
                if text:
                    caption += f"\n{text}"
                await _send_cached_media(client, dest, data, caption)
            else:
                body = f"{prefix}{fwd}"
                if text:
                    body += f"\n{text}"
                elif data.get("media_too_large"):
                    body += "\n📎 _رسانه — بزرگ‌تر از ۲۰MB بود_"
                else:
                    body += "\n_(بدون متن)_"
                await client.send_message(dest, body)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            try:
                if data.get("media_bytes"):
                    await _send_cached_media(client, dest, data, caption)
                else:
                    await client.send_message(dest, body)
            except Exception as e2:
                log.error(f"Anti-delete bilateral resend: {e2}")
        except Exception as e:
            log.error(f"Anti-delete bilateral resend: {e}")

        await asyncio.sleep(NOTIFY_DELAY)

    # ── Footer ────────────────────────────────────────────────────────────
    await client.send_message(dest, f"━━━━━━━━━━━━━━━━━━━━\n🏁 **پایان مکالمه با {chat_label}**")

    log.ok(f"Anti-delete: bilateral delete with {chat_label} saved ({count} messages)")


def register(client):
    """Call once with the running TelegramClient to enable anti-delete.
    Call set_my_id(me.id) after login so Saved Messages is excluded."""

    @client.on(events.NewMessage())
    async def _cache_handler(event):
        if state.paused:
            return
        if not event.is_private:
            return
        if _my_id is not None and event.chat_id == _my_id:
            return
        if event.message.out and (event.message.raw_text or "").startswith(PREFIX):
            _mark_own_command(event.chat_id, event.message.id)
            return

        if not event.message.out:
            if not _control_bot_id_attempted:
                await _resolve_control_bot_id()
            if _control_bot_id is not None and event.sender_id == _control_bot_id:
                return
            if not _excluded_resolved:
                await _resolve_excluded_bots(client)
            if event.sender_id in _excluded_bot_ids:
                return

        if not event.message.out and _is_ttl(event.message):
            _pending_ttl[event.message.id] = event.chat_id
            while len(_pending_ttl) > _PENDING_TTL_CAP:
                _pending_ttl.popitem(last=False)
            if TTL_AUTO_OPEN:
                asyncio.create_task(_save_ttl(client, event.chat_id, event.message.id, unlock=True))
            return

        # Still cache even for muted chats — bilateral-delete detection
        # needs the cache. Muting only suppresses individual notifications.
        asyncio.create_task(_cache_in_background(event))

    @client.on(events.MessageEdited())
    async def _edit_handler(event):
        if state.paused:
            return
        if not event.is_private:
            return
        if _my_id is not None and event.chat_id == _my_id:
            return
        chat_id = event.chat_id
        msg_id = event.message.id
        new_text = event.message.raw_text or ""

        if _is_own_command(chat_id, msg_id):
            # A self-issued command's own reply being edited (e.g. a
            # `.حذف همه` progress update) — never cached, never reported.
            return

        if event.message.out:
            # Outgoing edits — you edited your own message. Nothing to report;
            # just refresh the cache so if the message later gets deleted the
            # most recent text is stored (not the stale original).
            asyncio.create_task(_cache_in_background(event))
            return

        if not _control_bot_id_attempted:
            await _resolve_control_bot_id()
        if _control_bot_id is not None and event.sender_id == _control_bot_id:
            return
        if not _excluded_resolved:
            await _resolve_excluded_bots(client)
        if event.sender_id in _excluded_bot_ids:
            return

        # Muted chat — refresh cache but don't notify about edits
        if event.chat_id in _muted_chats:
            asyncio.create_task(_cache_in_background(event))
            return

        cache = _chat_caches.get(chat_id)
        old_entry = cache.get(msg_id) if cache else None

        if old_entry is None:
            # We never cached a pre-edit version (e.g. the bot started
            # after this message was sent) — nothing to compare against.
            asyncio.create_task(_cache_in_background(event))
            return

        if new_text == old_entry.get("text", ""):
            # Same text, e.g. a link preview just attached — Telegram fires
            # an edit event for that too, but there's nothing to report.
            asyncio.create_task(_cache_in_background(event))
            return

        asyncio.create_task(
            _notify_edit_with_retry(client, old_entry, new_text, event.message.date, msg_id)
        )
        # Refresh the cache to the edited version so a *second* edit (or a
        # later delete) compares against/reports the latest text, not the
        # original one.
        asyncio.create_task(_cache_in_background(event))

    @client.on(events.Raw(UpdateReadMessagesContents))
    async def _ttl_opened_handler(update):
        if state.paused:
            return
        for mid in update.messages:
            chat_id = _pending_ttl.pop(mid, None)
            if chat_id is not None:
                asyncio.create_task(_save_ttl(client, chat_id, mid, unlock=False))

    # Debounce state for bilateral deletes: Telegram fires multiple
    # MessageDeleted batches for a single "delete for everyone" — sometimes
    # spread over 10+ seconds for large chats — so we accumulate all batches
    # in a window and call _resend_full_conversation exactly once per event.
    _bilateral_pending:   dict = {}  # chat_id -> {msg_id: data}
    _bilateral_tasks:     dict = {}  # chat_id -> asyncio.Task
    _bilateral_processed: dict = {}  # chat_id -> loop.time() of last processing
    BILATERAL_DEBOUNCE_SECS = 10.0   # was 3 s — Telegram can spread batches over 10 s+
    BILATERAL_COOLDOWN_SECS = 90.0   # ignore late batches for 90 s after processing

    async def _flush_bilateral(chat_id):
        await asyncio.sleep(BILATERAL_DEBOUNCE_SECS)
        entries = list(_bilateral_pending.pop(chat_id, {}).items())
        _bilateral_tasks.pop(chat_id, None)
        if entries:
            # Stamp BEFORE launching the task so any late-arriving batches
            # that slip in while rendering is still in progress are blocked
            # by the cooldown check in _delete_handler.
            _bilateral_processed[chat_id] = asyncio.get_event_loop().time()
            asyncio.create_task(_resend_full_conversation(client, chat_id, entries))

    @client.on(events.MessageDeleted())
    async def _delete_handler(event):
        if state.paused:
            return
        if not event.deleted_ids:
            return

        hits = []
        for msg_id in event.deleted_ids:
            data = _pop(msg_id)
            if data is not None:
                hits.append((msg_id, data))

        if not hits:
            return

        # Skip anything from Saved Messages or from the control bot.
        if _my_id is not None:
            hits = [(mid, d) for mid, d in hits if d["chat_id"] != _my_id]
        if _control_bot_id is not None:
            hits = [(mid, d) for mid, d in hits if d.get("sender_id") != _control_bot_id]

        if not hits:
            return

        own_hits   = [(mid, d) for mid, d in hits if     d["out"]]
        other_hits = [(mid, d) for mid, d in hits if not d["out"]]

        # ── Full 2-sided PV deletion ──────────────────────────────────────
        # Accumulate batches in a debounce window; the flush task creates
        # exactly ONE group after the window closes.
        if own_hits and other_hits:
            chat_id = hits[0][1]["chat_id"]

            # If we already processed a bilateral delete for this chat
            # recently, this is just a late-arriving batch from the same
            # event — skip it entirely so no second group gets created.
            now  = asyncio.get_event_loop().time()
            last = _bilateral_processed.get(chat_id, 0.0)
            if now - last < BILATERAL_COOLDOWN_SECS:
                return

            bucket = _bilateral_pending.setdefault(chat_id, {})
            for mid, d in hits:
                bucket[mid] = d
            # Also grab any remaining cached messages for this chat
            for mid, d in _chat_caches.get(chat_id, {}).items():
                bucket.setdefault(mid, d)
            # (Re)start the debounce timer — cancels the previous one so
            # a new batch resets the 10 s countdown from scratch.
            if chat_id in _bilateral_tasks:
                _bilateral_tasks[chat_id].cancel()
            _bilateral_tasks[chat_id] = asyncio.create_task(_flush_bilateral(chat_id))
            return

        if own_hits and not other_hits:
            return  # You deleted your own message — nothing to report.

        # Individual deletions by the other person
        for msg_id, data in other_hits:
            # Skip notification if this chat is muted (.انسیو was run here).
            # Bilateral deletes (own_hits AND other_hits) always bypass this
            # check above, so the conversation is always saved even when muted.
            if data.get("chat_id") in _muted_chats:
                continue
            await _notify_with_retry(client, data, msg_id)
            await asyncio.sleep(NOTIFY_DELAY)

    log.ok(f"Anti-delete / anti-edit enabled (PV only, {CACHE_LIMIT_PER_CHAT} messages cached per chat)")


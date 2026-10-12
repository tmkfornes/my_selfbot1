"""Moderation plugins: bulk-deleting the account's own messages."""

import asyncio

from telethon.errors import FloodWaitError

from logger import log
from telegram_layer import client

DELETE_MAX_COUNT = 2000  # safety cap so a typo can't nuke an entire chat history
SCAN_CHUNK = 200            # raw messages scanned per history page
DELETE_BATCH_PAUSE = 1.5    # seconds between batches


async def cmd_delete(event, arg):
    """Deletes your own messages in this chat (revoked for everyone —
    never touches anyone else's messages).

    Three modes, checked in this order:
    1. Reply mode: reply to one of your own messages (or anyone's — only
       the id matters) and send `.حذف` with no number. Deletes every
       message *you* sent in this chat from the replied-to message down
       to right now.
    2. `.حذف همه` / `.حذف all` — wipes every message you've ever sent in
       this chat.
    3. `.حذف <عدد>` — deletes your last N messages in this chat.
    """
    from config import PREFIX

    arg_norm = (arg or "").strip().lower()

    reply = await event.get_reply_message() if event.is_reply else None
    if reply is not None:
        try:
            await event.edit("🗑 در حال پاک‌سازی از پیام ریپلای‌شده تا الان…")
        except Exception:
            pass
        total = await _bulk_delete_own(event, min_id=reply.id - 1)
        await event.edit(f"✅ {total} پیام (از پیام ریپلای‌شده تا الان) حذف شد.")
        await asyncio.sleep(3)
        try:
            await event.delete()
        except Exception:
            pass
        return

    if arg_norm in ("همه", "all"):
        try:
            await event.edit("🗑 در حال پاک‌سازی کامل چت…")
        except Exception:
            pass
        total = await _bulk_delete_own(event)
        await event.edit(f"✅ پاک‌سازی کامل شد — {total} پیام حذف شد. اثری ازت تو این چت نموند.")
        await asyncio.sleep(4)
        try:
            await event.delete()
        except Exception:
            pass
        return

    if not arg or not arg.isdigit():
        await event.edit(
            f"استفاده: `{PREFIX}حذف <تعداد>` یا `{PREFIX}حذف همه`  (مثلاً `{PREFIX}حذف 100`)\n"
            f"یا رو یه پیام ریپلای کن و بدون عدد `{PREFIX}حذف` رو بزن — همه‌ی پیام‌های "
            "خودت از همون‌جا تا الان پاک می‌شه."
        )
        return

    count = int(arg)
    if count <= 0:
        await event.edit("عدد باید بزرگ‌تر از صفر باشه.")
        return
    count = min(count, DELETE_MAX_COUNT)

    total = await _bulk_delete_own(event, max_count=count)
    await event.edit(f"🗑 {total} پیام حذف شد.")
    await asyncio.sleep(3)
    try:
        await event.delete()
    except Exception:
        pass


async def _bulk_delete_own(event, min_id: int = 0, max_count: int | None = None) -> int:
    """Shared delete engine for all three modes above.

    Walks the chat's raw message history backward by id (`offset_id`),
    one page at a time, and deletes your own messages (`msg.out`) found
    in each page — instead of re-querying with `from_user="me"` after
    every batch like the old code did. That re-query approach is what
    caused `.حذف همه` to silently stop after ~200 messages on larger
    chats: Telegram's history view doesn't always reflect a delete
    instantly, so the "find what's left" step could undercount. Walking
    by raw id is immune to that — every message in range gets scanned
    and matched exactly once, regardless of deletion timing.

    `min_id`: only messages with id > min_id are touched (0 = no floor,
    i.e. delete everything). `max_count`: stop once this many of your
    messages have been deleted (None = no cap, used for "همه"/reply mode).
    """
    chat_id = event.chat_id
    total_deleted = 0
    offset_id = 0  # 0 = start scanning from the most recent message

    while max_count is None or total_deleted < max_count:
        batch_ids = []
        oldest_id_in_page = None

        async for msg in client.iter_messages(chat_id, offset_id=offset_id, limit=SCAN_CHUNK):
            oldest_id_in_page = msg.id
            if msg.id <= min_id:
                break
            if msg.id != event.id and msg.out:
                batch_ids.append(msg.id)
                if max_count is not None and total_deleted + len(batch_ids) >= max_count:
                    break

        if oldest_id_in_page is None:
            break  # reached the very start of the chat's history

        if batch_ids:
            try:
                await client.delete_messages(chat_id, batch_ids, revoke=True)
            except FloodWaitError as e:
                try:
                    await event.edit(
                        f"⏳ محدودیت تلگرام؛ {e.seconds} ثانیه صبر می‌کنم و ادامه می‌دم… "
                        f"(تا الان {total_deleted} پیام حذف شد)"
                    )
                except Exception:
                    pass
                await asyncio.sleep(e.seconds)
                continue  # retry this same page, offset_id unchanged
            except Exception as e:
                log.error(f"Bulk delete error after {total_deleted} messages: {e}")
                try:
                    await event.edit(f"⚠️ خطا بعد از حذف {total_deleted} پیام: {e}")
                except Exception:
                    pass
                return total_deleted

            total_deleted += len(batch_ids)
            try:
                await event.edit(f"🗑 در حال پاک‌سازی… {total_deleted} پیام حذف شد.")
            except Exception:
                pass  # a rate-limited edit shouldn't stop the actual delete loop

        if oldest_id_in_page <= min_id:
            break

        offset_id = oldest_id_in_page
        await asyncio.sleep(DELETE_BATCH_PAUSE)

    return total_deleted

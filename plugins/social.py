"""Social plugins: mass mentions, block/unblock, and id lookup by
reply/id/username/name."""

import asyncio
import difflib

from telethon.errors import FloodWaitError
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from telethon.tl.types import ChannelParticipantsAdmins, InputMessageEntityMentionName, InputUser

from logger import log
from telegram_layer import client

TAG_BATCH_SIZE = 90  # users per message — Telegram allows ~100 entities per message
TAG_BATCH_DELAY = 0.8  # seconds between batches — gentle pacing to avoid FloodWait


async def _send_mass_mention(event, users, label, reply_to=None):
    if not users:
        await event.edit(f"هیچ {label} پیدا نشد.")
        return

    chat_id = event.chat_id
    total = len(users)
    tagged = 0
    skipped = 0

    for i in range(0, total, TAG_BATCH_SIZE):
        batch = users[i:i + TAG_BATCH_SIZE]
        text = ""
        entities = []
        offset = 0
        batch_mention_count = 0
        for user in batch:
            username = getattr(user, "username", None)
            if username:
                piece = f"@{username}"
                text += piece + " "
                offset += len(piece) + 1
                batch_mention_count += 1
                continue

            try:
                input_user = await client.get_input_entity(user.id)
            except (ValueError, TypeError) as e:
                access_hash = getattr(user, "access_hash", None)
                if access_hash is None:
                    log.warn(f"Tag: no usable entity for user {user.id}: {e}")
                    skipped += 1
                    continue
                input_user = InputUser(user_id=user.id, access_hash=access_hash)

            piece = "."
            entities.append(InputMessageEntityMentionName(offset, len(piece), user_id=input_user))
            text += piece + " "
            offset += len(piece) + 1
            batch_mention_count += 1

        if batch_mention_count == 0:
            continue

        try:
            await client.send_message(
                chat_id, text, formatting_entities=entities or None, parse_mode=None, reply_to=reply_to
            )
            tagged += batch_mention_count
        except FloodWaitError as e:
            log.warn(f"Tag flood wait, sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            log.error(f"Tag batch error: {e}")

        await asyncio.sleep(TAG_BATCH_DELAY)

    summary = f"🔔 {tagged} نفر ({label}) تگ شدن."
    if skipped:
        summary += f" ({skipped} نفر قابل تگ نبودن)"
    await event.edit(summary)


async def cmd_tag_members(event):
    chat = await event.get_chat()
    try:
        participants = await client.get_participants(chat)
    except Exception as e:
        await event.edit(f"⚠️ نتونستم لیست اعضا رو بگیرم: {e}")
        return
    users = [u for u in participants if not u.bot]
    # If `.تگ اعضا` was itself sent as a reply to some message, tag the
    # members by replying to THAT message too — so every tag batch lands
    # threaded under the original message instead of floating loose in
    # the chat.
    reply_to = event.reply_to_msg_id if event.is_reply else None
    await _send_mass_mention(event, users, "عضو", reply_to=reply_to)


async def cmd_tag_admins(event):
    chat = await event.get_chat()
    try:
        admins = await client.get_participants(chat, filter=ChannelParticipantsAdmins)
    except Exception as e:
        await event.edit(f"⚠️ نتونستم لیست ادمین‌ها رو بگیرم (شاید گروه ساده‌ست، نه سوپرگروه): {e}")
        return
    reply_to = event.reply_to_msg_id if event.is_reply else None
    await _send_mass_mention(event, admins, "ادمین", reply_to=reply_to)


async def _block_entity(entity) -> bool:
    try:
        await client(BlockRequest(id=entity))
        return True
    except Exception as e:
        log.error(f"block failed: {e!r}")
        return False


async def _unblock_entity(entity) -> bool:
    try:
        await client(UnblockRequest(id=entity))
        return True
    except Exception as e:
        log.error(f"unblock failed: {e!r}")
        return False


def _name_match_score(entity, query: str) -> float:
    parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None), getattr(entity, "username", None)]
    haystack = " ".join(p for p in parts if p).strip()
    if not haystack or not query:
        return 0.0
    if query.lower() in haystack.lower():
        return 1.0
    return difflib.SequenceMatcher(None, query.lower(), haystack.lower()).ratio()


async def _find_user_in_pvs(name: str):
    if not name:
        return None
    best_entity, best_score = None, 0.0
    async for dialog in client.iter_dialogs():
        if not dialog.is_user:
            continue
        score = _name_match_score(dialog.entity, name)
        if score > best_score:
            best_score, best_entity = score, dialog.entity
    return best_entity if best_score >= 0.45 else None


async def _resolve_target(event, name: str):
    """Shared target resolution for block/unblock/get_id, in priority
    order: (1) reply to the target's message — always checked FIRST,
    regardless of whether a name was also typed, (2) numeric id,
    (3) @username, (4) name search across your own PVs, (5) — if no name
    and no reply — the other side of the current PV, if any."""
    reply = await event.get_reply_message()
    if reply is not None and reply.sender_id is not None:
        try:
            return await client.get_entity(reply.sender_id)
        except Exception:
            pass

    name = (name or "").strip()
    if name:
        if name.isdigit():
            try:
                return await client.get_entity(int(name))
            except Exception:
                return None
        username = name.lstrip("@")
        if username and " " not in username and username.replace("_", "").isalnum() and len(username) >= 5:
            try:
                return await client.get_entity(username)
            except Exception:
                pass  # fall through to name search
        return await _find_user_in_pvs(name)

    if event.is_private:
        try:
            return await event.get_chat()
        except Exception:
            return None

    return None


def _display_name(entity) -> str:
    return getattr(entity, "first_name", None) or getattr(entity, "username", None) or str(entity.id)


async def cmd_block(event, arg: str):
    from config import PREFIX

    entity = await _resolve_target(event, arg)
    if entity is None:
        await event.edit(
            f"استفاده: `{PREFIX}بلاک <اسم/آیدی/یوزرنیم>` — یا رو پیام طرف ریپلای بزن، "
            "یا تو پی‌وی خودش بدون اسم بزن."
        )
        return
    display = _display_name(entity)
    ok = await _block_entity(entity)
    await event.edit(f"🚫 {display} بلاک شد." if ok else f"⚠️ بلاک {display} انجام نشد — لاگ رو چک کن.")


async def cmd_unblock(event, arg: str):
    from config import PREFIX

    entity = await _resolve_target(event, arg)
    if entity is None:
        await event.edit(f"استفاده: `{PREFIX}آنبلاک <اسم/آیدی/یوزرنیم>` — یا رو پیام طرف ریپلای بزن.")
        return
    display = _display_name(entity)
    ok = await _unblock_entity(entity)
    await event.edit(f"✅ {display} از بلاک درومد." if ok else f"⚠️ آنبلاک {display} انجام نشد — لاگ رو چک کن.")


async def cmd_get_id(event, arg: str):
    """.ایدی — reply to someone's message and just send `.ایدی` with no
    args to get THEIR id; or give a name/username/id directly."""
    entity = await _resolve_target(event, arg)
    if entity is None:
        from config import PREFIX
        await event.edit(f"استفاده: `{PREFIX}ایدی <اسم/آیدی/یوزرنیم>` — یا رو پیام طرف ریپلای بزن.")
        return
    display = _display_name(entity)
    await event.edit(f"🆔 {display}: `{entity.id}`")

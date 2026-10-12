"""
group_inbox.py — group inboxes for anti-delete/edit.

TWO groups total, regardless of how many contacts you have:

  "📥 حذف‌شده‌ها"
      One persistent group for ALL one-sided deletes and edits, all
      contacts mixed in. Created once on first use, reused forever.

  "🗑 دیلیت دوطرفه — <contact name>  <timestamp>"
      A fresh group created ONCE per bilateral-delete event. Because
      Telegram fires multiple MessageDeleted batches for a single
      "delete for everyone", the handler in antidelete.py debounces
      them (waits a short window, collects all batches, then calls
      create_bilateral_group exactly once per event).

Groups are megagroups (real Telegram groups) with no other members.
The persistent-inbox id is stored in group_inbox_map.json so it
survives restarts without creating duplicates.
"""

import asyncio
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon.tl.functions.channels import CreateChannelRequest

from config import CLOCK_TIMEZONE
from logger import log

_MAP_FILE = os.path.join(os.path.dirname(__file__), "group_inbox_map.json")
_lock = asyncio.Lock()
_inbox_id: int | None = None
_loaded = False


def _load():
    global _loaded, _inbox_id
    if _loaded:
        return
    _loaded = True
    if os.path.exists(_MAP_FILE):
        try:
            data = json.loads(open(_MAP_FILE, encoding="utf-8").read())
            _inbox_id = data.get("inbox_id")
        except Exception:
            pass


def _save():
    try:
        with open(_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump({"inbox_id": _inbox_id}, f)
    except Exception as e:
        log.error(f"group_inbox: couldn't save map: {e}")


async def _create_group(client, title: str) -> int:
    result = await client(CreateChannelRequest(title=title, about="", megagroup=True))
    gid = result.chats[0].id
    log.ok(f"group_inbox: created «{title}» (id={gid})")
    return gid


async def get_inbox(client) -> int:
    """Returns the single persistent inbox group id, creating it if needed."""
    global _inbox_id
    async with _lock:
        _load()
        if _inbox_id is not None:
            return _inbox_id
        _inbox_id = await _create_group(client, "📥 حذف‌شده‌ها")
        _save()
        return _inbox_id


async def create_bilateral_group(client, contact_label: str) -> int:
    """Creates ONE fresh group for a bilateral-delete event."""
    now = datetime.now(ZoneInfo(CLOCK_TIMEZONE)).strftime("%m/%d %H:%M")
    title = f"🗑 {contact_label} — {now}"
    return await _create_group(client, title)

"""
Command Router — plain exact-command/alias dispatch. No AI, no agent, no
natural-language fallback: if the first word after the prefix isn't one of
the commands below (or close enough to one — see _resolve_cmd), the
message is silently ignored (same as a `.` typo in normal chat always did).
"""

import difflib
import sys

from telethon.errors import FloodWaitError

import antidelete
import state
from help_text import HELP_TEXT
from logger import log
from plugins import ai_chat, core, downloader, media, moderation, panel, social, translate

# Every command word this bot recognizes, canonical spelling. Used as the
# fuzzy-match target list below — NOT the dispatch logic itself, which
# still lives in the plain elif chain further down.
_KNOWN_COMMANDS = [
    "ping", "status", "clock", "font", "quote", "اسکرین", "حذف", "تگ",
    "پنل", "بستن", "بلاک", "block", "آنبلاک", "unblock", "تاریخ",
    "help", "راهنما", "ai", "ایدی", "id", "ترجمه", "translate",
    ".", "انسیو", "سیو", "تایم", "خاموش", "روشن",
]


def _resolve_cmd(cmd: str) -> str:
    """message_parser already normalizes Arabic/Persian character variants
    (آ/أ/إ -> ا, ي -> ی, ك -> ک, ...), so spelling differences like
    'آیدی' vs 'ایدی' already collapse to the same string by the time `cmd`
    gets here. This adds one more layer of tolerance on top, for near-miss
    typos (an extra/missing/swapped letter) — plain string similarity via
    the stdlib, no AI/network call involved. Exact matches skip this
    entirely; very short commands (id/ai) are excluded from fuzzy matching
    since a 2-letter string can "look close" to almost anything."""
    if cmd in _KNOWN_COMMANDS:
        return cmd
    if len(cmd) < 3:
        return cmd
    matches = difflib.get_close_matches(cmd, _KNOWN_COMMANDS, n=1, cutoff=0.8)
    return matches[0] if matches else cmd


async def route(event, cmd: str, arg: str, body: str):
    cmd = _resolve_cmd(cmd)
    try:
        # While paused (.خاموش), everything is ignored except .روشن
        if state.paused and cmd != "روشن":
            return
        if cmd == "ping":
            await core.cmd_ping(event)
        elif cmd == "status":
            await core.cmd_status(event)
        elif cmd == "clock":
            await core.cmd_clock(event, arg)
        elif cmd == "font":
            await core.cmd_font(event, arg)
        elif cmd == "quote":
            await media.cmd_quote(event)
        elif cmd == "اسکرین":
            await media.cmd_screen(event)
        elif cmd == "حذف":
            await moderation.cmd_delete(event, arg)
        elif cmd == "تگ":
            if arg == "اعضا":
                await social.cmd_tag_members(event)
            elif arg == "ادمین":
                await social.cmd_tag_admins(event)
            else:
                from config import PREFIX
                await event.edit(f"استفاده: `{PREFIX}تگ اعضا` یا `{PREFIX}تگ ادمین`")
        elif cmd == "پنل":
            await panel.cmd_panel(event)
        elif cmd == "بستن":
            await panel.cmd_panel_close(event)
        elif cmd in ("بلاک", "block"):
            await social.cmd_block(event, arg)
        elif cmd in ("آنبلاک", "unblock"):
            await social.cmd_unblock(event, arg)
        elif cmd == "تاریخ":
            await core.cmd_date(event)
        elif cmd in ("help", "راهنما"):
            await event.edit(HELP_TEXT)
        elif cmd == "ai":
            await ai_chat.cmd_ai(event, arg)
        elif cmd in ("ایدی", "id"):
            await social.cmd_get_id(event, arg)
        elif cmd in ("ترجمه", "translate"):
            # arg is lowercased by the parser (fine for the language word),
            # but body preserves original case — needed so English text to
            # translate doesn't get silently lowercased before sending.
            rest = body.split(maxsplit=1)[1] if len(body.split(maxsplit=1)) > 1 else ""
            rest_parts = rest.split(maxsplit=1)
            lang_word = rest_parts[0] if rest_parts else ""
            text_body = rest_parts[1] if len(rest_parts) > 1 else ""
            await translate.cmd_translate(event, lang_word, text_body)
        elif cmd == ".":
            await downloader.cmd_download_video(event)
        elif cmd == "انسیو":
            if event.is_private:
                muted = antidelete.toggle_unsave(event.chat_id)
                if muted:
                    await event.edit(
                        "🔕 **آنتی‌دیلیت این چت خاموش شد**\n"
                        "پیام‌های حذف/ویرایش‌شده دیگه سیو نمی‌شن.\n"
                        "_دیلیت دوطرفه همیشه سیو می‌شه._"
                    )
                else:
                    await event.edit("🔔 **آنتی‌دیلیت این چت دوباره روشن شد**")
            else:
                await event.edit("⚠️ این دستور فقط تو PV کار می‌کنه.")
        elif cmd == "تایم":
            await antidelete.cmd_save_ttl(event)
        elif cmd == "سیو":
            if event.is_private:
                antidelete.set_muted(event.chat_id, False)
                await event.edit("🔔 **آنتی‌دیلیت این چت روشن شد**\nپیام‌های حذف/ویرایش‌شده دوباره سیو می‌شن.")
            else:
                await event.edit("⚠️ این دستور فقط تو PV کار می‌کنه.")
        elif cmd == "خاموش":
            import clock
            from telegram_layer import client as tg_client
            if arg == "کامل":
                # Full process shutdown (old behavior) — can't be undone with .روشن
                await event.edit("🔴 سلف‌بات کامل خاموش شد (برای روشن‌کردن باید سرویس رو ری‌استارت کنی)…")
                try:
                    await clock.on_selfbot_stop(tg_client)
                except Exception:
                    pass
                sys.exit(0)
            state.paused = True
            state.clock_was_on = clock.clock_active
            if clock.clock_active:
                try:
                    await clock.stop_clock(tg_client)
                except Exception:
                    pass
            await event.edit("🔴 سلف‌بات خاموش شد (فقط `.روشن` کار می‌کنه).")
        elif cmd == "روشن":
            if not state.paused:
                await event.edit("🟢 سلف‌بات از قبل روشن بود.")
            else:
                state.paused = False
                if state.clock_was_on:
                    import clock
                    from telegram_layer import client as tg_client
                    try:
                        await clock.start_clock(tg_client)
                    except Exception:
                        pass
                state.clock_was_on = False
                await event.edit("🟢 سلف‌بات دوباره روشن شد.")
        # Nothing else matches → silently ignored

    except FloodWaitError as e:
        log.warn(f"Rate limited, wait {e.seconds}s")
    except Exception as e:
        log.error(f"Handler error ({cmd}): {e}")

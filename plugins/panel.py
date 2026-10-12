"""Panel plugins: the self-account can't show buttons, so this hands the
user a link to control_bot.py instead, which can."""

from logger import log

_control_bot_username_cache: str | None = None


async def _get_control_bot_username() -> str | None:
    """Fetches (and caches) the control bot's @username via the Bot API,
    so cmd_panel can link to it. Only needs to hit the network once."""
    global _control_bot_username_cache
    if _control_bot_username_cache:
        return _control_bot_username_cache
    try:
        from telegram import Bot
        from config import BOT_TOKEN

        bot = Bot(token=BOT_TOKEN)
        me = await bot.get_me()
        _control_bot_username_cache = me.username
        return me.username
    except Exception as e:
        log.error(f"Could not fetch control bot username: {e}")
        return None


async def cmd_panel(event):
    """Telegram only renders reply-markup buttons (both inline callback
    buttons AND custom reply keyboards) when the sending account is a bot —
    official clients silently drop reply_markup from regular user accounts.
    That's why the old Button.text(...) reply keyboard here never actually
    showed up: the message went out with no visible buttons at all.

    The project already ships a real bot for this (control_bot.py, using
    BOT_TOKEN) with working inline buttons. So instead of faking a keyboard
    from the self-account, just hand the user a link to that bot chat."""
    from config import PREFIX

    username = await _get_control_bot_username()
    if username:
        await event.edit(
            "🎛 پنل کنترل از طریق ربات زیر در دسترسه (چون دکمه از اکانت شخصی نمایش داده نمی‌شه):\n"
            f"👉 [باز کردن پنل](https://t.me/{username}?start=panel)\n\n"
            f"دستورات مخصوص این چت (حذف/تگ) رو همچنان با `{PREFIX}حذف` و `{PREFIX}تگ` از همینجا بفرست."
        )
    else:
        await event.edit(
            "⚠️ نتونستم آدرس ربات کنترل رو پیدا کنم — مطمئن شو BOT_TOKEN درست تنظیم شده."
        )


async def cmd_panel_close(event):
    await event.edit("✅ باشه.")


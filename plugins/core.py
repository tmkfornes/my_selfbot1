"""Core utility plugins: ping, date, status, clock, font."""

import time

import clock
import fontstyle
import persian_date
from config import CLOCK_TIMEZONE, PREFIX
from telegram_layer import client


async def cmd_ping(event):
    start = time.monotonic()
    await event.edit("🏓 …")
    latency_ms = (time.monotonic() - start) * 1000
    await event.edit(f"🏓 Pong! `{latency_ms:.0f}ms`")


async def cmd_date(event):
    await event.edit(persian_date.today_string(CLOCK_TIMEZONE))


async def cmd_status(event):
    font_active = fontstyle.get_active() or "خاموش"
    text = (
        "**📊 وضعیت سلف**\n"
        f"⏰ ساعت: {'روشن' if clock.clock_active else 'خاموش'}\n"
        f"🔠 فونت: {font_active}"
    )
    await event.edit(text)


async def cmd_clock(event, arg):
    if arg == "on":
        started, first_ok = await clock.start_clock(client)
        if not started:
            await event.edit("⏰ ساعت از قبل روشن بود.")
        elif first_ok:
            await event.edit("⏰ ساعت **روشن** شد.")
        else:
            await event.edit("⏰ ساعت فعال شد، ولی تلگرام موقتاً محدودش کرده — اسم خودش به‌محض رفع محدودیت آپدیت میشه.")
    elif arg == "off":
        stopped = await clock.stop_clock(client)
        await event.edit("⏰ ساعت **خاموش** شد و اسم پاک شد." if stopped else "⏰ ساعت از قبل خاموش بود.")
    else:
        await event.edit(f"استفاده: `{PREFIX}clock on` یا `{PREFIX}clock off`")


async def cmd_font(event, arg):
    if arg in ("", "status"):
        active = fontstyle.get_active()
        await event.edit(f"🔠 فونت فعلی: {active or 'خاموش'}")
    elif arg in ("off", "none"):
        fontstyle.set_font(None)
        await event.edit("🔠 استایل فونت **خاموش** شد.")
    elif arg == "on":
        restored = fontstyle.restore_font()
        if restored:
            label = fontstyle.FONTS[restored][0]
            await event.edit(f"🔠 فونت **{label}** دوباره فعال شد.")
        else:
            await event.edit(f"🔠 فونت قبلی‌ای تو حافظه نیست — یه فونت انتخاب کن: `{PREFIX}font list`")
    elif arg == "list":
        names = "\n".join(f"• `{key}` — {label}" for key, (label, _) in fontstyle.FONTS.items())
        await event.edit(f"🔠 فونت‌های موجود:\n{names}")
    elif arg in fontstyle.FONTS:
        fontstyle.set_font(arg)
        label = fontstyle.FONTS[arg][0]
        await event.edit(f"🔠 فونت روی **{label}** تنظیم شد.")
    else:
        await event.edit(f"فونت نامعتبره. برای لیست: `{PREFIX}font list`")

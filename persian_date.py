"""Gregorian → Jalali (Persian/Shamsi) calendar conversion, implemented
directly instead of depending on the third-party `jdatetime` package —
one less thing to `pip install` on whatever server this runs on."""

from datetime import datetime
from zoneinfo import ZoneInfo

WEEKDAY_NAMES_FA = (
    "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه",
)  # index = datetime.weekday() (Monday=0 .. Sunday=6)

MONTH_NAMES_FA = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_days_in_month = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]

    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621

    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_days_in_month[gm - 1]
    )

    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)

    return jy, jm, jd


def today_string(timezone: str) -> str:
    """Human-readable Persian line with today's Jalali + Gregorian date
    and current time, in the given IANA timezone."""
    now = datetime.now(ZoneInfo(timezone))
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    weekday = WEEKDAY_NAMES_FA[now.weekday()]
    jalali_month = MONTH_NAMES_FA[jm - 1]

    return (
        f"📅 {weekday}، {jd} {jalali_month} {jy}\n"
        f"🗓 میلادی: {now.strftime('%Y-%m-%d')}\n"
        f"🕒 ساعت: {now.strftime('%H:%M:%S')}"
    )

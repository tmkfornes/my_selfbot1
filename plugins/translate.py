"""`.ترجمه` — translates a replied-to message, or text given directly,
into the requested language. Uses deep-translator's free Google Translate
wrapper (no API key needed).

GoogleTranslator is imported lazily (inside cmd_translate) so the ~5 MB
requests/deep-translator dependency tree doesn't load at startup if
translation is never used.
"""

import asyncio

# Common Persian/English names -> ISO language codes. Anything else typed
# is tried as a raw code (e.g. "de", "ja") so this isn't a hard whitelist.
LANG_MAP = {
    "فارسی": "fa", "persian": "fa", "farsi": "fa",
    "انگلیسی": "en", "english": "en",
    "عربی": "ar", "arabic": "ar",
    "روسی": "ru", "russian": "ru", "روسیه": "ru",
    "ترکی": "tr", "turkish": "tr", "ترکیه": "tr",
    "فرانسوی": "fr", "french": "fr", "فرانسه": "fr",
    "آلمانی": "de", "german": "de", "آلمان": "de",
    "اسپانیایی": "es", "spanish": "es", "اسپانیا": "es",
    "چینی": "zh-CN", "chinese": "zh-CN", "چین": "zh-CN",
    "ژاپنی": "ja", "japanese": "ja", "ژاپن": "ja",
    "ایتالیایی": "it", "italian": "it", "ایتالیا": "it",
    "پرتغالی": "pt", "portuguese": "pt",
    "کره‌ای": "ko", "korean": "ko", "کره": "ko",
    "هندی": "hi", "hindi": "hi",
}


async def cmd_translate(event, lang_word: str, text: str = ""):
    from config import PREFIX

    lang_word = (lang_word or "").strip().lower()
    if not lang_word:
        await event.edit(
            f"استفاده: `{PREFIX}ترجمه <زبان مقصد>` (روی یه پیام متنی ریپلای کن) "
            f"یا `{PREFIX}ترجمه <زبان مقصد> <متن>`\n"
            f"مثال: `{PREFIX}ترجمه انگلیسی` یا `{PREFIX}ترجمه فرانسوی سلام چطوری`"
        )
        return

    target = LANG_MAP.get(lang_word, lang_word if 2 <= len(lang_word) <= 5 else None)
    if target is None:
        known = ", ".join(sorted({k for k in LANG_MAP if not k.isascii()}))
        await event.edit(f"⚠️ زبان «{lang_word}» رو نشناختم. یکی از این‌ها رو امتحان کن: {known}")
        return

    text = (text or "").strip()
    if not text:
        reply = await event.get_reply_message()
        if reply is None or not (reply.raw_text or "").strip():
            await event.edit("⚠️ یا رو یه پیام متنی ریپلای کن، یا متن رو خودت بعد از زبان بنویس.")
            return
        text = reply.raw_text.strip()

    await event.edit("🌐 …")
    try:
        from deep_translator import GoogleTranslator  # lazy
        translated = await asyncio.to_thread(
            lambda: GoogleTranslator(source="auto", target=target).translate(text)
        )
    except Exception as e:
        await event.edit(f"⚠️ ترجمه انجام نشد: {e}")
        return

    if not translated:
        await event.edit("⚠️ ترجمه‌ای برنگشت.")
        return

    await event.edit(f"🌐 {translated}"[:4090])

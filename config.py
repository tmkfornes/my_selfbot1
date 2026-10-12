import os
from dotenv import load_dotenv

load_dotenv()


def _get(name, default=None, required=False, cast=str):
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    if value is None:
        return None
    return cast(value)


# Telegram user-account (Telethon) credentials
API_ID = _get("API_ID", required=True, cast=int)
API_HASH = _get("API_HASH", required=True)
SESSION_NAME = _get("SESSION_NAME", default="my_session")
# When running somewhere without persistent disk (e.g. Render free tier),
# set SESSION_STRING instead (generate it once with generate_session.py).
SESSION_STRING = _get("SESSION_STRING", default="")
PREFIX = _get("PREFIX", default=".")

# Control bot (python-telegram-bot) token
BOT_TOKEN = _get("BOT_TOKEN", required=True)

# Clock update interval in seconds. Telegram rate-limits how often a profile
# name can change, so don't set this too low (60s is a safe default).
CLOCK_INTERVAL_SECONDS = _get("CLOCK_INTERVAL_SECONDS", default=60, cast=int)

# Timezone used for the clock in the display name
CLOCK_TIMEZONE = _get("CLOCK_TIMEZONE", default="Asia/Tehran")

# .ai command — powered by OpenRouter, chosen so the model/key/base URL
# are all just config, never code. Free tier: https://openrouter.ai/keys.
# Leave all 4 blank to disable .ai. Up to 4 keys can be set — when one hits
# its daily cap, ai_agent.py automatically rotates to the next one.
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY", default="")
OPENROUTER_API_KEY_2 = _get("OPENROUTER_API_KEY_2", default="")
OPENROUTER_API_KEY_3 = _get("OPENROUTER_API_KEY_3", default="")
OPENROUTER_API_KEY_4 = _get("OPENROUTER_API_KEY_4", default="")
OPENROUTER_MODEL = _get("OPENROUTER_MODEL", default="qwen/qwen3-4b:free")
OPENROUTER_BASE_URL = _get("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
OPENROUTER_TIMEOUT = _get("OPENROUTER_TIMEOUT", default=30, cast=int)
# Comma-separated fallback models, tried in order if OPENROUTER_MODEL's
# requests keep failing. Empty = no fallback.
OPENROUTER_FALLBACK_MODELS = [
    m.strip() for m in _get("OPENROUTER_FALLBACK_MODELS", default="").split(",") if m.strip()
]
# Client-side rate limit on OpenRouter calls. 0 = unlimited.
OPENROUTER_MAX_CALLS_PER_MINUTE = _get("OPENROUTER_MAX_CALLS_PER_MINUTE", default=0, cast=int)

# Comma-separated bot usernames OR numeric user IDs that antidelete should
# ignore entirely (neither cache nor report deleted/edited messages).
# Supports both formats, mixed freely:
#   ANTIDELETE_EXCLUDE_BOTS=mycontrolbot,123456789,anotherbot
ANTIDELETE_EXCLUDE_BOTS: set = {
    u.strip() for u in _get("ANTIDELETE_EXCLUDE_BOTS", default="").split(",") if u.strip()
}

# Port Render assigns for the health-check web server (irrelevant when
# running locally).
PORT = _get("PORT", default=8080, cast=int)


def get_proxy():
    """No proxy needed when running on cloud servers like Railway."""
    return None
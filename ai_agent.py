"""
AI layer for the `.ai` command only — plain conversational chat via
OpenRouter. Configurable API key(s) / model / base URL / timeout, so
switching models is a config change, never a code change.

Agent Mode (natural-language -> command routing) has been removed
entirely; this file no longer does any JSON classification.
"""

import asyncio
import datetime
import time

import httpx

from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_API_KEY_2,
    OPENROUTER_API_KEY_3,
    OPENROUTER_API_KEY_4,
    OPENROUTER_MODEL,
    OPENROUTER_BASE_URL,
    OPENROUTER_TIMEOUT,
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_MAX_CALLS_PER_MINUTE,
)
from logger import log

MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.0

# Up to 4 keys, in the order given in .env. When a key's daily cap gets
# hit, it's parked until the next UTC reset and calls automatically move
# on to the next configured key — no restart, no code change.
_api_keys = [
    k for k in [OPENROUTER_API_KEY, OPENROUTER_API_KEY_2, OPENROUTER_API_KEY_3, OPENROUTER_API_KEY_4]
    if k
]
_current_key_index = 0
_exhausted_until = {}  # api_key -> datetime.datetime it's safe to retry again


def has_api_key() -> bool:
    return bool(_api_keys)


def _mask(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 4 else "...."


def _next_utc_reset() -> datetime.datetime:
    """OpenRouter's free-tier daily cap resets at UTC midnight — this is a
    reasonable park time even when the exact reset isn't in the error
    body; worst case a key sits idle a bit longer than strictly needed,
    which is harmless since the other keys keep covering requests."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _mark_key_exhausted(key: str):
    _exhausted_until[key] = _next_utc_reset()
    log.warn(f"ai_agent: key {_mask(key)} looks daily-capped, parking it until UTC midnight")


def _keys_to_try() -> list:
    """All configured keys, ordered starting from _current_key_index so
    load spreads across keys instead of always hammering the first one —
    with any currently-parked (daily-capped) keys pushed to the back
    instead of skipped outright, so if every key is capped we still try
    something rather than failing immediately."""
    if not _api_keys:
        return []
    now = datetime.datetime.now(datetime.timezone.utc)
    start = _current_key_index % len(_api_keys)
    ordered = _api_keys[start:] + _api_keys[:start]
    fresh = [k for k in ordered if _exhausted_until.get(k, now) <= now]
    parked = [k for k in ordered if _exhausted_until.get(k, now) > now]
    return fresh + parked


class _RateLimiter:
    """Simple client-side sliding-window limiter — protects the account's
    own OpenRouter quota/budget from a runaway loop. Independent of
    OpenRouter's own 429 handling, which _call_one_model already retries
    with backoff; this caps calls BEFORE they're sent at all. 0 = unlimited."""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._timestamps = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        if self.max_per_minute <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            self._timestamps = [t for t in self._timestamps if now - t < 60]
            if len(self._timestamps) >= self.max_per_minute:
                wait = 60 - (now - self._timestamps[0])
                if wait > 0:
                    log.warn(f"ai_agent: client-side rate limit hit, waiting {wait:.1f}s")
                    await asyncio.sleep(wait)
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < 60]
            self._timestamps.append(now)


_rate_limiter = _RateLimiter(OPENROUTER_MAX_CALLS_PER_MINUTE)

# Model switching: mutable at runtime via `.مدل`, starts at the configured default.
_active_model = OPENROUTER_MODEL


def get_model() -> str:
    return _active_model


def set_model(model: str):
    global _active_model
    _active_model = model.strip()
    log.ok(f"ai_agent: active model switched to {_active_model}")


def _candidate_models() -> list:
    """Primary model first, then configured fallbacks — a request only
    reaches model N+1 if model N exhausted its own retries."""
    return [_active_model] + [m for m in OPENROUTER_FALLBACK_MODELS if m != _active_model]


async def _call_one_model(model: str, messages, temperature: float, http: httpx.AsyncClient, api_key: str):
    """Runs MAX_RETRIES attempts against a single model with a single key.
    Raises on total failure so the caller (_call) can move on to the next
    fallback model — or, if every model failed with a 429, the next key."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await http.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": messages, "temperature": temperature},
            )
            resp.raise_for_status()
            body = resp.json()
            return body["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            last_exc = e
            if e.response.status_code in (429, 502, 503) and attempt < MAX_RETRIES - 1:
                log.warn(f"ai_agent: {model} ({_mask(api_key)}) -> {e.response.status_code}, retrying...")
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                continue
            break
        except httpx.TransportError as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                continue
            break
    raise last_exc


async def _call(messages, temperature=0.3):
    """Tries each configured key in turn (starting from the current
    rotation point); for each key, tries the active model then each
    fallback. A key is only abandoned for the *rest of this call* once
    every model on it has failed — and if any of those failures was a 429,
    the key gets parked (see _mark_key_exhausted) so future calls skip it
    until the daily reset. Only raises once every key x model combination
    has been exhausted."""
    global _current_key_index

    await _rate_limiter.acquire()
    keys = _keys_to_try()
    if not keys:
        raise RuntimeError("no OpenRouter API key configured")

    async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT) as http:
        last_exc = None
        for key in keys:
            key_hit_429 = False
            for model in _candidate_models():
                try:
                    result = await _call_one_model(model, messages, temperature, http, key)
                    # Success — next call starts from the key *after* this
                    # one, so load balances forward across all keys instead
                    # of always starting the search at key #1.
                    _current_key_index = (_api_keys.index(key) + 1) % len(_api_keys)
                    return result
                except httpx.HTTPStatusError as e:
                    last_exc = e
                    if e.response.status_code == 429:
                        key_hit_429 = True
                    log.warn(f"ai_agent: model {model} on {_mask(key)} exhausted retries ({e}), trying next")
                    continue
                except Exception as e:
                    last_exc = e
                    log.warn(f"ai_agent: model {model} on {_mask(key)} exhausted retries ({e}), trying next")
                    continue
            if key_hit_429:
                _mark_key_exhausted(key)
        raise last_exc


async def health_check() -> dict:
    """Cheap connectivity probe — one tiny request. Never raises; returns
    ok=False with the error instead, so a bad key/network never crashes."""
    if not has_api_key():
        return {"ok": False, "model": _active_model, "error": "no OPENROUTER_API_KEY* set"}
    start = time.monotonic()
    try:
        await _call([{"role": "user", "content": "ping"}], temperature=0)
        return {"ok": True, "model": _active_model, "latency_ms": round((time.monotonic() - start) * 1000, 1)}
    except Exception as e:
        return {"ok": False, "model": _active_model, "error": str(e)}


def format_error(e: Exception) -> str:
    """User-facing error text. For a 429, OpenRouter's response body usually
    says whether it's the per-minute or daily free-tier cap and sometimes
    when it resets — surface that instead of just 'Too Many Requests'."""
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 429:
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except Exception:
                detail = e.response.text[:200]
            extra = ""
            if len(_api_keys) > 1:
                extra = f"\n\nهمه‌ی {len(_api_keys)} تا کلید تنظیم‌شده امروز به سقف خوردن."
            return (
                "به سقف رایگان OpenRouter خوردیم (429). "
                + (detail or "جزئیات بیشتری تو پاسخ نبود.")
                + extra
                + "\n\nمعمولاً یا باید چند دقیقه/تا فردا صبر کنی، یا با شارژ حداقلی "
                "($۱۰) تو openrouter.ai سقف روزانه از ۵۰ به ۱۰۰۰ میره."
            )
        return f"OpenRouter خطای {e.response.status_code} داد: {e.response.text[:200]}"
    return str(e)


async def chat(question: str) -> str:
    """Plain answer for the `.ai <question>` command."""
    return await _call([{"role": "user", "content": question}])

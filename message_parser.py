"""
Message Parser layer.

Pure text -> structure. Knows nothing about Telegram events, plugins, or
the AI — just whether a string is a `PREFIX`-command and, if so, its
command word and remaining argument text.
"""

from dataclasses import dataclass

from config import PREFIX

# Maps common Arabic-script character variants to their standard Persian
# form, so e.g. ".آیدی" (with alef-madda) and ".ایدی" (plain alef) parse to
# the exact same command word. Only applied to the *command word* itself,
# never to `arg`/`body` — those carry real content (text to translate,
# names to search, etc.) that shouldn't be silently rewritten.
_CHAR_NORMALIZE_MAP = str.maketrans({
    "آ": "ا", "أ": "ا", "إ": "ا", "ٱ": "ا",
    "ي": "ی", "ى": "ی", "ئ": "ی",
    "ك": "ک",
    "ة": "ه",
    "ؤ": "و",
    "\u200c": "",  # ZWNJ (half-space) — drop it so "می‌شه"-style joins don't matter here
    "\u064b": "", "\u064c": "", "\u064d": "", "\u064e": "",
    "\u064f": "", "\u0650": "", "\u0651": "", "\u0652": "",  # Arabic diacritics
})


def normalize_command_word(word: str) -> str:
    """Canonicalizes a command word for matching purposes only."""
    return word.translate(_CHAR_NORMALIZE_MAP)


@dataclass
class ParsedCommand:
    cmd: str    # normalized + lowercased first word after the prefix
    arg: str    # lowercased remainder (empty string if none)
    body: str   # original-case text after the prefix, for handlers that need it verbatim


def parse(text: str) -> ParsedCommand | None:
    """Returns a ParsedCommand if `text` is a `PREFIX`-prefixed command with
    a non-empty body, else None (not a command at all, or an empty one)."""
    if text is None or not text.startswith(PREFIX):
        return None

    body = text[len(PREFIX):].strip()
    if not body:
        return None

    parts = body.split(maxsplit=1)
    cmd = normalize_command_word(parts[0]).lower()
    arg = parts[1].strip().lower() if len(parts) > 1 else ""
    return ParsedCommand(cmd=cmd, arg=arg, body=body)

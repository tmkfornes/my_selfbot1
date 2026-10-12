import json
import os
import string

STATE_FILE = "font_state.json"


def _range_map(upper_base=None, lower_base=None, digit_base=None, exceptions=None):
    """Builds a char->char mapping from a Unicode Mathematical Alphanumeric
    block. `exceptions` overrides/extends it for the handful of codepoints
    Unicode reserves (e.g. italic 'h', circled digits)."""
    mapping = {}
    if upper_base is not None:
        for i, ch in enumerate(string.ascii_uppercase):
            mapping[ch] = chr(upper_base + i)
    if lower_base is not None:
        for i, ch in enumerate(string.ascii_lowercase):
            mapping[ch] = chr(lower_base + i)
    if digit_base is not None:
        for i, ch in enumerate(string.digits):
            mapping[ch] = chr(digit_base + i)
    if exceptions:
        mapping.update(exceptions)
    return mapping


def _combining_map(combiner: str) -> dict:
    """Builds a char map where each letter/digit gets `combiner` appended —
    used for overlay effects like strikethrough (U+0336) and underline
    (U+0332) that can't be done with simple codepoint substitution."""
    m = {}
    for ch in string.ascii_letters + string.digits:
        m[ch] = ch + combiner
    return m


_circled_digits = {str(n): chr(0x2460 + n - 1) for n in range(1, 10)}
_circled_digits["0"] = chr(0x24EA)

_small_caps_map = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "t": "ᴛ", "u": "ᴜ", "v": "ᴠ",
    "w": "ᴡ", "y": "ʏ", "z": "ᴢ",
}
_small_caps_map.update({k.upper(): v for k, v in _small_caps_map.items()})


def _upper_only_map(base: int, digit_base=None) -> dict:
    """For Unicode blocks that only define uppercase glyphs (squared,
    negative-squared, negative-circled, regional-indicator letters) —
    lowercase input maps to the same all-caps-looking glyph, matching how
    every popular 'fancy text' generator handles these styles."""
    m = {}
    for i, ch in enumerate(string.ascii_uppercase):
        glyph = chr(base + i)
        m[ch] = glyph
        m[ch.lower()] = glyph
    if digit_base is not None:
        for i, ch in enumerate(string.digits):
            m[ch] = chr(digit_base + i)
    return m


# Superscript/subscript letters aren't a contiguous Unicode block — several
# letters (mostly consonants) simply have no standard superscript/subscript
# form and are left as-is below, same as other fancy-text generators do.
_superscript_map = {
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ",
    "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ",
    "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ", "v": "ᵛ",
    "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
    "A": "ᴬ", "B": "ᴮ", "D": "ᴰ", "E": "ᴱ", "G": "ᴳ", "H": "ᴴ", "I": "ᴵ",
    "J": "ᴶ", "K": "ᴷ", "L": "ᴸ", "M": "ᴹ", "N": "ᴺ", "O": "ᴼ", "P": "ᴾ",
    "R": "ᴿ", "T": "ᵀ", "U": "ᵁ", "V": "ⱽ", "W": "ᵂ",
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼",
}

_subscript_map = {
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "l": "ₗ",
    "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ",
    "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌",
}
# Uppercase falls back to the lowercase subscript glyph (no uppercase
# subscript letters exist in Unicode at all) — same convention every
# fancy-text generator uses for this style.
_subscript_map.update({k.upper(): v for k, v in _subscript_map.items() if k.isalpha()})

# key -> (display name shown in the panel, char map)
FONTS = {
    "bold":            ("𝗕𝗼𝗹𝗱",           _range_map(0x1D400, 0x1D41A, 0x1D7CE)),
    "italic":          ("𝘐𝘵𝘢𝘭𝘪𝘤",         _range_map(0x1D434, 0x1D44E, None, {"h": "\u210E"})),
    "bold_italic":     ("𝘽𝙤𝙡𝙙 𝙄𝙩𝙖𝙡𝙞𝙘",   _range_map(0x1D468, 0x1D482, None)),
    "double_struck":   ("𝔻𝕠𝕦𝕓𝕝𝕖 𝕊𝕥𝕣𝕦𝕔𝕜", _range_map(0x1D538, 0x1D552, 0x1D7D8, {
        "C": "\u2102", "H": "\u210D", "N": "\u2115", "P": "\u2119",
        "Q": "\u211A", "R": "\u211D", "Z": "\u2124",
    })),
    "monospace":       ("𝙼𝚘𝚗𝚘𝚜𝚙𝚊𝚌𝚎",     _range_map(0x1D670, 0x1D68A, 0x1D7F6)),
    "fraktur":         ("𝔉𝔯𝔞𝔨𝔱𝔲𝔯",       _range_map(0x1D504, 0x1D51E, None, {
        "C": "\u212D", "H": "\u210C", "I": "\u2111", "R": "\u211C", "Z": "\u2128",
    })),
    "bold_fraktur":    ("𝕭𝖔𝖑𝖉 𝕱𝖗𝖆𝖐𝖙𝖚𝖗", _range_map(0x1D56C, 0x1D586, None)),
    "circled":         ("Ⓒⓘⓡⓒⓛⓔⓓ",       _range_map(0x24B6, 0x24D0, None, _circled_digits)),
    "fullwidth":       ("Ｆｕｌｌｗｉｄｔｈ",   _range_map(0xFF21, 0xFF41, 0xFF10)),
    "sans":            ("𝖲𝖺𝗇𝗌",           _range_map(0x1D5A0, 0x1D5BA, 0x1D7E2)),
    "sans_bold":       ("𝗦𝗮𝗻𝘀 𝗕𝗼𝗹𝗱",     _range_map(0x1D5D4, 0x1D5EE, 0x1D7EC)),
    "sans_italic":     ("𝘚𝘢𝘯𝘴 𝘐𝘵𝘢𝘭𝘪𝘤",   _range_map(0x1D608, 0x1D622, None)),
    "sans_bold_italic":("𝙎𝙖𝙣𝙨 𝘽𝙤𝙡𝙙 𝙄𝙩", _range_map(0x1D63C, 0x1D656, None)),
    "script":          ("𝒮𝒸𝓇𝒾𝓅𝓉",       _range_map(0x1D49C, 0x1D4B6, None, {
        "B": "\u212C", "E": "\u2130", "F": "\u2131", "H": "\u210B", "I": "\u2110",
        "L": "\u2112", "M": "\u2133", "R": "\u211B",
        "e": "\u212F", "g": "\u210A", "o": "\u2134",
    })),
    "bold_script":     ("𝓑𝓸𝓵𝓭 𝓢𝓬𝓻𝓲𝓹𝓽", _range_map(0x1D4D0, 0x1D4EA, None)),
    "small_caps":      ("Sᴍᴀʟʟ Cᴀᴘs",   dict(_small_caps_map)),
    "strikethrough":   ("S̶t̶r̶i̶k̶e̶",       _combining_map("\u0336")),
    "underline":       ("U̲n̲d̲e̲r̲l̲i̲n̲e̲",    _combining_map("\u0332")),
    "squared":         ("🅂🄰🄼🄿🄻🄴",       _upper_only_map(0x1F130)),
    "negative_squared":("🆂🅰🅼🅿🅻🅴",       _upper_only_map(0x1F170)),
    "negative_circled":("🅢🅐🅜🅟🅛🅔",       _upper_only_map(0x1F150)),
    "regional":        ("🇸🇦🇲🇵🇱🇪",         _upper_only_map(0x1F1E6)),
    "superscript":     ("ˢᵃᵐᵖˡᵉ",         dict(_superscript_map)),
    "subscript":       ("ₛₐₘₚₗₑ",         dict(_subscript_map)),
}

# _state tracks the active font key AND the last non-None key, so
# `.font off` + `.font on` round-trips cleanly without losing context.
_state = {"active": None, "last": None}


def _save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f)
    except Exception:
        pass


def _load_state():
    global _state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            _state = {"active": loaded.get("active"), "last": loaded.get("last")}
    except (FileNotFoundError, json.JSONDecodeError):
        _state = {"active": None, "last": None}


_load_state()


def set_font(key):
    """Set the active font. Pass None to turn off (last is preserved for restore)."""
    if key is not None:
        _state["last"] = key
    _state["active"] = key
    _save_state()


def restore_font() -> str | None:
    """Restore the last active font (used by `.font on`).
    Returns the restored key, or None if there was nothing to restore."""
    last = _state.get("last")
    if last and last in FONTS:
        _state["active"] = last
        _save_state()
        return last
    return None


def get_active():
    return _state.get("active")


def apply(text: str) -> str:
    key = _state.get("active")
    if not key or key not in FONTS:
        return text
    _, mapping = FONTS[key]
    return "".join(mapping.get(ch, ch) for ch in text)

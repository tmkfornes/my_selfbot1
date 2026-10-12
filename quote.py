import io
import os
import textwrap
from datetime import datetime

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_PATH = os.path.join(os.path.dirname(__file__), "font.ttf")

_font_cache = {}

# Telegram picks a name color per user from a fixed palette (based on user
# id) — reproducing that instead of one flat purple makes rendered bubbles
# look like real Telegram messages instead of a generic "quote card". The
# same color is reused for the avatar background when there's no photo.
NAME_COLOR_PALETTE = [
    (237, 112, 116),  # red
    (237, 168, 108),  # orange
    (166, 149, 231),  # purple
    (123, 200, 98),   # green
    (110, 201, 203),  # cyan
    (101, 170, 221),  # blue
    (238, 122, 174),  # pink
]

BUBBLE_FILL = (41, 44, 60, 255)      # Telegram dark-theme bubble color
BUBBLE_TEXT = (232, 232, 235, 255)   # near-white message text
BUBBLE_TIME = (150, 154, 165, 255)   # muted gray timestamp
TICK_COLOR = (117, 190, 240, 255)    # light blue "read" double-tick


def _shape(text: str) -> str:
    """PIL can't do Arabic/Persian letter-joining or right-to-left order on
    its own — this reshapes the text into its correct visual form first."""
    if not text:
        return ""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def _load_font(size: int):
    if size not in _font_cache:
        if os.path.exists(FONT_PATH):
            _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
        else:
            # Fallback so the bot doesn't crash if font.ttf is missing —
            # Persian text will look broken, but it won't error out.
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def _name_color(sender_id) -> tuple:
    if sender_id is None:
        return NAME_COLOR_PALETTE[0]
    return NAME_COLOR_PALETTE[sender_id % len(NAME_COLOR_PALETTE)]


def _initials(sender_name: str) -> str:
    words = (sender_name or "").split()
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[1][:1]).upper()


def _draw_double_tick(draw, x, y, size, color):
    """Two overlapping checkmarks — Telegram's 'read' indicator."""
    offset = size * 0.55
    for dx in (0, offset):
        draw.line(
            [
                (x + dx, y + size * 0.5),
                (x + dx + size * 0.35, y + size * 0.85),
                (x + dx + size, y),
            ],
            fill=color,
            width=max(2, round(size / 7)),
            joint="curve",
        )


STATIC_STICKER_MAX_BYTES = 512 * 1024  # Telegram's real cap for static WEBP stickers


def _encode_webp_max_quality(img: Image.Image) -> bytes:
    """Always tries lossless first (best possible quality) and only steps
    down if the result would be too big for Telegram to accept as a
    sticker — never compresses more than the size limit actually forces."""
    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True, quality=100, method=6)
    if buf.tell() <= STATIC_STICKER_MAX_BYTES:
        return buf.getvalue()

    for q in (95, 90, 85, 80, 70, 60):
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=q, method=6)
        if buf.tell() <= STATIC_STICKER_MAX_BYTES:
            return buf.getvalue()

    return buf.getvalue()  # last resort: whatever we got at the lowest quality tried


def build_quote_image(
    sender_name: str,
    text: str,
    avatar_bytes: bytes | None,
    sender_id=None,
    sent_at: datetime | None = None,
) -> bytes:
    """Renders a Telegram-style message bubble matching the real dark-theme
    look: avatar circle OUTSIDE the bubble on the left (colored initials
    when there's no photo), a dark rounded bubble with one sharp "tail"
    corner, the sender's name in Telegram's per-user palette color, wrapped
    message text, and a timestamp with a double read-tick. This is a
    from-scratch re-render, not a screenshot — quality only depends on the
    source image/font, not on however compressed a screenshot would be."""
    max_bubble_width = 420
    padding_x = 22
    padding_top = 16
    avatar_size = 60
    avatar_gap = 12
    line_height = 34
    name_font = _load_font(26)
    text_font = _load_font(26)
    time_font = _load_font(19)
    initials_font = _load_font(24)
    corner_radius = 28
    tail_corner_radius = 6  # the "pointer" corner — sharper, like a real bubble tail

    text = text or "(بدون متن)"
    wrapped = textwrap.wrap(text, width=28) or [""]
    wrapped = wrapped[:12]  # keep the sticker from growing unreasonably tall

    name_shaped = _shape(sender_name)
    shaped_lines = [_shape(line) for line in wrapped]
    name_color = _name_color(sender_id)

    # Measure content to size the bubble around it instead of always using
    # a fixed width — short messages get a smaller, tighter bubble.
    measure_img = Image.new("RGBA", (10, 10))
    measure_draw = ImageDraw.Draw(measure_img)
    content_width = measure_draw.textlength(name_shaped, font=name_font)
    for line in shaped_lines:
        content_width = max(content_width, measure_draw.textlength(line, font=text_font))
    bubble_width = int(min(max_bubble_width, max(220, content_width + padding_x * 2)))

    text_block_height = line_height * len(shaped_lines)
    name_row_height = 34
    footer_height = 30
    bubble_height = padding_top + name_row_height + text_block_height + footer_height
    bubble_height = int(min(512, max(120, bubble_height)))

    canvas_width = avatar_size + avatar_gap + bubble_width
    canvas_height = max(avatar_size, bubble_height)
    img = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # --- Avatar: outside the bubble, on the left, vertically centered ---
    avatar_x = 0
    avatar_y = (canvas_height - avatar_size) // 2
    if avatar_bytes:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
        avatar = ImageOps.fit(avatar, (avatar_size, avatar_size))
        avatar_mask = Image.new("L", (avatar_size, avatar_size), 0)
        ImageDraw.Draw(avatar_mask).ellipse([0, 0, avatar_size, avatar_size], fill=255)
        img.paste(avatar, (avatar_x, avatar_y), avatar_mask)
    else:
        draw.ellipse(
            [avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size],
            fill=name_color + (255,),
        )
        initials = _shape(_initials(sender_name))
        iw = draw.textlength(initials, font=initials_font)
        draw.text(
            (avatar_x + avatar_size / 2 - iw / 2, avatar_y + avatar_size / 2 - 15),
            initials, font=initials_font, fill=(255, 255, 255, 255),
        )

    # --- Bubble: rounded on 3 corners, sharp on the bottom-left (tail,
    # pointing toward the avatar) ---
    bubble_x0 = avatar_size + avatar_gap
    bubble_y0 = 0
    bubble_x1 = canvas_width - 1
    bubble_y1 = bubble_height - 1

    bubble_mask = Image.new("L", img.size, 0)
    bd = ImageDraw.Draw(bubble_mask)
    bd.rounded_rectangle([bubble_x0, bubble_y0, bubble_x1, bubble_y1], radius=corner_radius, fill=255)
    bd.rectangle(
        [bubble_x0, bubble_y1 - corner_radius, bubble_x0 + corner_radius, bubble_y1],
        fill=0,
    )
    bd.rounded_rectangle(
        [bubble_x0, bubble_y1 - corner_radius * 2, bubble_x0 + corner_radius * 2, bubble_y1],
        radius=tail_corner_radius,
        fill=255,
    )
    img.paste(Image.new("RGBA", img.size, BUBBLE_FILL), (0, 0), bubble_mask)

    # --- Sender name, top of the bubble, in the per-user palette color ---
    name_x = bubble_x1 - padding_x - draw.textlength(name_shaped, font=name_font)
    name_y = padding_top
    draw.text((name_x, name_y), name_shaped, font=name_font, fill=name_color + (255,))

    # --- Message text, right-aligned, wrapped ---
    text_y = padding_top + name_row_height
    for line in shaped_lines:
        line_width = draw.textlength(line, font=text_font)
        draw.text((bubble_x1 - padding_x - line_width, text_y), line, font=text_font, fill=BUBBLE_TEXT)
        text_y += line_height

    # --- Timestamp + double read-tick, bottom-right of the bubble ---
    time_label = sent_at.strftime("%H:%M") if sent_at else ""
    tick_size = 16
    tick_width = tick_size * 1.55
    footer_y = bubble_y1 - 26
    cursor_x = bubble_x1 - padding_x
    if time_label:
        _draw_double_tick(draw, cursor_x - tick_width, footer_y + 2, tick_size, TICK_COLOR)
        cursor_x -= tick_width + 8
        time_width = draw.textlength(time_label, font=time_font)
        draw.text((cursor_x - time_width, footer_y), time_label, font=time_font, fill=BUBBLE_TIME)

    return _encode_webp_max_quality(img)


def image_to_sticker(image_bytes: bytes) -> bytes:
    """Resize an arbitrary image down to Telegram's sticker size limit
    (longest side 512px) and re-encode as WEBP. Used for turning a replied
    photo — or a video's thumbnail frame — into a static sticker."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    max_dim = 512
    w, h = img.size
    scale = max_dim / max(w, h)
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    img = img.resize(new_size, Image.LANCZOS)

    return _encode_webp_max_quality(img)

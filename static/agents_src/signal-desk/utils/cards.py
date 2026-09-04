# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

"""
Drawing a signal.

The reason this agent exists. A webhook carrying a line of text gets skimmed
past; a card gets looked at, and gets forwarded into other rooms by the people
who see it. That difference is the whole product.

Everything is drawn with Pillow and the fonts the system already has, so there
is nothing to install beyond one library and nothing to fetch at runtime. If
the fonts cannot be found it falls back to the default face and still produces
a card rather than failing the post.
"""

import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

W = 1600
# Height is worked out from what the card actually says. A fixed frame left a
# third of every short card empty, which reads as something failed to load.
MIN_H, MAX_H = 520, 1100

INK = (8, 12, 18)
PANEL = (14, 22, 32)
WHITE = (240, 247, 252)
BODY = (198, 214, 226)
DIM = (110, 130, 146)

# Where a card's accent comes from, so good news and bad news do not look the
# same at a glance in a fast moving chat.
TONES = {
    "good": (110, 231, 183),
    "watch": (251, 191, 36),
    "bad": (248, 113, 113),
    "neutral": (34, 211, 238),
}

# Faces that exist on most machines, tried in order. A missing font must not
# stop a post going out.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]
MONO_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "C:\\Windows\\Fonts\\consola.ttf",
]


def _font(size, mono=False):
    for path in (MONO_CANDIDATES if mono else FONT_CANDIDATES):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    words, lines, line = str(text).split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def render_card(signal, brand, logger) -> bytes:
    """
    One signal as a PNG, returned as bytes.

    Never raises. A card that cannot be drawn returns None and the post goes
    out as text instead, because a missing picture is better than a missing
    alert.
    """
    try:
        return _draw(signal, brand or {})
    except Exception as error:
        logger.warning("Could not draw a card: %s", error, exc_info=True)
        return None


def _measure(signal):
    """How tall this card needs to be before anything is drawn."""
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    title_lines = min(2, len(_wrap(probe, signal.get("title", "Signal"),
                                   _font(66), W - 260)))
    body_lines = min(6, len(signal.get("lines", [])))

    height = 168 + title_lines * 78 + 14 + body_lines * 48
    if signal.get("facts"):
        height += 30 + 90
    height += 90 if signal.get("mint") else 30
    height += 130          # the footer row and the panel's bottom margin
    return max(MIN_H, min(MAX_H, height))


def _draw(signal, brand):
    accent = TONES.get(signal.get("tone", "neutral"), TONES["neutral"])
    H = _measure(signal)
    image = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(image)

    # A faint grid, so the card has a surface rather than being a flat block.
    # Drawn in a colour rather than with an alpha: this canvas is RGB, so an
    # alpha here is ignored and the lines come out solid white.
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=(16, 24, 33), width=1)
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=(16, 24, 33), width=1)

    d.rounded_rectangle([60, 40, W - 60, H - 40], radius=28, fill=PANEL,
                        outline=(38, 54, 68), width=2)

    # The accent bar carries the tone, so the shape of the news is readable
    # before a word of it is.
    d.rounded_rectangle([60, 100, 70, H - 100], radius=5, fill=accent)

    kind = str(signal.get("kind", "signal")).upper()
    d.text((110, 118), kind, font=_font(26, mono=True), fill=accent, anchor="lt")

    title_font = _font(66)
    y = 168
    for line in _wrap(d, signal.get("title", "Signal"), title_font, W - 260)[:2]:
        d.text((108, y), line, font=title_font, fill=WHITE, anchor="lt")
        y += 78

    y += 14
    body_font = _font(34)
    for line in signal.get("lines", [])[:6]:
        d.text((110, y), str(line), font=body_font, fill=BODY, anchor="lt")
        y += 48

    # Facts as a row of small stacked pairs, which reads faster than a
    # paragraph containing the same numbers. Placed under whatever came before
    # rather than pinned to the bottom, which left a hole down the middle of
    # every card that had a short body.
    facts = list((signal.get("facts") or {}).items())[:4]
    if facts:
        x = 110
        # Straight under whatever came before. This was clamped against the
        # bottom of the card, which on a short one put the facts back above the
        # body text and printed the two on top of each other. The height is
        # measured with the facts included, so there is always room.
        fy = y + 30
        for label, value in facts:
            d.text((x, fy), str(label).upper(), font=_font(24, mono=True),
                   fill=DIM, anchor="lt")
            d.text((x, fy + 34), str(value), font=_font(44), fill=WHITE,
                   anchor="lt")
            x += 340

    # The mint, if there is one, because that is what somebody copies out of a
    # screenshot.
    if signal.get("mint"):
        d.text((110, H - 130), str(signal["mint"]), font=_font(26, mono=True),
               fill=DIM, anchor="lt")

    # Whose desk this is. Theirs first, ours small, since they are the one
    # posting it.
    name = brand.get("name") or "Signal Desk"
    d.text((110, H - 88), name, font=_font(30), fill=BODY, anchor="lt")
    d.text((W - 108, H - 84), "built on Aetheron", font=_font(26),
           fill=DIM, anchor="rt")

    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()

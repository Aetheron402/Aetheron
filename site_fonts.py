"""
Typography for the generated pages.

Every page was set in Helvetica, Georgia or Arial Black, because the file has to
work with no network and a font import would break that. System stacks are the
single loudest signal that a page was generated rather than made, so they get
real faces instead, embedded in the file as base64 so nothing is fetched.

The model never sees the font data. It is told which family names to use for its
direction and writes ordinary CSS against them, and the face is spliced into the
head afterwards. Asking a model to emit twenty kilobytes of base64 would produce
twenty kilobytes of plausible nonsense.

Two faces per page, a display and a body, chosen to match the direction. Latin
subsets, which is what an English landing page needs and why each is twenty
kilobytes rather than two hundred.
"""

import base64
import logging
import os
import re

logger = logging.getLogger(__name__)

FONT_DIR = os.path.join("static", "fonts")

# family name -> file. The name is what the model writes in its CSS.
FILES = {
    "Archivo Black": "archivo-black.woff2",
    "Space Grotesk": "space-grotesk.woff2",
    "Fraunces": "fraunces.woff2",
    "JetBrains Mono": "jetbrains-mono.woff2",
    "Cormorant Garamond": "cormorant-garamond.woff2",
    "Inter": "inter.woff2",
    "Bricolage Grotesque": "bricolage-grotesque.woff2",
}

# What each direction is set in. Paired so the two faces disagree enough to
# create a hierarchy on their own, which is most of what makes a page look
# designed rather than typed.
PAIRS = {
    "brutalist": ("Archivo Black", "Inter"),
    "neon night": ("Space Grotesk", "Inter"),
    "clean editorial": ("Fraunces", "Inter"),
    "retro terminal": ("JetBrains Mono", "JetBrains Mono"),
    "soft gradient": ("Bricolage Grotesque", "Inter"),
    "cyber grid": ("Space Grotesk", "JetBrains Mono"),
    "playful sticker": ("Bricolage Grotesque", "Inter"),
    "luxury minimal": ("Cormorant Garamond", "Inter"),
}

DEFAULT = ("Inter", "Inter")

# The stack each family falls back to. A face that fails to decode, or a browser
# too old for woff2, has to land somewhere deliberate rather than on Times.
FALLBACKS = {
    "Archivo Black": '"Helvetica Neue", Helvetica, Arial, sans-serif',
    "Space Grotesk": '"Helvetica Neue", Helvetica, Arial, sans-serif',
    "Bricolage Grotesque": '"Helvetica Neue", Helvetica, Arial, sans-serif',
    "Inter": '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    "Fraunces": 'Georgia, "Times New Roman", serif',
    "Cormorant Garamond": 'Georgia, "Times New Roman", serif',
    "JetBrains Mono": '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
}

_cache: dict[str, str] = {}


def pair_for(direction: str) -> tuple:
    """The display and body faces for a direction."""
    return PAIRS.get((direction or "").strip().lower(), DEFAULT)


def stack(family: str) -> str:
    """The full font-family value, face first and a real fallback after it."""
    return f"'{family}', {FALLBACKS.get(family, 'sans-serif')}"


def _encoded(family: str) -> str | None:
    """The face as base64, read once and kept."""
    if family in _cache:
        return _cache[family]

    name = FILES.get(family)
    if not name:
        return None

    path = os.path.join(FONT_DIR, name)
    try:
        with open(path, "rb") as handle:
            _cache[family] = base64.b64encode(handle.read()).decode("ascii")
    except OSError:
        # A missing face is not worth failing a paid build over. The page falls
        # back to its stack and still looks deliberate.
        logger.warning("Font %s missing at %s", family, path)
        return None
    return _cache[family]


def css_for(direction: str) -> str:
    """The @font-face rules for a direction, with the faces embedded."""
    display, body = pair_for(direction)

    rules = []
    for family in dict.fromkeys((display, body)):
        data = _encoded(family)
        if not data:
            continue
        rules.append(
            "@font-face{"
            f"font-family:'{family}';"
            "font-style:normal;font-weight:400 900;font-display:swap;"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');"
            "}"
        )

    return "\n".join(rules)


def inject(html: str, direction: str) -> str:
    """
    Put the faces into a finished page.

    First thing in the head, so the rest of the stylesheet can use them. If
    there is no head to put them in, the page is returned untouched rather than
    guessed at: a page that does not parse is not one to start editing.
    """
    css = css_for(direction)
    if not css or not html:
        return html

    block = f"<style>\n{css}\n</style>"

    match = re.search(r"<head[^>]*>", html, re.I)
    if match:
        at = match.end()
        return html[:at] + "\n" + block + html[at:]

    logger.warning("No head to embed fonts into")
    return html


def brief_for(direction: str) -> str:
    """
    What to tell the model about the faces it has been given.

    Names and stacks only. The data is spliced in afterwards, and a model asked
    to produce base64 produces something that looks like base64.
    """
    display, body = pair_for(direction)
    return (
        f"Set headings in {stack(display)} and body text in {stack(body)}. "
        "Write those font-family values exactly as given, including the "
        "fallbacks. The faces themselves are embedded into the page after you "
        "write it, so do not add a font import, a link tag, or an @font-face "
        "rule of your own, and do not use any other family."
    )

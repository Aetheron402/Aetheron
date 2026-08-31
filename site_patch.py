"""
Applying a change to a page without rewriting the page.

A revision used to hand the whole document back, so changing one word in a
headline produced thirteen thousand characters of output. Output is the
expensive half of a model call, and almost all of it was the file being copied
out unchanged, which is both slow and paid for by somebody who asked for one
word.

So a revision now comes back as a short list of replacements: find this exact
text, put that in its place. A headline change is a hundred characters instead
of thirteen thousand.

The risk is obvious. A replacement that does not match, or matches in two
places, would either do nothing or corrupt the page. So every patch is checked
against the document before any of it is applied, and if a single one fails the
whole set is rejected and the caller falls back to a full rewrite. A partly
applied patch is the one outcome worse than an expensive one.
"""

import json
import re

# A patch that rewrites almost the whole file is not a patch, and usually means
# the model gave up and pasted the document into a replacement. Better to spend
# the tokens on an honest rewrite than to apply something this large blind.
MAX_PATCH_SHARE = 0.6


class PatchError(Exception):
    """The patch could not be trusted, so it was not applied."""


def parse(raw: str) -> list:
    """
    Read a patch out of whatever the model wrapped it in.

    Fences and a sentence of preamble are common enough to be worth stripping
    rather than failing on, since the alternative is a full rewrite that costs
    forty times as much.
    """
    text = (raw or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text).strip()

    # A bare array somewhere in the reply, if it did not come back clean.
    if not text.startswith("["):
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise PatchError("no patch found in the reply")
        text = text[start:end + 1]

    try:
        edits = json.loads(text)
    except ValueError as exc:
        raise PatchError(f"the patch was not readable json: {exc}")

    if not isinstance(edits, list) or not edits:
        raise PatchError("the patch was empty")

    # The model's own way of saying a patch will not do, which the brief asks
    # for rather than leaving it to produce something unusable.
    if len(edits) == 1 and isinstance(edits[0], dict) \
            and edits[0].get("find") == "IMPOSSIBLE":
        raise PatchError("the change needs the page rewritten")

    cleaned = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise PatchError("a patch entry was not an object")
        find = edit.get("find")
        replace = edit.get("replace")
        if not isinstance(find, str) or not find:
            raise PatchError("a patch entry had nothing to find")
        if not isinstance(replace, str):
            raise PatchError("a patch entry had nothing to put in its place")
        cleaned.append({"find": find, "replace": replace})

    return cleaned


def apply(html: str, edits: list) -> str:
    """
    Apply every replacement, or none of them.

    Checked one at a time against the document as it stands, so an edit that
    depends on an earlier one still works, and an edit that has been made
    ambiguous by an earlier one is caught rather than guessed at.
    """
    if not html:
        raise PatchError("there is no page to patch")

    edits = edits or []
    if not edits:
        raise PatchError("nothing to apply")

    total_replaced = sum(len(e["find"]) for e in edits)
    if total_replaced > len(html) * MAX_PATCH_SHARE:
        raise PatchError(
            "the patch rewrites most of the page, which is a rewrite pretending "
            "to be a patch")

    working = html
    for index, edit in enumerate(edits, start=1):
        find, replace = edit["find"], edit["replace"]
        count = working.count(find)

        if count == 0:
            raise PatchError(
                f"change {index} looks for text that is not in the page")
        if count > 1:
            raise PatchError(
                f"change {index} looks for text that appears {count} times, so "
                "there is no telling which one was meant")

        working = working.replace(find, replace, 1)

    if working == html:
        raise PatchError("the patch changed nothing")

    lowered = working.lower()
    if "<html" not in lowered or "</html>" not in lowered:
        raise PatchError("the patched page is no longer a whole document")

    return working


# The one line every pre-launch page is built around. Matched loosely on
# spacing and quote style because the page is written by a model and the exact
# formatting is not guaranteed, but the shape of it is: the brief demands this
# declaration and nothing like it anywhere else.
CONTRACT_LINE = re.compile(
    r"""(const\s+CONTRACT_ADDRESS\s*=\s*)(['"])(.*?)\2""")


def set_contract_address(html: str, address: str) -> str:
    """
    Fill in the contract address, without a model call.

    This is a string replacement, so it is instant and free. Handing it to the
    model would mean paying for a generation and waiting a minute to change
    forty characters, and risking it touching something else on the way past.

    Raises when the line is not there, which happens on a page built after
    launch that never had a placeholder.
    """
    address = (address or "").strip()
    if not address:
        raise PatchError("No address to put in")

    if not html:
        raise PatchError("There is no page to change")

    found = CONTRACT_LINE.search(html)
    if not found:
        raise PatchError(
            "This page has no contract address line to fill in. It was built "
            "with the address already in it.")

    if found.group(3).strip() == address:
        raise PatchError("That address is already on this page")

    patched = CONTRACT_LINE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{address}{m.group(2)}", html, count=1)

    if patched == html:
        raise PatchError("The address could not be written in")
    return patched


def summarise(html: str, patched: str, edits: list) -> dict:
    """
    What the patch actually did, for the log.

    Worth recording because the number that matters, how much was written out
    rather than copied out, is the whole reason this exists.
    """
    return {
        "changes": len(edits),
        "characters_written": sum(len(e["replace"]) for e in edits),
        "page_characters": len(html),
        "size_change": len(patched) - len(html),
    }

"""
Getting back something already paid for.

A report bought in a chat arrives once, as a file, in a conversation that will
scroll. Telegram keeps it, but only until somebody clears a cache or changes
phone, and the thing they bought is then gone from the only place it existed for
them.

So this is not a convenience. It is the difference between selling a file and
selling access to a file, and the second one is what people think they are
buying. The ledger already knows every asset against every wallet, so the whole
feature is a lookup, a list, and a way to ask for one back.

Everything here keys off the linked wallet rather than the chat, which is what
makes it survive somebody moving to a new device: link the same wallet, and the
history is there.
"""

import logging

import tg_api
from tg_transport import TransportError

logger = logging.getLogger(__name__)

# How many to show at once. Long enough to be useful, short enough that the
# list is readable on a phone without becoming three messages.
PAGE_SIZE = 8


def _label(entry) -> str:
    component = entry.get("component") or "component"
    return tg_api.COMPONENTS.get(component, {}).get("label", component)


def list_assets(ctx, api):
    """
    Everything this wallet has bought that produced a file.

    Rows without a filename are left out rather than listed as unavailable. A
    pending or failed job is not something to offer back, and showing it here
    would put a broken entry next to the working ones with no way to tell which
    is which until it is asked for.
    """
    try:
        answer = api.my_assets(ctx.wallet)
    except tg_api.ApiError as exc:
        logger.warning("Could not read assets for %s: %s", ctx.wallet, exc)
        ctx.say("I could not read your history just now. Try again shortly.")
        return []

    entries = [e for e in (answer.get("entries") or []) if e.get("filename")]

    if not entries:
        ctx.say("Nothing bought on this wallet yet.\n\n"
                "/components lists what I can run, and /example shows you a "
                "real report for free first.")
        return []

    lines = ["What you have bought on this wallet:", ""]
    for index, entry in enumerate(entries[:PAGE_SIZE], start=1):
        price = entry.get("price")
        currency = entry.get("currency") or "USDC"
        cost = f"{price} {currency}" if price not in (None, "") else ""
        lines.append(f"{index}. {_label(entry)}{'  ' + cost if cost else ''}")

    lines += ["", f"Send /get followed by a number to have one sent again."]
    if len(entries) > PAGE_SIZE:
        lines.append(f"Showing the {PAGE_SIZE} most recent of {len(entries)}.")

    ctx.say("\n".join(lines))
    return entries[:PAGE_SIZE]


def send_asset(ctx, api, entries, choice):
    """
    Send one of them back.

    The number is an index into what was just listed rather than an id, because
    an id is a thing to copy wrongly and a number is a thing to read off the
    message above.
    """
    try:
        index = int(str(choice).strip())
    except (TypeError, ValueError):
        ctx.say("Send /get followed by the number from the list, for example "
                "/get 1.")
        return False

    if not entries:
        ctx.say("Send /assets first, then /get with a number from that list.")
        return False

    if not 1 <= index <= len(entries):
        ctx.say(f"There is no {index} on that list. It goes from 1 to "
                f"{len(entries)}.")
        return False

    entry = entries[index - 1]
    filename = entry.get("filename")

    try:
        data = api.download(f"/download/{filename}")
    except tg_api.ApiError as exc:
        logger.warning("Could not fetch %s: %s", filename, exc)
        # Assets are purged after a retention window, so an old enough one is
        # genuinely gone and saying so is better than a generic failure.
        ctx.say("I could not fetch that one. Files are kept for a while after "
                "they are made, so a very old report may no longer be stored.")
        return False

    try:
        ctx.send_file(data, filename, caption=_label(entry))
    except TransportError as exc:
        logger.warning("Could not deliver %s: %s", filename, exc)
        return False

    return True


def register(router, api, last_listed):
    """
    Add the history commands.

    `last_listed` maps a chat to what it was last shown, so /get can take a
    number off the message above it. Held by the caller for the same reason the
    preview queue is: this file stays handlers, the worker owns its state.
    """

    @router.command("assets", help="Files you have bought, to download again.",
                    requires_wallet=True, private_only=True,
                    aliases=("history", "files"))
    def _assets(ctx):
        last_listed[ctx.chat_id] = list_assets(ctx, api)

    @router.command("get", usage="<number>",
                    help="Send one of your files again.",
                    requires_wallet=True, private_only=True)
    def _get(ctx):
        if not ctx.args.strip():
            ctx.say("Send /get followed by the number from /assets.")
            return
        send_asset(ctx, api, last_listed.get(ctx.chat_id, []), ctx.args)

    return router

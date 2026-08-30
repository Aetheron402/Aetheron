"""
What the bot posts to a channel without being asked.

Three things: AETH burned, components being bought, and the service going down
or coming back. All three read from endpoints that already exist, so nothing
here computes anything. It formats, and it decides when there is something worth
saying.

The deciding is the harder half. A channel that posts every purchase becomes
noise nobody reads, and a channel that posts nothing is a dead channel that
makes the project look abandoned. So each of these keeps a mark of what it last
said and only speaks when that has moved.

Wallets are never posted in full. A purchases feed showing whole addresses turns
a channel into a list of who bought what, which is a thing people did not agree
to when they paid. The count is the point, not the buyer.
"""

import logging

logger = logging.getLogger(__name__)

SOLSCAN_TX = "https://solscan.io/tx/"
SOLSCAN_TOKEN = "https://solscan.io/token/"


def shorten(address, keep=4) -> str:
    """
    An address short enough to be recognisable and too short to be a record.

    Somebody can check their own against it. Nobody can build a list of buyers
    from a channel full of them.
    """
    address = (address or "").strip()
    if len(address) <= keep * 2 + 3:
        return address
    return f"{address[:keep]}...{address[-keep:]}"


# ── burns ───────────────────────────────────────────────────────────────────

def burn_post(burns, last_signature=None) -> str | None:
    """
    A post about AETH burned, or None when there is nothing new.

    Returns None rather than an empty post when the totals have not moved,
    because a weekly burn channel that says nothing happened every day is worse
    than one that only speaks when something did.
    """
    if not burns:
        return None

    recent = burns.get("recent") or []
    if not recent:
        return None

    newest = recent[0]
    signature = newest.get("signature") or newest.get("tx_signature")
    if signature and signature == last_signature:
        return None

    amount = newest.get("amount")
    total = burns.get("burned")
    outstanding = burns.get("outstanding")

    lines = ["AETH burned."]
    if amount is not None:
        lines.append(f"\nThis burn: {_number(amount)} AETH")
    if total is not None:
        lines.append(f"Burned in total: {_number(total)} AETH")
    if outstanding:
        lines.append(f"Collected and not yet burned: {_number(outstanding)} AETH")

    if signature:
        lines.append(f"\nCheck it: {SOLSCAN_TX}{signature}")

    lines.append("\nEvery AETH paid for a component is burned. Nothing is kept "
                 "back and nothing is minted.")
    return "\n".join(lines)


# ── the purchases feed ──────────────────────────────────────────────────────

def ledger_post(entries, last_id=None) -> tuple:
    """
    A short note about what has been bought since last time.

    Returns (text, newest_id), with text None when nothing new landed. Summed
    by component rather than listed one by one, because ten lines saying the
    same thing is noise and one line saying it ten times is information.
    """
    if not entries:
        return None, last_id

    fresh = [e for e in entries
             if e.get("status") == "success"
             and (last_id is None or (e.get("id") or 0) > last_id)]

    newest_id = max([e.get("id") or 0 for e in entries] + [last_id or 0])

    if not fresh:
        return None, newest_id

    counts = {}
    for entry in fresh:
        name = entry.get("component") or "component"
        counts[name] = counts.get(name, 0) + 1

    total = sum(counts.values())
    lines = [f"{total} component {'run' if total == 1 else 'runs'} paid for:", ""]
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"{count}x {name}")

    lines += ["", "Every one settled on chain before it ran."]
    return "\n".join(lines), newest_id


# ── status ──────────────────────────────────────────────────────────────────

def status_post(status, was_ok=True) -> str | None:
    """
    Say something only when the state changed.

    A channel posting all systems operational every ten minutes trains people
    to ignore it, which means they also ignore the one that matters. This
    speaks on the way down and on the way back up, and stays quiet in between.
    """
    if not status:
        return None

    now_ok = bool(status.get("ok"))

    if now_ok == was_ok:
        return None

    if not now_ok:
        broken = [name for name, service in (status.get("services") or {}).items()
                  if service.get("status") != "operational"]
        detail = ", ".join(broken) if broken else "something"
        return (f"Aetheron is having trouble with {detail}.\n\n"
                "Anything you paid for is recorded and will run. "
                "Live status: aetheronprotocol.com/status")

    return ("Aetheron is back to normal.\n\n"
            "Anything queued while it was down has been picked up.")


def _number(value) -> str:
    """Readable, and never dressed up as more precision than it has."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if value >= 1000:
        return f"{value:,.0f}"
    if value == int(value):
        return str(int(value))
    return f"{value:,.4f}".rstrip("0").rstrip(".")


class ChannelPoster:
    """
    Keeps the marks, so each feed only speaks when something moved.

    Held in memory on purpose. Everything here is a nice to have on top of a
    channel, and the worst a restart does is repeat one post or miss one, which
    is not worth a table and a write on every poll.
    """

    def __init__(self, transport, channel_id):
        self.transport = transport
        self.channel_id = channel_id
        self.last_burn_signature = None
        self.last_ledger_id = None
        self.was_ok = True

    def post_burns(self, burns) -> bool:
        text = burn_post(burns, self.last_burn_signature)
        if not text:
            return False
        recent = (burns.get("recent") or [{}])[0]
        self.last_burn_signature = (recent.get("signature")
                                    or recent.get("tx_signature"))
        return self._send(text)

    def post_ledger(self, entries) -> bool:
        text, newest = ledger_post(entries, self.last_ledger_id)
        self.last_ledger_id = newest
        if not text:
            return False
        return self._send(text)

    def post_status(self, status) -> bool:
        text = status_post(status, self.was_ok)
        self.was_ok = bool((status or {}).get("ok"))
        if not text:
            return False
        return self._send(text)

    def _send(self, text) -> bool:
        if not self.channel_id:
            return False
        try:
            self.transport.send_text(self.channel_id, text)
            return True
        except Exception:
            logger.warning("Could not post to channel %s", self.channel_id,
                           exc_info=True)
            return False

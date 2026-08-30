"""
What happens when somebody types something at the bot.

A registry, a dispatcher, and the three guards that sit in front of every
command: do not run the same update twice, do not let one chat hammer the API,
and do not run a paid flow in public.

The reason those are here rather than inside each command is that forgetting one
of them in one command is exactly how they get forgotten. A command declares
what it needs and the router enforces it, so adding a command later cannot
quietly skip a check.

The duplicate guard is the one that costs money if it is missing. Telegram
redelivers an update until its id is acknowledged, so a crash, a restart or a
slow reply can hand the same message back a second time. If that message was a
purchase confirmation, running it twice tries to spend twice.
"""

import logging
import time
from dataclasses import dataclass, field

import tg_link
from tg_transport import TransportError, Update

logger = logging.getLogger(__name__)

# A chat may run this many commands in this many seconds. Generous for a person
# and tight enough that a script cannot use the bot as a free pipe to the API,
# which would bill our inference rather than theirs.
RATE_LIMIT_COMMANDS = 20
RATE_LIMIT_WINDOW_SECONDS = 60

# How many update ids to remember. Telegram only redelivers what has not been
# acknowledged, so this only has to outlive a restart's worth of backlog.
SEEN_UPDATES_KEPT = 2000


@dataclass
class Context:
    """
    Everything a command is handed.

    `wallet` is resolved by the router rather than looked up per command, so a
    command cannot forget to check and end up quoting an unlinked chat a
    discounted price.
    """
    update: Update
    transport: object
    args: str = ""
    wallet: str | None = None

    @property
    def chat_id(self):
        return self.update.chat_id

    def say(self, text: str, **kwargs):
        return self.transport.send_text(self.chat_id, text, **kwargs)

    def send_file(self, data: bytes, filename: str, caption: str = ""):
        return self.transport.send_document(self.chat_id, data, filename, caption)


@dataclass
class Command:
    name: str
    handler: object
    help: str
    usage: str = ""
    requires_wallet: bool = False
    private_only: bool = False
    hidden: bool = False
    aliases: tuple = ()


class Router:
    """
    Holds the commands and runs the right one.

    Deliberately does not know what any command does. Everything it enforces is
    declared on the command itself, so the list of guards is visible in one
    place and a new command opts into them by saying what it is rather than by
    remembering to write them out.
    """

    def __init__(self, rate_limit=RATE_LIMIT_COMMANDS,
                 window=RATE_LIMIT_WINDOW_SECONDS):
        self.commands: dict[str, Command] = {}
        self._rate_limit = rate_limit
        self._window = window
        self._hits: dict[str, list] = {}
        self._seen: list = []
        self._seen_set: set = set()
        self._fallback = None

    # ── building the router ─────────────────────────────────────────────────

    def command(self, name, help="", usage="", requires_wallet=False,
                private_only=False, hidden=False, aliases=()):
        """Register a handler. Used as a decorator."""
        def register(fn):
            entry = Command(name=name, handler=fn, help=help, usage=usage,
                            requires_wallet=requires_wallet,
                            private_only=private_only, hidden=hidden,
                            aliases=tuple(aliases))
            self.commands[name] = entry
            for alias in aliases:
                self.commands[alias] = entry
            return fn
        return register

    def fallback(self, fn):
        """
        What runs for a message that is not a command.

        This is where a bare contract address gets picked up later, which is
        the thing most likely to be typed in a chat full of traders.
        """
        self._fallback = fn
        return fn

    # ── running one update ──────────────────────────────────────────────────

    def dispatch(self, update: Update, transport) -> bool:
        """
        Handle one update. Returns whether anything ran.

        Never raises. A handler that blows up must not take the bot down with
        it, because the next person's message is unrelated to whatever went
        wrong in this one.
        """
        if self._already_seen(update):
            logger.info("Ignoring repeat of update %s", update.update_id)
            return False

        name = update.command

        if name is None:
            if self._fallback is None:
                return False
            return self._run(self._fallback, update, transport, args=update.text)

        entry = self.commands.get(name)
        if entry is None:
            self._unknown(update, transport, name)
            return False

        # A purchase in a public chat shows everyone what somebody bought and
        # what they paid for it.
        if entry.private_only and update.is_group:
            self._reply(transport, update,
                        f"/{entry.name} only works in a direct message. "
                        "Open a chat with me and send it there.")
            return False

        if self._rate_limited(update):
            self._reply(transport, update,
                        "That is a lot of commands at once. Give it a minute "
                        "and try again.")
            return False

        wallet = None
        if entry.requires_wallet:
            wallet = tg_link.wallet_for(update.chat_id)
            if not wallet:
                self._reply(
                    transport, update,
                    "Link a wallet first, so I know what you own and what you "
                    "pay.\n\nSend /link followed by your wallet address.")
                return False

        return self._run(entry.handler, update, transport,
                         args=update.args, wallet=wallet)

    def run_batch(self, updates, transport) -> int:
        """Handle a batch, returning how many actually ran."""
        return sum(1 for u in updates if self.dispatch(u, transport))

    # ── help, generated rather than written ─────────────────────────────────

    def help_text(self, is_group: bool = False) -> str:
        """
        Built from the registry, so it cannot drift from what exists.

        A help list maintained by hand goes stale the first time a command is
        renamed, and then it is worse than none.
        """
        seen, lines = set(), []
        for entry in self.commands.values():
            if entry.hidden or id(entry) in seen:
                continue
            seen.add(id(entry))
            if is_group and entry.private_only:
                continue
            usage = f"/{entry.name} {entry.usage}".strip()
            lines.append(f"{usage}\n   {entry.help}")

        lines.sort()
        body = "\n\n".join(lines)

        if is_group:
            body += ("\n\nAnything involving payment or your own files works "
                     "in a direct message only.")
        return body

    # ── the guards ──────────────────────────────────────────────────────────

    def _already_seen(self, update) -> bool:
        """
        Whether this exact update ran before.

        Telegram redelivers anything unacknowledged, so a restart mid handling
        hands the same message back. Harmless for /help and expensive for a
        purchase confirmation.
        """
        key = update.update_id
        if key is None:
            return False
        if key in self._seen_set:
            return True

        self._seen.append(key)
        self._seen_set.add(key)
        if len(self._seen) > SEEN_UPDATES_KEPT:
            self._seen_set.discard(self._seen.pop(0))
        return False

    def _rate_limited(self, update) -> bool:
        """
        Whether this chat has had its allowance for now.

        Held in memory rather than the database on purpose. It is a courtesy
        limit against someone leaning on the keyboard or scripting the bot, and
        a restart clearing it costs nothing. Putting it in the ledger would add
        a write to every command to defend against something that does not
        matter that much.
        """
        now = time.time()
        key = str(update.chat_id)
        hits = [t for t in self._hits.get(key, []) if now - t < self._window]

        if len(hits) >= self._rate_limit:
            self._hits[key] = hits
            return True

        hits.append(now)
        self._hits[key] = hits
        return False

    def _unknown(self, update, transport, name):
        """
        Say what exists rather than only what does not.

        In a group this stays quiet: an unknown slash command is usually meant
        for a different bot, and answering every one of them is how a bot gets
        removed from a chat.
        """
        if update.is_group:
            return
        near = self._closest(name)
        text = f"I do not know /{name}."
        if near:
            text += f" Did you mean /{near}?"
        text += "\n\nSend /help to see everything I can do."
        self._reply(transport, update, text)

    def _closest(self, name: str) -> str | None:
        """A near miss, so a typo gets a useful answer rather than a list."""
        import difflib
        names = [c.name for c in self.commands.values() if not c.hidden]
        matches = difflib.get_close_matches(name, sorted(set(names)), n=1, cutoff=0.6)
        return matches[0] if matches else None

    def _run(self, handler, update, transport, args="", wallet=None) -> bool:
        """
        Run a handler and contain anything it throws.

        Two kinds of failure, told apart because they need different answers. A
        transport error means the person cannot be reached at all, so there is
        no point trying to tell them about it. Anything else is our bug, and
        they get told plainly without being shown the inside of it.
        """
        ctx = Context(update=update, transport=transport, args=args, wallet=wallet)
        try:
            handler(ctx)
            return True
        except TransportError:
            logger.warning("Could not deliver to chat %s", update.chat_id,
                           exc_info=True)
            return False
        except Exception:
            logger.exception("Handler for %r failed", update.command)
            self._reply(transport, update,
                        "Something went wrong on my side. Nothing was charged. "
                        "Try again in a moment.")
            return False

    def _reply(self, transport, update, text):
        """Answer, and do not care if the answer cannot be delivered."""
        try:
            transport.send_text(update.chat_id, text)
        except TransportError:
            logger.warning("Could not reach chat %s", update.chat_id)


def build_router() -> Router:
    """
    The router with the commands that need nothing else to exist yet.

    Kept here so each later step adds its own commands to a router that already
    works, rather than everything landing at once in one file.
    """
    router = Router()

    @router.command("help", help="What I can do.", aliases=("start",))
    def _help(ctx):
        ctx.say(
            "Aetheron runs AI components you pay for per call, in USDC or AETH. "
            "No signup and no subscription.\n\n"
            + router.help_text(is_group=ctx.update.is_group))

    @router.command("wallet", help="Show the wallet linked to this chat.",
                    private_only=True)
    def _wallet(ctx):
        wallet = tg_link.wallet_for(ctx.chat_id)
        if not wallet:
            ctx.say("No wallet linked here yet. Send /link followed by your "
                    "wallet address.")
            return
        ctx.say(f"This chat is linked to:\n{wallet}\n\n"
                "Send /unlink to forget it.")

    @router.command("unlink", help="Forget the wallet linked here.",
                    private_only=True)
    def _unlink(ctx):
        if tg_link.unlink(ctx.chat_id):
            ctx.say("Forgotten. This chat is no longer linked to any wallet.")
        else:
            ctx.say("There was no wallet linked here.")

    return router

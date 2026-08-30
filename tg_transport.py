"""
The seam between the bot's behaviour and Telegram itself.

Everything the bot does, linking a wallet, quoting a price, walking somebody
through a payment, handing back a file, is ordinary logic that happens to end in
a message. Only the last inch of that is Telegram's business. Putting that inch
behind an interface means the rest can be written and tested now, against a fake
that records what was sent, rather than waiting on a token and a network.

It also means the real client, when it arrives, is one file. Nothing else in the
bot imports the Telegram library or knows what its payloads look like.

Three things live here that look like details and are not:

- Telegram rejects a message over 4096 characters. A report or a ledger listing
  will pass that, and a command that simply sent one would fail at the moment it
  had something worth saying. Splitting belongs here, because it is a fact about
  the transport rather than anything a command should carry.
- Messages are sent as plain text unless formatting is asked for. Wallet
  addresses, file names and signatures are full of underscores, asterisks and
  dots, all of which Telegram's markdown treats as syntax, and one stray
  character makes it reject the whole message. Plain by default, escaped when
  not.
- Sending can fail for reasons the caller has to know about, a chat that blocked
  the bot, a file too large, a rate limit. Those come back as one exception with
  the reason on it rather than a silent False.
"""

import re
from dataclasses import dataclass, field

# Telegram's limits. Text is a hard cap; the caption on a file is much smaller,
# which is why a long explanation is sent as its own message rather than
# attached to the document.
MAX_TEXT = 4096
MAX_CAPTION = 1024

# The largest file a bot may send. Agent templates are a few hundred KB and
# reports smaller, so nothing today is close, but a caller deserves the real
# error rather than a truncated upload.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024

# Everything MarkdownV2 treats as syntax. A wallet address alone contains
# several of these.
_MARKDOWN_SPECIALS = r"_*[]()~`>#+-=|{}.!"
_MARKDOWN_RE = re.compile("([" + re.escape(_MARKDOWN_SPECIALS) + "])")


class TransportError(Exception):
    """A message could not be delivered, with a reason worth acting on."""


@dataclass(frozen=True)
class Update:
    """
    One incoming thing, in the shape the rest of the bot wants.

    Telegram's own payload is deeply nested, differs between a message and a
    button press, and gains fields over time. Normalising it here means a
    command handler reads `update.text` and `update.chat_id` and never learns
    what shape any of that arrived in.

    `is_group` matters more than it looks. A purchase flow in a public chat
    would show everyone what somebody bought and what they paid, so commands
    need to be able to refuse.
    """
    update_id: int
    chat_id: int
    user_id: int
    text: str = ""
    username: str | None = None
    is_group: bool = False
    message_id: int | None = None
    callback_data: str | None = None
    callback_id: str | None = None

    @property
    def command(self) -> str | None:
        """
        The command word, without its slash or the @botname suffix.

        Telegram appends @thebot to commands typed in groups, so a handler
        matching on the raw text would work in a direct message and quietly
        fail in exactly the place a group command is used.
        """
        text = (self.text or "").strip()
        if not text.startswith("/"):
            return None
        word = text.split(maxsplit=1)[0][1:]
        return word.split("@", 1)[0].lower() or None

    @property
    def args(self) -> str:
        """Everything after the command word, untouched."""
        text = (self.text or "").strip()
        if not text.startswith("/"):
            return ""
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""


@dataclass
class SentMessage:
    """A record of one thing the bot sent, for tests to read back."""
    chat_id: int
    text: str = ""
    document: bytes | None = None
    filename: str | None = None
    markdown: bool = False
    buttons: list | None = None
    reply_to: int | None = None


def escape_markdown(text: str) -> str:
    """
    Escape everything MarkdownV2 would otherwise read as formatting.

    Used only when a caller explicitly asks for markdown. The reason this is
    not applied everywhere is that escaping already-escaped text mangles it,
    so the rule is one place decides, and that is the caller.
    """
    return _MARKDOWN_RE.sub(r"\\\1", text or "")


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """
    Break a long message into pieces Telegram will accept.

    Split on paragraph breaks first, then lines, and only cut mid-line when a
    single line is itself too long. A report split in the middle of a sentence
    reads as broken; split between paragraphs it reads as a message that
    continued.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []

    chunks, current = [], ""

    for block in text.split("\n\n"):
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(block) <= limit:
            current = block
            continue

        # A single paragraph too long for one message. Fall to lines, and only
        # then to a hard cut, which is the one case where a word can break.
        for line in block.split("\n"):
            candidate = (current + "\n" + line) if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line

    if current:
        chunks.append(current)
    return chunks


class Transport:
    """
    What the bot needs from Telegram, and nothing else.

    Deliberately small. Anything added here has to be implemented twice, once
    for real and once for the fake, and every extra method is another thing the
    tests can only pretend to know.
    """

    def send_text(self, chat_id: int, text: str, markdown: bool = False,
                  buttons: list | None = None, reply_to: int | None = None) -> list:
        raise NotImplementedError

    def send_document(self, chat_id: int, data: bytes, filename: str,
                      caption: str = "") -> object:
        raise NotImplementedError

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        raise NotImplementedError

    def get_updates(self, offset: int = 0, timeout: int = 30) -> list:
        raise NotImplementedError


class FakeTransport(Transport):
    """
    A transport that keeps everything instead of sending it.

    This is what lets the whole bot be built before a token exists. Tests drive
    it by queueing updates and then reading `sent`, so a purchase flow can be
    walked end to end, including the parts that only happen when somebody
    replies with the wrong thing.

    It also refuses what Telegram would refuse. A fake that accepts a 60MB file
    or a 9000 character message teaches the bot habits that break the first
    time it runs for real.
    """

    def __init__(self):
        self.sent: list[SentMessage] = []
        self.answered: list[tuple] = []
        self.pending: list[Update] = []
        self.fail_next: str | None = None
        self._next_update_id = 1

    # ── what the bot calls ──────────────────────────────────────────────────

    def send_text(self, chat_id, text, markdown=False, buttons=None, reply_to=None):
        self._maybe_fail()
        pieces = split_text(text)
        if not pieces:
            raise TransportError("Refusing to send an empty message")

        records = []
        for piece in pieces:
            record = SentMessage(chat_id=chat_id, text=piece, markdown=markdown,
                                 buttons=buttons, reply_to=reply_to)
            self.sent.append(record)
            records.append(record)
        return records

    def send_document(self, chat_id, data, filename, caption=""):
        self._maybe_fail()
        if not data:
            raise TransportError("Refusing to send an empty file")
        if len(data) > MAX_DOCUMENT_BYTES:
            raise TransportError(
                f"{filename} is {len(data)} bytes, over Telegram's "
                f"{MAX_DOCUMENT_BYTES} limit for a bot upload")
        if len(caption) > MAX_CAPTION:
            raise TransportError(
                f"A caption may be {MAX_CAPTION} characters, this one is "
                f"{len(caption)}. Send the long part as its own message.")

        record = SentMessage(chat_id=chat_id, text=caption, document=data,
                             filename=filename)
        self.sent.append(record)
        return record

    def answer_callback(self, callback_id, text=""):
        self._maybe_fail()
        self.answered.append((callback_id, text))

    def get_updates(self, offset=0, timeout=30):
        updates, self.pending = self.pending, []
        return updates

    # ── what a test calls ───────────────────────────────────────────────────

    def receive(self, chat_id: int, text: str, user_id: int | None = None,
                is_group: bool = False, username: str | None = None) -> Update:
        """Queue an incoming message as though somebody typed it."""
        update = Update(
            update_id=self._next_update_id, chat_id=chat_id,
            user_id=user_id if user_id is not None else chat_id,
            text=text, username=username, is_group=is_group,
            message_id=self._next_update_id)
        self._next_update_id += 1
        self.pending.append(update)
        return update

    def texts(self, chat_id: int | None = None) -> list[str]:
        """Everything said, for asserting on."""
        return [m.text for m in self.sent
                if m.document is None and (chat_id is None or m.chat_id == chat_id)]

    def documents(self, chat_id: int | None = None) -> list[SentMessage]:
        return [m for m in self.sent
                if m.document is not None and (chat_id is None or m.chat_id == chat_id)]

    def last_text(self, chat_id: int | None = None) -> str:
        texts = self.texts(chat_id)
        return texts[-1] if texts else ""

    def said(self, fragment: str, chat_id: int | None = None) -> bool:
        """Whether anything sent contains this, case insensitively."""
        needle = fragment.lower()
        return any(needle in t.lower() for t in self.texts(chat_id))

    def clear(self):
        self.sent.clear()
        self.answered.clear()

    def _maybe_fail(self):
        """
        Fail the next send, once, on request.

        Delivery failure is normal rather than exceptional: people block bots,
        and a flow that has already taken somebody's money has to cope with not
        being able to hand over the result.
        """
        if self.fail_next:
            reason, self.fail_next = self.fail_next, None
            raise TransportError(reason)

"""
The transport that actually talks to Telegram.

Everything else in the bot was written against the Transport interface and
tested against the fake one, so this is the only file that knows Telegram's
HTTP API exists. It is deliberately the last thing built and the smallest
thing possible: normalise what comes in, post what goes out, and get out of
the way.

Long polling rather than a webhook. A webhook needs a public URL registered
with Telegram, breaks quietly whenever the deployment address changes, and
buys nothing at this size. Polling holds one long request open and Telegram
answers it when something happens, so it is not the busy loop it sounds like.

The token is read from the environment and never logged, never echoed back
into a chat, and never written to disk.
"""

import logging
import os
import time

import requests

from tg_transport import SentMessage, Transport, TransportError, Update, split_text

logger = logging.getLogger(__name__)

API_ROOT = os.getenv("TELEGRAM_API_ROOT", "https://api.telegram.org")

# How long Telegram holds a poll open with nothing to say. Long enough that
# an idle bot is nearly silent on the network, short enough that a deploy is
# not waiting on it.
POLL_SECONDS = int(os.getenv("TG_POLL_SECONDS", "25"))

# The request gives up after the poll it is waiting on, plus room for the
# response itself. Cutting it any finer means a healthy long poll times out
# on our side and every message arrives twice.
HTTP_TIMEOUT = POLL_SECONDS + 15


def token() -> str | None:
    """The bot token, or None when there is not one configured."""
    value = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    return value or None


class HttpTransport(Transport):
    """Telegram over HTTP. One session, reused, so connections are not remade."""

    def __init__(self, bot_token: str | None = None):
        self.token = bot_token or token()
        if not self.token:
            raise TransportError(
                "No TELEGRAM_BOT_TOKEN is set, so there is nothing to connect "
                "with."
            )
        self.session = requests.Session()

    # ── plumbing ────────────────────────────────────────────────────────────

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self.token}/{method}"

    def _call(self, method: str, http_timeout: int | None = None, **payload):
        """
        One API call.

        Telegram answers 200 with ok:false for things like being blocked by a
        user, so the body has to be read rather than trusting the status code.
        """
        try:
            response = self.session.post(
                self._url(method), json=payload,
                timeout=http_timeout or HTTP_TIMEOUT)
        except requests.RequestException as error:
            # The token is in the URL, so the exception text is not safe to
            # pass on as it is.
            raise TransportError(f"{method} could not be sent") from error

        try:
            body = response.json()
        except ValueError:
            raise TransportError(f"{method} returned something that is not JSON")

        if not body.get("ok"):
            description = body.get("description", "no reason given")
            # 429 carries how long to wait. Honouring it is the difference
            # between a brief pause and being cut off for longer each time.
            if response.status_code == 429:
                after = (body.get("parameters") or {}).get("retry_after", 1)
                logger.warning("Rate limited on %s, waiting %ss", method, after)
                time.sleep(min(int(after), 30))
                raise TransportError(f"{method} was rate limited")
            raise TransportError(f"{method} refused: {description}")

        return body.get("result")

    # ── sending ─────────────────────────────────────────────────────────────

    def send_text(self, chat_id, text, markdown=False, buttons=None,
                  reply_to=None) -> list:
        """
        Send a message, split if it is over Telegram's limit.

        Only the first piece carries the buttons and the reply, so a long
        answer does not repeat its keyboard three times.
        """
        sent = []
        pieces = split_text(text)
        for index, piece in enumerate(pieces):
            payload = {"chat_id": chat_id, "text": piece,
                       "disable_web_page_preview": True}
            if markdown:
                payload["parse_mode"] = "MarkdownV2"
            if buttons and index == 0:
                payload["reply_markup"] = {"inline_keyboard": buttons}
            if reply_to and index == 0:
                payload["reply_to_message_id"] = reply_to

            result = self._call("sendMessage", **payload)
            sent.append(SentMessage(
                chat_id=chat_id, text=piece, markdown=markdown,
                buttons=buttons if index == 0 else None,
                reply_to=reply_to if index == 0 else None))
            if result:
                sent[-1].message_id = result.get("message_id")
        return sent

    def send_document(self, chat_id, data: bytes, filename: str, caption=""):
        """
        Send a file.

        Multipart rather than JSON, because the file is bytes. This is the one
        call that cannot go through _call, so it repeats its error handling.
        """
        try:
            response = self.session.post(
                self._url("sendDocument"),
                data={"chat_id": str(chat_id), "caption": caption[:1024]},
                files={"document": (filename, data)},
                timeout=HTTP_TIMEOUT)
            body = response.json()
        except (requests.RequestException, ValueError) as error:
            raise TransportError("The file could not be sent") from error

        if not body.get("ok"):
            raise TransportError(
                f"The file was refused: {body.get('description', 'no reason')}")

        return SentMessage(chat_id=chat_id, document=data, filename=filename,
                           text=caption)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """
        Acknowledge a button press.

        Telegram shows a loading spinner on the button until this arrives, so
        a failure here is cosmetic and never worth failing the work behind it.
        """
        try:
            self._call("answerCallbackQuery", http_timeout=10,
                       callback_query_id=callback_id, text=text[:200])
        except TransportError:
            logger.debug("Could not answer callback %s", callback_id)

    # ── receiving ───────────────────────────────────────────────────────────

    def get_updates(self, offset: int = 0, timeout: int | None = None) -> list:
        """
        Wait for things to happen, and return them in the bot's own shape.

        Only messages and button presses are asked for. Telegram will happily
        deliver edits, reactions, join events and poll answers, and every one
        of those the bot does not use is a thing it has to skip on every pass.
        """
        wait = POLL_SECONDS if timeout is None else timeout
        result = self._call(
            "getUpdates", http_timeout=wait + 15,
            offset=offset, timeout=wait,
            allowed_updates=["message", "callback_query"])
        return [u for u in (normalise(raw) for raw in (result or [])) if u]


def normalise(raw: dict) -> Update | None:
    """
    Telegram's payload, in the shape the rest of the bot expects.

    Returns None for anything with no text and no callback, which covers the
    photos, stickers and join notices that arrive in any group and that no
    handler here has an answer for.
    """
    update_id = raw.get("update_id", 0)

    callback = raw.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        user = callback.get("from") or {}
        return Update(
            update_id=update_id,
            chat_id=chat.get("id", 0),
            user_id=user.get("id", 0),
            username=user.get("username"),
            is_group=chat.get("type") in ("group", "supergroup"),
            message_id=message.get("message_id"),
            callback_data=callback.get("data"),
            callback_id=callback.get("id"),
        )

    message = raw.get("message")
    if not message:
        return None

    text = message.get("text") or message.get("caption") or ""
    if not text:
        return None

    chat = message.get("chat") or {}
    user = message.get("from") or {}
    return Update(
        update_id=update_id,
        chat_id=chat.get("id", 0),
        user_id=user.get("id", 0),
        text=text,
        username=user.get("username"),
        is_group=chat.get("type") in ("group", "supergroup"),
        message_id=message.get("message_id"),
    )

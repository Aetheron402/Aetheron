"""
The bot, assembled and running.

Every piece of it was built and tested separately against a fake transport and
a fake API. This is where they are put together and pointed at the real thing:
one router with every command on it, one poll loop taking messages in, and one
worker moving paid jobs along and delivering what comes back.

It runs as a thread inside the web service rather than as its own deployment.
The bot is idle almost all the time, it needs the same database and the same
environment, and a second service is a second thing to configure, pay for and
watch. If it ever gets busy enough to matter it lifts out of here unchanged,
because nothing above this file knows where it runs.

Nothing here starts unless TELEGRAM_BOT_TOKEN is set. An instance without one
carries on exactly as it did before this file existed.
"""

import logging
import os
import threading
import time

import tg_assets
import tg_commands
import tg_flows
import tg_free
import tg_link
import tg_purchase
from tg_api import HttpApiClient
from tg_http import HttpTransport, token
from tg_transport import TransportError

logger = logging.getLogger(__name__)

# How long to wait after a failure before trying again. Telegram being briefly
# unreachable is normal and should not turn into a hot loop hammering it.
BACKOFF_SECONDS = int(os.getenv("TG_BACKOFF", "5"))
BACKOFF_MAX = int(os.getenv("TG_BACKOFF_MAX", "120"))

# How often the worker looks at running jobs. Separate from the poll loop
# because a job finishing is not something Telegram tells us about.
WORK_EVERY_SECONDS = int(os.getenv("TG_WORK_EVERY", "5"))


def build(api=None, transport=None):
    """
    One router with every command, and the state the workers own.

    The pending lists live here rather than inside the modules that queue onto
    them, so the handlers stay handlers and there is exactly one place that
    knows what is outstanding.
    """
    api = api or HttpApiClient()
    transport = transport or HttpTransport()

    pending_previews: list = []
    last_listed: dict = {}

    router = tg_commands.build_router()
    tg_flows.register(router, api)
    tg_free.register(router, api, pending_previews)
    tg_assets.register(router, api, last_listed)

    return {
        "router": router,
        "api": api,
        "transport": transport,
        "pending_previews": pending_previews,
        "last_listed": last_listed,
    }


def work_once(bot) -> dict:
    """
    Move everything that is in flight one step forward.

    Both halves are wrapped, because a single job that cannot be delivered has
    to not stop every other job from being.
    """
    done = {"purchases": {}, "previews": {}}

    try:
        done["purchases"] = tg_flows.advance_all(bot["api"], bot["transport"])
    except Exception:
        logger.exception("Could not advance purchases")

    try:
        done["previews"] = tg_free.advance_previews(
            bot["pending_previews"], bot["api"], bot["transport"])
    except Exception:
        logger.exception("Could not advance previews")

    return done


def poll_forever(bot, stop: threading.Event):
    """
    Take messages in, one long poll at a time.

    The offset is what stops a message being handled twice: Telegram keeps
    returning an update until it is acknowledged by asking for the one after
    it. It is only moved past an update once dispatch has returned, so a crash
    mid handler means the message is seen again rather than lost.
    """
    offset = 0
    backoff = BACKOFF_SECONDS
    last_work = 0.0

    while not stop.is_set():
        try:
            updates = bot["transport"].get_updates(offset=offset)
            backoff = BACKOFF_SECONDS

            for update in updates:
                offset = max(offset, update.update_id + 1)
                try:
                    bot["router"].dispatch(update, bot["transport"])
                except Exception:
                    # One bad message must never take the bot down. The person
                    # who sent it gets nothing back, which is worse for them
                    # than an error, but better than the bot stopping for
                    # everybody else.
                    logger.exception("Update %s failed", update.update_id)

        except TransportError as error:
            logger.warning("Telegram: %s. Waiting %ss", error, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue
        except Exception:
            logger.exception("Poll loop error. Waiting %ss", backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue

        now = time.time()
        if now - last_work >= WORK_EVERY_SECONDS:
            last_work = now
            work_once(bot)


def start(daemon: bool = True) -> threading.Event | None:
    """
    Start the bot if there is a token, and say so if there is not.

    Returns the event that stops it, or None when nothing was started. Never
    raises: a bot that cannot start is not a reason for the shop to not serve
    a single page.
    """
    if not token():
        logger.info("No TELEGRAM_BOT_TOKEN set, so the bot is not starting.")
        return None

    try:
        tg_link.init()
        tg_purchase.init()
        bot = build()
    except Exception:
        logger.exception("The bot could not be assembled, so it is not running")
        return None

    stop = threading.Event()
    thread = threading.Thread(
        target=poll_forever, args=(bot, stop), name="telegram-bot",
        daemon=daemon)
    thread.start()
    logger.info("Telegram bot polling")
    return stop

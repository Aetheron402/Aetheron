# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

"""
Getting the card into a room.

Telegram and Discord both take an image with a caption over one request, which
is all this needs. Nothing here retries forever: a channel that is refusing
posts will still be refusing them in a minute, and the desk has other things
to do.
"""

import requests


# What each kind needs before it can post anything.
REQUIRED = {
    "telegram": ("token", "chat_id"),
    "discord": ("webhook_url",),
}


class Publisher:
    def __init__(self, channels, logger):
        # A channel counts as on when it has its credentials. There used to be
        # a separate enabled flag, which meant somebody could fill in their
        # token, see nothing get posted, and have no way of telling why.
        self.channels = []
        for kind, settings in (channels or {}).items():
            settings = settings or {}
            needed = REQUIRED.get(kind)
            if not needed:
                logger.warning("Unknown channel: %s", kind)
                continue
            if all(str(settings.get(key, "")).strip() for key in needed):
                self.channels.append((kind, settings))

        self.logger = logger

    def count(self) -> int:
        return len(self.channels)

    def post(self, signal, card, logger) -> bool:
        """
        Send one signal everywhere it goes. True if anywhere took it.

        A channel that fails does not stop the others, because one dead webhook
        should not silence every room.
        """
        if not self.channels:
            # Nothing configured is a normal state, not an error. It is how the
            # desk runs while somebody is still setting it up, and the log is
            # the preview of what would have gone out.
            logger.info("No channels enabled, so nothing was sent.")
            return True

        sent = False
        for kind, settings in self.channels:
            try:
                if kind == "telegram":
                    self._telegram(settings, signal, card)
                else:
                    self._discord(settings, signal, card)
                sent = True
            except Exception as error:
                logger.warning("Could not post to %s: %s", kind, error)
        return sent

    def _caption(self, signal) -> str:
        parts = [signal.get("title", "Signal")]
        if signal.get("mint"):
            parts.append(signal["mint"])
        if signal.get("link"):
            parts.append(signal["link"])
        return "\n".join(parts)[:1000]

    def _telegram(self, channel, signal, card):
        token, chat = channel["token"], channel["chat_id"]
        if card:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat, "caption": self._caption(signal)},
                files={"photo": ("signal.png", card)}, timeout=20,
            ).raise_for_status()
        else:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": self._caption(signal)},
                timeout=20,
            ).raise_for_status()

    def _discord(self, channel, signal, card):
        url = channel["webhook_url"]
        if card:
            requests.post(url, data={"content": self._caption(signal)},
                          files={"file": ("signal.png", card)},
                          timeout=20).raise_for_status()
        else:
            requests.post(url, json={"content": self._caption(signal)},
                          timeout=20).raise_for_status()

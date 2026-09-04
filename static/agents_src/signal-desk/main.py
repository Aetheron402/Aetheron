# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

"""
Signal Desk.

This one does not watch anything. Every other agent in the store detects
something and then prints it, and three of them fire a webhook with a line of
text in it. That line is what gets a channel muted.

This is the layer above them. It takes signals from wherever they come from,
decides which are worth posting and when, draws each one as a card, and posts
it. Point your other agents at its inbox and they stop being loggers and start
being a feed people actually read.

The editorial part is the half that matters. A room will forgive a bot that
posts four good things a day and will mute one that posts forty, so nothing is
posted twice, nothing is posted during quiet hours, and anything held back is
rolled into a digest instead of being lost.
"""

import time

from utils.cards import render_card
from utils.editorial import Editorial
from utils.helpers import load_config, setup_logger
from utils.inbox import Inbox
from utils.publish import Publisher


class SignalDesk:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

        self.inbox = Inbox(config["inbox"], logger)
        self.editorial = Editorial(config["editorial"], logger)
        self.publisher = Publisher(config["channels"], logger)

        self.brand = config.get("brand", {})
        self.interval = config["general"]["poll_interval_seconds"]

        self.logger.info(
            "Signal Desk ready. Watching %s, publishing to %d channel(s).",
            self.inbox.describe(), self.publisher.count())

    # ── the loop ────────────────────────────────────────────────────────────

    def run(self):
        self.logger.info("Starting publish loop (every %ss)...", self.interval)

        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception as error:
                # One bad signal must never take the desk down. A publisher
                # that dies at three in the morning is worse than one that
                # skips a post.
                self.logger.error("Cycle failed: %s", error, exc_info=True)

            time.sleep(self.interval)

    def tick(self):
        for signal in self.inbox.read():
            self.consider(signal)

        due = self.editorial.digest_due()
        if due:
            self.publish_digest(due)

    # ── one signal ──────────────────────────────────────────────────────────

    def consider(self, signal):
        """Decide, draw, post. In that order, because two of them cost money."""
        verdict = self.editorial.judge(signal)

        if verdict.hold:
            self.logger.info("Holding %s: %s", signal.get("title", "signal"),
                             verdict.reason)
            return

        card = render_card(signal, self.brand, self.logger)
        if card:
            self.logger.info("Drew a card, %.0f KB", len(card) / 1024)

        posted = self.publisher.post(signal, card, self.logger)

        if posted:
            self.editorial.remember(signal)
            self.logger.info("Posted: %s", signal.get("title", "signal"))

    def publish_digest(self, held):
        """
        What was held back, as one post rather than none.

        Quiet hours and cooldowns are for not being annoying, not for throwing
        things away. Somebody who wakes up should still be able to see what
        happened overnight.
        """
        self.logger.info("Publishing digest of %d held signal(s)", len(held))
        card = render_card({
            "kind": "digest",
            "title": f"{len(held)} signals while you were away",
            "lines": [s.get("title", "signal") for s in held[:8]],
        }, self.brand, self.logger)

        self.publisher.post({"title": "Digest", "kind": "digest"}, card,
                            self.logger)


def main():
    config = load_config()
    logger = setup_logger(**config.get("logging", {}))
    SignalDesk(config, logger).run()


if __name__ == "__main__":
    main()

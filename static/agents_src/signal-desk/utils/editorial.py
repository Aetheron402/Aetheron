# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

"""
What gets posted, and when.

This is the half of the desk that decides rather than draws. A channel will
forgive four good posts a day and will mute forty, so the rules here are all
about restraint: nothing twice, nothing too often, nothing at three in the
morning, and nothing at all below the bar you set.

Everything held back is kept rather than dropped, and comes out later as one
digest. The point is to be quiet, not to lose things.
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Verdict:
    hold: bool = False
    reason: str = ""


class Editorial:
    def __init__(self, config, logger):
        self.logger = logger

        self.min_score = float(config.get("min_score", 0))
        self.cooldown = int(config.get("per_subject_cooldown_minutes", 60)) * 60
        self.max_per_hour = int(config.get("max_posts_per_hour", 6))
        self.quiet_from = config.get("quiet_hours_from")
        self.quiet_to = config.get("quiet_hours_to")
        self.digest_at = config.get("digest_at")

        self.state_file = config.get("state_file", "signal_desk_state.json")
        self.seen, self.posted_at, self.held = {}, [], []
        self._load()

    # ── the decision ────────────────────────────────────────────────────────

    def judge(self, signal) -> Verdict:
        subject = self.subject_of(signal)

        score = float(signal.get("score", 1))
        if score < self.min_score:
            return Verdict(True, f"score {score} below {self.min_score}")

        # The same token twice in an hour reads as a broken bot, even when both
        # events are real.
        last = self.seen.get(subject)
        if last and time.time() - last < self.cooldown:
            left = int((self.cooldown - (time.time() - last)) / 60)
            self._hold(signal)
            return Verdict(True, f"{subject} posted {left}m ago")

        self._forget_old_posts()
        if len(self.posted_at) >= self.max_per_hour:
            self._hold(signal)
            return Verdict(True, f"{self.max_per_hour} posts already this hour")

        if self.in_quiet_hours():
            self._hold(signal)
            return Verdict(True, "quiet hours")

        return Verdict(False)

    def remember(self, signal):
        self.seen[self.subject_of(signal)] = time.time()
        self.posted_at.append(time.time())
        self._save()

    # ── holding, and letting go later ───────────────────────────────────────

    def _hold(self, signal):
        """
        Kept rather than dropped.

        Bounded, because a desk left running through a weekend of quiet hours
        would otherwise carry a thousand items into Monday's digest.
        """
        self.held.append(signal)
        self.held = self.held[-50:]
        self._save()

    def digest_due(self):
        """Whether it is time to publish what was held, and what to publish."""
        if not self.held or not self.digest_at:
            return None

        now = datetime.now()
        if now.strftime("%H:%M") != self.digest_at:
            return None
        # Only once, even though the loop runs many times inside that minute.
        if self.seen.get("__digest__", 0) > time.time() - 120:
            return None

        held, self.held = self.held, []
        self.seen["__digest__"] = time.time()
        self._save()
        return held

    # ── the small print ─────────────────────────────────────────────────────

    def subject_of(self, signal) -> str:
        """
        What two signals have to share to count as the same thing.

        The mint where there is one, since the same token arriving from two
        sources is still one story. The title otherwise.
        """
        return (signal.get("mint") or signal.get("subject")
                or signal.get("title") or "signal")

    def in_quiet_hours(self) -> bool:
        if not self.quiet_from or not self.quiet_to:
            return False

        now = datetime.now().strftime("%H:%M")
        if self.quiet_from <= self.quiet_to:
            return self.quiet_from <= now < self.quiet_to
        # Wraps past midnight, which is the normal case for quiet hours.
        return now >= self.quiet_from or now < self.quiet_to

    def _forget_old_posts(self):
        cutoff = time.time() - 3600
        self.posted_at = [at for at in self.posted_at if at > cutoff]

    # ── state that survives a restart ───────────────────────────────────────

    def _load(self):
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as handle:
                data = json.load(handle)
            self.seen = data.get("seen", {})
            self.posted_at = data.get("posted_at", [])
            self.held = data.get("held", [])
        except Exception as error:
            # A corrupt state file is not worth refusing to start over. The
            # worst it costs is one repeated post.
            self.logger.warning("Could not read %s: %s", self.state_file, error)

    def _save(self):
        try:
            with open(self.state_file, "w") as handle:
                json.dump({"seen": self.seen, "posted_at": self.posted_at,
                           "held": self.held}, handle)
        except Exception as error:
            self.logger.warning("Could not write %s: %s", self.state_file, error)

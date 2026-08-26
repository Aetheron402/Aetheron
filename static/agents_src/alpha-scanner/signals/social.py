from datetime import datetime
from typing import List

from schemas.signal import Signal, SignalSource
from .base import BaseSignalGenerator


class SocialSignalGenerator(BaseSignalGenerator):
    """
    Social mentions and their velocity.

    Nothing is emitted. Every source worth reading for this, X above all,
    requires a paid API key, and this template ships without one.

    The previous version returned a Signal with value 0.8 for a narrative
    called ai_agents on every cycle. That is worse than returning nothing: a
    fabricated signal is fused and ranked exactly like a measured one, so the
    scanner reported social confirmation it had never looked for.

    To enable this, add a client for whichever source you have access to and
    emit one Signal per narrative keyed on the token address, so it fuses with
    the market and on-chain signals.
    """

    source = SignalSource.SOCIAL

    def __init__(self, *args, **kwargs):
        super().__init__() if hasattr(super(), "__init__") else None
        self._warned = False

    def generate(self, timestamp: datetime) -> List[Signal]:
        if not self._warned:
            self._warned = True
            print(
                "[social] No social source is configured, so no social signals "
                "are produced. Scores below come from market and on-chain data "
                "only. See signals/social.py to connect one."
            )
        return []

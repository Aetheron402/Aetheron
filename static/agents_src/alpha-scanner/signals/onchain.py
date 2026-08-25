from typing import List
from datetime import datetime

from schemas.signal import Signal, SignalSource
from .base import BaseSignalGenerator


class OnchainSignalGenerator(BaseSignalGenerator):
    source = SignalSource.ONCHAIN

    def generate(self, timestamp: datetime) -> List[Signal]:
        signals: List[Signal] = []

        signals.append(
            Signal(
                source=self.source,
                key="ai_agents",   # MUST be identical in all generators
                value=0.8,
                confidence=0.8,
                timestamp=timestamp,
                metadata={
                    "scan": "placeholder"
                }
            )
        )

        return signals

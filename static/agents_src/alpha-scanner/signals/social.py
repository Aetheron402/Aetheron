from datetime import datetime
from typing import List

from schemas.signal import Signal, SignalSource
from .base import BaseSignalGenerator


class SocialSignalGenerator(BaseSignalGenerator):
    source = SignalSource.SOCIAL

    def generate(self, timestamp: datetime) -> List[Signal]:
        # Placeholder scan logic
        # In reality this would scan X, forums, etc.
        return [
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
        ]
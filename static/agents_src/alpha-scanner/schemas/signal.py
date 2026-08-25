from dataclasses import dataclass
from typing import Dict, Optional
from enum import Enum
from datetime import datetime


class SignalSource(str, Enum):
    SOCIAL = "social"
    ONCHAIN = "onchain"
    MARKET = "market"


@dataclass
class Signal:
    source: SignalSource
    key: str                 # token, narrative keyword, wallet cluster id, etc.
    value: float             # normalized signal strength (0–1 or z-score)
    confidence: float        # how reliable this signal is (0–1)
    timestamp: datetime

    metadata: Optional[Dict] = None

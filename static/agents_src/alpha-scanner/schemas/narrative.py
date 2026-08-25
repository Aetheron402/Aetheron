from dataclasses import dataclass, field
from typing import List, Dict
from datetime import datetime

from .signal import Signal


@dataclass
class Narrative:
    id: str
    name: str                       
    keywords: List[str]

    signals: List[Signal] = field(default_factory=list)

    strength: float = 0.0            # aggregated signal strength
    momentum: float = 0.0            # rate of change
    freshness: float = 0.0           # time-decayed score

    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_updated: datetime = field(default_factory=datetime.utcnow)

    metadata: Dict = field(default_factory=dict)

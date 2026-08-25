from dataclasses import dataclass, field
from typing import List, Dict
from datetime import datetime

from .narrative import Narrative


@dataclass
class Opportunity:
    id: str
    narrative: Narrative

    related_assets: List[str]        # tokens, markets, contracts, etc.

    score: float                     # ranking score
    confidence: float                # final confidence score

    reasons: List[str] = field(default_factory=list)

    created_at: datetime = field(default_factory=datetime.utcnow)

    metadata: Dict = field(default_factory=dict)

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime

@dataclass
class Outcome:
    id: str
    label: str
    probability: float
    price: Optional[float] = None
    liquidity: Optional[float] = None


@dataclass
class Market:
    id: str
    title: str
    outcomes: List[Outcome]
    close_time: datetime
    status: str
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Order:
    market_id: str
    outcome_id: str
    side: str
    price: float
    size: float
    timestamp: datetime

@dataclass
class Position:
    id: str
    market_id: str
    outcome_id: str
    size: float
    entry_price: float
    current_price: float
    status: str
    opened_at: datetime

@dataclass
class AgentConfig:
    bankroll: float
    max_exposure: float
    strategy: str
    sizing: str
    run_interval: int 
    risk_limits: Dict[str, float]

from typing import TypedDict, Literal, List, Dict


ModuleState = Literal["positive", "neutral", "negative"]
TrendState = Literal["improving", "stable", "deteriorating"]
EnvironmentState = Literal["risk_on", "neutral", "risk_off"]


class ModuleResult(TypedDict):
    state: ModuleState
    score: float          # normalized -1.0 → +1.0
    confidence: float     # 0.0 → 1.0
    trend: TrendState
    notes: List[str]


class AgentOutput(TypedDict):
    timestamp: str
    environment: EnvironmentState
    confidence: float
    modules: Dict[str, ModuleResult]
    summary: List[str]

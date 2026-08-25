from dataclasses import dataclass
from typing import Dict


@dataclass
class SignalWeights:
    social: float
    onchain: float
    market: float


@dataclass
class DecayConfig:
    signal_half_life_hours: float
    narrative_half_life_hours: float


@dataclass
class RiskConfig:
    enable_noise_filter: bool
    enable_recycled_filter: bool
    enable_regime_filter: bool


@dataclass
class RankingConfig:
    min_confidence: float
    max_results: int


@dataclass
class AgentConfig:
    signal_weights: SignalWeights
    decay: DecayConfig
    risk: RiskConfig
    ranking: RankingConfig

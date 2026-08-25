from abc import ABC, abstractmethod
from typing import Optional

from schemas import Market


class StrategyDecision:
    def __init__(self, outcome_id: str, price: float):
        self.outcome_id = outcome_id
        self.price = price


class BaseStrategy(ABC):
    @abstractmethod
    def evaluate(self, market: Market) -> Optional[StrategyDecision]:
        pass


class ProbabilityThresholdStrategy(BaseStrategy):
    def __init__(self, min_probability: float):
        self.min_probability = min_probability

    def evaluate(self, market: Market) -> Optional[StrategyDecision]:
        if market.status != "open":
            return None

        for outcome in market.outcomes:
            if outcome.probability <= self.min_probability:
                if outcome.price is None:
                    continue

                return StrategyDecision(
                    outcome_id=outcome.id,
                    price=outcome.price,
                )

        return None
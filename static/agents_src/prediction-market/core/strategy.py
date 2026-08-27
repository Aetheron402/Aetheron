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
    """
    Enter an outcome the market already gives at least `min_probability`.

    This is a scaffold, not an edge. It holds no independent estimate of fair
    value, so it cannot tell whether a price is wrong; it only keeps the agent
    out of the tail. Replace `evaluate` with your own view and the rest of the
    engine, sizing, risk and lifecycle, keeps working unchanged.

    The comparison used to run the other way, `<= min_probability`, so a
    setting of 0.30 entered anything priced at or below 30% and the opening
    trades of a run were routinely dead long-shots at a twentieth of a cent.
    The key is named minimum, and it is now a minimum.
    """

    def __init__(self, min_probability: float):
        self.min_probability = min_probability

    def evaluate(self, market: Market) -> Optional[StrategyDecision]:
        if market.status != "open":
            return None

        eligible = [
            outcome for outcome in market.outcomes
            if outcome.price is not None
            and outcome.probability is not None
            and outcome.probability >= self.min_probability
        ]
        if not eligible:
            return None

        # The strongest qualifying outcome, not whichever the API happened to
        # list first. On a binary market that ordering is arbitrary, so the
        # side taken was effectively random.
        best = max(eligible, key=lambda outcome: outcome.probability)

        return StrategyDecision(outcome_id=best.id, price=best.price)
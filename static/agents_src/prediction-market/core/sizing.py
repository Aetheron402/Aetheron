from abc import ABC, abstractmethod

from schemas import Market


class BaseSizer(ABC):
    @abstractmethod
    def calculate(
        self,
        market: Market,
        outcome_id: str,
        bankroll: float,
    ) -> float:
        pass


class FixedFractionSizer(BaseSizer):
    def __init__(self, fraction: float, max_size: float):
        self.fraction = fraction
        self.max_size = max_size

    def calculate(
        self,
        market: Market,
        outcome_id: str,
        bankroll: float,
    ) -> float:
        size = bankroll * self.fraction
        return min(size, self.max_size)


class FixedSizeSizer(BaseSizer):
    def __init__(self, size: float):
        self.size = size

    def calculate(
        self,
        market: Market,
        outcome_id: str,
        bankroll: float,
    ) -> float:
        return min(self.size, bankroll)
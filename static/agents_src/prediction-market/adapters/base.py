from abc import ABC, abstractmethod
from typing import List

from schemas import Market, Order, Position


class PredictionMarketAdapter(ABC):
    """
    Base interface for connecting the agent to a prediction market.
    """

    @abstractmethod
    def list_markets(self) -> List[Market]:
        pass

    @abstractmethod
    def get_market(self, market_id: str) -> Market:
        pass

    @abstractmethod
    def place_order(self, order: Order) -> str:
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def close_position(self, position_id: str) -> None:
        pass

    @abstractmethod
    def settle(self) -> None:
        pass

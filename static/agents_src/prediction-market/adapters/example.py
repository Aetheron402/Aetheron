import uuid
from datetime import datetime, timedelta
from typing import List

from .base import PredictionMarketAdapter
from schemas import Market, Outcome, Order, Position
from utils.logging import setup_logger

logger = setup_logger("example-adapter")


class ExampleAdapter(PredictionMarketAdapter):
    """
    Example prediction market adapter.

    This adapter DOES NOT connect to a real prediction market.
    It returns mock data and simulates execution in memory.

    Replace this class with your own adapter that:
    - fetches real markets
    - places real orders
    - tracks real positions
    """

    def __init__(self):
        logger.info("Initializing ExampleAdapter (mock implementation)")
        self.positions: List[Position] = []

    def list_markets(self) -> List[Market]:
        logger.info("Returning mock market data (example only)")

        return [
            Market(
                id="market_1",
                title="Will Example Event Happen?",
                outcomes=[
                    Outcome(
                        id="yes",
                        label="YES",
                        probability=0.28,
                        price=0.28,
                        liquidity=1000.0,
                    ),
                    Outcome(
                        id="no",
                        label="NO",
                        probability=0.72,
                        price=0.72,
                        liquidity=1000.0,
                    ),
                ],
                close_time=datetime.utcnow() + timedelta(hours=6),
                status="open",
            )
        ]

    def get_market(self, market_id: str) -> Market:
        logger.info(f"Fetching market {market_id} (mock)")

        for market in self.list_markets():
            if market.id == market_id:
                return market
        raise ValueError("Market not found")

    def place_order(self, order: Order) -> str:
        logger.info(
            f"[MOCK ORDER] market={order.market_id} "
            f"outcome={order.outcome_id} "
            f"price={order.price} "
            f"size={order.size}"
        )

        position_id = str(uuid.uuid4())

        position = Position(
            id=position_id,
            market_id=order.market_id,
            outcome_id=order.outcome_id,
            size=order.size,
            entry_price=order.price,
            current_price=order.price,
            status="open",
            opened_at=datetime.utcnow(),
        )

        self.positions.append(position)

        logger.info(f"[MOCK POSITION] opened position_id={position_id}")
        return position_id

    def get_positions(self) -> List[Position]:
        logger.info(f"Returning {len(self.positions)} open positions (mock)")
        return self.positions

    def close_position(self, position_id: str) -> None:
        logger.info(f"[MOCK CLOSE] closing position_id={position_id}")

        for position in self.positions:
            if position.id == position_id:
                position.status = "closed"
                return

    def settle(self) -> None:
        logger.info("[MOCK SETTLE] settling all open positions")

        for position in self.positions:
            if position.status == "open":
                position.status = "settled"

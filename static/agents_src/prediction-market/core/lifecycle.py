from datetime import datetime
from typing import List

from schemas import Position, Market, Order
from utils.logging import setup_logger

logger = setup_logger("lifecycle")


class LifecycleManager:
    def __init__(self, max_hold_time_seconds: int):
        self.max_hold_time_seconds = max_hold_time_seconds
        logger.info(
            f"LifecycleManager initialized | max_hold_time={max_hold_time_seconds}s"
        )

    def manage_positions(
        self,
        positions: List[Position],
        markets: List[Market],
    ) -> List[Order]:
        orders: List[Order] = []

        market_lookup = {market.id: market for market in markets}
        now = datetime.utcnow()

        logger.info(f"Evaluating lifecycle for {len(positions)} positions")

        for position in positions:
            if position.status != "open":
                continue

            market = market_lookup.get(position.market_id)
            if market is None:
                logger.warning(
                    f"Market not found for position {position.id}, skipping"
                )
                continue

            # Exit if market is no longer open
            if market.status != "open":
                logger.info(
                    f"Exiting position {position.id} "
                    f"(reason: market_closed)"
                )

                orders.append(
                    Order(
                        market_id=position.market_id,
                        outcome_id=position.outcome_id,
                        side="exit",
                        price=position.current_price,
                        size=position.size,
                        timestamp=now,
                    )
                )
                continue

            # Exit if position exceeded max hold time
            hold_time = (now - position.opened_at).total_seconds()
            if hold_time >= self.max_hold_time_seconds:
                logger.info(
                    f"Exiting position {position.id} "
                    f"(reason: max_hold_time_exceeded | hold_time={int(hold_time)}s)"
                )

                orders.append(
                    Order(
                        market_id=position.market_id,
                        outcome_id=position.outcome_id,
                        side="exit",
                        price=position.current_price,
                        size=position.size,
                        timestamp=now,
                    )
                )

        logger.info(f"Lifecycle produced {len(orders)} exit orders")
        return orders
from datetime import datetime

from schemas import Market, Order, AgentConfig
from adapters.base import PredictionMarketAdapter
from utils.logging import setup_logger

logger = setup_logger("engine")


class PredictionMarketEngine:
    def __init__(
        self,
        adapter: PredictionMarketAdapter,
        strategy,
        sizer,
        risk_manager,
        lifecycle_manager,
        config: AgentConfig,
    ):
        self.adapter = adapter
        self.strategy = strategy
        self.sizer = sizer
        self.risk_manager = risk_manager
        self.lifecycle_manager = lifecycle_manager
        self.config = config

    def run(self) -> None:
        logger.info("Engine tick")

        markets = self.adapter.list_markets()
        positions = self.adapter.get_positions()

        logger.info(f"Fetched {len(markets)} markets")
        logger.info(f"Managing {len(positions)} open positions")

        # 1) Manage existing positions (exits)
        exit_orders = self.lifecycle_manager.manage_positions(
            positions=positions,
            markets=markets,
        )

        for order in exit_orders:
            logger.info(
                f"Exiting position | market={order.market_id} "
                f"outcome={order.outcome_id} size={order.size}"
            )
            self.adapter.place_order(order)

        # 2) Process new entries
        for market in markets:
            self._process_market(market, positions)

    def _process_market(self, market: Market, positions) -> None:
        logger.info(f"Evaluating market: {market.id} | {market.title}")

        if market.status != "open":
            logger.info("Market is not open, skipping")
            return

        decision = self.strategy.evaluate(market)
        if decision is None:
            logger.info("Strategy returned no decision")
            return

        size = self.sizer.calculate(
            market=market,
            outcome_id=decision.outcome_id,
            bankroll=self.config.bankroll,
        )

        logger.info(
            f"Strategy decision | outcome={decision.outcome_id} "
            f"price={decision.price} calculated_size={size}"
        )

        if not self.risk_manager.allow(
            positions=positions,
            proposed_size=size,
        ):
            logger.warning("Risk manager blocked order due to exposure limits")
            return

        order = Order(
            market_id=market.id,
            outcome_id=decision.outcome_id,
            side="enter",
            price=decision.price,
            size=size,
            timestamp=datetime.utcnow(),
        )

        logger.info(
            f"Placing order | market={order.market_id} "
            f"outcome={order.outcome_id} size={order.size}"
        )

        self.adapter.place_order(order)
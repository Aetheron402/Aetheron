"""
Polymarket adapter: real markets, paper execution.

The agent shipped with ExampleAdapter, which returned invented markets and
logged invented fills, so a strategy could be run but never against anything
that existed. This reads the live board.

Reads are real. Markets, outcomes, prices and close times come from
Polymarket's public Gamma API, which needs no key and no account.

Orders are not. Placing a real order means holding a funded wallet and signing
on the user's behalf, which is custody, and no template priced like this one
should be doing that. Orders fill in memory against the live price, so a
strategy can be measured honestly against real probabilities without anyone's
money moving. Every fill says so.

To trade for real, implement place_order and close_position against
Polymarket's CLOB with your own signer. Nothing else here needs to change.
"""

import itertools
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from schemas import Market, Order, Outcome, Position
from .base import PredictionMarketAdapter

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
TIMEOUT = 15
CACHE_SECONDS = 20


def _as_list(value) -> list:
    """Gamma returns these fields as JSON encoded strings on some routes."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _parse_time(raw) -> datetime:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


class PolymarketAdapter(PredictionMarketAdapter):
    """Live prediction market data with in memory execution."""

    def __init__(self, logger, limit: int = 20, min_liquidity: float = 5_000.0):
        self.logger = logger
        self.limit = limit
        # A market with almost no liquidity has a price, but not one anybody
        # could trade at, so its probability is not worth acting on.
        self.min_liquidity = min_liquidity

        self._positions: Dict[str, Position] = {}
        self._ids = itertools.count(1)
        self._cache: Dict[str, Any] = {"at": 0.0, "markets": None}

        self.logger.info(
            "PolymarketAdapter: live market data, orders filled in memory only."
        )

    # ── reads, against the live board ───────────────────────────────────────

    def list_markets(self) -> List[Market]:
        if self._cache["markets"] is not None and \
                time.time() - self._cache["at"] < CACHE_SECONDS:
            return self._cache["markets"]

        try:
            response = requests.get(GAMMA_URL, params={
                "closed": "false",
                "limit": self.limit,
                "order": "volume24hr",
                "ascending": "false",
            }, timeout=TIMEOUT, headers={"User-Agent": "prediction-market-agent"})
            response.raise_for_status()
            raw = response.json()
        except Exception as exc:
            # An empty board is the honest answer to a failed fetch. Returning
            # invented markets is what this adapter exists to stop.
            self.logger.error(f"Polymarket fetch failed: {exc}. No markets this cycle.")
            return []

        markets = []
        for row in raw if isinstance(raw, list) else []:
            market = self._to_market(row)
            if market is not None:
                markets.append(market)

        self._cache.update({"at": time.time(), "markets": markets})
        self.logger.info(f"Loaded {len(markets)} live markets from Polymarket.")
        return markets

    def _to_market(self, row: Dict[str, Any]):
        labels = _as_list(row.get("outcomes"))
        prices = _as_list(row.get("outcomePrices"))
        if not labels or len(labels) != len(prices):
            return None

        try:
            liquidity = float(row.get("liquidity") or 0)
        except (TypeError, ValueError):
            liquidity = 0.0
        if liquidity < self.min_liquidity:
            return None

        outcomes = []
        for index, (label, price) in enumerate(zip(labels, prices)):
            try:
                value = float(price)
            except (TypeError, ValueError):
                return None
            outcomes.append(Outcome(
                id=f"{row.get('id')}:{index}",
                label=str(label),
                # On a binary market the price is the implied probability.
                probability=value,
                price=value,
                liquidity=liquidity,
            ))

        return Market(
            id=str(row.get("id")),
            title=row.get("question") or "(untitled market)",
            outcomes=outcomes,
            close_time=_parse_time(row.get("endDate")),
            status="closed" if row.get("closed") else "open",
            metadata={
                "liquidity": liquidity,
                "volume_24h": row.get("volume24hr"),
                "condition_id": row.get("conditionId"),
                "source": "polymarket-gamma",
            },
        )

    def get_market(self, market_id: str):
        for market in self.list_markets():
            if market.id == market_id:
                return market
        self.logger.info(f"Market {market_id} is not on the current board.")
        return None

    # ── execution, in memory ────────────────────────────────────────────────

    # How many finished positions to keep for the record before dropping the
    # oldest. Settled ones were kept forever, so an agent left running for
    # weeks accumulated every position it had ever opened.
    MAX_FINISHED = 200

    def _retire_old(self) -> None:
        """Drop the oldest finished positions once there are too many."""
        finished = [p for p in self._positions.values() if p.status != "open"]
        if len(finished) <= self.MAX_FINISHED:
            return

        finished.sort(key=lambda p: p.opened_at)
        for position in finished[:len(finished) - self.MAX_FINISHED]:
            self._positions.pop(position.id, None)

    def place_order(self, order: Order) -> str:
        """Fill against the live price and record the position. No funds move."""
        market = self.get_market(order.market_id)
        price = order.price
        if market:
            for outcome in market.outcomes:
                if outcome.id == order.outcome_id:
                    price = outcome.price if outcome.price is not None else order.price
                    break

        self._retire_old()

        position_id = f"paper-{next(self._ids)}"
        self._positions[position_id] = Position(
            id=position_id,
            market_id=order.market_id,
            outcome_id=order.outcome_id,
            size=order.size,
            entry_price=price,
            current_price=price,
            status="open",
            opened_at=order.timestamp or datetime.now(timezone.utc),
        )

        self.logger.info(
            f"[PAPER FILL] {order.side} {order.size} of {order.outcome_id} "
            f"at {price:.4f}. No order was sent and no funds moved."
        )
        return position_id

    def get_positions(self) -> List[Position]:
        """Mark every open position against the current live price."""
        board = {m.id: m for m in self.list_markets()}

        for position in self._positions.values():
            market = board.get(position.market_id)
            if not market:
                continue
            for outcome in market.outcomes:
                if outcome.id == position.outcome_id and outcome.price is not None:
                    position.current_price = outcome.price
                    break

        return list(self._positions.values())

    def close_position(self, position_id: str) -> None:
        position = self._positions.get(position_id)
        if not position:
            self.logger.info(f"No open position {position_id}.")
            return

        position.status = "closed"
        moved = position.current_price - position.entry_price
        self.logger.info(
            f"[PAPER CLOSE] {position_id} at {position.current_price:.4f} "
            f"against an entry of {position.entry_price:.4f}, "
            f"{moved * position.size:+.4f} on {position.size} units."
        )

    def _closed_market(self, market_id: str):
        """Fetch one market by id, including closed ones."""
        # By path, not by query. The id filter on the collection route returns
        # an empty list, so settlement silently never found its market.
        try:
            response = requests.get(f"{GAMMA_URL}/{market_id}", timeout=TIMEOUT,
                                    headers={"User-Agent": "prediction-market-agent"})
            response.raise_for_status()
            row = response.json()
        except Exception:
            return None
        if isinstance(row, list):
            row = row[0] if row else None
        return row if isinstance(row, dict) else None

    def settle(self) -> None:
        """
        Settle positions against the outcome the market actually resolved to.

        A resolved market prices the winning outcome at 1 and the rest at 0, so
        the real result is readable without the CLOB. This previously marked a
        position settled and said the outcome could not be determined, which
        left a paper run with no result: every position closed as a shrug and
        the strategy could never be scored.
        """
        live = {m.id for m in self.list_markets()}

        for position in self._positions.values():
            if position.status != "open" or position.market_id in live:
                continue

            row = self._closed_market(position.market_id)
            if not row or not row.get("closed"):
                continue

            labels = _as_list(row.get("outcomes"))
            prices = _as_list(row.get("outcomePrices"))
            if not labels or len(labels) != len(prices):
                position.status = "settled"
                self.logger.info(
                    f"{position.id}: market {position.market_id} closed, but its "
                    "resolution was not published in a readable form."
                )
                continue

            try:
                index = int(str(position.outcome_id).split(":")[-1])
                final = float(prices[index])
            except (ValueError, IndexError):
                position.status = "settled"
                continue

            position.current_price = final
            position.status = "settled"

            # Binary settlement: the stake either pays out at 1 or is lost.
            pnl = (final - position.entry_price) * position.size
            won = final >= 0.5
            self.logger.info(
                f"[SETTLED] {position.id} on {row.get('question', '')[:60]}: "
                f"{labels[index]} resolved {'YES' if won else 'NO'}, "
                f"entry {position.entry_price:.4f} to {final:.0f}, "
                f"{pnl:+.2f} on {position.size} units."
            )

from typing import List
from datetime import datetime

from schemas.signal import Signal, SignalSource
from utils.market_data import fetch_pairs, token_key
from .base import BaseSignalGenerator


class OnchainSignalGenerator(BaseSignalGenerator):
    """
    Trade flow, from the buy and sell counts recorded against each pair.

    Keyed on the same token address the market generator uses, since fusion
    only combines signals whose keys match.
    """

    source = SignalSource.ONCHAIN

    def generate(self, timestamp: datetime) -> List[Signal]:
        signals: List[Signal] = []

        for pair in fetch_pairs():
            key = token_key(pair)
            txns = (pair.get("txns") or {}).get("h24") or {}
            buys = txns.get("buys") or 0
            sells = txns.get("sells") or 0
            total = buys + sells
            if not key or total < 50:
                # Too few trades to read an imbalance from.
                continue

            # How one sided the flow is. Even flow scores zero, not neutral
            # noise: a token trading in both directions is not a signal.
            imbalance = abs(buys - sells) / total
            value = max(0.0, min(1.0, imbalance))

            # More trades means the imbalance is less likely to be chance.
            confidence = max(0.3, min(0.9, 0.3 + min(total / 5_000, 1.0) * 0.6))

            signals.append(
                Signal(
                    source=self.source,
                    key=key,
                    value=value,
                    confidence=confidence,
                    timestamp=timestamp,
                    metadata={
                        "symbol": (pair.get("baseToken") or {}).get("symbol"),
                        "buys_24h": buys,
                        "sells_24h": sells,
                        "direction": "accumulation" if buys > sells else "distribution",
                        "imbalance_pct": round(imbalance * 100, 1),
                    },
                )
            )

        return signals

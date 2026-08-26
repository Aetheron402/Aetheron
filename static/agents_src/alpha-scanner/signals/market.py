from typing import List
from datetime import datetime

from schemas.signal import Signal, SignalSource
from utils.market_data import fetch_pairs, token_key
from .base import BaseSignalGenerator


class MarketSignalGenerator(BaseSignalGenerator):
    """
    Price and volume behaviour, read from live pairs.

    This used to emit one Signal with value 0.8 for a key called ai_agents on
    every cycle, so the scanner ranked the same imaginary narrative whatever
    the market was doing.
    """

    source = SignalSource.MARKET

    def generate(self, timestamp: datetime) -> List[Signal]:
        signals: List[Signal] = []

        for pair in fetch_pairs():
            key = token_key(pair)
            if not key:
                continue

            change = (pair.get("priceChange") or {}).get("h24")
            volume = (pair.get("volume") or {}).get("h24") or 0
            liquidity = (pair.get("liquidity") or {}).get("usd") or 0
            if change is None or not volume:
                continue

            # Two things matter and they are different: how far it moved, and
            # how much turnover carried it. A large move on no volume is noise.
            move = min(abs(float(change)) / 50.0, 1.0)
            turnover = min(volume / max(liquidity, 1) / 5.0, 1.0)
            value = max(0.0, min(1.0, 0.6 * move + 0.4 * turnover))
            if value <= 0:
                continue

            # Deeper pairs are harder to push, so their reading is more
            # trustworthy than the same number on a thin one.
            confidence = max(0.3, min(0.95, 0.3 + min(liquidity / 500_000, 1.0) * 0.6))

            signals.append(
                Signal(
                    source=self.source,
                    key=key,
                    value=value,
                    confidence=confidence,
                    timestamp=timestamp,
                    metadata={
                        "symbol": (pair.get("baseToken") or {}).get("symbol"),
                        "price_change_24h_pct": float(change),
                        "volume_24h_usd": volume,
                        "liquidity_usd": liquidity,
                        "turnover_ratio": round(volume / max(liquidity, 1), 2),
                        "dex": pair.get("dexId"),
                    },
                )
            )

        return signals

"""
Data access layer.

This file used to hold ten functions that each returned a constant, and the
modules did not even call them: they inlined the same numbers again. The regime
score was therefore arithmetic over fixed values, identical in a crash and in a
melt-up, with nothing in the output saying so.

Everything here is now fetched from a public endpoint that needs no API key.
A source that does not respond returns None rather than a stand-in number, and
the module reading it says the input was unavailable and lowers its own
confidence. A reader who cannot tell a measurement from a default has no reason
to trust either.

Sources: CoinGecko for prices and stablecoin supply, alternative.me for the
Fear and Greed index, Binance for funding, positioning and candles, Deribit for
the DVOL implied volatility index.
"""

import statistics
import time
from typing import Any, Dict, Optional

import requests

TIMEOUT = 10
CACHE_SECONDS = 30           # one round of requests per cycle, not five

_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}


def _get(url: str, params: dict = None):
    try:
        response = requests.get(
            url, params=params or {}, timeout=TIMEOUT,
            headers={"User-Agent": "market-tracker"},
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _scale(value: float, low: float, high: float) -> float:
    """Map a real quantity onto the -1..1 range the modules score in."""
    if high == low:
        return 0.0
    return max(-1.0, min(1.0, 2 * (value - low) / (high - low) - 1))


def _fetch() -> Dict[str, Any]:
    """One reading of the market, shared by every module in a cycle."""
    if _CACHE["data"] and time.time() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["data"]

    values: Dict[str, Any] = {}
    sources, missing = [], []

    coins = _get("https://api.coingecko.com/api/v3/coins/markets", {
        "vs_currency": "usd",
        "ids": "bitcoin,ethereum,solana,tether,usd-coin",
        "price_change_percentage": "24h",
    })
    coins = {c["id"]: c for c in coins if isinstance(c, dict) and "id" in c} \
        if isinstance(coins, list) else None

    if coins:
        sources.append("CoinGecko")
        moves = [
            coins[c].get("price_change_percentage_24h")
            for c in ("bitcoin", "ethereum", "solana") if c in coins
        ]
        moves = [m for m in moves if isinstance(m, (int, float))]
        if moves:
            mean_move = statistics.fmean(moves)
            # A ten percent daily move either way is a full reading.
            values["crypto_momentum"] = _scale(mean_move, -10, 10)
            values["crypto_moves_pct"] = [round(m, 2) for m in moves]
            # Breadth: how much of the market is moving the same way.
            values["participation_rate"] = (
                sum(1 for m in moves if (m > 0) == (mean_move > 0)) / len(moves)
            )
            # Dispersion stands in for correlation. When everything moves
            # together the spread between the majors collapses.
            spread = statistics.pstdev(moves) if len(moves) > 1 else 0.0
            values["cross_asset_correlation"] = max(0.0, 1.0 - min(spread / 5.0, 1.0))

        peg = [
            coins[c].get("price_change_percentage_24h")
            for c in ("tether", "usd-coin") if c in coins
        ]
        peg = [p for p in peg if isinstance(p, (int, float))]
        if peg:
            # A stablecoin drifting off its peg is itself a liquidity signal.
            values["stablecoin_flow"] = _scale(statistics.fmean(peg), -0.5, 0.5)
        values["stablecoin_supply_usd"] = sum(
            coins[c].get("market_cap") or 0 for c in ("tether", "usd-coin") if c in coins
        )
    else:
        missing.append("prices and stablecoin supply (CoinGecko)")

    fng = _get("https://api.alternative.me/fng/", {"limit": 1})
    try:
        raw = float(fng["data"][0]["value"])
        values["fear_index"] = raw / 100.0
        values["fear_raw"] = raw
        sources.append("Fear and Greed index")
    except Exception:
        missing.append("sentiment (Fear and Greed index)")

    rates = []
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        data = _get("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})
        try:
            rates.append(float(data["lastFundingRate"]))
        except Exception:
            continue
    if rates:
        funding = statistics.fmean(rates)
        # Eight hourly funding of 0.05 percent is about the usual ceiling.
        values["funding_rate_pressure"] = _scale(funding, -0.0005, 0.0005)
        values["funding_rate"] = funding
        sources.append("Binance funding")
    else:
        missing.append("perpetual funding rates (Binance)")

    ls = _get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
              {"symbol": "BTCUSDT", "period": "1h", "limit": 1})
    try:
        ratio = float(ls[0]["longShortRatio"])
        # Parity is 1.0, and crowding shows up as distance from it.
        values["positioning_extreme"] = min(1.0, abs(ratio - 1.0))
        values["long_short_ratio"] = ratio
        sources.append("Binance positioning")
    except Exception:
        missing.append("positioning (Binance long/short ratio)")

    klines = _get("https://api.binance.com/api/v3/klines",
                  {"symbol": "BTCUSDT", "interval": "1h", "limit": 30})
    try:
        closes = [float(c[4]) for c in klines]
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        annualised = statistics.pstdev(returns) * (24 * 365) ** 0.5
        values["realized_vol"] = min(1.0, annualised / 1.5)
        values["realized_vol_annualised"] = annualised
        sources.append("Binance klines")
    except Exception:
        missing.append("realised volatility (Binance klines)")

    now_ms = int(time.time() * 1000)
    dvol = _get("https://www.deribit.com/api/v2/public/get_volatility_index_data", {
        "currency": "BTC", "start_timestamp": now_ms - 3_600_000,
        "end_timestamp": now_ms, "resolution": "3600",
    })
    try:
        index = float(dvol["result"]["data"][-1][4]) / 100.0
        values["implied_vol"] = min(1.0, index / 1.5)
        values["implied_vol_index"] = index
        sources.append("Deribit DVOL")
    except Exception:
        missing.append("implied volatility (Deribit DVOL)")

    # Broad equities have no keyless source, so this is reported absent rather
    # than stood in for. The risk module scores on crypto alone and says so.
    missing.append("equity momentum (no keyless source available)")

    result = {"values": values, "sources": sources, "missing": missing,
              "fetched_at": time.time()}
    _CACHE.update({"at": time.time(), "data": result})
    return result


def snapshot() -> Dict[str, Any]:
    """The full reading, including which sources answered and which did not."""
    return _fetch()


def _value(name: str) -> Optional[float]:
    return _fetch()["values"].get(name)


# The getters the modules call. Each returns None when its source did not
# answer, which the module reports rather than papering over.

def get_equity_momentum() -> Optional[float]:
    """Broad equity momentum. Always None: no keyless source exists."""
    return None


def get_crypto_momentum() -> Optional[float]:
    """Mean 24h move across BTC, ETH and SOL, scaled to -1..1."""
    return _value("crypto_momentum")


def get_realized_volatility() -> Optional[float]:
    """Annualised realised volatility from the last 30 hourly BTC candles."""
    return _value("realized_vol")


def get_implied_volatility() -> Optional[float]:
    """Deribit's DVOL index, the market's own forward volatility estimate."""
    return _value("implied_vol")


def get_funding_pressure() -> Optional[float]:
    """Mean perpetual funding across the majors. Positive means longs pay."""
    return _value("funding_rate_pressure")


def get_stablecoin_flows() -> Optional[float]:
    """Peg drift across USDT and USDC."""
    return _value("stablecoin_flow")


def get_cross_asset_correlation() -> Optional[float]:
    """Derived from how tightly the majors moved together over 24h."""
    return _value("cross_asset_correlation")


def get_participation_rate() -> Optional[float]:
    """Share of the majors moving with the market direction."""
    return _value("participation_rate")


def get_fear_index() -> Optional[float]:
    """Crypto Fear and Greed, 0 to 1. Higher is greedier."""
    return _value("fear_index")


def get_positioning_extremes() -> Optional[float]:
    """How far the long/short account ratio sits from parity."""
    return _value("positioning_extreme")

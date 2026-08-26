"""
Live pair data from DexScreener.

The three signal generators each returned one hardcoded Signal for a key
literally commented "placeholder", so the scanner ranked the same imaginary
narrative on every cycle regardless of the market.

This fetches real pairs once per cycle and shares them, so the market and
on-chain generators key on the same tokens. The fusion step requires that:
signals only combine when their keys match.

No API key is needed. A failed fetch returns nothing, and the generator says
the source was unavailable rather than inventing a signal.
"""

import time
from typing import Any, Dict, List

import requests

SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
TIMEOUT = 12
CACHE_SECONDS = 30

_CACHE: Dict[str, Any] = {"at": 0.0, "pairs": None}


def fetch_pairs(queries=("SOL/USDC", "BONK", "WIF", "JUP", "PYTH", "JTO"),
                chain: str = "solana",
                min_liquidity_usd: float = 25_000) -> List[Dict[str, Any]]:
    """
    Recently active pairs on the configured chain.

    Thin pairs are excluded: a token with a few thousand dollars of liquidity
    can be moved by a single trade, so its price change measures one buyer
    rather than a narrative.
    """
    if _CACHE["pairs"] is not None and time.time() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["pairs"]

    # Best pair per token, not the first one seen. A token usually trades on
    # several venues, and the quiet ones say nothing about it: reading the
    # deepest, busiest pair is what makes the signal about the token.
    best: Dict[str, Dict[str, Any]] = {}
    for query in queries:
        try:
            response = requests.get(
                SEARCH_URL, params={"q": query}, timeout=TIMEOUT,
                headers={"User-Agent": "alpha-scanner"},
            )
            response.raise_for_status()
            found = (response.json() or {}).get("pairs") or []
        except Exception:
            continue

        for pair in found:
            if not isinstance(pair, dict):
                continue
            if chain and pair.get("chainId") != chain:
                continue
            liquidity = (pair.get("liquidity") or {}).get("usd") or 0
            if liquidity < min_liquidity_usd:
                continue
            address = (pair.get("baseToken") or {}).get("address")
            if not address:
                continue
            volume = (pair.get("volume") or {}).get("h24") or 0
            incumbent = best.get(address)
            if incumbent is None or volume > ((incumbent.get("volume") or {}).get("h24") or 0):
                best[address] = pair

    pairs = sorted(
        best.values(),
        key=lambda p: (p.get("volume") or {}).get("h24") or 0,
        reverse=True,
    )
    _CACHE.update({"at": time.time(), "pairs": pairs})
    return pairs


def token_key(pair: Dict[str, Any]) -> str:
    """
    The identity both generators agree on.

    Symbols are not unique and are trivially spoofed, so the mint address is
    the key and the symbol is carried alongside for display.
    """
    return ((pair.get("baseToken") or {}).get("address") or "").strip()

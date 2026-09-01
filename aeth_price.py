"""
AETH price oracle.

Prices AETH in USDC so a component with a USDC price can be paid for in AETH.
This decides how many tokens a user must send, so a wrong number here either
overcharges customers or lets them underpay.

Sources, in order:

1. DexScreener, the deepest-liquidity pair where AETH is the base token.
   Free, keyless, and already used elsewhere in this codebase.
2. The pump.fun bonding curve, converted through SOL/USD. This is the path a
   freshly launched token needs, before any DEX pool exists for it.

Both the primary source and the fallback previously called Birdeye, so a
Birdeye outage took out both at once, a single point of failure presented as
two. Neither path depends on an API key now.
"""

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

AETH_MINT = os.getenv("AETH_MINT_ADDRESS")

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
PUMP_FUN_API = "https://frontend-api-v3.pump.fun/coins"

WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

# A price taken from a shallow pool can be moved cheaply, and moving it upward
# reduces the number of tokens a payment requires. Pools thinner than this are
# ignored rather than trusted.
MIN_LIQUIDITY_USD = float(os.getenv("AETH_MIN_POOL_LIQUIDITY_USD", "5000"))

# Guards against a malformed or manipulated quote producing an absurd number of
# tokens. These are deliberately wide; they catch nonsense, not volatility.
MIN_PLAUSIBLE_PRICE_USD = 1e-12
MAX_PLAUSIBLE_PRICE_USD = 1e6

# pump.fun reports reserves in lamports and, for its own launches, six decimals.
LAMPORTS_PER_SOL = 1_000_000_000
PUMPFUN_DEFAULT_DECIMALS = 6

# How long a fetched price is served without thinking about it.
CACHE_TTL = 60

# And how long past that it is still served immediately while a refresh runs
# behind it. A twenty second cache meant almost every real visitor paid for a
# round trip to the price source, three seconds of staring at a button, because
# it is rare for two people to ask inside the same twenty seconds. Serving the
# last known price while fetching the next one costs nobody anything: the quote
# a buyer is given is honoured for ten minutes regardless, so a price a minute
# old is already well inside the tolerance the settlement check allows.
STALE_TTL = int(os.getenv("AETH_PRICE_STALE_TTL", "900"))
_last_ts = 0
_cached_price_usdc = None


class AethPricingError(Exception):
    pass


def _sanity_check(price: float, source: str) -> float:
    if not price or price <= 0:
        raise AethPricingError(f"{source} returned a non-positive price: {price!r}")
    if not (MIN_PLAUSIBLE_PRICE_USD < price < MAX_PLAUSIBLE_PRICE_USD):
        raise AethPricingError(f"{source} returned an implausible price: {price}")
    return price


def _dexscreener_price_usd(mint: str) -> float:
    """
    USD price from the deepest pool in which `mint` is the base token.

    DexScreener returns any pair the mint appears in, including ones where it
    is the quote side and unrelated pairs that merely matched. Taking the first
    result would price a payment off the wrong token entirely, so candidates
    are filtered to base-token matches and the deepest pool wins.
    """
    if not mint:
        raise AethPricingError("No mint address supplied")

    try:
        response = requests.get(f"{DEXSCREENER_API}/tokens/{mint}", timeout=10)
    except requests.RequestException as exc:
        raise AethPricingError(f"DexScreener request failed: {exc}") from exc

    if response.status_code != 200:
        raise AethPricingError(f"DexScreener HTTP {response.status_code}")

    pairs = (response.json() or {}).get("pairs") or []

    best_price = None
    best_liquidity = 0.0
    for pair in pairs:
        if (pair.get("baseToken") or {}).get("address") != mint:
            continue
        try:
            price = float(pair.get("priceUsd"))
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0 and liquidity > best_liquidity:
            best_price, best_liquidity = price, liquidity

    if best_price is None:
        raise AethPricingError(f"No DexScreener pair prices {mint} as the base token")

    if best_liquidity < MIN_LIQUIDITY_USD:
        raise AethPricingError(
            f"Deepest pool for {mint} holds only ${best_liquidity:,.0f}, "
            f"below the ${MIN_LIQUIDITY_USD:,.0f} floor"
        )

    logger.info(
        "Priced %s at $%s via DexScreener (pool liquidity $%.0f)",
        mint,
        best_price,
        best_liquidity,
    )
    return _sanity_check(best_price, "DexScreener")


def _sol_price_usd() -> float:
    return _dexscreener_price_usd(WRAPPED_SOL_MINT)


def _pumpfun_price_usd(mint: str) -> float:
    """
    Price from the pump.fun bonding curve, converted through SOL/USD.

    Needed while a token is still on its bonding curve and has no DEX pool for
    DexScreener to quote.
    """
    if not mint:
        raise AethPricingError("No mint address supplied")

    try:
        response = requests.get(f"{PUMP_FUN_API}/{mint}", timeout=10)
    except requests.RequestException as exc:
        raise AethPricingError(f"pump.fun request failed: {exc}") from exc

    if response.status_code != 200:
        raise AethPricingError(f"pump.fun HTTP {response.status_code}")

    coin = response.json() or {}

    try:
        sol_reserves = float(coin["virtual_sol_reserves"])
        token_reserves = float(coin["virtual_token_reserves"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AethPricingError(f"pump.fun response missing reserve fields: {exc}") from exc

    if sol_reserves <= 0 or token_reserves <= 0:
        raise AethPricingError("pump.fun reported non-positive reserves")

    # Reserves arrive in base units. The previous implementation guessed at this
    # with a magnitude heuristic ("if v_sol > 1e6") and its own comment warned
    # the units might vary; the conversion is fixed and explicit now.
    decimals = coin.get("decimals")
    if not isinstance(decimals, int) or decimals < 0:
        decimals = PUMPFUN_DEFAULT_DECIMALS

    sol_amount = sol_reserves / LAMPORTS_PER_SOL
    token_amount = token_reserves / (10 ** decimals)
    price_in_sol = sol_amount / token_amount

    price_usd = price_in_sol * _sol_price_usd()
    logger.info("AETH priced at $%s via the pump.fun bonding curve", price_usd)
    return _sanity_check(price_usd, "pump.fun")


_refreshing = False


def _refresh_in_background():
    """
    Fetch the next price without holding anybody up.

    One at a time. Without the flag, a burst of requests against a stale cache
    would each start their own fetch, which is the hammering the cache exists
    to prevent.
    """
    global _refreshing
    if _refreshing:
        return
    _refreshing = True

    def run():
        global _refreshing
        try:
            get_aeth_price_usdc(force_refresh=True)
        except Exception as exc:
            logger.warning("Background AETH price refresh failed: %s", exc)
        finally:
            _refreshing = False

    threading.Thread(target=run, daemon=True, name="aeth-price").start()


def warm():
    """
    Fetch a price at startup so the first visitor does not pay for it.

    Failures are ignored: this is a courtesy, and the ordinary path still works
    if it does not happen.
    """
    _refresh_in_background()


def get_aeth_price_usdc(force_refresh: bool = False) -> float:
    """AETH price in USDC, cached briefly to avoid hammering the sources."""
    global _last_ts, _cached_price_usdc

    now = time.time()
    age = now - _last_ts if _cached_price_usdc is not None else None

    if not force_refresh and age is not None and age < CACHE_TTL:
        return _cached_price_usdc

    # Past its freshness but not past usefulness: hand back what we have and
    # fetch the next one behind the request, so nobody waits on the network.
    if not force_refresh and age is not None and age < STALE_TTL:
        _refresh_in_background()
        return _cached_price_usdc

    if not AETH_MINT:
        raise AethPricingError("Missing AETH_MINT_ADDRESS env var")

    errors = []
    for source, fetch in (
        ("DexScreener", lambda: _dexscreener_price_usd(AETH_MINT)),
        ("pump.fun", lambda: _pumpfun_price_usd(AETH_MINT)),
    ):
        try:
            price_usdc = fetch()
            break
        except Exception as exc:
            # Logged rather than swallowed: a silent fallback previously hid an
            # outage behind a price that still looked plausible.
            logger.warning("AETH pricing via %s failed: %s", source, exc)
            errors.append(f"{source}: {exc}")
    else:
        raise AethPricingError("All AETH price sources failed, " + "; ".join(errors))

    _cached_price_usdc = price_usdc
    _last_ts = now
    return price_usdc


def calculate_required_aeth(usdc_price: float, force_refresh: bool = False) -> float:
    """How many AETH cover a component priced in USDC."""
    if usdc_price <= 0:
        raise ValueError("usdc_price must be > 0")

    return usdc_price / get_aeth_price_usdc(force_refresh=force_refresh)

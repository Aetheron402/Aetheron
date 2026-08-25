import logging
import os
import time
import requests

logger = logging.getLogger(__name__)

AETH_MINT = os.getenv("AETH_MINT_ADDRESS")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")

PUMP_FUN_API = "https://frontend-api-v3.pump.fun/coins"

CACHE_TTL = 20
_last_ts = 0
_cached_price_usdc = None


class AethPricingError(Exception):
    pass


def _birdeye_price_usdc(token_address: str) -> float:
    """
    Birdeye price endpoint (works for SOL + SPL tokens).
    Docs: /defi/price?address=<mint>  (x-api-key required)
    """
    if not BIRDEYE_API_KEY:
        raise AethPricingError("Missing BIRDEYE_API_KEY env var")

    url = f"https://public-api.birdeye.so/defi/price?address={token_address}"
    headers = {"accept": "application/json", "x-api-key": BIRDEYE_API_KEY}

    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200:
        raise AethPricingError(f"Birdeye API error: {r.status_code} -> {r.text}")

    data = r.json()
    try:
        price = float(data["data"]["value"])
    except Exception:
        raise AethPricingError(f"Unexpected Birdeye response: {data}")

    if price <= 0:
        raise AethPricingError("Invalid price from Birdeye")

    return price


def _pumpfun_price_usdc_via_sol() -> float:
    """
    Fallback: compute AETH price using pump.fun virtual reserves * SOL/USD.
    WARNING: units/fields may vary; normalize cautiously.
    """
    if not AETH_MINT:
        raise AethPricingError("Missing AETH_MINT_ADDRESS env var")

    # 1) fetch pump.fun coin
    url = f"{PUMP_FUN_API}/{AETH_MINT}"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        raise AethPricingError(f"Pump.fun API error: {r.status_code} -> {r.text}")

    coin = r.json()

    # 2) read reserves
    try:
        v_sol = float(coin["virtual_sol_reserves"])
        v_tok = float(coin["virtual_token_reserves"])
    except Exception:
        raise AethPricingError(f"Missing pump.fun reserve fields in response: {coin}")

    if v_sol <= 0 or v_tok <= 0:
        raise AethPricingError("Invalid pump.fun reserves")

    # 3) BEST GUESS normalization:
    if v_sol > 1e6:  # heuristic threshold
        v_sol = v_sol / 1e9  # lamports -> SOL

    # token decimals (if present)
    decimals = coin.get("decimals")
    if isinstance(decimals, int) and decimals >= 0:
        v_tok = v_tok / (10 ** decimals)

    price_in_sol = v_sol / v_tok
    if price_in_sol <= 0:
        raise AethPricingError("Computed invalid price_in_sol")

    # 4) SOL price in USDC
    sol_usdc = _birdeye_price_usdc("So11111111111111111111111111111111111111112")
    return price_in_sol * sol_usdc


def get_aeth_price_usdc(force_refresh: bool = False) -> float:
    global _last_ts, _cached_price_usdc

    now = time.time()
    if (
        not force_refresh
        and _cached_price_usdc is not None
        and (now - _last_ts) < CACHE_TTL
    ):
        return _cached_price_usdc

    if not AETH_MINT:
        raise AethPricingError("Missing AETH_MINT_ADDRESS env var")

    # Primary: Birdeye direct token price
    try:
        price_usdc = _birdeye_price_usdc(AETH_MINT)
    except Exception as exc:
        # Fallback: pump.fun math. Logged because a silent fallback here hid
        # Birdeye outages behind a price that still looked plausible.
        logger.warning("Birdeye price lookup failed (%s); falling back to pump.fun", exc)
        price_usdc = _pumpfun_price_usdc_via_sol()

    if price_usdc <= 0:
        raise AethPricingError("Computed invalid AETH price in USDC")

    _cached_price_usdc = price_usdc
    _last_ts = now
    return price_usdc


def calculate_required_aeth(usdc_price: float, force_refresh: bool = False) -> float:
    if usdc_price <= 0:
        raise ValueError("usdc_price must be > 0")

    aeth_usdc = get_aeth_price_usdc(force_refresh=force_refresh)
    return usdc_price / aeth_usdc

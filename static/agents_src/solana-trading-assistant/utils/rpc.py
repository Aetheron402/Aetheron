# utils/rpc.py

import time

import requests
import logging


# SOLANA RPC CLIENT
class SolanaRPCClient:
    """Lightweight Solana RPC wrapper."""

    def __init__(self, rpc_url: str, timeout_seconds: int, logger: logging.Logger):
        self.rpc_url = rpc_url
        self.timeout = timeout_seconds
        self.logger = logger

    # Base RPC JSON-RPC request
    def _post(self, method: str, params: list):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

        try:
            response = requests.post(
                self.rpc_url, json=body, timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            self.logger.error(f"RPC call failed ({method}): {exc}")
            return None

    # Example RPC method: get account info
    def get_account_info(self, account):
        return self._post("getAccountInfo", [account, {"encoding": "jsonParsed"}])

    # Example: get supply of a token mint
    def get_token_supply(self, mint):
        return self._post("getTokenSupply", [mint])

    # Example: get token's decimals
    def get_token_decimals(self, mint):
        info = self.get_account_info(mint)
        try:
            return info["result"]["value"]["data"]["parsed"]["info"]["decimals"]
        except (KeyError, TypeError):
            # The account is missing, or not a parsed mint. Naming the errors
            # means a genuine fault still surfaces instead of reading as a
            # token without decimals.
            return None


# BIRDEYE API CLIENT
class BirdeyeClient:
    """Handles price, liquidity, volume, candles via Birdeye API."""

    def __init__(self, api_key: str, base_url: str, timeout_seconds: int, logger: logging.Logger):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.logger = logger
        self.headers = {
            "X-API-KEY": self.api_key,
            "x-chain": "solana",
            "accept": "application/json",
        }

    # Internal GET request
    def _get(self, endpoint: str, params: dict = None):
        url = f"{self.base_url}{endpoint}"

        try:
            resp = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("success") is False:
                self.logger.error(f"Birdeye error: {data}")
                return None

            return data.get("data", {})
        except Exception as exc:
            self.logger.error(f"Birdeye request failed ({endpoint}): {exc}")
            return None

    # Price
    def get_token_price(self, mint: str):
        data = self._get("/defi/price", {"address": mint})
        return {"price": data.get("value", 0)} if data else {"price": 0}

    # Liquidity
    def get_token_liquidity(self, mint: str):
        data = self._get("/defi/token_overview", {"address": mint})
        return {"liquidity": data.get("liquidity", 0)} if data else {"liquidity": 0}

    # Volume
    def get_token_volume(self, mint: str):
        data = self._get("/defi/token_overview", {"address": mint})
        return {"volume_24h": data.get("v24hUSD", 0)} if data else {"volume_24h": 0}

    # Candles (OHLCV)
    def get_token_candles(self, mint: str, timeframes: list):
        """
        Returns:
        {
            "5m": [candles],
            "15m": [candles],
            "1h": [candles]
        }
        Candle fields: o, h, l, c, v
        """
        result = {}
        for tf in timeframes:
            data = self._get(
                "/public/candles",
                {"address": mint, "type": tf}
            )
            result[tf] = data.get("items", []) if data else []
        return result

# DEXSCREENER CLIENT
class DexScreenerClient:
    """
    Price, liquidity, volume and trend for a Solana mint, without an API key.

    This is the default source. Birdeye retired the /public endpoints this
    agent was written against, and its current /defi routes need a key, so an
    agent shipped without one produced a stream of 404s and no analysis. Every
    buyer can run this one immediately.

    The same method names as BirdeyeClient, so nothing downstream changes.
    """

    BASE = "https://api.dexscreener.com/latest/dex/tokens/"

    def __init__(self, timeout_seconds: int, logger):
        self.timeout = timeout_seconds
        self.logger = logger
        self._cache = {}

    def _pair(self, mint: str):
        """The deepest pair for this mint: the one whose price means the most."""
        cached = self._cache.get(mint)
        if cached and time.time() - cached[0] < 20:
            return cached[1]

        try:
            response = requests.get(
                self.BASE + mint, timeout=self.timeout,
                headers={"User-Agent": "solana-trading-assistant"},
            )
            response.raise_for_status()
            pairs = (response.json() or {}).get("pairs") or []
        except Exception as exc:
            self.logger.error(f"DexScreener request failed for {mint}: {exc}")
            return None

        if not pairs:
            self.logger.warning(f"No DexScreener pairs found for {mint}.")
            return None

        best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        self._cache[mint] = (time.time(), best)
        return best

    def get_token_price(self, mint: str):
        pair = self._pair(mint)
        try:
            return {"price": float(pair.get("priceUsd"))} if pair else {"price": 0}
        except (TypeError, ValueError):
            return {"price": 0}

    def get_token_liquidity(self, mint: str):
        pair = self._pair(mint)
        return {"liquidity": (pair.get("liquidity") or {}).get("usd") or 0} if pair else {"liquidity": 0}

    def get_token_volume(self, mint: str):
        pair = self._pair(mint)
        return {"volume_24h": (pair.get("volume") or {}).get("h24") or 0} if pair else {"volume_24h": 0}

    def get_token_candles(self, mint: str, timeframes: list):
        """
        Two points per timeframe: where the price was, and where it is.

        DexScreener does not serve candles, but it does serve the percentage
        move over each window. Combined with the current price that gives the
        open and close exactly, which is all the trend calculation reads. Both
        numbers are measured; neither is filled in.
        """
        pair = self._pair(mint)
        if not pair:
            return {tf: [] for tf in timeframes}

        try:
            price = float(pair.get("priceUsd"))
        except (TypeError, ValueError):
            return {tf: [] for tf in timeframes}

        changes = pair.get("priceChange") or {}
        windows = {"5m": "m5", "15m": "m5", "1h": "h1", "6h": "h6", "24h": "h24", "1d": "h24"}

        result = {}
        for tf in timeframes:
            key = windows.get(tf)
            move = changes.get(key) if key else None
            if move is None:
                result[tf] = []
                continue
            try:
                pct = float(move) / 100.0
            except (TypeError, ValueError):
                result[tf] = []
                continue

            opened = price / (1 + pct) if (1 + pct) else price
            volume = (pair.get("volume") or {}).get("h24") or 0
            result[tf] = [{
                "o": opened, "h": max(opened, price),
                "l": min(opened, price), "c": price, "v": volume,
            }]
        return result

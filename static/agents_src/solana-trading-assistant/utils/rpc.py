# utils/rpc.py

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
        except:
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
            "accept": "application/json"
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
        data = self._get("/public/price", {"address": mint})
        return {"price": data.get("value", 0)} if data else {"price": 0}

    # Liquidity
    def get_token_liquidity(self, mint: str):
        data = self._get("/public/liquidity", {"address": mint})
        return {"liquidity": data.get("value", 0)} if data else {"liquidity": 0}

    # Volume
    def get_token_volume(self, mint: str):
        data = self._get("/public/volume", {"address": mint})
        return {"volume_24h": data.get("value", 0)} if data else {"volume_24h": 0}

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

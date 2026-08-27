import requests
import time
from typing import Any, Dict, List


class PumpFunClient:
    """
    Fetches newly launched pump.fun tokens and prepares opportunity dictionaries
    for the Pump.fun Launch Assistant.

    Responsibilities:
    - Query the pump.fun "recent tokens" endpoint
    - Normalize data fields into a consistent format
    - Optionally fetch extra info from Solana RPC (liquidity, market cap, etc.)
    """

    def __init__(self, rpc_config: Dict[str, Any], pumpfun_config: Dict[str, Any], logger):
        self.rpc_url = rpc_config["url"]
        self.timeout = rpc_config["timeout_seconds"]

        self.pump_api_url = pumpfun_config["api_url"]
        self.logger = logger

        self.logger.info(f"PumpFunClient initialized with API: {self.pump_api_url}")

    # JSON-RPC POST helper
    def rpc_post(self, method: str, params: list) -> Any:
        """
        Minimal JSON-RPC POST wrapper for optional extra Solana queries.
        """
        try:
            response = requests.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"RPC error calling {method}: {e}")
            return None

    # Fetch pump.fun launches
    def fetch_recent_launches(self) -> List[Dict[str, Any]]:
        """
        Queries pump.fun's public API for recently launched tokens.

        Expected API format (simplified):
        [
            {
                "mint": "...",
                "name": "...",
                "symbol": "...",
                "creator": "...",
                "bonding_curve": { "percent": float },
                "liquidity": { "sol": float },
                "market_cap_usd": float,
                "trades_5m": int,
                "renounced": bool,
                "liquidity_locked": bool,
                "mint_authority_disabled": bool,
                "program_id": "..."
            }
        ]

        This function MUST return a list of normalized token dictionaries.
        """

        # api.pump.fun answers 530 and has for some time, so this path
        # returned nothing whenever it was used. DexScreener's latest token
        # profiles carry the same launches and need no key.
        try:
            res = requests.get(self.pump_api_url, timeout=self.timeout,
                               headers={"User-Agent": "pumpfun-launcher"})
            res.raise_for_status()
            data = res.json()
            if isinstance(data, dict):
                data = data.get("data") or data.get("tokens") or []
        except Exception as e:
            self.logger.debug(f"Recent token feed unavailable: {e}")
            return []

        if not isinstance(data, list):
            return []

        normalized = []

        for item in data:
            try:
                token = {
                    "mint": item.get("mint"),
                    "name": item.get("name") or item.get("symbol") or "Unknown",
                    "creator": item.get("creator"),
                    "program_id": item.get("program_id"),

                    # Liquidity + bonding curve
                    "liquidity_sol": (item.get("liquidity") or {}).get("sol", 0),
                    "bonding_curve_percent": (item.get("bonding_curve") or {}).get("percent", 0),

                    # Market cap + activity
                    "market_cap_usd": item.get("market_cap_usd", None),
                    "trades_5m": item.get("trades_5m", 0),

                    # Safety flags
                    "renounced": item.get("renounced", False),
                    "liquidity_locked": item.get("liquidity_locked", False),
                    "mint_authority_disabled": item.get("mint_authority_disabled", False)
                }

                normalized.append(token)

            except Exception as parse_err:
                self.logger.error(f"Error parsing token: {parse_err}")

        return normalized

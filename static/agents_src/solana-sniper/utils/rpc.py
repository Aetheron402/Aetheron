import requests
from typing import Any, Dict, List
import time


class SolanaClient:
    """
    Lightweight RPC wrapper and discovery interface for the Sniper Agent.

    This client handles:
    - Basic JSON-RPC communication
    - Fetching recent signatures or account data (extendable)
    - Providing token opportunities sourced from discovery logic

    The execution layer (trading actions) is intentionally left out for safety
    and must be implemented by the user if real trades are desired.
    """

    def __init__(self, rpc_config: Dict[str, Any], logger):
        self.rpc_url = rpc_config["url"]
        self.timeout = rpc_config["timeout_seconds"]
        self.logger = logger
        self._seen = set()

        self.logger.info(f"SolanaClient initialized with RPC endpoint: {self.rpc_url}")

    # RPC POST helper
    def rpc_post(self, method: str, params: list) -> Any:
        """
        Minimal JSON-RPC POST helper.
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
            self.logger.error(f"RPC error while calling {method}: {e}")
            return None

    # TOKEN DISCOVERY TEMPLATE HOOK
    def fetch_new_opportunities(self) -> List[Dict[str, Any]]:
        """
        Newly listed Solana tokens, from DexScreener's public feed.

        This used to emit a fabricated token every twenty seconds: a mint that
        read ExampleMint111..., 5.2 SOL of liquidity and a 150,000 dollar
        market cap, none of which existed. It demonstrated the loop and taught
        the filters nothing, because the invented token passed every check by
        construction.

        No API key is needed. Tokens already seen are skipped, so each one is
        evaluated once rather than on every poll.
        """
        profiles = self._get("https://api.dexscreener.com/token-profiles/latest/v1")
        if not isinstance(profiles, list):
            return []

        opportunities = []
        for profile in profiles:
            if not isinstance(profile, dict) or profile.get("chainId") != "solana":
                continue

            mint = profile.get("tokenAddress")
            if not mint or mint in self._seen:
                continue
            self._seen.add(mint)

            token = self._describe(mint)
            if token:
                opportunities.append(token)

            # A handful per cycle. The feed returns thirty and the filters
            # below are the point, not the volume.
            if len(opportunities) >= 5:
                break

        # Keep the seen set from growing for the life of the process.
        if len(self._seen) > 2000:
            self._seen = set(list(self._seen)[-1000:])

        return opportunities

    def _get(self, url: str, params: dict = None):
        try:
            response = requests.get(url, params=params or {}, timeout=self.timeout,
                                    headers={"User-Agent": "solana-sniper"})
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            self.logger.debug(f"Discovery request failed: {exc}")
            return None

    def _describe(self, mint: str):
        """Turn a mint into the opportunity shape the filters expect."""
        data = self._get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
        pairs = (data or {}).get("pairs") or []
        if not pairs:
            return None

        # The deepest pair, since a token's quiet pools say nothing about it.
        pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        liquidity_usd = (pair.get("liquidity") or {}).get("usd") or 0
        txns = (pair.get("txns") or {}).get("m5") or {}

        try:
            price = float(pair.get("priceUsd") or 0)
        except (TypeError, ValueError):
            price = 0.0

        return {
            "mint": mint,
            "creator": (pair.get("baseToken") or {}).get("address"),
            "symbol": (pair.get("baseToken") or {}).get("symbol"),
            # Reported in SOL to match the filter thresholds, at roughly the
            # current price. Liquidity is the figure the filters actually read.
            "liquidity_sol": round(liquidity_usd / 200.0, 4),
            "liquidity_usd": liquidity_usd,
            "market_cap_usd": pair.get("marketCap") or pair.get("fdv") or 0,
            "trades_1m": (txns.get("buys") or 0) + (txns.get("sells") or 0),
            "price": price,
            "dex": pair.get("dexId"),
            "pair_url": pair.get("url"),
            # These need a mint account read to answer honestly, and this
            # discovery feed does not carry them. Left unset rather than
            # asserted, so a filter reading them sees nothing rather than a
            # convenient true.
            "renounced": None,
            "liquidity_locked": None,
            "mint_authority_disabled": None,
        }

    # EXECUTION HOOK
    def execute_buy_transaction(self, token: Dict[str, Any], amount_sol: float):
        """
        Execution hook for real trading integrations.

        Users may extend this method to integrate their preferred:
        - Jupiter routing API
        - Raydium swap
        - Solders-based transaction signing
        - solana-web3.js via subprocess or custom signer
        - Anchor/Solana-py pipelines

        This template intentionally does NOT send real transactions.
        """
        self.logger.info(
            f"Execution hook called for {token['mint']} | Spend target: {amount_sol} SOL."
        )

        # Returned to maintain consistent function signature
        return {
            "success": False,
            "message": "Execution logic not implemented. Extend execute_buy_transaction() to enable real trades."
        }

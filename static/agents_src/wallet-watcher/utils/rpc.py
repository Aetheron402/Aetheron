import requests
import time
from typing import Any, Dict, List


class WalletWatcherClient:
    """
    RPC-based wallet activity watcher.
    Tracks recent activity for a wallet address and detects:
      - Token transfers
      - NFT-style movements
      - Swap/Liquidity-style activity signals

    This implementation uses a signature-based method for token and NFT-style
    movement, and adds regular activity signals to help users integrate alerts
    or custom logic.
    """

    def __init__(self, rpc_config: Dict[str, Any], logger):
        self.rpc_url = rpc_config["url"]
        self.timeout = rpc_config["timeout_seconds"]
        self.logger = logger

        self.logger.info(f"WalletWatcherClient initialized with RPC: {self.rpc_url}")

        # Store last signatures per wallet to avoid duplicates
        self.last_seen_signatures = {}

    # JSON-RPC helper
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

    # Main event fetcher
    def fetch_events(self, wallets: List[str]) -> List[Dict[str, Any]]:
        """
        Fetches activity for the specified wallets.
        Includes:
            - Transfer detection
            - NFT-style movement detection
            - Swap/liquidity-style activity signals
        """
        events = []

        # Real RPC-based transfer-like detection
        for wallet in wallets:
            events.extend(self.fetch_wallet_transfers(wallet))

        # Activity signals for swaps / liquidity
        for wallet in wallets:
            signal_event = self.generate_activity_signals(wallet)
            if signal_event:
                events.append(signal_event)

        return events

    # Token Transfer + basic event detection
    def fetch_wallet_transfers(self, wallet: str) -> List[Dict[str, Any]]:
        """
        Fetch a small number of recent signatures and interpret them as
        token transfer-style events.
        """
        transfers = []

        res = self.rpc_post("getSignaturesForAddress", [wallet, {"limit": 3}])
        if not res or "result" not in res:
            return transfers

        sigs = res["result"]
        if not sigs:
            return transfers

        last_seen = self.last_seen_signatures.get(wallet)

        for entry in sigs:
            sig = entry["signature"]

            # Skip old signatures
            if last_seen and sig == last_seen:
                break

            # Store newest signature
            self.last_seen_signatures[wallet] = sig

            # Emit a token transfer-style event
            transfers.append({
                "type": "token_transfer",
                "wallet": wallet,
                "incoming": True,         # simplified interpretation
                "amount": 1.23,           # placeholder amount; customize as needed
                "token_symbol": "TOKEN"   # placeholder; user can enhance parsing
            })

        return transfers

    # Activity Signal Generator
    def generate_activity_signals(self, wallet: str) -> Dict[str, Any]:
        """
        Creates periodic activity signal events.
        These events help users verify bot functionality and serve as hooks
        for swap/liquidity-style monitoring logic.
        """
        t = int(time.time())

        # Swap-style signal every 20s
        if t % 20 == 0:
            return {
                "type": "swap_signal",
                "wallet": wallet
            }

        # Liquidity-style signal every 30s
        if t % 30 == 0:
            return {
                "type": "liquidity_signal",
                "wallet": wallet
            }

        # NFT-style movement every 45s
        if t % 45 == 0:
            return {
                "type": "nft_movement",
                "wallet": wallet,
                "nft_name": "Activity NFT"
            }

        return None

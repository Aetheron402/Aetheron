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
    def rpc_post(self, method: str, params: list, timeout: float = None) -> Any:
        """
        Minimal JSON-RPC POST helper.
        """
        try:
            response = requests.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout or self.timeout
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


    # Opening position
    def fetch_balances(self, wallet: str) -> Dict[str, Any]:
        """
        What the wallet holds right now: SOL, and every token with a balance.

        Read once at startup. Without it the agent printed that it was watching
        and then nothing at all until somebody moved funds, which on a quiet
        wallet is indistinguishable from a watcher that is not working.
        """
        # tokens stays None until the call succeeds, so "could not read" is
        # never reported as "holds nothing".
        out = {"sol": None, "tokens": None}

        res = self.rpc_post("getBalance", [wallet])
        try:
            out["sol"] = res["result"]["value"] / 1_000_000_000
        except (KeyError, TypeError):
            pass

        # A busy wallet can hold thousands of token accounts, and the public
        # endpoint is slow to assemble them, so this one call gets longer than
        # the rest.
        res = self.rpc_post("getTokenAccountsByOwner", [
            wallet,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ], timeout=max(self.timeout, 30))
        try:
            accounts = res["result"]["value"]
        except (KeyError, TypeError):
            return out

        out["tokens"] = []

        for account in accounts:
            try:
                info = account["account"]["data"]["parsed"]["info"]
                amount = (info.get("tokenAmount") or {}).get("uiAmount")
            except (KeyError, TypeError):
                continue
            # Empty token accounts are left behind by past activity and say
            # nothing about what is held now.
            if amount:
                out["tokens"].append({"mint": info.get("mint"), "amount": amount})

        out["tokens"].sort(key=lambda t: t["amount"], reverse=True)
        return out

    # Token Transfer + basic event detection
    def fetch_wallet_transfers(self, wallet: str) -> List[Dict[str, Any]]:
        """
        Read what actually moved in each new transaction touching this wallet.

        Signatures alone say that something happened, not what. Every balance
        below is the difference between the pre and post balances the validator
        recorded for this wallet in that transaction, so the amount and the
        mint are the real ones rather than a fixed example.
        """
        transfers = []

        res = self.rpc_post("getSignaturesForAddress", [wallet, {"limit": 5}])
        if not res or not res.get("result"):
            return transfers

        signatures = res["result"]
        last_seen = self.last_seen_signatures.get(wallet)
        fresh = []
        for entry in signatures:
            if last_seen and entry["signature"] == last_seen:
                break
            fresh.append(entry)

        # Newest first from the RPC, so remember it before walking oldest first.
        if signatures:
            self.last_seen_signatures[wallet] = signatures[0]["signature"]

        # On the first poll there is no baseline, and replaying the wallet's
        # recent history as if it just happened would be wrong.
        if last_seen is None:
            self.logger.info(
                f"Baseline set at {signatures[0]['signature'][:16]}... "
                f"for {wallet[:8]}..., watching for new activity"
            )
            return transfers

        for entry in reversed(fresh):
            if entry.get("err"):
                continue        # the transaction failed, nothing moved
            transfers.extend(self.describe_transaction(entry["signature"], wallet))

        return transfers

    def describe_transaction(self, signature: str, wallet: str) -> List[Dict[str, Any]]:
        """Turn one signature into the balance changes it caused for this wallet."""
        res = self.rpc_post("getTransaction", [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ])
        tx = (res or {}).get("result")
        if not tx:
            return []

        meta = tx.get("meta") or {}
        events = []

        # SPL tokens: match on owner, so a change to somebody else's account in
        # the same transaction is not attributed to this wallet.
        pre = {}
        for bal in meta.get("preTokenBalances") or []:
            if bal.get("owner") == wallet:
                amount = (bal.get("uiTokenAmount") or {}).get("uiAmount")
                pre[bal.get("accountIndex")] = (bal.get("mint"), amount or 0.0)

        for bal in meta.get("postTokenBalances") or []:
            if bal.get("owner") != wallet:
                continue
            index = bal.get("accountIndex")
            mint = bal.get("mint")
            after = (bal.get("uiTokenAmount") or {}).get("uiAmount") or 0.0
            before = pre.get(index, (mint, 0.0))[1]
            delta = after - before
            if abs(delta) < 1e-12:
                continue
            events.append({
                "type": "token_transfer",
                "wallet": wallet,
                "incoming": delta > 0,
                "amount": abs(delta),
                "token_symbol": mint,      # the real mint; resolve to a ticker if you want one
                "signature": signature,
            })

        # Native SOL, net of the fee the wallet paid when it signed.
        keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
        index = None
        for i, key in enumerate(keys):
            address = key.get("pubkey") if isinstance(key, dict) else key
            if address == wallet:
                index = i
                break

        pre_lamports = meta.get("preBalances") or []
        post_lamports = meta.get("postBalances") or []
        if index is not None and index < len(pre_lamports) and index < len(post_lamports):
            delta = (post_lamports[index] - pre_lamports[index]) / 1_000_000_000
            # Ignore movements that are only the transaction fee.
            if abs(delta) > 0.000_01:
                events.append({
                    "type": "sol_transfer",
                    "wallet": wallet,
                    "incoming": delta > 0,
                    "amount": abs(delta),
                    "token_symbol": "SOL",
                    "signature": signature,
                })

        return events

    # Kept so an existing loop calling this keeps working. It used to invent
    # swap, liquidity and NFT events on a timer, which produced convincing
    # looking alerts for a wallet that had done nothing at all.
    def generate_activity_signals(self, wallet: str) -> Dict[str, Any]:
        return None

"""
Burn on use: the accounting, and the proof.

The AETH taken as payment is destroyed rather than kept. This module is the
half of that which can live on a server safely.

It does not burn anything, and it holds no key. Burning requires signing a
transaction from the wallet that holds the tokens, and putting that key in the
web process would mean a compromise drains every payment ever taken, and would
make "this code cannot sign anything" false. The signing happens elsewhere,
from a wallet we control off this machine.

What this does instead is make the claim checkable. It knows how much AETH came
in, because every settled payment is already recorded with its amount and
currency. It knows how much went out, because each burn transaction is
submitted here and verified against the chain before it counts. A burn is only
recorded if Solana agrees a burn instruction for our mint actually executed in
that transaction, so the published figure is not a number somebody typed.

Anyone can recompute both sides from public data. That is the point.
"""

import os
import time

import requests

import ledger_utils

AETH_MINT = (os.getenv("AETH_MINT_ADDRESS") or "").strip() or None
SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Solana reports token amounts in base units. AETH uses six decimals, like USDC.
AETH_DECIMALS = int(os.getenv("AETH_DECIMALS", "6"))


class BurnVerificationError(Exception):
    """The chain did not confirm a burn of our mint in that transaction."""


def init_burns() -> None:
    """Create the burn table. Safe to call repeatedly."""
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS burns (
                tx_signature TEXT PRIMARY KEY,
                amount_raw {BIGINT} NOT NULL,
                block_time REAL,
                recorded_at REAL NOT NULL
            );
            """.replace("{BIGINT}", "BIGINT" if ledger_utils.USE_POSTGRES else "INTEGER")
        )


def _rpc(method: str, params: list, tries: int = 4):
    for attempt in range(tries):
        try:
            response = requests.post(
                SOLANA_RPC,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=40,
            ).json()
        except Exception:
            time.sleep(1 + attempt * 2)
            continue
        if "error" in response and response["error"].get("code") == 429:
            time.sleep(1 + attempt * 2)
            continue
        return response
    return {}


def _burned_in(tx: dict) -> int:
    """
    How much of our mint was destroyed by this transaction, in base units.

    Reads the parsed instructions rather than trusting a caller's figure, and
    counts only burns of our own mint. A transfer, even one to a dead address,
    does not count: the supply has to actually go down.
    """
    if not AETH_MINT:
        raise BurnVerificationError("No AETH_MINT_ADDRESS configured")

    message = (tx.get("transaction") or {}).get("message") or {}
    meta = tx.get("meta") or {}

    instructions = list(message.get("instructions") or [])
    for inner in meta.get("innerInstructions") or []:
        instructions.extend(inner.get("instructions") or [])

    total = 0
    for instruction in instructions:
        parsed = instruction.get("parsed")
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") not in ("burn", "burnChecked"):
            continue
        info = parsed.get("info") or {}
        if info.get("mint") != AETH_MINT:
            continue

        amount = info.get("amount")
        if amount is None:
            amount = (info.get("tokenAmount") or {}).get("amount")
        try:
            total += int(amount)
        except (TypeError, ValueError):
            continue

    return total


def verify_burn(tx_signature: str) -> dict:
    """
    Confirm on chain that this transaction burned our mint, and say how much.

    Raises rather than returning zero, so a mistyped or unrelated signature is
    never quietly recorded as a burn of nothing.
    """
    response = _rpc("getTransaction", [
        tx_signature,
        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
    ])
    tx = response.get("result")
    if not tx:
        raise BurnVerificationError(f"Transaction {tx_signature} not found on chain")

    if (tx.get("meta") or {}).get("err") is not None:
        raise BurnVerificationError("That transaction failed on chain")

    amount = _burned_in(tx)
    if amount <= 0:
        raise BurnVerificationError(
            f"No burn of {AETH_MINT} found in {tx_signature}"
        )

    return {"amount_raw": amount, "block_time": tx.get("blockTime")}


def record_burn(tx_signature: str) -> dict:
    """
    Verify a burn and add it to the ledger. Recording the same one twice is a
    no-op rather than double counting.
    """
    init_burns()
    verified = verify_burn(tx_signature)

    try:
        with ledger_utils._cursor(commit=True) as cur:
            cur.execute(
                ledger_utils._q(
                    "INSERT INTO burns (tx_signature, amount_raw, block_time, recorded_at) "
                    "VALUES (%s, %s, %s, %s);"
                ),
                (tx_signature, verified["amount_raw"], verified["block_time"], time.time()),
            )
    except ledger_utils.INTEGRITY_ERRORS:
        return {**verified, "already_recorded": True}

    return {**verified, "already_recorded": False}


def _scale(raw: int) -> float:
    return raw / (10 ** AETH_DECIMALS)


def summary() -> dict:
    """
    Taken in, burned, and the difference. Both sides are counted from records
    rather than kept as a running total, so the figures cannot drift.
    """
    init_burns()
    with ledger_utils._cursor() as cur:
        cur.execute(
            ledger_utils._q(
                "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM consumed_signatures "
                "WHERE currency = %s;"
            ),
            ("AETH",),
        )
        received_raw, payments = cur.fetchone()

        cur.execute("SELECT COALESCE(SUM(amount_raw), 0), COUNT(*) FROM burns;")
        burned_raw, burns = cur.fetchone()

    received_raw, burned_raw = int(received_raw or 0), int(burned_raw or 0)
    return {
        "mint": AETH_MINT,
        "received": _scale(received_raw),
        "burned": _scale(burned_raw),
        "outstanding": _scale(max(0, received_raw - burned_raw)),
        "payments": int(payments or 0),
        "burns": int(burns or 0),
    }


def recent(limit: int = 25) -> list:
    """The most recent verified burns, newest first."""
    init_burns()
    with ledger_utils._cursor() as cur:
        cur.execute(
            ledger_utils._q(
                "SELECT tx_signature, amount_raw, block_time FROM burns "
                "ORDER BY COALESCE(block_time, recorded_at) DESC LIMIT %s;"
            ),
            (limit,),
        )
        return [
            {"signature": row[0], "amount": _scale(int(row[1])), "block_time": row[2]}
            for row in cur.fetchall()
        ]

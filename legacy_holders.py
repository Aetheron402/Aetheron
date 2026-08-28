"""
The permanent discount for wallets that held the first AETH mint.

They bought a token that lost its value, and this is what can be given back
without breaking the fair launch: half price on everything, forever, for the
wallets that were there. No allocation, no airdrop, no supply to hand out.

Two things about this file matter more than the rest of it.

The list is fixed. Eligibility was decided by a snapshot taken before any of
this was announced, so nobody can buy the old token now to qualify, and nothing
about the list changes as a result of anything a caller does.

The discount is derived from the wallet, on the server, by one function that
both the quote and the settlement check call. If the price shown and the price
verified could ever disagree, an eligible buyer would be told one number and
charged against another: too high and their payment is rejected as short, too
low and anybody could pay half. There is exactly one source for it.

The wallet list lives in the database rather than in this repository. It is
public on chain either way, but a compiled list of 1,358 people's addresses is
not something to publish on our side.
"""

import math
import os

import ledger_utils

# Half price, forever, on every component and every agent template.
LEGACY_DISCOUNT = float(os.getenv("LEGACY_HOLDER_DISCOUNT", "0.5"))

# The mint that was held. Recorded so the snapshot can be audited later.
LEGACY_MINT = "DGNicx6qMPKSL1deR3fZfbHYjnm5ZJWmHNdY2NhDpump"

_cache: set | None = None


def init_legacy_holders() -> None:
    """Create the table. Safe to call repeatedly."""
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS legacy_holders (
                wallet TEXT PRIMARY KEY,
                first_held_at REAL
            );
            """
        )


def load(wallets: dict) -> int:
    """
    Replace the eligible set. Takes {wallet: first_held_unix_ts}.

    Used by the snapshot loader, never by a request.
    """
    global _cache
    init_legacy_holders()
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute("DELETE FROM legacy_holders;")
        for wallet, ts in wallets.items():
            cur.execute(
                ledger_utils._q(
                    "INSERT INTO legacy_holders (wallet, first_held_at) VALUES (%s, %s);"
                ),
                (wallet, ts),
            )
    _cache = None
    return len(wallets)


def _eligible_set() -> set:
    """
    The eligible wallets, read once per process.

    Cached because this is consulted on the hot path of every quote and every
    settlement, and the list only changes when a snapshot is deliberately
    reloaded, which is a restart anyway.
    """
    global _cache
    if _cache is None:
        try:
            init_legacy_holders()
            with ledger_utils._cursor() as cur:
                cur.execute("SELECT wallet FROM legacy_holders;")
                _cache = {row[0] for row in cur.fetchall()}
        except Exception:
            # A database that cannot be read must not hand out a discount, and
            # must not block a full price sale either.
            return set()
    return _cache


def is_legacy_holder(wallet: str | None) -> bool:
    if not wallet:
        return False
    return wallet.strip() in _eligible_set()


def price_for(wallet: str | None, base_price) -> float:
    """
    What this wallet actually pays.

    The single source of truth for the discount. Both the 402 quote and the
    on-chain amount check call this, so the number a buyer is shown and the
    number their transfer is measured against are the same by construction.

    Rounded down to the cent that USDC settles at. Down rather than nearest, so
    the discount is never quietly less than the half it promises: 4.99 becomes
    2.49, not the 2.50 that rounding to nearest produced.

    Floored at a cent, because a price of zero makes the expected amount zero,
    and the settlement check rejects that outright. At today's prices it cannot
    happen, but a cheaper component later would have locked its buyers out.
    """
    price = float(base_price)
    if not is_legacy_holder(wallet):
        return price
    return max(0.01, math.floor(price * (1.0 - LEGACY_DISCOUNT) * 100) / 100)

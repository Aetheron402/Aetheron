"""
Locked AETH quotes.

A USDC price is exact: a component costs 0.25 and settlement expects 0.25. An
AETH price is a conversion, and the rate moves.

That broke real payments. The shop quoted a number of AETH, the buyer sent
exactly that, and settlement then recomputed the requirement against a fresh
rate. AETH is on a bonding curve where every trade moves the price, so by the
time a person had sent a transfer and pressed the button, the rate had usually
moved. A one percent tolerance does not cover that. If the price ticked down in
between, the requirement went up and a correct payment was rejected as short.
The buyer had paid and had nothing to show for it.

So the quote is now a promise. When a price is quoted it is written down, and
settlement honours what the buyer was actually told rather than working out a
new number behind them. Quotes are held for a few minutes, which is long enough
to send a transfer and short enough that a stale rate cannot be farmed.

The quote is generated and stored server side, keyed to the wallet and the
component. Nothing a caller sends decides what they owe.
"""

import os
import time

import ledger_utils

# Long enough to open a wallet, confirm and come back. Short enough that a
# quote taken during a dip cannot be held until it is worth using.
QUOTE_TTL_SECONDS = int(os.getenv("AETH_QUOTE_TTL", "600"))


def init_quotes() -> None:
    """Create the quote table. Safe to call repeatedly."""
    bigint = "BIGINT" if ledger_utils.USE_POSTGRES else "INTEGER"
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS aeth_quotes (
                wallet TEXT NOT NULL,
                component TEXT NOT NULL,
                amount_raw {bigint} NOT NULL,
                usd REAL NOT NULL,
                issued_at REAL NOT NULL,
                PRIMARY KEY (wallet, component)
            );
            """
        )


def record(wallet: str | None, component: str, amount_raw: int, usd: float) -> None:
    """
    Remember what this wallet was told a component costs in AETH.

    Overwrites any previous quote for the same pair, so the most recent number
    a buyer saw is the one that counts. Without a wallet there is nobody to
    hold the promise to, so nothing is stored.
    """
    if not wallet or amount_raw <= 0:
        return

    init_quotes()
    now = time.time()
    with ledger_utils._cursor(commit=True) as cur:
        if ledger_utils.USE_POSTGRES:
            cur.execute(
                ledger_utils._q(
                    "INSERT INTO aeth_quotes (wallet, component, amount_raw, usd, issued_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (wallet, component) DO UPDATE SET "
                    "amount_raw = EXCLUDED.amount_raw, usd = EXCLUDED.usd, "
                    "issued_at = EXCLUDED.issued_at;"
                ),
                (wallet, component, int(amount_raw), float(usd), now),
            )
        else:
            cur.execute(
                "INSERT OR REPLACE INTO aeth_quotes "
                "(wallet, component, amount_raw, usd, issued_at) VALUES (?, ?, ?, ?, ?);",
                (wallet, component, int(amount_raw), float(usd), now),
            )


def live(wallet: str | None, component: str) -> int | None:
    """
    The AETH amount this wallet was quoted, if it has not expired.

    Returns None when there is nothing to honour, and the caller prices against
    the current rate instead.
    """
    if not wallet:
        return None

    try:
        init_quotes()
        with ledger_utils._cursor() as cur:
            cur.execute(
                ledger_utils._q(
                    "SELECT amount_raw, issued_at FROM aeth_quotes "
                    "WHERE wallet = %s AND component = %s;"
                ),
                (wallet, component),
            )
            row = cur.fetchone()
    except Exception:
        # A quote lookup failing must not stop a sale. Falling back to the live
        # rate is the behaviour that existed before quotes were held at all.
        return None

    if not row:
        return None

    amount_raw, issued_at = int(row[0]), float(row[1])
    if time.time() - issued_at > QUOTE_TTL_SECONDS:
        return None
    return amount_raw


def clear(wallet: str | None, component: str) -> None:
    """Drop a quote once it has been settled, so it cannot be reused later."""
    if not wallet:
        return
    try:
        with ledger_utils._cursor(commit=True) as cur:
            cur.execute(
                ledger_utils._q(
                    "DELETE FROM aeth_quotes WHERE wallet = %s AND component = %s;"),
                (wallet, component),
            )
    except Exception:
        pass


def purge_expired() -> int:
    """Remove quotes nobody can use any more."""
    init_quotes()
    cutoff = time.time() - QUOTE_TTL_SECONDS
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q("DELETE FROM aeth_quotes WHERE issued_at < %s;"), (cutoff,))
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

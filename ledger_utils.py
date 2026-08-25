import os
import time
from contextlib import contextmanager

# The ledger runs on Postgres in production (Railway sets DB_HOST) and on a
# local SQLite file otherwise, so a fresh clone runs with no database to set up.
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

USE_POSTGRES = bool(DB_HOST)
SQLITE_PATH = os.getenv("LEDGER_DB_PATH", "ledger.db")

if USE_POSTGRES:
    import psycopg2
else:
    import sqlite3


def _conn():
    """Open a new ledger connection against whichever backend is configured."""
    if USE_POSTGRES:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
        )
    return sqlite3.connect(SQLITE_PATH)


def _q(sql: str) -> str:
    """Statements are written with Postgres placeholders; SQLite wants '?'."""
    return sql if USE_POSTGRES else sql.replace("%s", "?")


@contextmanager
def _cursor(commit: bool = False):
    """
    Yield a cursor and guarantee the connection is closed.

    Replay protection deliberately raises on a duplicate signature, so the
    error path here is routine rather than exceptional. Closing only on the
    success path leaked a connection every time it fired — locking SQLite
    outright, and exhausting the Postgres pool over time.
    """
    conn = _conn()
    try:
        yield conn.cursor()
        if commit:
            conn.commit()
    finally:
        conn.close()


def backend_name() -> str:
    """Which store is in use — surfaced by /api/status for diagnostics."""
    return "postgres" if USE_POSTGRES else f"sqlite ({SQLITE_PATH})"


def row_to_dict(row):
    """Convert a ledger row tuple into a dictionary for JSON responses."""
    return {
        "id": row[0],
        "asset_id": row[1],
        "wallet": row[2],
        "tx_signature": row[3],
        "component": row[4],
        "price": row[5],
        "currency": row[6],
        "status": row[7],
        "filename": row[8],
        "timestamp": row[9],
    }


def get_by_tx_sig(tx_sig):
    """Return ledger rows matching a transaction signature."""
    with _cursor() as cur:
        cur.execute(
            _q(
                """
                SELECT
                    id, asset_id, wallet, tx_signature,
                    component, price, currency, status, filename, timestamp
                FROM ledger
                WHERE tx_signature = %s
                LIMIT 1;
                """
            ),
            (tx_sig,),
        )
        row = cur.fetchone()
    return row


def init_ledger():
    primary_key = (
        "id SERIAL PRIMARY KEY"
        if USE_POSTGRES
        else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS ledger (
                {primary_key},
                asset_id TEXT NOT NULL,
                wallet TEXT,
                tx_signature TEXT,
                component TEXT NOT NULL,
                price REAL,
                currency TEXT DEFAULT 'USDC',
                status TEXT NOT NULL,
                filename TEXT,
                timestamp REAL NOT NULL
            );
            """
        )
        conn.commit()

        # Replay protection: one successful charge per transaction signature.
        # Both engines support partial unique indexes and IF NOT EXISTS here.
        # Committed separately because a failed DDL statement aborts the whole
        # transaction on Postgres, which would roll the table creation back too.
        try:
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_tx_sig_success
                ON ledger (tx_signature)
                WHERE status = 'success';
                """
            )
            conn.commit()
        except Exception as exc:
            print("Ledger index creation skipped:", exc)
            conn.rollback()
    finally:
        conn.close()


def add_entry(*, asset_id, wallet, tx_sig, component, price, currency, status, filename):
    """Insert one ledger row including payment currency."""
    with _cursor(commit=True) as cur:
        cur.execute(
            _q(
                """
                INSERT INTO ledger
                    (asset_id, wallet, tx_signature, component, price, currency, status, filename, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """
            ),
            (asset_id, wallet, tx_sig, component, float(price), currency, status, filename, time.time()),
        )


def get_by_wallet_paginated(wallet, limit=5, offset=0):
    """Return rows for a wallet with limit/offset."""
    with _cursor() as cur:
        cur.execute(
            _q(
                """
                SELECT
                    id, asset_id, wallet, tx_signature,
                    component, price, currency, status, filename, timestamp
                FROM ledger
                WHERE wallet = %s
                ORDER BY timestamp DESC
                LIMIT %s OFFSET %s;
                """
            ),
            (wallet, limit, offset),
        )
        rows = cur.fetchall()
    return rows


def get_wallet_entry_count(wallet):
    """Return how many rows exist for a wallet."""
    with _cursor() as cur:
        cur.execute(_q("SELECT COUNT(*) FROM ledger WHERE wallet = %s;"), (wallet,))
        total = cur.fetchone()[0]
    return total


def get_recent(limit=50):
    """Return most recent rows overall."""
    with _cursor() as cur:
        cur.execute(
            _q(
                """
                SELECT
                    id, asset_id, wallet, tx_signature,
                    component, price, currency, status, filename, timestamp
                FROM ledger
                ORDER BY timestamp DESC
                LIMIT %s;
                """
            ),
            (limit,),
        )
        rows = cur.fetchall()
    return rows


def get_by_wallet(wallet, limit=100):
    """Return recent rows for a wallet."""
    with _cursor() as cur:
        cur.execute(
            _q(
                """
                SELECT
                    id, asset_id, wallet, tx_signature,
                    component, price, currency, status, filename, timestamp
                FROM ledger
                WHERE wallet = %s
                ORDER BY timestamp DESC
                LIMIT %s;
                """
            ),
            (wallet, limit),
        )
        rows = cur.fetchall()
    return rows


def finalize_asset(asset_id: str, filename: str):
    """
    Mark an asset as successfully generated.
    Sets filename and flips status from 'pending' → 'success'.
    """
    with _cursor(commit=True) as cur:
        cur.execute(
            _q(
                """
                UPDATE ledger
                SET filename = %s,
                    status = 'success'
                WHERE asset_id = %s
                  AND status = 'pending';
                """
            ),
            (filename, asset_id),
        )

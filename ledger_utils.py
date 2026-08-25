import os
import time
import psycopg2

# Read DB credentials from environment variables
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")


def _conn():
    """Open a new PostgreSQL connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )


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
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 
            id, asset_id, wallet, tx_signature,
            component, price, currency, status, filename, timestamp
        FROM ledger
        WHERE tx_signature = %s
        LIMIT 1;
        """,
        (tx_sig,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def init_ledger():
    conn = _conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger (
            id SERIAL PRIMARY KEY,
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

    # SAFE index creation
    try:
        cur.execute(
            """
            CREATE UNIQUE INDEX idx_ledger_tx_sig_success
            ON ledger (tx_signature)
            WHERE status = 'success';
            """
        )
    except psycopg2.errors.UniqueViolation as e:
        print("Ledger index already exists or duplicates present, skipping:", e)
        conn.rollback()
    except Exception as e:
        print("Ledger index creation skipped:", e)
        conn.rollback()

    conn.commit()
    conn.close()


def add_entry(*, asset_id, wallet, tx_sig, component, price, currency, status, filename):
    """Insert one ledger row including payment currency."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ledger
            (asset_id, wallet, tx_signature, component, price, currency, status, filename, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
        """,
        (asset_id, wallet, tx_sig, component, float(price), currency, status, filename, time.time()),
    )
    conn.commit()
    conn.close()


def get_by_wallet_paginated(wallet, limit=5, offset=0):
    """Return rows for a wallet with limit/offset."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 
            id, asset_id, wallet, tx_signature, 
            component, price, currency, status, filename, timestamp
        FROM ledger
        WHERE wallet = %s
        ORDER BY timestamp DESC
        LIMIT %s OFFSET %s;
        """,
        (wallet, limit, offset),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_wallet_entry_count(wallet):
    """Return how many rows exist for a wallet."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ledger WHERE wallet = %s;", (wallet,))
    total = cur.fetchone()[0]
    conn.close()
    return total


def get_recent(limit=50):
    """Return most recent rows overall."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 
            id, asset_id, wallet, tx_signature, 
            component, price, currency, status, filename, timestamp
        FROM ledger
        ORDER BY timestamp DESC
        LIMIT %s;
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_by_wallet(wallet, limit=100):
    """Return recent rows for a wallet."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 
            id, asset_id, wallet, tx_signature, 
            component, price, currency, status, filename, timestamp
        FROM ledger
        WHERE wallet = %s
        ORDER BY timestamp DESC
        LIMIT %s;
        """,
        (wallet, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def finalize_asset(asset_id: str, filename: str):
    """
    Mark an asset as successfully generated.
    Sets filename and flips status from 'pending' → 'success'.
    """
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE ledger
        SET filename = %s,
            status = 'success'
        WHERE asset_id = %s
          AND status = 'pending';
        """,
        (filename, asset_id),
    )
    conn.commit()
    conn.close()

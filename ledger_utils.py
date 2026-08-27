import os
import time
from contextlib import contextmanager

# The ledger runs on Postgres in production and on a local SQLite file
# otherwise, so a fresh clone runs with no database to set up.
#
# DATABASE_URL is the form to prefer. Railway publishes one per database, so
# wiring it up is a single reference rather than five variables that all have to
# agree with each other. The individual DB_* settings stay supported for hosts
# that do not hand out a URL.
DATABASE_URL = os.getenv("DATABASE_URL")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

USE_POSTGRES = bool(DATABASE_URL or DB_HOST)
SQLITE_PATH = os.getenv("LEDGER_DB_PATH", "ledger.db")

if USE_POSTGRES:
    import psycopg2
    import psycopg2.errors

    INTEGRITY_ERRORS = (psycopg2.errors.UniqueViolation, psycopg2.IntegrityError)
    BIGINT = "BIGINT"
else:
    import sqlite3

    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
    BIGINT = "INTEGER"


def _conn():
    """Open a new ledger connection against whichever backend is configured."""
    if USE_POSTGRES:
        if DATABASE_URL:
            return psycopg2.connect(DATABASE_URL)
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
    success path leaked a connection every time it fired, locking SQLite
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
    """Which store is in use, surfaced by /api/status for diagnostics."""
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

        # Every transaction signature ever credited, partial payments included.
        # The primary key IS the replay check: claiming a signature is an
        # INSERT that fails if it was already claimed, so two concurrent
        # requests cannot both be credited for the same transfer.
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS consumed_signatures (
                tx_signature TEXT PRIMARY KEY,
                wallet TEXT,
                component TEXT,
                amount {BIGINT} NOT NULL,
                currency TEXT,
                consumed_at REAL NOT NULL
            );
            """
        )

        # Partial payments live here rather than in process memory, so they
        # survive a deploy and are shared by every web and worker process.
        # Amounts are integer base units, never floats, which lose precision.
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS partial_payments (
                wallet TEXT NOT NULL,
                component TEXT NOT NULL,
                currency TEXT NOT NULL,
                paid {BIGINT} NOT NULL,
                required {BIGINT} NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (wallet, component, currency)
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


def consume_signature(tx_sig, wallet, component, amount, currency) -> bool:
    """
    Atomically claim a transaction signature, returning False if it was already
    claimed.

    The INSERT is the check. Reading first and writing later left a window in
    which two concurrent requests both saw an unused signature, and partial
    payments were never recorded at all, so the same small transfer could be
    replayed until it accumulated past the price.
    """
    try:
        with _cursor(commit=True) as cur:
            cur.execute(
                _q(
                    """
                    INSERT INTO consumed_signatures
                        (tx_signature, wallet, component, amount, currency, consumed_at)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """
                ),
                (tx_sig, wallet, component, int(amount), currency, time.time()),
            )
        return True
    except INTEGRITY_ERRORS:
        return False


def signature_already_used(tx_sig) -> bool:
    """Read-only companion to consume_signature, for reporting."""
    with _cursor() as cur:
        cur.execute(
            _q("SELECT 1 FROM consumed_signatures WHERE tx_signature = %s LIMIT 1;"),
            (tx_sig,),
        )
        return cur.fetchone() is not None


def get_partial(wallet, component, currency):
    """Return the accumulated partial payment, or None."""
    with _cursor() as cur:
        cur.execute(
            _q(
                """
                SELECT wallet, component, currency, paid, required, updated_at
                FROM partial_payments
                WHERE wallet = %s AND component = %s AND currency = %s;
                """
            ),
            (wallet, component, currency),
        )
        row = cur.fetchone()

    if not row:
        return None
    return {
        "wallet": row[0],
        "component": row[1],
        "currency": row[2],
        "paid": int(row[3]),
        "required": int(row[4]),
        "updated_at": row[5],
    }


def add_partial(wallet, component, currency, amount, required):
    """Add to a wallet's running total for one component, and return it."""
    existing = get_partial(wallet, component, currency)
    total = (existing["paid"] if existing else 0) + int(amount)

    with _cursor(commit=True) as cur:
        if existing:
            cur.execute(
                _q(
                    """
                    UPDATE partial_payments
                    SET paid = %s, required = %s, updated_at = %s
                    WHERE wallet = %s AND component = %s AND currency = %s;
                    """
                ),
                (total, int(required), time.time(), wallet, component, currency),
            )
        else:
            cur.execute(
                _q(
                    """
                    INSERT INTO partial_payments
                        (wallet, component, currency, paid, required, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """
                ),
                (wallet, component, currency, total, int(required), time.time()),
            )

    return {
        "wallet": wallet,
        "component": component,
        "currency": currency,
        "paid": total,
        "required": int(required),
    }


def clear_partial(wallet, component, currency):
    """Drop a wallet's partial balance once the component is fully paid."""
    with _cursor(commit=True) as cur:
        cur.execute(
            _q(
                """
                DELETE FROM partial_payments
                WHERE wallet = %s AND component = %s AND currency = %s;
                """
            ),
            (wallet, component, currency),
        )


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


# ── free views: examples and agent previews ─────────────────────────────────
# A wallet gets a small allowance of each before buying anything. The allowance
# is per category across the whole shop rather than per item, so choosing what
# to spend it on is a real decision. Reports and agent runs are counted
# separately, since they are different products.

EXAMPLE_ALLOWANCE = int(os.getenv("EXAMPLE_ALLOWANCE", "3"))
PREVIEW_ALLOWANCE = int(os.getenv("PREVIEW_ALLOWANCE", "3"))

ALLOWANCES = {"example": EXAMPLE_ALLOWANCE, "preview": PREVIEW_ALLOWANCE}


def init_examples():
    """Create the view table. Safe to call repeatedly."""
    with _cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS example_views (
                wallet TEXT NOT NULL,
                slug TEXT NOT NULL,
                viewed_at REAL NOT NULL,
                PRIMARY KEY (wallet, slug)
            );
            """
        )


def _key(kind: str, slug: str) -> str:
    """Namespaced so a report and an agent of the same name cannot collide."""
    return f"{kind}:{slug}"


def views_seen(wallet: str, kind: str = "example") -> list:
    """Which items of this kind the wallet has already opened."""
    init_examples()
    prefix = f"{kind}:"
    with _cursor() as cur:
        cur.execute(
            _q("SELECT slug FROM example_views WHERE wallet = %s AND slug LIKE %s;"),
            (wallet, prefix + "%"),
        )
        return [row[0][len(prefix):] for row in cur.fetchall()]


def claim_view(wallet: str, slug: str, kind: str = "example") -> dict:
    """
    Spend one of this wallet's free views, or report why it cannot.

    Reopening something already claimed is free: the allowance limits how many
    different items a wallet sees, not how many times it returns to the ones it
    chose.
    """
    allowance = ALLOWANCES.get(kind, EXAMPLE_ALLOWANCE)
    seen = views_seen(wallet, kind)

    if slug in seen:
        return {"allowed": True, "remaining": allowance - len(seen),
                "already_seen": True, "allowance": allowance}

    if len(seen) >= allowance:
        return {"allowed": False, "remaining": 0, "already_seen": False,
                "allowance": allowance, "seen": seen}

    try:
        with _cursor(commit=True) as cur:
            cur.execute(
                _q("INSERT INTO example_views (wallet, slug, viewed_at) VALUES (%s, %s, %s);"),
                (wallet, _key(kind, slug), time.time()),
            )
    except INTEGRITY_ERRORS:
        # Two tabs claiming at once. The row exists either way.
        pass

    return {"allowed": True, "remaining": allowance - len(seen) - 1,
            "already_seen": False, "allowance": allowance}


# Kept for the report path, which reads more clearly with the specific names.
def examples_seen(wallet: str) -> list:
    return views_seen(wallet, "example")


def claim_example(wallet: str, slug: str) -> dict:
    return claim_view(wallet, slug, "example")

"""
Where generated reports live.

Two backends, chosen by configuration rather than by a flag:

- **Cloudflare R2**, when the R2 variables are set. Files are served straight
  from the public bucket and never touch this process again.
- **The ledger database**, otherwise. Reports are a few hundred kilobytes and
  both the web process and the workers already hold a connection to it.

The database backend exists because a container filesystem is not storage. On
Railway the disk is replaced on every deploy, so a report written there is gone
the next time you ship, and the customer who paid for it has nothing to
download. Postgres survives deploys and is reachable from both processes, which
is the actual requirement.
"""

import logging
import os
import time

import ledger_utils
from ledger_utils import _cursor, _q, USE_POSTGRES

logger = logging.getLogger(__name__)

BLOB = "BYTEA" if USE_POSTGRES else "BLOB"

# Reports are text and charts. Anything dramatically larger than this is a bug
# rather than a big report, and the database is the wrong home for it.
MAX_ASSET_BYTES = int(os.getenv("MAX_ASSET_BYTES", str(20 * 1024 * 1024)))

_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html",
    "md": "text/markdown",
    "txt": "text/plain",
    "zip": "application/zip",
}


def content_type_for(filename: str) -> str:
    _, _, ext = filename.lower().rpartition(".")
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


def using_r2() -> bool:
    return bool(os.getenv("R2_PUBLIC_BASE") and os.getenv("R2_BUCKET_NAME"))


def backend_name() -> str:
    if using_r2():
        return f"r2 ({os.getenv('R2_BUCKET_NAME')})"
    return f"database ({ledger_utils.backend_name()})"


_initialised_for = None


def _target() -> str:
    """Which database the table would be created in, read live."""
    return "postgres" if ledger_utils.USE_POSTGRES else ledger_utils.SQLITE_PATH


def init_storage():
    """
    Create the asset table. Safe to call repeatedly.

    Every entry point calls this so none of them depends on being reached
    second, but the statement only needs to run once per process. Opening a
    connection to a hosted Postgres costs around a second, and this was paying
    that on every store, fetch and status probe in order to re-run a CREATE
    TABLE that had already succeeded.

    Remembering which database it ran against, rather than just that it ran,
    means pointing at a different one re-creates the table there instead of
    assuming the previous one's schema carries over.
    """
    global _initialised_for
    if using_r2() or _initialised_for == _target():
        return
    with _cursor(commit=True) as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS assets (
                filename TEXT PRIMARY KEY,
                content {BLOB} NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
    _initialised_for = _target()


def store_asset(data: bytes, filename: str) -> str:
    """
    Persist a generated report and return the URL it can be fetched from.

    Returns an R2 public URL when R2 is configured, and this service's own
    /download route otherwise, so callers never have to know which is in use.
    """
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError(
            f"{filename} is {len(data)} bytes, over the {MAX_ASSET_BYTES} limit"
        )

    if using_r2():
        from r2_client import r2_upload_bytes
        return r2_upload_bytes(data, filename)

    init_storage()
    with _cursor(commit=True) as cur:
        cur.execute(
            _q(
                """
                INSERT INTO assets (filename, content, content_type, size_bytes, created_at)
                VALUES (%s, %s, %s, %s, %s);
                """
            ),
            (filename, data, content_type_for(filename), len(data), time.time()),
        )

    logger.info("Stored %s (%d bytes) in the database", filename, len(data))
    return f"/download/{filename}"


def fetch_asset(filename: str):
    """Return (bytes, content_type) for a stored report, or None."""
    if using_r2():
        return None  # R2 serves these directly; the caller proxies instead.

    init_storage()
    with _cursor() as cur:
        cur.execute(
            _q("SELECT content, content_type FROM assets WHERE filename = %s;"),
            (filename,),
        )
        row = cur.fetchone()

    if not row:
        return None
    # psycopg2 hands back a memoryview for BYTEA; sqlite3 gives bytes.
    return bytes(row[0]), row[1]


def purge_expired(max_age_days: int = 30) -> int:
    """
    Drop reports older than the retention window.

    Without this the table grows for the life of the service. Customers keep
    their downloaded copy; this is a cache of deliverables, not an archive.
    """
    if using_r2():
        return 0

    cutoff = time.time() - max_age_days * 86400
    init_storage()
    with _cursor(commit=True) as cur:
        cur.execute(_q("DELETE FROM assets WHERE created_at < %s;"), (cutoff,))
        removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    if removed:
        logger.info("Purged %d reports older than %d days", removed, max_age_days)
    return removed


def usage():
    """Row count and total bytes, for the status page."""
    if using_r2():
        return {"backend": backend_name(), "counted": False}
    try:
        init_storage()
        with _cursor() as cur:
            cur.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM assets;")
            count, total = cur.fetchone()
        return {
            "backend": backend_name(),
            "counted": True,
            "assets": int(count),
            "bytes": int(total),
        }
    except Exception:
        return {"backend": backend_name(), "counted": False}

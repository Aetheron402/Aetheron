from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any


def utc_now() -> datetime:
    """
    Return the current UTC datetime.
    """
    return datetime.now(timezone.utc)


def parse_iso(value: Any) -> Optional[datetime]:
    """
    Parse an ISO timestamp safely.

    Accepts:
    - "2026-03-06T12:00:00"
    - "2026-03-06T12:00:00Z"
    """
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt

    except Exception:
        return None
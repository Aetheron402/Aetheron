from datetime import datetime, timedelta


def now_utc() -> datetime:
    return datetime.utcnow()


def seconds_until(timestamp: datetime) -> float:
    return (timestamp - now_utc()).total_seconds()


def has_expired(timestamp: datetime) -> bool:
    return now_utc() >= timestamp


def within_window(
    timestamp: datetime,
    window_seconds: int,
) -> bool:
    delta = abs((timestamp - now_utc()).total_seconds())
    return delta <= window_seconds

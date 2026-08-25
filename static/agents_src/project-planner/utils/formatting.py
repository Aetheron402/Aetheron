from __future__ import annotations

from datetime import datetime


def format_timestamp(dt: datetime) -> str:
    """
    Format a datetime into a readable timestamp.
    """
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_task_title(title: str) -> str:
    """
    Normalize task titles for output.
    """
    return title.strip().replace("\n", " ")
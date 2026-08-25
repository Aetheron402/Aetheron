from __future__ import annotations

import uuid


def generate_id(prefix: str | None = None) -> str:
    """
    Generate a unique identifier.

    Example:
        generate_id() -> "a3f19b4c..."
        generate_id("task") -> "task_a3f19b4c..."
    """
    uid = uuid.uuid4().hex

    if prefix:
        return f"{prefix}_{uid}"

    return uid
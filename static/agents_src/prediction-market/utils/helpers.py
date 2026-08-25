import uuid
from typing import Any, Dict


def generate_id() -> str:
    return str(uuid.uuid4())


def safe_get(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    return data.get(key, default)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
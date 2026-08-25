from datetime import datetime
import math


def time_decay(
    timestamp: datetime,
    now: datetime,
    half_life_seconds: float
) -> float:
    """
    Exponential decay based on half-life.
    """
    age = (now - timestamp).total_seconds()
    if age <= 0:
        return 1.0

    return math.exp(-math.log(2) * age / half_life_seconds)

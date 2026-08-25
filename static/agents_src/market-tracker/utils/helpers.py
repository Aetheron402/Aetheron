from typing import List


def average(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0

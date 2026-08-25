def normalize_score(
    value: float,
    min_value: float = 0.0,
    max_value: float = 1.0
) -> float:
    """
    Clamp a score into a fixed range.
    """
    if max_value <= min_value:
        return min_value

    return max(min_value, min(value, max_value))

"""
Smoothing utilities to prevent regime whiplash.
"""


def smooth_score(
    previous: float,
    current: float,
    alpha: float = 0.3
) -> float:
    """
    Exponential smoothing.
    Lower alpha = more inertia.
    """
    return alpha * current + (1 - alpha) * previous

from typing import Dict
from schemas import ModuleResult


def aggregate_scores(
    module_results: Dict[str, ModuleResult],
    weights: Dict[str, float]
) -> float:
    total_weight = 0.0
    weighted_sum = 0.0

    for name, result in module_results.items():
        weight = weights.get(name, 1.0)
        weighted_sum += result["score"] * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0

    return weighted_sum / total_weight


def aggregate_confidence(module_results: Dict[str, ModuleResult]) -> float:
    if not module_results:
        return 0.0

    return sum(r["confidence"] for r in module_results.values()) / len(module_results)

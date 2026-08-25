from typing import List, Dict


def cluster_values(values: List[str]) -> Dict[str, List[str]]:
    """
    Group identical values together.
    V1: simple exact-match clustering.
    """
    clusters: Dict[str, List[str]] = {}

    for value in values:
        clusters.setdefault(value, []).append(value)

    return clusters

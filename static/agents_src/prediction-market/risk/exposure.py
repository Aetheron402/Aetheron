from typing import List

from schemas import Position


class ExposureManager:
    def __init__(self, max_exposure: float):
        self.max_exposure = max_exposure

    def current_exposure(self, positions: List[Position]) -> float:
        return sum(position.size for position in positions if position.status == "open")

    def allow(
        self,
        positions: List[Position],
        proposed_size: float,
    ) -> bool:
        total_exposure = self.current_exposure(positions)
        return (total_exposure + proposed_size) <= self.max_exposure
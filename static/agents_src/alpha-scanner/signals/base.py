from abc import ABC, abstractmethod
from typing import List
from datetime import datetime

from schemas.signal import Signal, SignalSource


class BaseSignalGenerator(ABC):
    source: SignalSource

    @abstractmethod
    def generate(self, timestamp: datetime) -> List[Signal]:
        """
        Produce a list of normalized Signal objects.
        No filtering, no ranking, no side effects.
        """
        raise NotImplementedError

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class StorageBackend(ABC):
    """
    Abstract base class for storage backends.

    All storage implementations must follow this interface
    so the rest of the system does not care how data is stored.
    """

    @abstractmethod
    def load(self) -> None:
        """Load data from underlying storage."""
        raise NotImplementedError

    @abstractmethod
    def save(self) -> None:
        """Persist in-memory data."""
        raise NotImplementedError

    @abstractmethod
    def get_all(self, collection: str) -> List[Dict[str, Any]]:
        """Return all items in a collection."""
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        """Return item by ID."""
        raise NotImplementedError

    @abstractmethod
    def add(self, collection: str, item: Dict[str, Any]) -> None:
        """Add item to collection."""
        raise NotImplementedError

    @abstractmethod
    def update(self, collection: str, item_id: str, updates: Dict[str, Any]) -> None:
        """Update item in collection."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, collection: str, item_id: str) -> None:
        """Delete item from collection."""
        raise NotImplementedError
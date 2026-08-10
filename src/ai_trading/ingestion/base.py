"""Common interface for all data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any


class DataSource(ABC):
    """Abstract base for a normalized data source.

    Concrete implementations wrap a provider API, handle auth and rate limits,
    and yield normalized records (dicts matching the design-doc schemas).
    """

    name: str = "base"

    @abstractmethod
    def fetch(self, **params: Any) -> Iterable[dict[str, Any]]:
        """Fetch normalized records from the source.

        Implementations must respect provider rate limits and use only public
        or licensed endpoints (no scraping of protected content).
        """
        raise NotImplementedError

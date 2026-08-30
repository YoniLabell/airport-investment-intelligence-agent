"""Provider protocol shared by every data source."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.data.dataset import AirportDataset


class DataUnavailableError(RuntimeError):
    """Raised when a provider cannot produce a dataset (network, parse, ...)."""


@runtime_checkable
class AirportDataProvider(Protocol):
    """Anything that can produce an :class:`AirportDataset`.

    Implement this to plug in a different upstream (a BTS T-100 extract, an
    internal warehouse, a vendor API) without touching the analytics layer.
    """

    name: str

    def load(self) -> AirportDataset:
        """Return a fully populated dataset or raise :class:`DataUnavailableError`."""
        ...

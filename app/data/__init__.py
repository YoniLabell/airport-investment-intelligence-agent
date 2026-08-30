"""Data-provider layer.

The rest of the application only ever talks to :class:`DataRepository`.
Swapping the upstream source means writing one new provider class.
"""

from app.data.dataset import AirportDataset, DataProvenance, DataStatus
from app.data.provider import AirportDataProvider, DataUnavailableError
from app.data.repository import DataRepository, get_repository

__all__ = [
    "AirportDataset",
    "DataProvenance",
    "DataStatus",
    "AirportDataProvider",
    "DataUnavailableError",
    "DataRepository",
    "get_repository",
]

"""In-memory representation of an aviation dataset plus its provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import pandas as pd

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "airports": {"iata", "name", "city", "state", "region", "latitude",
                 "longitude", "runways", "gates", "slot_controlled"},
    "annual": {"iata", "year", "passengers", "seats", "flights"},
    "routes": {"origin", "destination", "distance_miles",
               "departures_performed", "seats", "passengers"},
}


class DataStatus(str, Enum):
    """How the currently loaded dataset was obtained.

    ``DEMO`` data must never be labelled as ``LIVE`` anywhere in the UI or API.
    """

    LIVE = "live"
    CACHED = "cached"
    DEMO = "demo"


@dataclass(frozen=True)
class DataProvenance:
    """Human-readable provenance for the loaded dataset."""

    status: DataStatus
    source_name: str
    description: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    coverage_years: tuple[int, ...] = ()
    airport_count: int = 0
    notes: str = ""

    @property
    def is_demo(self) -> bool:
        return self.status is DataStatus.DEMO

    @property
    def label(self) -> str:
        """Short badge text, e.g. ``DEMO — bundled snapshot``."""
        return f"{self.status.value.upper()} — {self.source_name}"

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "source_name": self.source_name,
            "description": self.description,
            "fetched_at": self.fetched_at.isoformat(),
            "coverage_years": list(self.coverage_years),
            "airport_count": self.airport_count,
            "notes": self.notes,
            "label": self.label,
            "is_demo": self.is_demo,
        }


@dataclass(frozen=True)
class AirportDataset:
    """A validated bundle of the three tables the analytics layer needs.

    ``airports``  one row per analysed US airport (metadata + physical capacity)
    ``annual``    one row per (airport, year) with passengers / seats / flights
    ``routes``    one row per non-stop segment out of an analysed airport
    """

    airports: pd.DataFrame
    annual: pd.DataFrame
    routes: pd.DataFrame
    provenance: DataProvenance

    def __post_init__(self) -> None:
        for name, required in REQUIRED_COLUMNS.items():
            frame: pd.DataFrame = getattr(self, name)
            missing = required - set(frame.columns)
            if missing:
                raise ValueError(
                    f"dataset table '{name}' is missing columns: {sorted(missing)}"
                )
            if frame.empty:
                raise ValueError(f"dataset table '{name}' is empty")

    @property
    def years(self) -> list[int]:
        return sorted(int(y) for y in self.annual["year"].unique())

    @property
    def latest_year(self) -> int:
        return self.years[-1]

    @property
    def iata_codes(self) -> list[str]:
        return sorted(self.airports["iata"].tolist())

    def has_airport(self, iata: str) -> bool:
        return iata.strip().upper() in set(self.airports["iata"])

"""Bundled demo dataset provider.

Reads the CSVs in ``app/data/seed/``. This provider never touches the network,
so the application is always demoable. Data produced here is *always* tagged
``DataStatus.DEMO`` — it is never presented as live.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config import SEED_DIR
from app.data.dataset import AirportDataset, DataProvenance, DataStatus
from app.data.provider import DataUnavailableError
from app.logging_config import get_logger

log = get_logger(__name__)

DEMO_DESCRIPTION = (
    "Bundled offline snapshot shipped with the repository. Airport-level "
    "passenger, seat and flight totals are rounded approximations of publicly "
    "reported FAA/BTS figures; the route table is a synthesized stand-in for a "
    "BTS T-100 segment extract. Suitable for demonstrating the methodology, "
    "not for investment decisions."
)


class DemoDataProvider:
    """Loads the committed CSV snapshot from disk."""

    name = "Bundled demo snapshot (FAA/BTS-shaped)"

    def __init__(self, seed_dir: Path | None = None) -> None:
        self.seed_dir = Path(seed_dir or SEED_DIR)

    def load(self) -> AirportDataset:
        try:
            airports = pd.read_csv(self.seed_dir / "airports.csv")
            annual = pd.read_csv(self.seed_dir / "airport_annual.csv")
            routes = pd.read_csv(self.seed_dir / "routes.csv")
        except (OSError, pd.errors.ParserError) as exc:  # pragma: no cover
            raise DataUnavailableError(f"cannot read bundled seed data: {exc}") from exc

        airports["iata"] = airports["iata"].str.strip().str.upper()
        airports["slot_controlled"] = (
            airports["slot_controlled"].astype(str).str.lower().eq("true")
        )
        annual["iata"] = annual["iata"].str.strip().str.upper()
        routes["origin"] = routes["origin"].str.strip().str.upper()
        routes["destination"] = routes["destination"].str.strip().str.upper()

        provenance = DataProvenance(
            status=DataStatus.DEMO,
            source_name="Bundled demo snapshot",
            description=DEMO_DESCRIPTION,
            coverage_years=tuple(sorted(int(y) for y in annual["year"].unique())),
            airport_count=int(airports["iata"].nunique()),
            notes="Offline data. Do not treat as live BTS output.",
        )
        log.info("loaded demo dataset: %d airports, %d route rows",
                 len(airports), len(routes))
        return AirportDataset(airports=airports, annual=annual,
                              routes=routes, provenance=provenance)

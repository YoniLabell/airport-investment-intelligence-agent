"""BTS / US DOT T-100 Segment provider.

The US DOT Bureau of Transportation Statistics publishes the T-100 Domestic and
International Segment tables through TranStats. There is no stable, keyless
JSON API for them: the public route is a filtered CSV/ZIP export. This provider
therefore accepts the extract in either of two swappable ways:

1. ``BTS_LOCAL_EXTRACT_DIR`` — a directory of already-downloaded T-100 Segment
   CSVs (what an analyst realistically has). Preferred; no network needed.
2. ``BTS_T100_URL`` — a direct URL to a CSV or zipped CSV, fetched with a hard
   timeout.

If neither is configured or the fetch/parse fails, we raise
:class:`DataUnavailableError` and the repository falls back to cached or demo
data — clearly labelled as such.

Airport *metadata* (region, gates, runways) is stable reference data and always
comes from the bundled reference table; only the volume figures are live.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from app.config import Settings
from app.data.dataset import AirportDataset, DataProvenance, DataStatus
from app.data.demo_provider import DemoDataProvider
from app.data.provider import DataUnavailableError
from app.logging_config import get_logger

log = get_logger(__name__)

# T-100 Segment column names (TranStats exports them upper-case).
COLUMN_MAP = {
    "ORIGIN": "origin",
    "DEST": "destination",
    "DISTANCE": "distance_miles",
    "DEPARTURES_PERFORMED": "departures_performed",
    "SEATS": "seats",
    "PASSENGERS": "passengers",
    "YEAR": "year",
}
REQUIRED_RAW = set(COLUMN_MAP)


class BTSDataProvider:
    """Builds a dataset from BTS T-100 Segment records."""

    name = "US DOT / BTS T-100 Segment"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._reference = DemoDataProvider()

    # -- public API --------------------------------------------------------
    def load(self) -> AirportDataset:
        raw = self._read_extract()
        return self._build(raw)

    # -- extract acquisition -----------------------------------------------
    def _read_extract(self) -> pd.DataFrame:
        local_dir = self.settings.bts_local_extract_dir.strip()
        if local_dir:
            return self._read_local(Path(local_dir))
        url = self.settings.bts_t100_url.strip()
        if url:
            return self._read_remote(url)
        raise DataUnavailableError(
            "No BTS source configured (set BTS_LOCAL_EXTRACT_DIR or BTS_T100_URL)."
        )

    def _read_local(self, directory: Path) -> pd.DataFrame:
        if not directory.is_dir():
            raise DataUnavailableError(f"BTS extract directory not found: {directory}")
        files = sorted(directory.glob("*.csv")) + sorted(directory.glob("*.zip"))
        if not files:
            raise DataUnavailableError(f"No CSV/ZIP extracts found in {directory}")
        frames = []
        for path in files:
            try:
                if path.suffix == ".zip":
                    frames.extend(self._frames_from_zip(path.read_bytes()))
                else:
                    frames.append(pd.read_csv(path, low_memory=False))
            except (OSError, ValueError, pd.errors.ParserError) as exc:
                log.warning("skipping unreadable BTS extract %s: %s", path, exc)
        if not frames:
            raise DataUnavailableError(f"No parseable BTS extract in {directory}")
        return pd.concat(frames, ignore_index=True)

    def _read_remote(self, url: str) -> pd.DataFrame:
        timeout = self.settings.data_timeout_seconds
        log.info("fetching BTS extract from %s (timeout %.1fs)", url, timeout)
        try:
            response = httpx.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DataUnavailableError(f"BTS fetch failed: {exc}") from exc

        content = response.content
        try:
            if content[:2] == b"PK":
                frames = self._frames_from_zip(content)
                if not frames:
                    raise DataUnavailableError("BTS ZIP contained no CSV member")
                return pd.concat(frames, ignore_index=True)
            return pd.read_csv(io.BytesIO(content), low_memory=False)
        except (ValueError, pd.errors.ParserError, zipfile.BadZipFile) as exc:
            raise DataUnavailableError(f"BTS payload not parseable: {exc}") from exc

    @staticmethod
    def _frames_from_zip(blob: bytes) -> list[pd.DataFrame]:
        frames: list[pd.DataFrame] = []
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for member in archive.namelist():
                if member.lower().endswith(".csv"):
                    with archive.open(member) as fh:
                        frames.append(pd.read_csv(fh, low_memory=False))
        return frames

    # -- shaping -----------------------------------------------------------
    def _build(self, raw: pd.DataFrame) -> AirportDataset:
        raw.columns = [str(c).strip().upper() for c in raw.columns]
        missing = REQUIRED_RAW - set(raw.columns)
        if missing:
            raise DataUnavailableError(
                f"BTS extract missing expected columns: {sorted(missing)}"
            )

        seg = raw[list(COLUMN_MAP)].rename(columns=COLUMN_MAP).copy()
        for col in ("distance_miles", "departures_performed", "seats", "passengers"):
            seg[col] = pd.to_numeric(seg[col], errors="coerce")
        seg["year"] = pd.to_numeric(seg["year"], errors="coerce").astype("Int64")
        seg = seg.dropna(subset=["origin", "destination", "year"])
        seg = seg[seg["departures_performed"].fillna(0) > 0]
        if seg.empty:
            raise DataUnavailableError("BTS extract contained no performed departures")

        reference = self._reference.load()
        metadata = reference.airports
        analysed = set(metadata["iata"])
        seg = seg[seg["origin"].isin(analysed)]
        if seg.empty:
            raise DataUnavailableError(
                "BTS extract contained none of the reference airports"
            )

        annual = (
            seg.groupby(["origin", "year"], as_index=False)
            .agg(passengers=("passengers", "sum"),
                 seats=("seats", "sum"),
                 flights=("departures_performed", "sum"))
            .rename(columns={"origin": "iata"})
        )
        annual["year"] = annual["year"].astype(int)
        annual = annual[annual["passengers"] > 0]

        # Keep only airports with a usable multi-year series.
        usable = annual.groupby("iata")["year"].nunique()
        keep = set(usable[usable >= 1].index)
        annual = annual[annual["iata"].isin(keep)]
        metadata = metadata[metadata["iata"].isin(keep)]
        if metadata.empty:
            raise DataUnavailableError("No airport in the extract has usable volumes")

        latest = int(annual["year"].max())
        routes = (
            seg[(seg["year"] == latest) & (seg["origin"].isin(keep))]
            .groupby(["origin", "destination"], as_index=False)
            .agg(distance_miles=("distance_miles", "max"),
                 departures_performed=("departures_performed", "sum"),
                 seats=("seats", "sum"),
                 passengers=("passengers", "sum"))
            .dropna(subset=["distance_miles"])
        )
        if routes.empty:
            raise DataUnavailableError("BTS extract produced no usable route rows")

        years = tuple(sorted(int(y) for y in annual["year"].unique()))
        provenance = DataProvenance(
            status=DataStatus.LIVE,
            source_name=self.name,
            description=(
                "Live US DOT / BTS T-100 Segment extract. Passenger, seat and "
                "departure totals are aggregated per origin airport; non-stop "
                "distances come from the segment records themselves."
            ),
            coverage_years=years,
            airport_count=int(metadata["iata"].nunique()),
            notes="Airport reference metadata (region, gates, runways) is static.",
        )
        log.info("built live BTS dataset: %d airports, years %s",
                 metadata["iata"].nunique(), years)
        return AirportDataset(airports=metadata.reset_index(drop=True),
                              annual=annual.reset_index(drop=True),
                              routes=routes.reset_index(drop=True),
                              provenance=provenance)

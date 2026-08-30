"""Dataset acquisition with an explicit, user-visible fallback ladder.

    live upstream  ->  cached copy of a previous live pull  ->  bundled demo

Whatever wins, the resulting :class:`DataProvenance` says so honestly. Demo data
is never relabelled as live.
"""

from __future__ import annotations

import functools
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.config import Settings, get_settings
from app.data.bts_provider import BTSDataProvider
from app.data.cache import TTLCache
from app.data.dataset import AirportDataset, DataProvenance, DataStatus
from app.data.demo_provider import DemoDataProvider
from app.data.provider import AirportDataProvider, DataUnavailableError
from app.logging_config import get_logger

log = get_logger(__name__)

CACHE_KEY = "airport_dataset_v1"


class DataRepository:
    """Single entry point the rest of the app uses to obtain a dataset."""

    def __init__(
        self,
        settings: Settings | None = None,
        live_provider: AirportDataProvider | None = None,
        demo_provider: AirportDataProvider | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.live_provider = live_provider or BTSDataProvider(self.settings)
        self.demo_provider = demo_provider or DemoDataProvider()
        self.cache = cache or TTLCache(self.settings.cache_ttl_seconds,
                                       self.settings.cache_dir)
        self._lock = threading.Lock()
        self._dataset: AirportDataset | None = None

    # -- public API --------------------------------------------------------
    def get_dataset(self, refresh: bool = False) -> AirportDataset:
        """Return the active dataset, loading it at most once per process."""
        with self._lock:
            if self._dataset is None or refresh:
                self._dataset = self._load(refresh=refresh)
            return self._dataset

    @property
    def provenance(self) -> DataProvenance:
        return self.get_dataset().provenance

    def reset(self) -> None:
        """Drop the in-process dataset (used by tests and ``?refresh=true``)."""
        with self._lock:
            self._dataset = None

    # -- internals ---------------------------------------------------------
    def _load(self, refresh: bool = False) -> AirportDataset:
        if self.settings.use_demo_data:
            log.info("USE_DEMO_DATA is set; serving the bundled demo dataset")
            return self.demo_provider.load()

        if not refresh:
            cached = self._from_cache()
            if cached is not None:
                return cached

        try:
            dataset = self.live_provider.load()
        except DataUnavailableError as exc:
            log.warning("live provider unavailable (%s); falling back", exc)
        except Exception as exc:  # noqa: BLE001 - never let data kill the app
            log.exception("unexpected live-provider failure: %s", exc)
        else:
            self._to_cache(dataset)
            return dataset

        cached = self._from_cache(allow_stale=True)
        if cached is not None:
            return cached

        demo = self.demo_provider.load()
        return replace_provenance(
            demo,
            notes=(demo.provenance.notes
                   + " Live BTS source was unavailable, so this fallback is in use."),
        )

    def _from_cache(self, allow_stale: bool = False) -> AirportDataset | None:
        entry = self.cache.get(CACHE_KEY)
        if entry is None and allow_stale:
            # A stale entry still beats nothing; re-read ignoring the TTL.
            stale = TTLCache(0, self.settings.cache_dir).get(CACHE_KEY)
            entry = stale
        if entry is None:
            return None
        payload, age = entry
        try:
            dataset = _deserialize(payload)
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("dropping unusable cache payload: %s", exc)
            self.cache.invalidate(CACHE_KEY)
            return None
        fetched_at = datetime.now(timezone.utc) - timedelta(seconds=age)
        provenance = replace(
            dataset.provenance,
            status=DataStatus.CACHED,
            fetched_at=fetched_at,
            notes=f"Served from cache, {int(age // 60)} minute(s) old.",
        )
        log.info("serving cached dataset (age %.0fs)", age)
        return AirportDataset(dataset.airports, dataset.annual,
                              dataset.routes, provenance)

    def _to_cache(self, dataset: AirportDataset) -> None:
        try:
            self.cache.set(CACHE_KEY, _serialize(dataset))
        except Exception as exc:  # noqa: BLE001 - caching must never be fatal
            log.warning("failed to cache dataset: %s", exc)


def replace_provenance(dataset: AirportDataset, **changes) -> AirportDataset:
    """Return ``dataset`` with an updated provenance record."""
    return AirportDataset(dataset.airports, dataset.annual, dataset.routes,
                          replace(dataset.provenance, **changes))


def _serialize(dataset: AirportDataset) -> dict:
    return {
        "airports": dataset.airports.to_dict(orient="records"),
        "annual": dataset.annual.to_dict(orient="records"),
        "routes": dataset.routes.to_dict(orient="records"),
        "provenance": dataset.provenance.to_dict(),
    }


def _deserialize(payload: dict) -> AirportDataset:
    prov = payload["provenance"]
    provenance = DataProvenance(
        status=DataStatus(prov["status"]),
        source_name=prov["source_name"],
        description=prov["description"],
        fetched_at=datetime.fromisoformat(prov["fetched_at"]),
        coverage_years=tuple(prov.get("coverage_years", ())),
        airport_count=int(prov.get("airport_count", 0)),
        notes=prov.get("notes", ""),
    )
    return AirportDataset(
        airports=pd.DataFrame(payload["airports"]),
        annual=pd.DataFrame(payload["annual"]),
        routes=pd.DataFrame(payload["routes"]),
        provenance=provenance,
    )


@functools.lru_cache(maxsize=1)
def get_repository() -> DataRepository:
    """Process-wide repository singleton (FastAPI dependency)."""
    return DataRepository()

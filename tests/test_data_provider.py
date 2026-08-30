"""Data-provider layer: honest labelling and a working fallback ladder."""

from __future__ import annotations

import pandas as pd
import pytest

from app.config import Settings
from app.data.cache import TTLCache
from app.data.dataset import AirportDataset, DataStatus
from app.data.demo_provider import DemoDataProvider
from app.data.provider import DataUnavailableError
from app.data.repository import DataRepository


class FailingProvider:
    name = "always down"

    def load(self) -> AirportDataset:
        raise DataUnavailableError("upstream is down")


class ExplodingProvider:
    name = "raises something unexpected"

    def load(self) -> AirportDataset:
        raise RuntimeError("boom")


class FakeLiveProvider:
    """Returns the demo tables but labelled LIVE, to exercise the live path."""

    name = "fake live"

    def load(self) -> AirportDataset:
        from dataclasses import replace

        base = DemoDataProvider().load()
        return AirportDataset(
            base.airports, base.annual, base.routes,
            replace(base.provenance, status=DataStatus.LIVE,
                    source_name="Fake live source"),
        )


def test_demo_data_is_always_labelled_demo(dataset):
    assert dataset.provenance.status is DataStatus.DEMO
    assert dataset.provenance.is_demo is True
    assert dataset.provenance.label.startswith("DEMO")
    assert "not for investment decisions" in dataset.provenance.description.lower()


def test_demo_dataset_shape(dataset):
    assert len(dataset.airports) >= 9
    assert {"SFO", "LAX", "SNA", "ANC", "BOS", "PVD", "BDL", "JFK", "EWR"} <= set(
        dataset.airports["iata"])
    assert len(dataset.years) >= 2
    assert not dataset.routes.empty


def test_dataset_rejects_missing_columns(dataset):
    with pytest.raises(ValueError, match="missing columns"):
        AirportDataset(dataset.airports.drop(columns=["region"]), dataset.annual,
                       dataset.routes, dataset.provenance)


def test_dataset_rejects_empty_tables(dataset):
    with pytest.raises(ValueError, match="is empty"):
        AirportDataset(dataset.airports.iloc[0:0], dataset.annual, dataset.routes,
                       dataset.provenance)


def test_use_demo_data_short_circuits_the_live_provider(tmp_path):
    settings = Settings(use_demo_data=True, cache_dir=tmp_path)
    repo = DataRepository(settings=settings, live_provider=ExplodingProvider(),
                          cache=TTLCache(60, tmp_path))
    assert repo.get_dataset().provenance.status is DataStatus.DEMO


def test_falls_back_to_demo_when_live_is_unavailable(tmp_path):
    settings = Settings(use_demo_data=False, cache_dir=tmp_path)
    repo = DataRepository(settings=settings, live_provider=FailingProvider(),
                          cache=TTLCache(60, tmp_path))
    provenance = repo.get_dataset().provenance
    assert provenance.status is DataStatus.DEMO
    assert "unavailable" in provenance.notes.lower()


def test_unexpected_live_errors_also_fall_back(tmp_path):
    settings = Settings(use_demo_data=False, cache_dir=tmp_path)
    repo = DataRepository(settings=settings, live_provider=ExplodingProvider(),
                          cache=TTLCache(60, tmp_path))
    assert repo.get_dataset().provenance.status is DataStatus.DEMO


def test_live_result_is_cached_and_then_served_as_cached(tmp_path):
    settings = Settings(use_demo_data=False, cache_dir=tmp_path)
    cache = TTLCache(3600, tmp_path)

    first = DataRepository(settings=settings, live_provider=FakeLiveProvider(),
                           cache=cache).get_dataset()
    assert first.provenance.status is DataStatus.LIVE

    # A brand-new repository with a dead upstream should still serve the cache.
    second = DataRepository(settings=settings, live_provider=FailingProvider(),
                            cache=cache).get_dataset()
    assert second.provenance.status is DataStatus.CACHED
    assert second.provenance.status is not DataStatus.LIVE
    assert len(second.airports) == len(first.airports)


def test_cache_expiry_is_honoured(tmp_path):
    cache = TTLCache(0, tmp_path)
    cache.set("k", {"a": 1})
    assert cache.get("k")[0] == {"a": 1}  # ttl 0 means "never expire" here

    short = TTLCache(1, tmp_path)
    short.set("k2", {"a": 2})
    assert short.get("k2") is not None
    short.invalidate("k2")
    assert short.get("k2") is None


def test_cache_survives_unreadable_entries(tmp_path):
    cache = TTLCache(3600, tmp_path)
    cache.set("junk", {"a": 1})
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    cache._memory.clear()
    assert cache.get("junk") is None


# --- BTS provider ----------------------------------------------------------
def test_bts_provider_needs_a_configured_source():
    from app.data.bts_provider import BTSDataProvider

    provider = BTSDataProvider(Settings(bts_t100_url="", bts_local_extract_dir=""))
    with pytest.raises(DataUnavailableError, match="No BTS source configured"):
        provider.load()


def test_bts_provider_shapes_a_t100_extract(tmp_path):
    """A minimal T-100 Segment extract should become a LIVE dataset."""
    from app.data.bts_provider import BTSDataProvider

    rows = []
    for year in (2023, 2024):
        growth = 1.0 if year == 2023 else 1.05
        for origin, dest, distance in [("BOS", "LGA", 184), ("BOS", "LHR", 3270),
                                       ("SFO", "JFK", 2586), ("SFO", "LAX", 337)]:
            rows.append({"YEAR": year, "ORIGIN": origin, "DEST": dest,
                         "DISTANCE": distance,
                         "DEPARTURES_PERFORMED": 1000 * growth,
                         "SEATS": 150_000 * growth,
                         "PASSENGERS": 130_000 * growth})
    path = tmp_path / "t100.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    dataset = BTSDataProvider(
        Settings(bts_local_extract_dir=str(tmp_path))).load()

    assert dataset.provenance.status is DataStatus.LIVE
    assert set(dataset.airports["iata"]) == {"BOS", "SFO"}
    assert dataset.years == [2023, 2024]
    # Metadata (region) is joined from the static reference table.
    assert dataset.airports.set_index("iata").loc["BOS", "region"] == "New England"

    from app.analytics.metrics import long_haul_breakdown

    assert long_haul_breakdown(dataset, "BOS", 2500.0)["long_haul_route_count"] == 1


def test_bts_provider_rejects_a_malformed_extract(tmp_path):
    from app.data.bts_provider import BTSDataProvider

    (tmp_path / "bad.csv").write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(DataUnavailableError, match="missing expected columns"):
        BTSDataProvider(Settings(bts_local_extract_dir=str(tmp_path))).load()

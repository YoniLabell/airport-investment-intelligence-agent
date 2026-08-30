"""New England (and other geography) filtering must be data-driven."""

from __future__ import annotations

import pytest

from app.analytics.regions import (
    NEW_ENGLAND_STATES,
    filter_airports,
    resolve_region,
)

NEW_ENGLAND_EXPECTED = {"BOS", "PVD", "BDL", "MHT", "PWM", "BTV"}


def test_new_england_resolves_to_the_region(dataset):
    subset, resolution = filter_airports(dataset.airports, "New England")
    assert resolution.matched
    assert resolution.kind == "region"
    assert resolution.regions == ("New England",)
    assert set(subset["iata"]) == NEW_ENGLAND_EXPECTED


def test_new_england_contains_only_the_six_states(dataset):
    subset, _ = filter_airports(dataset.airports, "new england")
    assert set(subset["state"]) <= NEW_ENGLAND_STATES
    assert set(subset["state"]) == {"MA", "RI", "CT", "NH", "ME", "VT"}


@pytest.mark.parametrize("code", ["JFK", "EWR", "LAX", "SFO", "ANC", "ATL"])
def test_non_new_england_airports_excluded(dataset, code):
    subset, _ = filter_airports(dataset.airports, "New England")
    assert code not in set(subset["iata"])


def test_new_england_is_case_and_spacing_insensitive(dataset):
    for query in ["NEW ENGLAND", "  new   england ", "New England"]:
        subset, resolution = filter_airports(dataset.airports, query)
        assert resolution.matched
        assert set(subset["iata"]) == NEW_ENGLAND_EXPECTED


def test_northeast_alias_widens_to_two_regions(dataset):
    subset, resolution = filter_airports(dataset.airports, "northeast")
    assert set(resolution.regions) == {"New England", "Mid-Atlantic"}
    assert NEW_ENGLAND_EXPECTED <= set(subset["iata"])
    assert "JFK" in set(subset["iata"])


def test_state_name_and_code_resolve(dataset):
    by_name, res_name = filter_airports(dataset.airports, "Massachusetts")
    by_code, res_code = filter_airports(dataset.airports, "MA")
    assert res_name.kind == res_code.kind == "state"
    assert set(by_name["iata"]) == set(by_code["iata"]) == {"BOS"}


def test_empty_query_returns_every_airport(dataset):
    subset, resolution = filter_airports(dataset.airports, None)
    assert resolution.kind == "all"
    assert len(subset) == len(dataset.airports)


def test_unknown_geography_matches_nothing(dataset):
    subset, resolution = filter_airports(dataset.airports, "Middle Earth")
    assert not resolution.matched
    assert subset.empty


def test_every_airport_has_a_canonical_region(dataset):
    from app.analytics.regions import CANONICAL_REGIONS

    assert set(dataset.airports["region"]) <= set(CANONICAL_REGIONS)

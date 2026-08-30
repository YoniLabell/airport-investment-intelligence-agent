"""Ranking and comparison behaviour."""

from __future__ import annotations

import pytest

from app.analytics.metrics import UnknownAirportError
from app.analytics.ranking import (
    SORTABLE_FIELDS,
    compare_airports,
    congestion_comparison,
    dataset_overview,
    rank_airports,
)

NEW_ENGLAND = {"BOS", "PVD", "BDL", "MHT", "PWM", "BTV"}


def test_ranking_is_sorted_descending_by_score(dataset):
    result = rank_airports(dataset, limit=15)
    scores = [r["expansion_score"] for r in result["results"]]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in result["results"]] == list(range(1, len(scores) + 1))


def test_limit_is_respected(dataset):
    assert len(rank_airports(dataset, limit=3)["results"]) == 3
    assert len(rank_airports(dataset, limit=1)["results"]) == 1


def test_limit_beyond_the_pool_returns_everything(dataset):
    result = rank_airports(dataset, region="New England", limit=50)
    assert result["count"] == len(NEW_ENGLAND)


def test_new_england_ranking_only_contains_new_england(dataset):
    result = rank_airports(dataset, region="New England", limit=10)
    assert {r["iata"] for r in result["results"]} == NEW_ENGLAND
    assert all(r["region"] == "New England" for r in result["results"])
    assert result["region_resolution"]["matched"] is True


def test_regional_ranking_agrees_with_the_national_ordering(dataset):
    national = rank_airports(dataset, limit=100)["results"]
    order = [r["iata"] for r in national if r["iata"] in NEW_ENGLAND]
    regional = [r["iata"] for r in rank_airports(dataset, region="New England",
                                                 limit=10)["results"]]
    assert order == regional


def test_ascending_flag_reverses_the_order(dataset):
    scores = [r["expansion_score"]
              for r in rank_airports(dataset, limit=10, ascending=True)["results"]]
    assert scores == sorted(scores)


@pytest.mark.parametrize("field", sorted(SORTABLE_FIELDS))
def test_every_advertised_sort_field_works(dataset, field):
    result = rank_airports(dataset, limit=5, sort_by=field)
    values = [r["sort_value"] for r in result["results"]]
    assert values == sorted(values, reverse=True)
    assert result["sort_label"] == SORTABLE_FIELDS[field]


def test_unknown_sort_field_is_rejected(dataset):
    with pytest.raises(ValueError, match="Unsupported sort field"):
        rank_airports(dataset, sort_by="vibes")


def test_unknown_region_returns_an_explanatory_empty_result(dataset):
    result = rank_airports(dataset, region="Atlantis")
    assert result["count"] == 0
    assert result["results"] == []
    assert "Atlantis" in result["note"]


def test_state_scoped_ranking(dataset):
    result = rank_airports(dataset, region="CA", limit=20)
    assert result["count"] > 1
    assert all(r["state"] == "CA" for r in result["results"])


# --- comparison ------------------------------------------------------------
def test_comparison_covers_both_airports(dataset):
    result = compare_airports(dataset, ["LAX", "SNA"])
    assert result["iatas"] == ["LAX", "SNA"]
    assert {a["iata"] for a in result["airports"]} == {"LAX", "SNA"}
    assert result["higher_score"] in {"LAX", "SNA"}
    assert result["score_margin"] >= 0


def test_comparison_leader_matches_the_values(dataset):
    for row in compare_airports(dataset, ["LAX", "SNA", "SFO"])["table"]:
        assert row["leader"] == max(row["values"], key=row["values"].get)


def test_comparison_deduplicates_and_needs_two_airports(dataset):
    with pytest.raises(ValueError):
        compare_airports(dataset, ["LAX", "lax"])
    with pytest.raises(ValueError):
        compare_airports(dataset, ["LAX"])


def test_comparison_rejects_unknown_airports(dataset):
    with pytest.raises(UnknownAirportError):
        compare_airports(dataset, ["LAX", "ZZZ"])


def test_congestion_view_reports_throughput_per_capacity(dataset):
    result = congestion_comparison(dataset, ["LAX", "SNA"])
    assert "delay" in result["definition"].lower()  # states what it does NOT claim
    assert {a["iata"] for a in result["airports"]} == {"LAX", "SNA"}
    busiest = max(result["airports"], key=lambda a: a["departures_per_runway"])
    assert result["busiest_airfield"] == busiest["iata"]


def test_overview_reports_provenance(dataset):
    overview = dataset_overview(dataset)
    assert overview["airport_count"] == len(dataset.airports)
    assert overview["provenance"]["status"] == "demo"
    assert overview["long_haul_threshold_miles"] == 2500.0

"""Airport Expansion Score: weights, bounds, reproducibility and monotonicity."""

from __future__ import annotations

import pytest

from app.analytics.metrics import compute_metrics_frame, scale
from app.analytics.scoring import (
    ANCHORS,
    CAPACITY_CONSTRAINT_MIX,
    DEMAND_PRESSURE_MIX,
    UNMET_DEMAND_MIX,
    WEIGHTS,
    expansion_score,
    expansion_score_from_metrics,
    rating_for,
    score_frame,
    unmet_demand_proxy,
)


def test_headline_weights_match_the_documented_split():
    assert WEIGHTS == {
        "demand_pressure": 0.30,
        "passenger_growth": 0.25,
        "capacity_constraint": 0.20,
        "flight_growth": 0.15,
        "long_haul_connectivity": 0.10,
    }
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("mix", [DEMAND_PRESSURE_MIX, CAPACITY_CONSTRAINT_MIX,
                                 UNMET_DEMAND_MIX])
def test_pillar_mixes_sum_to_one(mix):
    assert sum(mix.values()) == pytest.approx(1.0)


def test_every_score_is_within_bounds(dataset):
    frame = score_frame(dataset)
    assert len(frame) == len(dataset.airports)
    assert frame["expansion_score"].between(0, 100).all()


def test_components_sum_to_the_headline_score(dataset):
    for code in ["SFO", "LAX", "SNA", "BOS", "ANC", "AUS", "PVD"]:
        result = expansion_score(dataset, code)
        total = sum(c["points"] for c in result["components"])
        assert total == pytest.approx(result["expansion_score"], abs=0.05), code


def test_component_points_never_exceed_their_weight(dataset):
    for code in dataset.iata_codes:
        for component in expansion_score(dataset, code)["components"]:
            assert 0.0 <= component["points"] <= component["max_points"] + 1e-9
            assert 0.0 <= component["sub_score"] <= 1.0


def test_scoring_is_deterministic(dataset):
    first = expansion_score(dataset, "SFO")["expansion_score"]
    second = expansion_score(dataset, "SFO")["expansion_score"]
    assert first == second


def test_score_is_reproducible_by_hand(dataset):
    """Recompute one airport's score from the documented formula."""
    metrics = compute_metrics_frame(dataset).loc["BOS"].to_dict()

    demand = (DEMAND_PRESSURE_MIX["load_factor"]
              * scale(metrics["load_factor"], *ANCHORS["load_factor"])
              + DEMAND_PRESSURE_MIX["departures_per_runway"]
              * scale(metrics["departures_per_runway"], *ANCHORS["departures_per_runway"]))
    capacity = (CAPACITY_CONSTRAINT_MIX["passengers_per_gate"]
                * scale(metrics["passengers_per_gate"], *ANCHORS["passengers_per_gate"])
                + CAPACITY_CONSTRAINT_MIX["slot_controlled"]
                * (1.0 if metrics["slot_controlled"] else 0.0)
                + CAPACITY_CONSTRAINT_MIX["capacity_growth_lag"]
                * scale(metrics["capacity_growth_lag"], *ANCHORS["capacity_growth_lag"]))
    expected = 100 * (
        WEIGHTS["demand_pressure"] * demand
        + WEIGHTS["passenger_growth"] * scale(metrics["passenger_cagr"],
                                              *ANCHORS["passenger_cagr"])
        + WEIGHTS["capacity_constraint"] * capacity
        + WEIGHTS["flight_growth"] * scale(metrics["flight_cagr"],
                                           *ANCHORS["flight_cagr"])
        + WEIGHTS["long_haul_connectivity"] * scale(metrics["long_haul_departure_share"],
                                                    *ANCHORS["long_haul_departure_share"])
    )
    assert expansion_score(dataset, "BOS")["expansion_score"] == pytest.approx(
        round(expected, 1), abs=0.05)


def test_higher_growth_scores_higher_all_else_equal(dataset):
    base = compute_metrics_frame(dataset).loc["BDL"].to_dict()
    slower = dict(base, passenger_cagr=0.01)
    faster = dict(base, passenger_cagr=0.06)
    assert (expansion_score_from_metrics(faster)["expansion_score"]
            > expansion_score_from_metrics(slower)["expansion_score"])


def test_higher_load_factor_raises_demand_pressure(dataset):
    base = compute_metrics_frame(dataset).loc["BDL"].to_dict()
    low = expansion_score_from_metrics(dict(base, load_factor=0.78))
    high = expansion_score_from_metrics(dict(base, load_factor=0.87))
    assert (high["pillar_detail"]["demand_pressure"]["value"]
            > low["pillar_detail"]["demand_pressure"]["value"])
    assert high["expansion_score"] > low["expansion_score"]


def test_slot_control_only_helps(dataset):
    base = compute_metrics_frame(dataset).loc["BDL"].to_dict()
    without = expansion_score_from_metrics(dict(base, slot_controlled=False))
    with_slots = expansion_score_from_metrics(dict(base, slot_controlled=True))
    assert with_slots["expansion_score"] > without["expansion_score"]


def test_anchor_bands_clamp_extremes(dataset):
    base = compute_metrics_frame(dataset).loc["BDL"].to_dict()
    extreme = dict(base, load_factor=5.0, passenger_cagr=10.0, flight_cagr=10.0,
                   passengers_per_gate=1e9, departures_per_runway=1e9,
                   capacity_growth_lag=10.0, long_haul_departure_share=1.0,
                   slot_controlled=True)
    assert expansion_score_from_metrics(extreme)["expansion_score"] == pytest.approx(100.0)

    floor = dict(base, load_factor=0.0, passenger_cagr=-1.0, flight_cagr=-1.0,
                 passengers_per_gate=0.0, departures_per_runway=0.0,
                 capacity_growth_lag=-1.0, long_haul_departure_share=0.0,
                 slot_controlled=False)
    assert expansion_score_from_metrics(floor)["expansion_score"] == pytest.approx(0.0)


@pytest.mark.parametrize("score,expected", [
    (95.0, "Strong candidate"), (70.0, "Strong candidate"),
    (69.9, "Promising"), (55.0, "Promising"),
    (54.9, "Watch list"), (40.0, "Watch list"),
    (39.9, "Low priority"), (0.0, "Low priority"),
])
def test_rating_buckets(score, expected):
    assert rating_for(score) == expected


def test_breakdown_explains_itself(dataset):
    result = expansion_score(dataset, "SFO")
    assert len(result["components"]) == 5
    for component in result["components"]:
        assert component["explanation"]
        assert component["raw_display"]
    assert result["top_drivers"] and result["biggest_gaps"]
    assert "30%" in result["methodology"]


# --- Unmet demand proxy ----------------------------------------------------
def test_unmet_demand_is_flagged_as_a_proxy(dataset):
    result = unmet_demand_proxy(dataset, "SFO")
    assert result["is_proxy"] is True
    assert "PROXY" in result["disclaimer"]
    assert "latent" in result["disclaimer"].lower()


def test_unmet_demand_index_bounds_and_composition(dataset):
    for code in dataset.iata_codes:
        result = unmet_demand_proxy(dataset, code)
        assert 0.0 <= result["unmet_demand_index"] <= 100.0
        total = sum(s["points"] for s in result["signals"])
        assert total == pytest.approx(result["unmet_demand_index"], abs=0.05)
        assert {s["key"] for s in result["signals"]} == set(UNMET_DEMAND_MIX)


def test_capacity_lag_drives_the_unmet_demand_signal(dataset):
    """SFO grows passengers well ahead of seats, so the lag signal should lead."""
    result = unmet_demand_proxy(dataset, "SFO")
    lag = next(s for s in result["signals"] if s["key"] == "capacity_growth_lag")
    assert lag["raw_value"] > 0
    assert result["unmet_demand_index"] > 50

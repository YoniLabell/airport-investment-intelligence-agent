"""Long-haul share: a purely distance-based, threshold-driven definition."""

from __future__ import annotations

import pytest

from app.analytics.metrics import (
    cagr,
    compute_metrics_frame,
    long_haul_breakdown,
    safe_ratio,
)

THRESHOLD = 2500.0


def test_share_matches_a_hand_computation(dataset):
    """Recompute ANC's long-haul share straight from the route table."""
    routes = dataset.routes[dataset.routes["origin"] == "ANC"]
    long_haul = routes[routes["distance_miles"] >= THRESHOLD]
    expected = long_haul["departures_performed"].sum() / routes["departures_performed"].sum()

    result = long_haul_breakdown(dataset, "ANC", THRESHOLD)
    assert result["long_haul_departure_share"] == pytest.approx(expected, abs=5e-5)
    assert 0.0 < result["long_haul_departure_share"] < 1.0


@pytest.mark.parametrize("code", ["SFO", "LAX", "JFK", "BOS", "ANC", "SNA", "PVD"])
def test_share_is_a_valid_proportion(dataset, code):
    result = long_haul_breakdown(dataset, code, THRESHOLD)
    for key in ("long_haul_departure_share", "long_haul_seat_share",
                "long_haul_passenger_share"):
        assert 0.0 <= result[key] <= 1.0
    assert result["long_haul_route_count"] <= result["route_count"]
    assert result["long_haul_departures"] <= result["total_departures"]


def test_every_reported_long_haul_route_clears_the_threshold(dataset):
    for code in ["SFO", "LAX", "JFK", "ANC", "SEA", "MIA"]:
        result = long_haul_breakdown(dataset, code, THRESHOLD)
        for route in result["top_long_haul_routes"]:
            assert route["distance_miles"] >= THRESHOLD


def test_threshold_is_configurable_and_monotonic(dataset):
    """A stricter threshold can only ever shrink the long-haul share."""
    shares = [
        long_haul_breakdown(dataset, "SFO", miles)["long_haul_departure_share"]
        for miles in (1000, 2000, 2500, 4000, 8000)
    ]
    assert shares == sorted(shares, reverse=True)
    assert shares[0] > shares[-1]


def test_a_threshold_below_every_segment_gives_full_share(dataset):
    result = long_haul_breakdown(dataset, "PVD", 1.0)
    assert result["long_haul_departure_share"] == pytest.approx(1.0)


def test_an_unreachable_threshold_gives_zero_share(dataset):
    result = long_haul_breakdown(dataset, "SFO", 100_000.0)
    assert result["long_haul_departure_share"] == 0.0
    assert result["top_long_haul_routes"] == []


def test_sna_has_no_long_haul_segments(dataset):
    """SNA's runway limits its stage length — a real characteristic, not a gap."""
    result = long_haul_breakdown(dataset, "SNA", THRESHOLD)
    assert result["long_haul_route_count"] == 0
    assert result["long_haul_departure_share"] == 0.0
    assert result["route_count"] > 0


def test_definition_string_states_the_threshold(dataset):
    result = long_haul_breakdown(dataset, "BOS", THRESHOLD)
    assert "2,500" in result["definition"]
    assert result["long_haul_threshold_miles"] == THRESHOLD


def test_metrics_frame_agrees_with_the_breakdown(dataset):
    frame = compute_metrics_frame(dataset, THRESHOLD)
    for code in ["BOS", "ANC", "LAX", "SFO"]:
        detail = long_haul_breakdown(dataset, code, THRESHOLD)
        assert (frame.loc[code, "long_haul_departure_share"]
                == pytest.approx(detail["long_haul_departure_share"], abs=5e-5))


def test_unknown_airport_raises(dataset):
    from app.analytics.metrics import UnknownAirportError

    with pytest.raises(UnknownAirportError):
        long_haul_breakdown(dataset, "ZZZ", THRESHOLD)


def test_lowercase_codes_are_accepted(dataset):
    assert (long_haul_breakdown(dataset, "anc", THRESHOLD)["long_haul_departure_share"]
            == long_haul_breakdown(dataset, "ANC", THRESHOLD)["long_haul_departure_share"])


# --- numeric helpers -------------------------------------------------------
def test_safe_ratio_handles_zero_denominator():
    assert safe_ratio(10, 0) == 0.0
    assert safe_ratio(10, 0, default=-1.0) == -1.0
    assert safe_ratio(10, 4) == pytest.approx(2.5)


def test_cagr_matches_the_definition():
    assert cagr(100, 121, 2) == pytest.approx(0.1)
    assert cagr(100, 100, 3) == pytest.approx(0.0)
    assert cagr(0, 100, 2) == 0.0
    assert cagr(100, 121, 0) == 0.0


def test_growth_round_trips_through_the_dataset(dataset):
    """The metric layer recovers the CAGR that generated the seed series."""
    frame = compute_metrics_frame(dataset)
    annual = dataset.annual
    for code in ["BOS", "AUS", "ANC"]:
        rows = annual[annual["iata"] == code].sort_values("year")
        span = int(rows["year"].iloc[-1] - rows["year"].iloc[0])
        expected = cagr(rows["passengers"].iloc[0], rows["passengers"].iloc[-1], span)
        assert frame.loc[code, "passenger_cagr"] == pytest.approx(expected, abs=1e-4)

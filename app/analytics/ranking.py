"""Ranking and head-to-head comparison of airports."""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

from app.analytics.metrics import (
    DEFAULT_LONG_HAUL_MILES,
    UnknownAirportError,
    compute_metrics_frame,
    long_haul_breakdown,
    resolve_iata,
)
from app.analytics.regions import filter_airports
from app.analytics.scoring import (
    PILLAR_LABELS,
    expansion_score_from_metrics,
    score_frame,
    unmet_demand_proxy,
)
from app.data.dataset import AirportDataset

SORTABLE_FIELDS: dict[str, str] = {
    "expansion_score": "Airport Expansion Score",
    "passengers": "Latest-year passengers",
    "passenger_cagr": "Passenger growth (CAGR)",
    "flight_cagr": "Flight growth (CAGR)",
    "load_factor": "Seat utilization (load factor)",
    "passengers_per_gate": "Passengers per gate",
    "departures_per_runway": "Departures per runway",
    "long_haul_departure_share": "Long-haul departure share",
    "unmet_demand_index": "Unmet Demand Proxy index",
}


def rank_airports(
    dataset: AirportDataset,
    region: str | None = None,
    limit: int = 10,
    sort_by: str = "expansion_score",
    ascending: bool = False,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> dict[str, Any]:
    """Rank airports, optionally restricted to a region or state.

    Region membership comes from the airport metadata table, never from a model.
    """
    if sort_by not in SORTABLE_FIELDS:
        raise ValueError(
            f"Unsupported sort field '{sort_by}'. Choose one of: "
            f"{', '.join(sorted(SORTABLE_FIELDS))}"
        )

    subset, resolution = filter_airports(dataset.airports, region)
    metrics = compute_metrics_frame(dataset, long_haul_miles)
    scores = score_frame(dataset, long_haul_miles)

    codes = [c for c in subset["iata"].tolist() if c in metrics.index]
    if not codes:
        return {
            "region_query": region,
            "region_resolution": resolution.to_dict(),
            "sort_by": sort_by,
            "sort_label": SORTABLE_FIELDS[sort_by],
            "count": 0,
            "results": [],
            "note": (
                f"No airports matched '{region}'. Recognised geographies include "
                "region names (e.g. New England) and US state names or codes."
            ),
        }

    merged = metrics.loc[codes].join(
        scores.loc[codes][["expansion_score", "rating"] +
                          [c for c in scores.columns if c.startswith(("pts_", "sub_"))]],
        rsuffix="_score",
    )
    if sort_by == "unmet_demand_index":
        merged["unmet_demand_index"] = [
            unmet_demand_proxy(dataset, c, long_haul_miles)["unmet_demand_index"]
            for c in merged.index
        ]

    merged = merged.sort_values(sort_by, ascending=ascending, kind="mergesort")
    limited = merged.head(max(1, int(limit)))

    results = []
    for position, (code, row) in enumerate(limited.iterrows(), start=1):
        results.append({
            "rank": position,
            "iata": code,
            "name": row["name"],
            "city": row["city"],
            "state": row["state"],
            "region": row["region"],
            "expansion_score": float(row["expansion_score"]),
            "rating": row["rating"],
            "sort_value": float(row[sort_by]),
            "passengers": int(row["passengers"]),
            "passenger_cagr": float(row["passenger_cagr"]),
            "flight_cagr": float(row["flight_cagr"]),
            "load_factor": float(row["load_factor"]),
            "passengers_per_gate": float(row["passengers_per_gate"]),
            "long_haul_departure_share": float(row["long_haul_departure_share"]),
            "component_points": {
                key: float(row[f"pts_{key}"]) for key in PILLAR_LABELS
            },
        })

    return {
        "region_query": region,
        "region_resolution": resolution.to_dict(),
        "sort_by": sort_by,
        "sort_label": SORTABLE_FIELDS[sort_by],
        "ascending": ascending,
        "candidate_pool": len(codes),
        "count": len(results),
        "results": results,
        "latest_year": int(metrics["latest_year"].iloc[0]),
    }


def compare_airports(
    dataset: AirportDataset,
    iatas: Iterable[str],
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> dict[str, Any]:
    """Side-by-side comparison of two or more airports, with explicit deltas."""
    codes = [resolve_iata(dataset, code) for code in iatas]
    unique = list(dict.fromkeys(codes))
    if len(unique) < 2:
        raise ValueError("Comparison needs at least two distinct airports.")

    metrics = compute_metrics_frame(dataset, long_haul_miles)
    airports = []
    for code in unique:
        bundle = metrics.loc[code].to_dict()
        score = expansion_score_from_metrics(bundle)
        proxy = unmet_demand_proxy(dataset, code, long_haul_miles)
        airports.append({
            "iata": code,
            "metrics": bundle,
            "score": score,
            "unmet_demand": {
                "unmet_demand_index": proxy["unmet_demand_index"],
                "interpretation": proxy["interpretation"],
                "is_proxy": True,
            },
        })

    comparison_fields = [
        ("expansion_score", "Airport Expansion Score", "score"),
        ("passengers", "Passengers (latest year)", "count"),
        ("flights", "Departures (latest year)", "count"),
        ("load_factor", "Seat utilization (load factor)", "pct"),
        ("departures_per_runway", "Departures per runway", "count"),
        ("passengers_per_gate", "Passengers per gate", "count"),
        ("passenger_cagr", "Passenger growth (CAGR)", "pct"),
        ("flight_cagr", "Flight growth (CAGR)", "pct"),
        ("capacity_growth_lag", "Passenger minus seat growth", "pp"),
        ("long_haul_departure_share", "Long-haul departure share", "pct"),
        ("unmet_demand_index", "Unmet Demand Proxy (0-100)", "score"),
    ]

    table = []
    for field, label, kind in comparison_fields:
        values: dict[str, float] = {}
        for entry in airports:
            if field == "expansion_score":
                values[entry["iata"]] = float(entry["score"]["expansion_score"])
            elif field == "unmet_demand_index":
                values[entry["iata"]] = float(entry["unmet_demand"]["unmet_demand_index"])
            else:
                values[entry["iata"]] = float(entry["metrics"][field])
        leader = max(values, key=values.get)
        spread = max(values.values()) - min(values.values())
        table.append({
            "field": field,
            "label": label,
            "kind": kind,
            "values": {k: round(v, 4) for k, v in values.items()},
            "leader": leader,
            "spread": round(spread, 4),
        })

    scores = {e["iata"]: e["score"]["expansion_score"] for e in airports}
    best = max(scores, key=scores.get)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    margin = round(ordered[0][1] - ordered[1][1], 1)

    return {
        "airports": airports,
        "iatas": unique,
        "table": table,
        "higher_score": best,
        "score_margin": margin,
        "verdict": (
            f"{best} scores {scores[best]:.1f} versus "
            + ", ".join(f"{k} at {v:.1f}" for k, v in ordered[1:])
            + f" — a {margin:.1f}-point margin on the Airport Expansion Score."
        ),
        "long_haul_threshold_miles": float(long_haul_miles),
        "latest_year": int(metrics["latest_year"].iloc[0]),
    }


def congestion_comparison(
    dataset: AirportDataset,
    iatas: Iterable[str],
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> dict[str, Any]:
    """Congestion-focused view: how hard each airport's physical plant works.

    'Congestion' here is defined as throughput per unit of physical capacity —
    departures per runway, passengers per gate, and seat utilization — because
    public schedule data does not include taxi-out or gate-hold delay minutes.
    """
    codes = [resolve_iata(dataset, code) for code in iatas]
    metrics = compute_metrics_frame(dataset, long_haul_miles)
    rows = []
    for code in dict.fromkeys(codes):
        bundle = metrics.loc[code].to_dict()
        rows.append({
            "iata": code,
            "name": bundle["name"],
            "runways": bundle["runways"],
            "gates": bundle["gates"],
            "slot_controlled": bool(bundle["slot_controlled"]),
            "passengers": bundle["passengers"],
            "flights": bundle["flights"],
            "departures_per_runway": bundle["departures_per_runway"],
            "passengers_per_gate": bundle["passengers_per_gate"],
            "load_factor": bundle["load_factor"],
            "demand_pressure_index": expansion_score_from_metrics(bundle)
            ["pillar_detail"]["demand_pressure"]["value"],
        })
    busiest_airfield = max(rows, key=lambda r: r["departures_per_runway"])["iata"]
    busiest_terminal = max(rows, key=lambda r: r["passengers_per_gate"])["iata"]
    return {
        "definition": (
            "Congestion is measured as throughput per unit of physical capacity: "
            "departures per runway (airside), passengers per gate (landside) and "
            "seat utilization. Public BTS data does not publish delay minutes per "
            "airport in this extract, so no delay-based congestion is claimed."
        ),
        "airports": rows,
        "busiest_airfield": busiest_airfield,
        "busiest_terminal": busiest_terminal,
        "latest_year": int(metrics["latest_year"].iloc[0]),
    }


def dataset_overview(
    dataset: AirportDataset,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> dict[str, Any]:
    """Small summary used by the UI header and the agent's system context."""
    metrics = compute_metrics_frame(dataset, long_haul_miles)
    scores = score_frame(dataset, long_haul_miles)
    return {
        "airport_count": int(len(metrics)),
        "route_count": int(len(dataset.routes)),
        "years": dataset.years,
        "latest_year": dataset.latest_year,
        "regions": sorted(metrics["region"].unique().tolist()),
        "mean_expansion_score": round(float(scores["expansion_score"].mean()), 1),
        "long_haul_threshold_miles": float(long_haul_miles),
        "provenance": dataset.provenance.to_dict(),
    }

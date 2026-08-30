"""The Airport Expansion Score and the Unmet Demand Proxy.

Both indices are computed here, in Python, from the metrics module. The LLM is
never asked to compute or adjust a score — it only explains the breakdown this
module produces.

Design choices
--------------
*Absolute, not cohort-relative.* Every component is scaled against fixed,
documented anchor bands rather than against the percentile rank of whatever
airports happen to be in the dataset. A score therefore means the same thing
whether you rank 9 airports or 900, and adding an airport never silently moves
everyone else's score.

*Every component is reproducible by hand.* The breakdown returns the raw input,
the anchor band, the 0-1 sub-score, the weight and the resulting points.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from app.analytics.metrics import (
    DEFAULT_LONG_HAUL_MILES,
    compute_metrics_frame,
    resolve_iata,
    scale,
)
from app.data.dataset import AirportDataset

# ---------------------------------------------------------------------------
# Anchor bands: (value scoring 0, value scoring 1). Fixed and documented.
# ---------------------------------------------------------------------------
ANCHORS: dict[str, tuple[float, float]] = {
    "load_factor": (0.76, 0.88),
    "departures_per_runway": (5_000.0, 90_000.0),
    "passengers_per_gate": (60_000.0, 400_000.0),
    "passenger_cagr": (0.005, 0.07),
    "flight_cagr": (0.005, 0.055),
    "capacity_growth_lag": (-0.005, 0.02),
    "long_haul_departure_share": (0.0, 0.25),
}

#: Headline weights, summing to 1.0.
WEIGHTS: dict[str, float] = {
    "demand_pressure": 0.30,
    "passenger_growth": 0.25,
    "capacity_constraint": 0.20,
    "flight_growth": 0.15,
    "long_haul_connectivity": 0.10,
}

#: How each composite pillar is assembled from its own sub-signals.
DEMAND_PRESSURE_MIX: dict[str, float] = {
    "load_factor": 0.60,
    "departures_per_runway": 0.40,
}
CAPACITY_CONSTRAINT_MIX: dict[str, float] = {
    "passengers_per_gate": 0.55,
    "slot_controlled": 0.20,
    "capacity_growth_lag": 0.25,
}

#: Unmet-demand proxy weights (a separate index, not part of the score).
UNMET_DEMAND_MIX: dict[str, float] = {
    "seat_utilization_pressure": 0.35,
    "passenger_growth": 0.25,
    "capacity_growth_lag": 0.25,
    "flight_growth": 0.15,
}
UTILIZATION_PRESSURE_ANCHOR = (0.78, 0.90)

PILLAR_LABELS: dict[str, str] = {
    "demand_pressure": "Demand pressure",
    "passenger_growth": "Passenger growth",
    "capacity_constraint": "Capacity constraint",
    "flight_growth": "Flight growth",
    "long_haul_connectivity": "Long-haul connectivity",
}

UNMET_DEMAND_DISCLAIMER = (
    "This is a PROXY, not a measurement. Public aviation datasets record flights "
    "that were actually operated and passengers who actually flew; they contain no "
    "record of the trips people wanted to take but could not book, or of fares that "
    "priced demand out. The index below infers demand pressure from four observable "
    "signals and should be treated as a screening flag for further study, not as an "
    "estimate of latent passengers."
)


@dataclass(frozen=True)
class Component:
    """One scored line of the breakdown."""

    key: str
    label: str
    raw_value: float
    raw_display: str
    anchor_low: float
    anchor_high: float
    sub_score: float
    weight: float
    points: float
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "raw_value": round(float(self.raw_value), 6),
            "raw_display": self.raw_display,
            "anchor_low": self.anchor_low,
            "anchor_high": self.anchor_high,
            "sub_score": round(float(self.sub_score), 4),
            "weight": self.weight,
            "weight_pct": round(self.weight * 100, 1),
            "points": round(float(self.points), 2),
            "max_points": round(self.weight * 100, 1),
            "explanation": self.explanation,
        }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _pct(value: float, digits: int = 1) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def _num(value: float) -> str:
    return f"{float(value):,.0f}"


def _band(key: str) -> tuple[float, float]:
    return ANCHORS[key]


def _scored(key: str, value: float) -> float:
    low, high = _band(key)
    return scale(value, low, high)


# ---------------------------------------------------------------------------
# Pillars
# ---------------------------------------------------------------------------
def demand_pressure(metrics: dict[str, Any]) -> dict[str, Any]:
    """How hard current demand presses on the aircraft and the airfield.

    Load factor says the aircraft are full; departures per runway says the
    airfield is busy. Both are 'the asset is working hard' signals.
    """
    lf = float(metrics["load_factor"])
    dpr = float(metrics["departures_per_runway"])
    lf_score = _scored("load_factor", lf)
    dpr_score = _scored("departures_per_runway", dpr)
    value = (DEMAND_PRESSURE_MIX["load_factor"] * lf_score
             + DEMAND_PRESSURE_MIX["departures_per_runway"] * dpr_score)
    return {
        "value": round(value, 4),
        "inputs": [
            {"key": "load_factor", "label": "Seat utilization (load factor)",
             "raw_value": lf, "raw_display": _pct(lf),
             "sub_score": round(lf_score, 4),
             "weight_within_pillar": DEMAND_PRESSURE_MIX["load_factor"],
             "anchor_low": _band("load_factor")[0], "anchor_high": _band("load_factor")[1]},
            {"key": "departures_per_runway", "label": "Departures per runway per year",
             "raw_value": dpr, "raw_display": _num(dpr),
             "sub_score": round(dpr_score, 4),
             "weight_within_pillar": DEMAND_PRESSURE_MIX["departures_per_runway"],
             "anchor_low": _band("departures_per_runway")[0],
             "anchor_high": _band("departures_per_runway")[1]},
        ],
        "explanation": (
            f"Aircraft leave {metrics['iata']} {_pct(lf)} full and each runway "
            f"handles {_num(dpr)} departures a year."
        ),
    }


def capacity_constraint(metrics: dict[str, Any]) -> dict[str, Any]:
    """How constrained the *terminal* is — the thing an expansion actually fixes.

    Passengers per gate is the core landside throughput signal; slot control is
    a hard regulatory ceiling; and seat capacity growing slower than passenger
    growth says the facility is not keeping up.
    """
    ppg = float(metrics["passengers_per_gate"])
    slot = bool(metrics["slot_controlled"])
    lag = float(metrics["capacity_growth_lag"])
    ppg_score = _scored("passengers_per_gate", ppg)
    slot_score = 1.0 if slot else 0.0
    lag_score = _scored("capacity_growth_lag", lag)
    value = (CAPACITY_CONSTRAINT_MIX["passengers_per_gate"] * ppg_score
             + CAPACITY_CONSTRAINT_MIX["slot_controlled"] * slot_score
             + CAPACITY_CONSTRAINT_MIX["capacity_growth_lag"] * lag_score)
    return {
        "value": round(value, 4),
        "inputs": [
            {"key": "passengers_per_gate", "label": "Passengers per gate per year",
             "raw_value": ppg, "raw_display": _num(ppg),
             "sub_score": round(ppg_score, 4),
             "weight_within_pillar": CAPACITY_CONSTRAINT_MIX["passengers_per_gate"],
             "anchor_low": _band("passengers_per_gate")[0],
             "anchor_high": _band("passengers_per_gate")[1]},
            {"key": "slot_controlled", "label": "Slot-controlled airport",
             "raw_value": float(slot), "raw_display": "yes" if slot else "no",
             "sub_score": slot_score,
             "weight_within_pillar": CAPACITY_CONSTRAINT_MIX["slot_controlled"],
             "anchor_low": 0.0, "anchor_high": 1.0},
            {"key": "capacity_growth_lag", "label": "Passenger growth minus seat growth",
             "raw_value": lag, "raw_display": f"{lag * 100:+.2f} pp/yr",
             "sub_score": round(lag_score, 4),
             "weight_within_pillar": CAPACITY_CONSTRAINT_MIX["capacity_growth_lag"],
             "anchor_low": _band("capacity_growth_lag")[0],
             "anchor_high": _band("capacity_growth_lag")[1]},
        ],
        "explanation": (
            f"{_num(ppg)} passengers per gate per year"
            + (", slot-controlled" if slot else ", not slot-controlled")
            + f", with seat capacity growing {abs(lag) * 100:.2f} pp/yr "
            + ("slower" if lag > 0 else "faster") + " than passengers."
        ),
    }


# ---------------------------------------------------------------------------
# Expansion score
# ---------------------------------------------------------------------------
def expansion_score_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Compute the 0-100 Airport Expansion Score for one metric bundle."""
    dp = demand_pressure(metrics)
    cc = capacity_constraint(metrics)
    pg = float(metrics["passenger_cagr"])
    fg = float(metrics["flight_cagr"])
    lh = float(metrics["long_haul_departure_share"])

    pillars: list[Component] = [
        Component(
            key="demand_pressure",
            label=PILLAR_LABELS["demand_pressure"],
            raw_value=dp["value"], raw_display=f"{dp['value']:.2f} index",
            anchor_low=0.0, anchor_high=1.0,
            sub_score=dp["value"], weight=WEIGHTS["demand_pressure"],
            points=dp["value"] * WEIGHTS["demand_pressure"] * 100,
            explanation=dp["explanation"],
        ),
        Component(
            key="passenger_growth",
            label=PILLAR_LABELS["passenger_growth"],
            raw_value=pg, raw_display=f"{pg * 100:.2f}% CAGR",
            anchor_low=_band("passenger_cagr")[0], anchor_high=_band("passenger_cagr")[1],
            sub_score=_scored("passenger_cagr", pg), weight=WEIGHTS["passenger_growth"],
            points=_scored("passenger_cagr", pg) * WEIGHTS["passenger_growth"] * 100,
            explanation=(
                f"Passengers grew {pg * 100:.2f}% a year between "
                f"{metrics['base_year']} and {metrics['latest_year']}."
            ),
        ),
        Component(
            key="capacity_constraint",
            label=PILLAR_LABELS["capacity_constraint"],
            raw_value=cc["value"], raw_display=f"{cc['value']:.2f} index",
            anchor_low=0.0, anchor_high=1.0,
            sub_score=cc["value"], weight=WEIGHTS["capacity_constraint"],
            points=cc["value"] * WEIGHTS["capacity_constraint"] * 100,
            explanation=cc["explanation"],
        ),
        Component(
            key="flight_growth",
            label=PILLAR_LABELS["flight_growth"],
            raw_value=fg, raw_display=f"{fg * 100:.2f}% CAGR",
            anchor_low=_band("flight_cagr")[0], anchor_high=_band("flight_cagr")[1],
            sub_score=_scored("flight_cagr", fg), weight=WEIGHTS["flight_growth"],
            points=_scored("flight_cagr", fg) * WEIGHTS["flight_growth"] * 100,
            explanation=(
                f"Departures grew {fg * 100:.2f}% a year, showing airlines are "
                "adding (or withholding) supply."
            ),
        ),
        Component(
            key="long_haul_connectivity",
            label=PILLAR_LABELS["long_haul_connectivity"],
            raw_value=lh, raw_display=_pct(lh),
            anchor_low=_band("long_haul_departure_share")[0],
            anchor_high=_band("long_haul_departure_share")[1],
            sub_score=_scored("long_haul_departure_share", lh),
            weight=WEIGHTS["long_haul_connectivity"],
            points=_scored("long_haul_departure_share", lh) * WEIGHTS["long_haul_connectivity"] * 100,
            explanation=(
                f"{_pct(lh)} of departures are long-haul "
                f"(>= {metrics['long_haul_threshold_miles']:,.0f} miles), which drives "
                "wide-body gate, customs and lounge requirements."
            ),
        ),
    ]

    score = sum(c.points for c in pillars)
    components = [c.to_dict() for c in pillars]
    ranked = sorted(components, key=lambda c: c["points"], reverse=True)
    gaps = sorted(components, key=lambda c: c["max_points"] - c["points"], reverse=True)

    return {
        "iata": metrics["iata"],
        "name": metrics["name"],
        "city": metrics["city"],
        "state": metrics["state"],
        "region": metrics["region"],
        "expansion_score": round(score, 1),
        "rating": rating_for(score),
        "components": components,
        "pillar_detail": {"demand_pressure": dp, "capacity_constraint": cc},
        "top_drivers": [c["label"] for c in ranked[:2]],
        "biggest_gaps": [c["label"] for c in gaps[:2]],
        "weights": {k: round(v * 100, 1) for k, v in WEIGHTS.items()},
        "methodology": (
            "Expansion Score = 30% demand pressure + 25% passenger growth + "
            "20% capacity constraint + 15% flight growth + 10% long-haul "
            "connectivity. Each component is scaled 0-1 against a fixed anchor "
            "band (not against other airports), then weighted and summed to 0-100."
        ),
        "latest_year": metrics["latest_year"],
        "base_year": metrics["base_year"],
    }


def rating_for(score: float) -> str:
    """Bucket a 0-100 score into a screening label."""
    if score >= 70:
        return "Strong candidate"
    if score >= 55:
        return "Promising"
    if score >= 40:
        return "Watch list"
    return "Low priority"


def expansion_score(
    dataset: AirportDataset,
    iata: str,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> dict[str, Any]:
    """Score a single airport by IATA code."""
    code = resolve_iata(dataset, iata)
    frame = compute_metrics_frame(dataset, long_haul_miles)
    return expansion_score_from_metrics(frame.loc[code].to_dict())


def score_frame(
    dataset: AirportDataset,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> pd.DataFrame:
    """Score every airport in the dataset; returns one row per airport."""
    frame = compute_metrics_frame(dataset, long_haul_miles)
    rows = []
    for code in frame.index:
        result = expansion_score_from_metrics(frame.loc[code].to_dict())
        row = {
            "iata": code,
            "name": result["name"],
            "city": result["city"],
            "state": result["state"],
            "region": result["region"],
            "expansion_score": result["expansion_score"],
            "rating": result["rating"],
        }
        for component in result["components"]:
            row[f"pts_{component['key']}"] = component["points"]
            row[f"sub_{component['key']}"] = component["sub_score"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("iata", drop=False)


# ---------------------------------------------------------------------------
# Unmet demand proxy
# ---------------------------------------------------------------------------
def unmet_demand_proxy(
    dataset: AirportDataset,
    iata: str,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> dict[str, Any]:
    """A 0-100 screening proxy for demand the current facility may not be serving.

    Deliberately NOT called an estimate of latent demand: see
    :data:`UNMET_DEMAND_DISCLAIMER`.
    """
    code = resolve_iata(dataset, iata)
    metrics = compute_metrics_frame(dataset, long_haul_miles).loc[code].to_dict()

    lf = float(metrics["load_factor"])
    pg = float(metrics["passenger_cagr"])
    lag = float(metrics["capacity_growth_lag"])
    fg = float(metrics["flight_cagr"])

    util_score = scale(lf, *UTILIZATION_PRESSURE_ANCHOR)
    growth_score = _scored("passenger_cagr", pg)
    lag_score = _scored("capacity_growth_lag", lag)
    flight_score = _scored("flight_cagr", fg)

    signals = [
        {"key": "seat_utilization_pressure",
         "label": "Seat utilization pressure",
         "raw_value": lf, "raw_display": _pct(lf),
         "sub_score": round(util_score, 4),
         "weight": UNMET_DEMAND_MIX["seat_utilization_pressure"],
         "points": round(util_score * UNMET_DEMAND_MIX["seat_utilization_pressure"] * 100, 2),
         "reads_as": ("Aircraft are leaving close to full, so incremental demand has "
                      "nowhere obvious to sit.")},
        {"key": "passenger_growth",
         "label": "Passenger growth",
         "raw_value": pg, "raw_display": f"{pg * 100:.2f}% CAGR",
         "sub_score": round(growth_score, 4),
         "weight": UNMET_DEMAND_MIX["passenger_growth"],
         "points": round(growth_score * UNMET_DEMAND_MIX["passenger_growth"] * 100, 2),
         "reads_as": "Demand for the airport is expanding year over year."},
        {"key": "capacity_growth_lag",
         "label": "Capacity growth lagging passenger growth",
         "raw_value": lag, "raw_display": f"{lag * 100:+.2f} pp/yr",
         "sub_score": round(lag_score, 4),
         "weight": UNMET_DEMAND_MIX["capacity_growth_lag"],
         "points": round(lag_score * UNMET_DEMAND_MIX["capacity_growth_lag"] * 100, 2),
         "reads_as": ("Seats are being added more slowly than passengers are arriving, "
                      "the clearest observable sign of a supply ceiling.")},
        {"key": "flight_growth",
         "label": "Flight growth",
         "raw_value": fg, "raw_display": f"{fg * 100:.2f}% CAGR",
         "sub_score": round(flight_score, 4),
         "weight": UNMET_DEMAND_MIX["flight_growth"],
         "points": round(flight_score * UNMET_DEMAND_MIX["flight_growth"] * 100, 2),
         "reads_as": "Airlines are adding departures, or are unable to."},
    ]
    index = sum(s["points"] for s in signals)
    drivers = sorted(signals, key=lambda s: s["points"], reverse=True)

    return {
        "iata": code,
        "name": metrics["name"],
        "unmet_demand_index": round(index, 1),
        "interpretation": _unmet_interpretation(index),
        "is_proxy": True,
        "disclaimer": UNMET_DEMAND_DISCLAIMER,
        "signals": signals,
        "top_signals": [d["label"] for d in drivers[:2]],
        "latest_year": metrics["latest_year"],
        "base_year": metrics["base_year"],
    }


def _unmet_interpretation(index: float) -> str:
    if index >= 70:
        return "Strong proxy signal of demand pressing against available capacity."
    if index >= 50:
        return "Moderate proxy signal; worth a closer look at gate and slot headroom."
    if index >= 30:
        return "Mild proxy signal; capacity appears broadly adequate for now."
    return "Little proxy evidence of constrained demand."

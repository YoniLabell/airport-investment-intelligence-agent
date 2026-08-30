"""Deterministic airport metrics.

Every number surfaced by the API, the UI or the agent originates in this
module. Definitions are fixed and documented so two analysts reading the same
dataset always get the same answer.

Key definitions
---------------
long-haul      A non-stop segment whose great-circle distance is
               ``>= LONG_HAUL_MILES`` (default 2,500 statute miles).
load factor    latest-year passengers / latest-year seats.
CAGR           compound annual growth rate across the dataset's year span.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.data.dataset import AirportDataset
from app.logging_config import get_logger

log = get_logger(__name__)

DEFAULT_LONG_HAUL_MILES = 2500.0

_METRIC_CACHE: dict[tuple[int, float], pd.DataFrame] = {}
_CACHE_LIMIT = 8


class UnknownAirportError(KeyError):
    """Raised when an IATA code is not present in the active dataset."""

    def __init__(self, iata: str, available: list[str]) -> None:
        self.iata = iata
        self.available = available
        super().__init__(
            f"Unknown airport '{iata}'. This dataset covers {len(available)} "
            f"airports, e.g. {', '.join(available[:8])}."
        )


@dataclass(frozen=True)
class AirportMetrics:
    """One airport's deterministic metric bundle."""

    iata: str
    name: str
    city: str
    state: str
    region: str

    base_year: int
    latest_year: int
    years_span: int

    passengers: int
    seats: int
    flights: int

    runways: int
    gates: int
    slot_controlled: bool

    load_factor: float
    passengers_per_gate: float
    departures_per_runway: float

    passenger_cagr: float
    seat_cagr: float
    flight_cagr: float
    capacity_growth_lag: float

    route_count: int
    long_haul_route_count: int
    long_haul_departure_share: float
    long_haul_seat_share: float
    long_haul_passenger_share: float
    average_stage_length_miles: float
    long_haul_threshold_miles: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------
def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Divide, returning ``default`` instead of raising on a zero denominator."""
    try:
        if denominator in (0, None) or pd.isna(denominator):
            return default
        value = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return default
    return default if pd.isna(value) else value


def cagr(first_value: float, last_value: float, periods: int) -> float:
    """Compound annual growth rate over ``periods`` years.

    Returns 0.0 when the series is too short or the base value is non-positive,
    which is the honest answer for "we cannot measure growth here".
    """
    if periods <= 0 or first_value is None or last_value is None:
        return 0.0
    if first_value <= 0 or last_value <= 0:
        return 0.0
    return float((last_value / first_value) ** (1.0 / periods) - 1.0)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def scale(value: float, low: float, high: float) -> float:
    """Linearly map ``value`` from the ``[low, high]`` anchor band onto 0..1."""
    if high == low:
        return 0.0
    return clamp((float(value) - low) / (high - low))


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------
def compute_metrics_frame(
    dataset: AirportDataset,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> pd.DataFrame:
    """Compute the full metric table for every airport in the dataset.

    Memoized per (dataset identity, threshold): the frame is pure a function of
    its inputs, and recomputing it per request is wasted work.
    """
    key = (id(dataset), float(long_haul_miles))
    cached = _METRIC_CACHE.get(key)
    if cached is not None:
        return cached

    annual = dataset.annual.copy()
    annual["year"] = annual["year"].astype(int)
    latest_year = int(annual["year"].max())
    base_year = int(annual["year"].min())
    span = max(latest_year - base_year, 0)

    latest = annual[annual["year"] == latest_year].set_index("iata")
    base = annual[annual["year"] == base_year].set_index("iata")

    routes = dataset.routes.copy()
    routes["is_long_haul"] = routes["distance_miles"].astype(float) >= float(long_haul_miles)
    route_totals = routes.groupby("origin").agg(
        route_count=("destination", "nunique"),
        total_departures=("departures_performed", "sum"),
        total_route_seats=("seats", "sum"),
        total_route_passengers=("passengers", "sum"),
        weighted_distance=("distance_miles",
                           lambda s: 0.0),  # replaced below
    )
    # Departure-weighted stage length needs two columns, so compute separately.
    routes["_dist_x_dep"] = routes["distance_miles"] * routes["departures_performed"]
    route_totals["weighted_distance"] = routes.groupby("origin")["_dist_x_dep"].sum()

    long_haul = routes[routes["is_long_haul"]].groupby("origin").agg(
        long_haul_route_count=("destination", "nunique"),
        long_haul_departures=("departures_performed", "sum"),
        long_haul_seats=("seats", "sum"),
        long_haul_passengers=("passengers", "sum"),
    )

    records: list[dict[str, Any]] = []
    for row in dataset.airports.itertuples():
        iata = str(row.iata)
        if iata not in latest.index:
            log.debug("skipping %s: no volume data for %d", iata, latest_year)
            continue
        latest_row = latest.loc[iata]
        base_row = base.loc[iata] if iata in base.index else latest_row

        passengers = float(latest_row["passengers"])
        seats = float(latest_row["seats"])
        flights = float(latest_row["flights"])
        gates = float(getattr(row, "gates", 0) or 0)
        runways = float(getattr(row, "runways", 0) or 0)

        totals = route_totals.loc[iata] if iata in route_totals.index else None
        lh = long_haul.loc[iata] if iata in long_haul.index else None
        total_dep = float(totals["total_departures"]) if totals is not None else 0.0
        route_seats = float(totals["total_route_seats"]) if totals is not None else 0.0
        route_pax = float(totals["total_route_passengers"]) if totals is not None else 0.0

        metrics = AirportMetrics(
            iata=iata,
            name=str(row.name),
            city=str(row.city),
            state=str(row.state),
            region=str(row.region),
            base_year=base_year,
            latest_year=latest_year,
            years_span=span,
            passengers=int(round(passengers)),
            seats=int(round(seats)),
            flights=int(round(flights)),
            runways=int(runways),
            gates=int(gates),
            slot_controlled=bool(getattr(row, "slot_controlled", False)),
            load_factor=round(safe_ratio(passengers, seats), 4),
            passengers_per_gate=round(safe_ratio(passengers, gates), 1),
            departures_per_runway=round(safe_ratio(flights, runways), 1),
            passenger_cagr=round(cagr(float(base_row["passengers"]), passengers, span), 5),
            seat_cagr=round(cagr(float(base_row["seats"]), seats, span), 5),
            flight_cagr=round(cagr(float(base_row["flights"]), flights, span), 5),
            capacity_growth_lag=round(
                cagr(float(base_row["passengers"]), passengers, span)
                - cagr(float(base_row["seats"]), seats, span), 5),
            route_count=int(totals["route_count"]) if totals is not None else 0,
            long_haul_route_count=int(lh["long_haul_route_count"]) if lh is not None else 0,
            long_haul_departure_share=round(
                safe_ratio(float(lh["long_haul_departures"]) if lh is not None else 0.0,
                           total_dep), 4),
            long_haul_seat_share=round(
                safe_ratio(float(lh["long_haul_seats"]) if lh is not None else 0.0,
                           route_seats), 4),
            long_haul_passenger_share=round(
                safe_ratio(float(lh["long_haul_passengers"]) if lh is not None else 0.0,
                           route_pax), 4),
            average_stage_length_miles=round(
                safe_ratio(float(totals["weighted_distance"]) if totals is not None else 0.0,
                           total_dep), 1),
            long_haul_threshold_miles=float(long_haul_miles),
        )
        records.append(metrics.to_dict())

    frame = pd.DataFrame.from_records(records).set_index("iata", drop=False)
    if len(_METRIC_CACHE) >= _CACHE_LIMIT:
        _METRIC_CACHE.clear()
    _METRIC_CACHE[key] = frame
    return frame


def resolve_iata(dataset: AirportDataset, iata: str) -> str:
    """Normalize and validate an airport code."""
    code = str(iata or "").strip().upper()
    if not dataset.has_airport(code):
        raise UnknownAirportError(code, dataset.iata_codes)
    return code


def get_airport_metrics(
    dataset: AirportDataset,
    iata: str,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
) -> dict[str, Any]:
    """Metric bundle for one airport."""
    code = resolve_iata(dataset, iata)
    frame = compute_metrics_frame(dataset, long_haul_miles)
    if code not in frame.index:
        raise UnknownAirportError(code, list(frame.index))
    return frame.loc[code].to_dict()


# ---------------------------------------------------------------------------
# Long-haul
# ---------------------------------------------------------------------------
def long_haul_breakdown(
    dataset: AirportDataset,
    iata: str,
    long_haul_miles: float = DEFAULT_LONG_HAUL_MILES,
    top_n: int = 10,
) -> dict[str, Any]:
    """Long-haul share for one airport, with the routes that drive it.

    ``long_haul`` is defined purely by distance: ``distance >= threshold``.
    """
    code = resolve_iata(dataset, iata)
    routes = dataset.routes[dataset.routes["origin"] == code].copy()
    if routes.empty:
        return {
            "iata": code,
            "long_haul_threshold_miles": float(long_haul_miles),
            "definition": f"non-stop segments of at least {long_haul_miles:,.0f} statute miles",
            "route_count": 0,
            "long_haul_route_count": 0,
            "total_departures": 0,
            "long_haul_departures": 0,
            "long_haul_departure_share": 0.0,
            "long_haul_seat_share": 0.0,
            "long_haul_passenger_share": 0.0,
            "average_stage_length_miles": 0.0,
            "top_long_haul_routes": [],
            "note": "No route records for this airport in the active dataset.",
        }

    routes["is_long_haul"] = routes["distance_miles"].astype(float) >= float(long_haul_miles)
    lh = routes[routes["is_long_haul"]]
    total_dep = float(routes["departures_performed"].sum())
    total_seats = float(routes["seats"].sum())
    total_pax = float(routes["passengers"].sum())

    top = (
        lh.sort_values("departures_performed", ascending=False)
        .head(top_n)[["destination", "distance_miles", "departures_performed", "seats", "passengers"]]
    )
    return {
        "iata": code,
        "long_haul_threshold_miles": float(long_haul_miles),
        "definition": f"non-stop segments of at least {long_haul_miles:,.0f} statute miles",
        "route_count": int(routes["destination"].nunique()),
        "long_haul_route_count": int(lh["destination"].nunique()),
        "total_departures": int(total_dep),
        "long_haul_departures": int(lh["departures_performed"].sum()),
        "long_haul_departure_share": round(safe_ratio(lh["departures_performed"].sum(), total_dep), 4),
        "long_haul_seat_share": round(safe_ratio(lh["seats"].sum(), total_seats), 4),
        "long_haul_passenger_share": round(safe_ratio(lh["passengers"].sum(), total_pax), 4),
        "average_stage_length_miles": round(
            safe_ratio((routes["distance_miles"] * routes["departures_performed"]).sum(), total_dep), 1),
        "top_long_haul_routes": [
            {"destination": r.destination,
             "distance_miles": float(r.distance_miles),
             "departures": int(r.departures_performed),
             "seats": int(r.seats),
             "passengers": int(r.passengers)}
            for r in top.itertuples()
        ],
    }

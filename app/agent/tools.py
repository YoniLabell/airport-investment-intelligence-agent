"""The deterministic toolbox exposed to the agent.

Each entry pairs a JSON schema (what Claude sees) with a Python callable that
runs real pandas analytics. Claude picks tools and narrates the results; it is
never the thing that adds, divides or ranks.
"""

from __future__ import annotations

from typing import Any, Callable

from app.analytics.metrics import (
    UnknownAirportError,
    get_airport_metrics,
    long_haul_breakdown,
)
from app.analytics.ranking import (
    SORTABLE_FIELDS,
    compare_airports,
    congestion_comparison,
    dataset_overview,
    rank_airports,
)
from app.analytics.regions import (
    CANONICAL_REGIONS,
    filter_airports,
    list_regions,
)
from app.analytics.metrics import resolve_iata
from app.analytics.scoring import expansion_score, unmet_demand_proxy
from app.data.dataset import AirportDataset
from app.services.aviation_weather import get_weather_provider
from app.logging_config import get_logger

log = get_logger(__name__)

ToolFn = Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------------
# Tool implementations (thin wrappers around the analytics layer)
# ---------------------------------------------------------------------------
def _list_airports(dataset: AirportDataset, long_haul_miles: float,
                   region: str | None = None) -> dict[str, Any]:
    subset, resolution = filter_airports(dataset.airports, region)
    return {
        "region_query": region,
        "region_resolution": resolution.to_dict(),
        "count": int(len(subset)),
        "airports": [
            {"iata": r.iata, "name": r.name, "city": r.city,
             "state": r.state, "region": r.region}
            for r in subset.itertuples()
        ],
        "available_regions": list(CANONICAL_REGIONS),
    }


def _regions(dataset: AirportDataset, long_haul_miles: float) -> dict[str, Any]:
    return {"regions": list_regions(dataset.airports)}


def _metrics(dataset: AirportDataset, long_haul_miles: float, iata: str) -> dict[str, Any]:
    return {"metrics": get_airport_metrics(dataset, iata, long_haul_miles)}


def _score(dataset: AirportDataset, long_haul_miles: float, iata: str) -> dict[str, Any]:
    return expansion_score(dataset, iata, long_haul_miles)


def _compare(dataset: AirportDataset, long_haul_miles: float,
             iatas: list[str]) -> dict[str, Any]:
    return compare_airports(dataset, iatas, long_haul_miles)


def _congestion(dataset: AirportDataset, long_haul_miles: float,
                iatas: list[str]) -> dict[str, Any]:
    return congestion_comparison(dataset, iatas, long_haul_miles)


def _rank(dataset: AirportDataset, long_haul_miles: float, region: str | None = None,
          limit: int = 10, sort_by: str = "expansion_score") -> dict[str, Any]:
    return rank_airports(dataset, region=region, limit=limit,
                         sort_by=sort_by, long_haul_miles=long_haul_miles)


def _long_haul(dataset: AirportDataset, long_haul_miles: float, iata: str) -> dict[str, Any]:
    return long_haul_breakdown(dataset, iata, long_haul_miles)


def _unmet(dataset: AirportDataset, long_haul_miles: float, iata: str) -> dict[str, Any]:
    return unmet_demand_proxy(dataset, iata, long_haul_miles)


def _overview(dataset: AirportDataset, long_haul_miles: float) -> dict[str, Any]:
    return dataset_overview(dataset, long_haul_miles)


def _conditions(dataset: AirportDataset, long_haul_miles: float, iata: str) -> dict[str, Any]:
    """Current weather at an airport — live context, never an analytics input.

    The provider is resolved at call time rather than import time so the
    singleton can be swapped in tests.
    """
    code = resolve_iata(dataset, iata)
    return get_weather_provider().get_conditions(code)


_IATA = {
    "type": "string",
    "description": "Three-letter IATA airport code, e.g. SFO, BOS, ANC.",
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "list_airports",
        "description": (
            "List the airports covered by the dataset, optionally filtered to a "
            "region ('New England', 'Pacific West', ...) or a US state name/code. "
            "Use this whenever a question names a geography — region membership "
            "comes from the dataset, not from your own knowledge."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Region name, state name, or two-letter state code.",
                }
            },
        },
        "fn": _list_airports,
    },
    {
        "name": "list_regions",
        "description": "List every region in the dataset and which airports belong to it.",
        "input_schema": {"type": "object", "properties": {}},
        "fn": _regions,
    },
    {
        "name": "get_airport_metrics",
        "description": (
            "Full deterministic metric bundle for one airport: passengers, seats, "
            "departures, load factor, passengers per gate, departures per runway, "
            "growth rates and long-haul share."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"iata": _IATA},
            "required": ["iata"],
        },
        "fn": _metrics,
    },
    {
        "name": "get_expansion_score",
        "description": (
            "The 0-100 Airport Expansion Score for one airport with a full "
            "component breakdown (raw value, anchor band, sub-score, weight and "
            "points for each of the five pillars). Use this to explain WHY an "
            "airport scored what it scored."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"iata": _IATA},
            "required": ["iata"],
        },
        "fn": _score,
    },
    {
        "name": "compare_airports",
        "description": (
            "Side-by-side comparison of two or more airports across scores, "
            "volumes, utilization, growth and the unmet-demand proxy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "iatas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "description": "Two or more IATA codes.",
                }
            },
            "required": ["iatas"],
        },
        "fn": _compare,
    },
    {
        "name": "compare_congestion",
        "description": (
            "Congestion-focused comparison: departures per runway, passengers per "
            "gate, load factor and slot control. Use this for questions phrased in "
            "terms of congestion, crowding or busy-ness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "iatas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "One or more IATA codes.",
                }
            },
            "required": ["iatas"],
        },
        "fn": _congestion,
    },
    {
        "name": "rank_airports",
        "description": (
            "Rank airports by a chosen metric, optionally within a region or "
            "state. This is the tool for 'which airports are the strongest "
            "candidates' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Region name, state name, or state code. Omit for nationwide.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                          "description": "How many airports to return (default 10)."},
                "sort_by": {
                    "type": "string",
                    "enum": sorted(SORTABLE_FIELDS),
                    "description": "Metric to rank by (default expansion_score).",
                },
            },
        },
        "fn": _rank,
    },
    {
        "name": "get_long_haul_share",
        "description": (
            "Share of an airport's departures, seats and passengers that are "
            "long-haul, plus the routes driving it. Long-haul is defined purely "
            "by non-stop great-circle distance against a fixed threshold."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"iata": _IATA},
            "required": ["iata"],
        },
        "fn": _long_haul,
    },
    {
        "name": "get_unmet_demand_proxy",
        "description": (
            "The 0-100 Unmet Demand Proxy for one airport, with the four signals "
            "behind it. Always relay that this is a proxy, not a measurement of "
            "true latent demand."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"iata": _IATA},
            "required": ["iata"],
        },
        "fn": _unmet,
    },
    {
        "name": "get_airport_conditions",
        "description": (
            "CURRENT weather and operating conditions at an airport, from "
            "AviationWeather.gov (NOAA/NWS) METAR observations: flight category, "
            "visibility, wind, present weather, ceiling, temperature and the raw "
            "METAR. This is LIVE OPERATIONAL CONTEXT ONLY — it is not part of any "
            "score, metric or ranking, all of which come from historical US DOT / "
            "BTS data. Never let conditions influence an investment judgement, and "
            "say plainly that today's weather is not an expansion signal if the "
            "user implies otherwise. Check the 'status' field: it is 'ok' only "
            "when an observation was returned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"iata": _IATA},
            "required": ["iata"],
        },
        "fn": _conditions,
    },
    {
        "name": "get_dataset_overview",
        "description": (
            "Dataset coverage and provenance: how many airports, which years, and "
            "whether the active data is live, cached or demo."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "fn": _overview,
    },
]

TOOL_REGISTRY: dict[str, ToolFn] = {spec["name"]: spec["fn"] for spec in TOOL_SPECS}


def anthropic_tool_definitions() -> list[dict[str, Any]]:
    """The tool list in the shape the Anthropic Messages API expects."""
    return [
        {"name": s["name"], "description": s["description"],
         "input_schema": s["input_schema"]}
        for s in TOOL_SPECS
    ]


def run_tool(
    name: str,
    arguments: dict[str, Any],
    dataset: AirportDataset,
    long_haul_miles: float,
) -> dict[str, Any]:
    """Execute a tool by name, converting failures into structured errors.

    Errors are returned rather than raised so the agent can recover (e.g. by
    asking the user for a valid airport code) instead of the request 500-ing.
    """
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"Unknown tool '{name}'.",
                "available_tools": sorted(TOOL_REGISTRY)}
    try:
        log.info("tool %s(%s)", name, arguments)
        return fn(dataset, long_haul_miles, **(arguments or {}))
    except UnknownAirportError as exc:
        return {"error": str(exc), "known_airports": exc.available}
    except (ValueError, TypeError, KeyError) as exc:
        return {"error": f"{name} failed: {exc}"}
    except Exception as exc:  # noqa: BLE001 - a tool must never crash the turn
        log.exception("unexpected failure in tool %s", name)
        return {"error": f"{name} raised an unexpected error: {exc}"}

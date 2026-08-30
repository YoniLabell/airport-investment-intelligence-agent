"""Deterministic question router used when Claude is unavailable.

Two jobs:

1. Keep the product demoable with no ``ANTHROPIC_API_KEY`` — every question
   still returns real, correct numbers, just in template prose rather than
   Claude's narration.
2. Give the chat endpoint a safe degraded mode when the Anthropic API errors.

Answers produced here are always labelled so nobody mistakes them for the
AI-narrated response.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.agent.tools import run_tool
from app.analytics.regions import REGION_ALIASES, resolve_region
from app.data.dataset import AirportDataset
from app.logging_config import get_logger

log = get_logger(__name__)

CODE_PATTERN = re.compile(r"\b([A-Z]{3})\b")

COMPARE_WORDS = ("compare", " vs ", " vs.", "versus", "against", "better", "which one")
CONGESTION_WORDS = ("congestion", "congested", "crowded", "busy", "busier", "utiliz")
LONG_HAUL_WORDS = ("long-haul", "long haul", "longhaul", "transcon", "international share")
UNMET_WORDS = ("unmet", "latent", "underserved", "under-served", "suppressed demand")
# Deliberately specific. Bare tokens like "right now" or "fog" would hijack
# analytics questions ("unmet demand at SFO right now"), so each phrase here has
# to be unambiguous on its own.
CONDITIONS_WORDS = ("weather", "metar", "current conditions", "conditions at",
                    "conditions right now", "visibility at", "wind at",
                    "raining", "snowing", "foggy", "flight category", "ceiling at")
RANK_WORDS = ("which airport", "which airports", "rank", "top ", "best ", "strongest",
              "candidates", "candidate for", "shortlist", "leading")
SCORE_WORDS = ("score", "expansion candidate", "expansion score")


def extract_codes(text: str, dataset: AirportDataset,
                  extra_text: Iterable[str] = ()) -> list[str]:
    """Pull IATA codes out of free text.

    Only upper-case three-letter tokens are considered, so ordinary words such
    as "sat" or "boi" cannot masquerade as airport codes. City and airport names
    are matched too, case-insensitively.
    """
    known = set(dataset.airports["iata"])
    found: list[str] = []

    def _scan(blob: str) -> None:
        for code in CODE_PATTERN.findall(blob or ""):
            if code in known and code not in found:
                found.append(code)
        lowered = (blob or "").lower()
        for row in dataset.airports.itertuples():
            if row.iata in found:
                continue
            city = str(row.city).lower()
            if re.search(rf"\b{re.escape(city)}\b", lowered):
                found.append(row.iata)

    _scan(text)
    if not found:
        for blob in extra_text:
            _scan(blob)
            if found:
                break
    return found


def extract_region(text: str) -> str | None:
    """Find a known region or state phrase in the question."""
    lowered = " ".join((text or "").lower().split())
    for alias in sorted(REGION_ALIASES, key=len, reverse=True):
        if alias in lowered:
            return alias
    match = re.search(r"\bin ([a-z ]{3,20}?)(?:\?|$|,| that| which| with| for)", lowered)
    if match:
        candidate = match.group(1).strip()
        if resolve_region(candidate).matched and resolve_region(candidate).kind != "all":
            return candidate
    return None


def _fmt_pct(value: float, digits: int = 1) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def answer(
    question: str,
    dataset: AirportDataset,
    long_haul_miles: float,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Route a question to tools and render a deterministic markdown answer."""
    history = history or []
    prior = [m.get("content", "") for m in reversed(history) if m.get("role") == "user"]
    lowered = f" {(question or '').lower()} "
    codes = extract_codes(question or "", dataset, extra_text=prior)
    region = extract_region(question or "")
    calls: list[dict[str, Any]] = []

    def call(name: str, **kwargs) -> dict[str, Any]:
        result = run_tool(name, kwargs, dataset, long_haul_miles)
        calls.append({"tool": name, "input": kwargs, "output": result})
        return result

    if any(w in lowered for w in LONG_HAUL_WORDS) and codes:
        text = _render_long_haul(call("get_long_haul_share", iata=codes[0]))
    elif any(w in lowered for w in UNMET_WORDS) and codes:
        text = _render_unmet(call("get_unmet_demand_proxy", iata=codes[0]))
    elif any(w in lowered for w in CONDITIONS_WORDS) and codes:
        text = _render_conditions(call("get_airport_conditions", iata=codes[0]))
    elif any(w in lowered for w in CONGESTION_WORDS) and codes:
        text = _render_congestion(call("compare_congestion", iatas=codes))
    elif len(codes) >= 2 and any(w in lowered for w in COMPARE_WORDS):
        text = _render_comparison(call("compare_airports", iatas=codes))
    elif any(w in lowered for w in RANK_WORDS) or (region and not codes):
        result = call("rank_airports", region=region, limit=5)
        text = _render_ranking(result)
    elif codes and any(w in lowered for w in SCORE_WORDS):
        text = _render_score(call("get_expansion_score", iata=codes[0]))
    elif len(codes) >= 2:
        text = _render_comparison(call("compare_airports", iatas=codes))
    elif codes:
        text = _render_score(call("get_expansion_score", iata=codes[0]))
    else:
        text = _render_overview(call("get_dataset_overview"))

    return {"answer": text, "tool_calls": calls, "used_llm": False}


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
def _render_score(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Could not score that airport.** {result['error']}"
    lines = [
        f"### {result['iata']} — {result['name']}",
        f"**Airport Expansion Score: {result['expansion_score']:.1f} / 100** "
        f"({result['rating']})",
        "",
        "| Component | Weight | Value | Sub-score | Points |",
        "|---|---:|---:|---:|---:|",
    ]
    for c in result["components"]:
        lines.append(
            f"| {c['label']} | {c['weight_pct']:.0f}% | {c['raw_display']} | "
            f"{c['sub_score']:.2f} | {c['points']:.1f} / {c['max_points']:.0f} |"
        )
    lines += ["", "**Why this score:**"]
    lines += [f"- {c['label']}: {c['explanation']}" for c in result["components"]]
    lines += ["", f"_{result['methodology']}_"]
    return "\n".join(lines)


def _render_comparison(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Comparison failed.** {result['error']}"
    codes = result["iatas"]
    lines = ["### " + " vs ".join(codes), "", result["verdict"], "",
             "| Metric | " + " | ".join(codes) + " | Leader |",
             "|---|" + "---:|" * len(codes) + "---|"]
    for row in result["table"]:
        cells = []
        for code in codes:
            value = row["values"][code]
            if row["kind"] == "pct":
                cells.append(_fmt_pct(value, 2))
            elif row["kind"] == "pp":
                cells.append(f"{value * 100:+.2f} pp")
            elif row["kind"] == "count":
                cells.append(f"{value:,.0f}")
            else:
                cells.append(f"{value:,.1f}")
        lines.append(f"| {row['label']} | " + " | ".join(cells) + f" | {row['leader']} |")
    lines += ["", "_Unmet Demand Proxy is a screening proxy, not a measurement of "
              "true latent demand._"]
    return "\n".join(lines)


def _render_congestion(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Congestion comparison failed.** {result['error']}"
    lines = ["### Congestion comparison", "", result["definition"], "",
             "| Airport | Runways | Gates | Departures / runway | Passengers / gate | "
             "Load factor | Slot-controlled |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for a in result["airports"]:
        lines.append(
            f"| {a['iata']} — {a['name']} | {a['runways']} | {a['gates']} | "
            f"{a['departures_per_runway']:,.0f} | {a['passengers_per_gate']:,.0f} | "
            f"{_fmt_pct(a['load_factor'], 1)} | {'yes' if a['slot_controlled'] else 'no'} |"
        )
    lines += ["", f"Busiest airfield per runway: **{result['busiest_airfield']}**. "
              f"Busiest terminal per gate: **{result['busiest_terminal']}**."]
    return "\n".join(lines)


def _render_ranking(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Ranking failed.** {result['error']}"
    if not result["results"]:
        return f"**No airports matched.** {result.get('note', '')}"
    scope = result["region_resolution"]["label"]
    lines = [f"### Top expansion candidates — {scope}",
             f"Ranked by {result['sort_label']} ({result['latest_year']} data).", "",
             "| # | Airport | Score | Rating | Load factor | Pax growth | Pax / gate |",
             "|---:|---|---:|---|---:|---:|---:|"]
    for r in result["results"]:
        lines.append(
            f"| {r['rank']} | {r['iata']} — {r['name']} | {r['expansion_score']:.1f} | "
            f"{r['rating']} | {_fmt_pct(r['load_factor'], 1)} | "
            f"{_fmt_pct(r['passenger_cagr'], 2)} | {r['passengers_per_gate']:,.0f} |"
        )
    top = result["results"][0]
    lines += ["", f"**{top['iata']}** leads at {top['expansion_score']:.1f} / 100."]
    return "\n".join(lines)


def _render_long_haul(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Long-haul lookup failed.** {result['error']}"
    lines = [
        f"### Long-haul share at {result['iata']}",
        f"**{_fmt_pct(result['long_haul_departure_share'], 1)} of departures** are "
        f"long-haul, defined as {result['definition']}.",
        "",
        f"- Long-haul seats: {_fmt_pct(result['long_haul_seat_share'], 1)} of all seats",
        f"- Long-haul passengers: {_fmt_pct(result['long_haul_passenger_share'], 1)}",
        f"- {result['long_haul_route_count']} of {result['route_count']} non-stop "
        f"destinations clear the {result['long_haul_threshold_miles']:,.0f}-mile threshold",
        f"- Departure-weighted average stage length: "
        f"{result['average_stage_length_miles']:,.0f} miles",
    ]
    if result["top_long_haul_routes"]:
        lines += ["", "| Destination | Distance (mi) | Departures |", "|---|---:|---:|"]
        for r in result["top_long_haul_routes"]:
            lines.append(f"| {r['destination']} | {r['distance_miles']:,.0f} | "
                         f"{r['departures']:,} |")
    return "\n".join(lines)


def _render_unmet(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Unmet-demand lookup failed.** {result['error']}"
    lines = [
        f"### Unmet Demand Proxy — {result['iata']} ({result['name']})",
        f"**Index: {result['unmet_demand_index']:.1f} / 100.** {result['interpretation']}",
        "",
        "| Signal | Value | Sub-score | Weight | Points |",
        "|---|---:|---:|---:|---:|",
    ]
    for s in result["signals"]:
        lines.append(f"| {s['label']} | {s['raw_display']} | {s['sub_score']:.2f} | "
                     f"{s['weight'] * 100:.0f}% | {s['points']:.1f} |")
    lines += ["", "**What each signal reads as:**"]
    lines += [f"- {s['label']}: {s['reads_as']}" for s in result["signals"]]
    lines += ["", f"> {result['disclaimer']}"]
    return "\n".join(lines)


def _render_conditions(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Conditions lookup failed.** {result['error']}"

    header = (f"### Current conditions — {result['iata']}"
              + (f" ({result['icao']})" if result.get("icao") else ""))
    footer = ("_Source: AviationWeather.gov (NOAA/NWS) — live operational context. "
              "This is **not** an input to the Airport Expansion Score or any "
              "ranking, which use historical US DOT / BTS data._")

    if result["status"] != "ok":
        return "\n".join([header, "", f"**No observation available.** {result['message']}",
                           "", footer])

    lines = [header, "", f"**{result['summary']}**", ""]
    if result.get("flight_category"):
        lines.append(f"- Flight category: **{result['flight_category']}**"
                     + (f" — {result['flight_category_meaning']}"
                        if result.get("flight_category_meaning") else "")
                     + (" _(derived from visibility and ceiling)_"
                        if result.get("flight_category_derived") else ""))
    if result["visibility"] and result["visibility"].get("display"):
        lines.append(f"- Visibility: {result['visibility']['display']}")
    if result["wind"] and result["wind"].get("display"):
        lines.append(f"- Wind: {result['wind']['display']}")
    if result.get("ceiling_feet_agl") is not None:
        lines.append(f"- Ceiling: {result['ceiling_feet_agl']:,} ft AGL")
    if result["weather"] and result["weather"].get("summary"):
        lines.append(f"- Weather: {result['weather']['summary']}")
    if result.get("temperature_c") is not None:
        lines.append(f"- Temperature: {result['temperature_c']:.0f} °C"
                     + (f", dew point {result['dewpoint_c']:.0f} °C"
                        if result.get("dewpoint_c") is not None else ""))
    if result.get("observed_at"):
        age = result.get("observation_age_minutes")
        lines.append(f"- Observed: {result['observed_at']}"
                     + (f" ({age:.0f} min ago)" if age is not None else ""))
    if result.get("raw_metar"):
        lines += ["", f"Raw METAR: `{result['raw_metar']}`"]
    lines += ["", footer]
    return "\n".join(lines)


def _render_overview(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"**Overview failed.** {result['error']}"
    prov = result["provenance"]
    return "\n".join([
        "### Airport Investment Intelligence",
        f"Covering **{result['airport_count']} US airports** and "
        f"{result['route_count']:,} non-stop segments, "
        f"years {result['years'][0]}–{result['years'][-1]}.",
        f"Data status: **{prov['label']}**.",
        "",
        "Try asking:",
        "- Which airports in New England are strong candidates for terminal expansion?",
        "- Compare LAX and SNA congestion levels.",
        "- What percentage of flights from ANC are long-haul?",
        "- What is the unmet flight demand at SFO and why?",
    ])

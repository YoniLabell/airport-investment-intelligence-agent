"""Region and geography resolution.

Region membership is decided by the airport metadata table, never by the LLM.
"New England" means exactly the six states CT/ME/MA/NH/RI/VT as recorded in
``airports.csv`` — not whatever a model happens to believe today.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: Canonical regions used in the airport metadata table.
CANONICAL_REGIONS: tuple[str, ...] = (
    "New England",
    "Mid-Atlantic",
    "Southeast",
    "Midwest",
    "South Central",
    "Mountain West",
    "Pacific West",
    "Non-Contiguous",
)

#: The six New England states, stated explicitly for auditability.
NEW_ENGLAND_STATES: frozenset[str] = frozenset({"CT", "ME", "MA", "NH", "RI", "VT"})

#: Colloquial names an analyst might type, mapped to canonical regions.
REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "new england": ("New England",),
    "northeast": ("New England", "Mid-Atlantic"),
    "north east": ("New England", "Mid-Atlantic"),
    "mid atlantic": ("Mid-Atlantic",),
    "mid-atlantic": ("Mid-Atlantic",),
    "atlantic": ("Mid-Atlantic",),
    "east coast": ("New England", "Mid-Atlantic", "Southeast"),
    "southeast": ("Southeast",),
    "south east": ("Southeast",),
    "south": ("Southeast", "South Central"),
    "midwest": ("Midwest",),
    "mid west": ("Midwest",),
    "south central": ("South Central",),
    "texas region": ("South Central",),
    "southwest": ("South Central", "Mountain West"),
    "south west": ("South Central", "Mountain West"),
    "mountain": ("Mountain West",),
    "mountain west": ("Mountain West",),
    "rockies": ("Mountain West",),
    "west": ("Pacific West", "Mountain West"),
    "pacific": ("Pacific West",),
    "pacific west": ("Pacific West",),
    "west coast": ("Pacific West",),
    "non-contiguous": ("Non-Contiguous",),
    "non contiguous": ("Non-Contiguous",),
    "alaska and hawaii": ("Non-Contiguous",),
}

STATE_NAMES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


@dataclass(frozen=True)
class RegionResolution:
    """The outcome of resolving a free-text geography to concrete airports."""

    matched: bool
    label: str
    kind: str  # "region" | "state" | "all" | "unknown"
    regions: tuple[str, ...] = ()
    states: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "label": self.label,
            "kind": self.kind,
            "regions": list(self.regions),
            "states": list(self.states),
        }


def normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().replace("_", " ").split())


def resolve_region(query: str | None) -> RegionResolution:
    """Map free text such as ``"new england"`` or ``"CA"`` to a filter."""
    key = normalize(query)
    if not key or key in {"us", "usa", "united states", "national", "all", "nationwide"}:
        return RegionResolution(True, "All US airports", "all")

    if key in REGION_ALIASES:
        regions = REGION_ALIASES[key]
        label = regions[0] if len(regions) == 1 else " + ".join(regions)
        return RegionResolution(True, label, "region", regions=regions)

    canonical = {r.lower(): r for r in CANONICAL_REGIONS}
    if key in canonical:
        return RegionResolution(True, canonical[key], "region", regions=(canonical[key],))

    if key in STATE_NAMES:
        abbr = STATE_NAMES[key]
        return RegionResolution(True, key.title(), "state", states=(abbr,))

    upper = key.upper()
    if len(upper) == 2 and upper in set(STATE_NAMES.values()):
        return RegionResolution(True, upper, "state", states=(upper,))

    return RegionResolution(False, str(query), "unknown")


def filter_airports(airports: pd.DataFrame, query: str | None) -> tuple[pd.DataFrame, RegionResolution]:
    """Return the airport rows in ``query``'s geography, plus how it resolved."""
    resolution = resolve_region(query)
    if resolution.kind == "all":
        return airports.copy(), resolution
    if resolution.kind == "region":
        subset = airports[airports["region"].isin(resolution.regions)]
        return subset.copy(), resolution
    if resolution.kind == "state":
        subset = airports[airports["state"].isin(resolution.states)]
        return subset.copy(), resolution
    return airports.iloc[0:0].copy(), resolution


def list_regions(airports: pd.DataFrame) -> list[dict]:
    """Summarize the regions actually present in the dataset."""
    grouped = (
        airports.groupby("region")
        .agg(airport_count=("iata", "nunique"),
             airports=("iata", lambda s: sorted(s.tolist())))
        .reset_index()
        .sort_values("region")
    )
    return [
        {"region": row.region,
         "airport_count": int(row.airport_count),
         "airports": list(row.airports)}
        for row in grouped.itertuples()
    ]

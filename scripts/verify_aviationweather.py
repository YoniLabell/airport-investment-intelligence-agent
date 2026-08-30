"""Verify the AviationWeather.gov integration against the LIVE API.

The mocked test-suite proves the parser handles the *documented* response
contract. This script proves the live API still emits that contract — the one
thing mocks structurally cannot tell you.

Run it from a machine with open outbound HTTPS:

    python scripts/verify_aviationweather.py
    python scripts/verify_aviationweather.py SFO LAX ANC BOS HNL

Exit code 0 means the live response matches what the provider expects.
Exit code 1 means something drifted — the output names which field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# Allow `python scripts/verify_aviationweather.py` from a clean checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services.aviation_weather import AviationWeatherProvider  # noqa: E402
from app.services.icao import resolve_icao  # noqa: E402

DEFAULT_AIRPORTS = ["SFO", "LAX", "ANC", "BOS"]

#: Fields the provider reads. Missing ones do not crash it (everything is
#: coerced defensively) but they do mean the payload is degraded, so they are
#: worth surfacing loudly.
EXPECTED_FIELDS = {
    "icaoId": "station id, used to match the requested station",
    "obsTime": "observation epoch, drives observed_at and age",
    "name": "station name",
    "rawOb": "raw METAR text",
    "temp": "temperature C",
    "dewp": "dew point C",
    "wdir": "wind direction (bearing or VRB)",
    "wspd": "wind speed kt",
    "visib": "visibility sm (may be '10+')",
    "altim": "altimeter hPa",
    "clouds": "cloud layers, drives the ceiling",
}
OPTIONAL_FIELDS = {"wgst", "wxString", "fltCat", "reportTime", "lat", "lon", "elev"}

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def main(argv: list[str]) -> int:
    airports = [a.upper() for a in argv[1:]] or DEFAULT_AIRPORTS
    settings = get_settings()
    url = f"{settings.aviation_weather_base_url.rstrip('/')}/metar"
    icaos = [resolve_icao(a) for a in airports]

    if any(i is None for i in icaos):
        bad(f"could not map to ICAO: {[a for a, i in zip(airports, icaos) if i is None]}")
        return 1

    print(f"\n{DIM}Endpoint{RESET} {url}")
    print(f"{DIM}Stations{RESET} " + ", ".join(f"{a}->{i}" for a, i in zip(airports, icaos)))

    # -- 1. Raw fetch -------------------------------------------------------
    print("\n1. Live fetch")
    try:
        response = httpx.get(
            url,
            params={"ids": ",".join(icaos), "format": "json", "taf": "false"},
            timeout=settings.aviation_weather_timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        bad(f"could not reach the API: {exc.__class__.__name__}: {exc}")
        print("\n  If this machine is behind a proxy or an egress allow-list, that is")
        print("  the likely cause — this is exactly the failure the app degrades on.")
        return 1
    ok(f"HTTP {response.status_code} in {response.elapsed.total_seconds():.2f}s")

    try:
        body = response.json()
    except ValueError:
        bad(f"response was not JSON (first 200 chars): {response.text[:200]!r}")
        return 1

    if isinstance(body, dict):
        warn(f"response was an object, not a list; keys: {sorted(body)[:8]}")
        body = body.get("data") or body.get("features") or []
    if not isinstance(body, list) or not body:
        bad(f"expected a non-empty list of station records, got {type(body).__name__}")
        return 1
    ok(f"{len(body)} station record(s) returned")

    # -- 2. Contract check --------------------------------------------------
    print("\n2. Response contract")
    failures = 0
    record = body[0]
    print(f"{DIM}     sample record keys: {sorted(record)}{RESET}")

    for field, why in EXPECTED_FIELDS.items():
        if field in record:
            ok(f"{field:<10} present  {DIM}({why}){RESET}")
        else:
            bad(f"{field:<10} MISSING  {DIM}({why}){RESET}")
            failures += 1

    unexpected = set(record) - set(EXPECTED_FIELDS) - OPTIONAL_FIELDS
    if unexpected:
        warn(f"new fields the provider ignores (harmless): {sorted(unexpected)}")

    # visib and wdir are the two fields whose *type* varies in practice.
    visib = record.get("visib")
    print(f"{DIM}     visib={visib!r}  wdir={record.get('wdir')!r}  "
          f"fltCat={record.get('fltCat')!r}{RESET}")

    # -- 3. The provider itself, no mocks -----------------------------------
    print("\n3. Provider output (live, unmocked)")
    provider = AviationWeatherProvider(settings=settings)
    for iata in airports:
        result = provider.get_conditions(iata)
        status = result["status"]
        if status == "ok":
            ok(f"{iata}: {result['summary']}")
            print(f"{DIM}       observed {result['observation_age_minutes']:.0f} min ago"
                  f" · category {result['flight_category']}"
                  f"{' (derived)' if result['flight_category_derived'] else ''}"
                  f" · {result['raw_metar']}{RESET}")
        elif status == "no_report":
            warn(f"{iata}: {result['message']}")
        else:
            bad(f"{iata}: status={status} — {result['message']}")
            failures += 1

        # The guarantee that matters most, checked on every payload.
        if result["used_in_scoring"] is not False:
            bad(f"{iata}: used_in_scoring must always be False")
            failures += 1

    # -- 4. Isolation -------------------------------------------------------
    print("\n4. Isolation from the analytics")
    from app.analytics.scoring import expansion_score
    from app.data.repository import get_repository

    dataset = get_repository().get_dataset()
    before = expansion_score(dataset, airports[0])["expansion_score"]
    provider.get_conditions(airports[0])
    after = expansion_score(dataset, airports[0])["expansion_score"]
    if before == after:
        ok(f"{airports[0]} Expansion Score unchanged by a weather fetch ({before})")
    else:
        bad(f"weather changed the score: {before} -> {after}")
        failures += 1

    print()
    if failures:
        print(f"{RED}{failures} check(s) failed.{RESET} The live response has drifted "
              f"from what the provider expects — see the FAIL lines above.")
        return 1
    print(f"{GREEN}All checks passed.{RESET} The live API matches the provider's "
          f"expectations.")
    print(f"{DIM}Full first payload:{RESET}")
    print(json.dumps(provider.get_conditions(airports[0]), indent=2, default=str)[:1400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""AviationWeather.gov METAR provider — live operational context.

Source
------
The US NOAA / NWS Aviation Weather Center publishes a public, keyless JSON API:

    GET https://aviationweather.gov/api/data/metar?ids=KSFO&format=json

It returns the most recent METAR observation per station. METARs are issued
roughly hourly (plus SPECIs when conditions change), so observations are cached
briefly rather than re-fetched per request.

Scope — read this before wiring it into anything
------------------------------------------------
This is **live operational context only**. It answers "what is it like at SFO
right now". It is deliberately *not* an input to any metric, ranking or to the
Airport Expansion Score: those are computed exclusively from historical US DOT /
BTS data. Today's fog does not make an airport a better expansion candidate, and
a weather outage must not be able to move an investment number.

Failure policy
--------------
Every failure is returned, never raised. The caller always receives a dict with
a ``status`` field, so a timeout at the AWC degrades one panel rather than the
application.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.data.cache import TTLCache
from app.logging_config import get_logger
from app.services.icao import resolve_icao

log = get_logger(__name__)

SOURCE_NAME = "AviationWeather.gov (NOAA/NWS Aviation Weather Center)"
SOURCE_URL = "https://aviationweather.gov/data/api/"
#: Stated on every payload so this can never be mistaken for an analytics input.
SOURCE_ROLE = (
    "Live operational context. Current conditions only — NOT an input to the "
    "Airport Expansion Score, the Unmet Demand Proxy, or any ranking, all of "
    "which use historical US DOT / BTS data."
)

HPA_PER_INHG = 33.8638866667


class ConditionsStatus(str, Enum):
    """Why a conditions payload does or does not carry an observation."""

    OK = "ok"                    # a current observation was returned
    NO_REPORT = "no_report"      # station is valid but reported nothing recent
    UNSUPPORTED = "unsupported"  # the code could not be mapped to an ICAO station
    UNAVAILABLE = "unavailable"  # upstream error, timeout or bad payload
    DISABLED = "disabled"        # live weather switched off by configuration


# --- METAR code books -------------------------------------------------------
CLOUD_COVER = {
    "SKC": "sky clear", "CLR": "clear below 12,000 ft", "NCD": "no cloud detected",
    "NSC": "no significant cloud", "FEW": "few", "SCT": "scattered",
    "BKN": "broken", "OVC": "overcast", "OVX": "obscured",
}
#: Layers at or below which the sky counts as a ceiling.
CEILING_COVERS = {"BKN", "OVC", "OVX"}

WX_INTENSITY = {"-": "light", "+": "heavy", "VC": "in the vicinity"}
WX_DESCRIPTOR = {
    "MI": "shallow", "PR": "partial", "BC": "patches of", "DR": "low drifting",
    "BL": "blowing", "SH": "showers of", "TS": "thunderstorm", "FZ": "freezing",
}
WX_PHENOMENA = {
    "DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains",
    "IC": "ice crystals", "PL": "ice pellets", "GR": "hail", "GS": "small hail",
    "UP": "unknown precipitation", "BR": "mist", "FG": "fog", "FU": "smoke",
    "VA": "volcanic ash", "DU": "dust", "SA": "sand", "HZ": "haze",
    "PY": "spray", "PO": "dust whirls", "SQ": "squalls", "FC": "funnel cloud",
    "SS": "sandstorm", "DS": "duststorm",
}

FLIGHT_CATEGORY_MEANING = {
    "VFR": "Visual flight rules — ceiling above 3,000 ft and visibility over 5 sm.",
    "MVFR": "Marginal VFR — ceiling 1,000-3,000 ft and/or visibility 3-5 sm.",
    "IFR": "Instrument flight rules — ceiling 500-1,000 ft and/or visibility 1-3 sm.",
    "LIFR": "Low IFR — ceiling below 500 ft and/or visibility under 1 sm.",
}


# --- Parsing helpers --------------------------------------------------------
def _to_float(value: Any) -> float | None:
    """Coerce a METAR numeric field, tolerating ``None`` and stray strings."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_visibility(raw: Any) -> dict[str, Any]:
    """Parse ``visib``, which the API reports as a number or a string like ``10+``.

    ``10+`` means "10 statute miles or more", so the numeric value is 10 and
    ``or_greater`` records that the true value is unbounded above.
    """
    if raw is None or raw == "":
        return {"statute_miles": None, "or_greater": False, "display": None}
    text = str(raw).strip()
    or_greater = text.endswith("+")
    miles = _to_float(text.rstrip("+"))
    if miles is None:
        return {"statute_miles": None, "or_greater": False, "display": text}
    display = f"{miles:g}{'+' if or_greater else ''} sm"
    return {"statute_miles": miles, "or_greater": or_greater, "display": display}


def _parse_wind(record: dict[str, Any]) -> dict[str, Any]:
    """Parse wind, where ``wdir`` may be a bearing, ``VRB`` or absent."""
    raw_dir = record.get("wdir")
    variable = str(raw_dir).strip().upper() == "VRB" if raw_dir is not None else False
    direction = None if variable else _to_float(raw_dir)
    speed = _to_float(record.get("wspd"))
    gust = _to_float(record.get("wgst"))

    if speed is None:
        display = None
    elif speed == 0:
        display = "calm"
    elif variable or direction is None:
        display = f"variable at {speed:g} kt"
    else:
        display = f"{direction:03.0f}° at {speed:g} kt"
    if display and gust:
        display += f", gusting {gust:g} kt"

    return {
        "direction_degrees": None if direction is None else int(direction),
        "variable": variable,
        "speed_knots": None if speed is None else int(speed),
        "gust_knots": None if gust is None else int(gust),
        "display": display,
    }


def _parse_clouds(record: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    """Return the cloud layers and the ceiling (lowest broken/overcast base, ft)."""
    layers: list[dict[str, Any]] = []
    ceiling: int | None = None
    for layer in record.get("clouds") or []:
        if not isinstance(layer, dict):
            continue
        cover = str(layer.get("cover") or "").upper()
        base = _to_float(layer.get("base"))
        base_ft = None if base is None else int(base)
        layers.append({
            "cover": cover,
            "cover_text": CLOUD_COVER.get(cover, cover.lower() or "unknown"),
            "base_feet_agl": base_ft,
        })
        if cover in CEILING_COVERS and base_ft is not None:
            ceiling = base_ft if ceiling is None else min(ceiling, base_ft)
    return layers, ceiling


def _decode_wx_token(token: str) -> str:
    """Decode one METAR present-weather group, e.g. ``-TSRA`` -> ``light thunderstorm rain``."""
    rest = token.upper()
    words: list[str] = []
    if rest.startswith("VC"):
        rest = rest[2:]
        words.append(WX_INTENSITY["VC"])
    elif rest and rest[0] in WX_INTENSITY:
        words.append(WX_INTENSITY[rest[0]])
        rest = rest[1:]
    while len(rest) >= 2:
        pair, rest = rest[:2], rest[2:]
        if pair in WX_DESCRIPTOR:
            words.append(WX_DESCRIPTOR[pair])
        elif pair in WX_PHENOMENA:
            words.append(WX_PHENOMENA[pair])
        else:
            words.append(pair.lower())
    return " ".join(w for w in words if w) or token.lower()


def _parse_weather(record: dict[str, Any]) -> dict[str, Any]:
    """Decode ``wxString`` into readable phenomena."""
    raw = str(record.get("wxString") or "").strip()
    if not raw:
        return {"raw": None, "phenomena": [], "summary": "no significant weather"}
    phenomena = [_decode_wx_token(tok) for tok in raw.split() if tok]
    return {"raw": raw, "phenomena": phenomena, "summary": ", ".join(phenomena)}


def _derive_flight_category(visibility_sm: float | None, ceiling_ft: int | None) -> str | None:
    """Standard FAA flight-category rules, used only when the API omits ``fltCat``.

    The category is the *worse* of the visibility and ceiling classifications.
    Missing inputs are treated as unlimited, matching how a METAR with no cloud
    group reports clear skies.
    """
    if visibility_sm is None and ceiling_ft is None:
        return None
    vis = float("inf") if visibility_sm is None else visibility_sm
    ceiling = float("inf") if ceiling_ft is None else ceiling_ft
    if vis < 1 or ceiling < 500:
        return "LIFR"
    if vis < 3 or ceiling < 1000:
        return "IFR"
    if vis <= 5 or ceiling <= 3000:
        return "MVFR"
    return "VFR"


def _observation_time(record: dict[str, Any]) -> tuple[str | None, float | None]:
    """Return the observation time as ISO-8601 UTC plus its age in minutes."""
    epoch = _to_float(record.get("obsTime"))
    if epoch is None:
        return str(record.get("reportTime") or "") or None, None
    observed = datetime.fromtimestamp(epoch, tz=timezone.utc)
    age = (datetime.now(timezone.utc) - observed).total_seconds() / 60.0
    return observed.isoformat(), round(age, 1)


@dataclass(frozen=True)
class _Envelope:
    """Fields present on every payload, successful or not."""

    iata: str
    icao: str | None
    status: ConditionsStatus
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "iata": self.iata,
            "icao": self.icao,
            "status": self.status.value,
            "message": self.message,
            "observation_available": self.status is ConditionsStatus.OK,
            "source": SOURCE_NAME,
            "source_url": SOURCE_URL,
            "source_role": SOURCE_ROLE,
            "data_kind": "live_operational_context",
            "used_in_scoring": False,
        }


class AviationWeatherProvider:
    """Fetches current METAR conditions for one airport.

    ``transport`` exists so tests can drive the full request/parse path through
    :class:`httpx.MockTransport` without touching the network.
    """

    name = SOURCE_NAME

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._transport = transport
        self.cache = cache if cache is not None else TTLCache(
            self.settings.aviation_weather_cache_ttl_seconds, None
        )

    # -- public API --------------------------------------------------------
    def get_conditions(self, code: str) -> dict[str, Any]:
        """Current conditions for ``code``. Never raises; check ``status``."""
        iata = str(code or "").strip().upper()

        if not self.settings.enable_live_weather:
            return _Envelope(iata, None, ConditionsStatus.DISABLED,
                             "Live weather is disabled by configuration "
                             "(ENABLE_LIVE_WEATHER=false).").to_dict()

        icao = resolve_icao(iata)
        if icao is None:
            return _Envelope(iata, None, ConditionsStatus.UNSUPPORTED,
                             f"'{iata}' could not be mapped to an ICAO weather "
                             "station identifier.").to_dict()

        cached = self.cache.get(icao)
        if cached is not None:
            payload, age = cached
            log.debug("serving cached conditions for %s (age %.0fs)", icao, age)
            return {**payload, "cached": True, "cache_age_seconds": round(age)}

        try:
            record = self._fetch(icao)
        except httpx.TimeoutException:
            return _Envelope(iata, icao, ConditionsStatus.UNAVAILABLE,
                             f"AviationWeather.gov did not respond within "
                             f"{self.settings.aviation_weather_timeout_seconds:.0f}s."
                             ).to_dict()
        except httpx.HTTPError as exc:
            log.warning("AviationWeather.gov request failed for %s: %s", icao, exc)
            return _Envelope(iata, icao, ConditionsStatus.UNAVAILABLE,
                             f"AviationWeather.gov is unreachable ({exc.__class__.__name__})."
                             ).to_dict()
        except ValueError as exc:
            log.warning("AviationWeather.gov payload unusable for %s: %s", icao, exc)
            return _Envelope(iata, icao, ConditionsStatus.UNAVAILABLE,
                             f"AviationWeather.gov returned an unusable response ({exc}).",
                             ).to_dict()

        if record is None:
            return _Envelope(iata, icao, ConditionsStatus.NO_REPORT,
                             f"No recent METAR observation is published for {icao}."
                             ).to_dict()

        payload = self._shape(iata, icao, record)
        self.cache.set(icao, payload)
        return {**payload, "cached": False}

    # -- internals ---------------------------------------------------------
    def _fetch(self, icao: str) -> dict[str, Any] | None:
        """One METAR request. Returns the station record, or ``None`` if empty."""
        url = f"{self.settings.aviation_weather_base_url.rstrip('/')}/metar"
        params = {"ids": icao, "format": "json", "taf": "false"}
        timeout = self.settings.aviation_weather_timeout_seconds
        log.info("fetching METAR for %s (timeout %.1fs)", icao, timeout)

        with httpx.Client(timeout=timeout, transport=self._transport,
                          follow_redirects=True,
                          headers={"Accept": "application/json"}) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise ValueError("response was not JSON") from exc

        # The API returns a list of station records; some deployments wrap it in
        # an envelope. Unwrap only a key we recognise — falling back to an empty
        # list for any other dict would report a malformed response as "this
        # station has no observations", which is a different and misleading fact.
        if isinstance(body, dict):
            for key in ("data", "features"):
                if key in body:
                    body = body[key]
                    break
        if not isinstance(body, list):
            raise ValueError("expected a list of station records")

        records = [r for r in body if isinstance(r, dict)]
        if not records:
            return None
        # Prefer an exact station match; fall back to the first record.
        for record in records:
            if str(record.get("icaoId") or "").upper() == icao:
                return record
        return records[0]

    def _shape(self, iata: str, icao: str, record: dict[str, Any]) -> dict[str, Any]:
        """Map one METAR record onto this application's stable schema."""
        visibility = _parse_visibility(record.get("visib"))
        wind = _parse_wind(record)
        layers, ceiling = _parse_clouds(record)
        weather = _parse_weather(record)
        observed_at, age_minutes = _observation_time(record)

        category = str(record.get("fltCat") or "").upper() or None
        derived = False
        if category not in FLIGHT_CATEGORY_MEANING:
            category = _derive_flight_category(visibility["statute_miles"], ceiling)
            derived = category is not None

        altimeter_hpa = _to_float(record.get("altim"))
        envelope = _Envelope(iata, icao, ConditionsStatus.OK,
                             f"Current METAR observation for {icao}.").to_dict()

        return {
            **envelope,
            "station_name": record.get("name") or None,
            "observed_at": observed_at,
            "observation_age_minutes": age_minutes,
            "raw_metar": record.get("rawOb") or None,
            "flight_category": category,
            "flight_category_meaning": FLIGHT_CATEGORY_MEANING.get(category or ""),
            "flight_category_derived": derived,
            "visibility": visibility,
            "wind": wind,
            "weather": weather,
            "cloud_layers": layers,
            "ceiling_feet_agl": ceiling,
            "temperature_c": _to_float(record.get("temp")),
            "dewpoint_c": _to_float(record.get("dewp")),
            "altimeter_hpa": altimeter_hpa,
            "altimeter_in_hg": (None if altimeter_hpa is None
                                else round(altimeter_hpa / HPA_PER_INHG, 2)),
            "summary": _summarize(icao, category, visibility, wind, weather, ceiling),
        }


def _summarize(icao: str, category: str | None, visibility: dict[str, Any],
               wind: dict[str, Any], weather: dict[str, Any],
               ceiling: int | None) -> str:
    """One readable sentence, so the agent has something to quote verbatim."""
    parts: list[str] = []
    if category:
        parts.append(category)
    if weather["summary"] and weather["summary"] != "no significant weather":
        parts.append(weather["summary"])
    if visibility["display"]:
        parts.append(f"visibility {visibility['display']}")
    if ceiling is not None:
        parts.append(f"ceiling {ceiling:,} ft")
    if wind["display"]:
        parts.append(f"wind {wind['display']}")
    return f"{icao}: " + ("; ".join(parts) if parts else "observation reported")


@functools.lru_cache(maxsize=1)
def get_weather_provider() -> AviationWeatherProvider:
    """Process-wide provider singleton (FastAPI dependency / tool dependency)."""
    return AviationWeatherProvider()

"""AviationWeather.gov integration — fully mocked, no network.

Every test drives the real request/parse path through httpx.MockTransport, so
the URL, query parameters, payload handling and failure behaviour are all
exercised without touching the internet.
"""

from __future__ import annotations

import time

import httpx
import pytest

from app.config import Settings
from app.data.cache import TTLCache
from app.services.aviation_weather import (
    AviationWeatherProvider,
    ConditionsStatus,
    _derive_flight_category,
)
from app.services.icao import ICAO_OVERRIDES, resolve_icao


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def metar(icao="KSFO", **overrides):
    """A METAR record shaped like a real AviationWeather.gov JSON response."""
    record = {
        "icaoId": icao,
        "obsTime": int(time.time()) - 600,
        "reportTime": "2026-08-30 21:00:00",
        "temp": 17.2, "dewp": 12.8,
        "wdir": 290, "wspd": 18, "wgst": 26,
        "visib": "10+", "altim": 1013.6, "wxString": None,
        "name": "San Francisco Intl, CA, US",
        "fltCat": "VFR",
        "clouds": [{"cover": "FEW", "base": 1500}],
        "rawOb": "KSFO 302056Z 29018G26KT 10SM FEW015 17/13 A2993",
    }
    record.update(overrides)
    return record


def provider(handler, **settings_kwargs):
    """Provider wired to a mock transport and a fresh, memory-only cache."""
    settings = Settings(**settings_kwargs)
    return AviationWeatherProvider(
        settings=settings,
        transport=httpx.MockTransport(handler),
        cache=TTLCache(settings.aviation_weather_cache_ttl_seconds, None),
    )


def responder(payload, status_code=200):
    return lambda request: httpx.Response(status_code, json=payload)


@pytest.fixture
def sfo():
    return provider(responder([metar()]))


# ---------------------------------------------------------------------------
# IATA -> ICAO
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("iata,icao", [
    ("SFO", "KSFO"), ("LAX", "KLAX"), ("BOS", "KBOS"), ("JFK", "KJFK"),
    ("ANC", "PANC"), ("FAI", "PAFA"), ("HNL", "PHNL"), ("OGG", "PHOG"),
    ("SJU", "TJSJ"), ("GUM", "PGUM"),
])
def test_iata_maps_to_icao(iata, icao):
    assert resolve_icao(iata) == icao


def test_resolution_is_case_and_whitespace_insensitive():
    assert resolve_icao("  sfo ") == "KSFO"
    assert resolve_icao("aNc") == "PANC"


def test_icao_codes_pass_through():
    assert resolve_icao("KSFO") == "KSFO"
    assert resolve_icao("panc") == "PANC"


@pytest.mark.parametrize("bad", ["", None, "Z", "ZZ", "TOOLONGCODE", "12", "!!!"])
def test_unmappable_codes_return_none(bad):
    assert resolve_icao(bad) is None


def test_non_contiguous_airports_never_take_a_k_prefix():
    """The K-prefix rule must not be applied to Alaska/Hawaii/territories."""
    for iata, icao in ICAO_OVERRIDES.items():
        assert resolve_icao(iata) == icao
        assert not icao.startswith("K"), f"{iata} wrongly K-prefixed"


def test_every_dataset_airport_resolves(dataset):
    """No airport we cover may be un-mappable to a weather station."""
    unmapped = [c for c in dataset.iata_codes if resolve_icao(c) is None]
    assert unmapped == []


# ---------------------------------------------------------------------------
# The request itself
# ---------------------------------------------------------------------------
def test_request_targets_the_documented_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[metar()])

    provider(handler).get_conditions("SFO")
    assert seen["url"].startswith("https://aviationweather.gov/api/data/metar")
    assert seen["path"] == "/api/data/metar"
    assert seen["params"] == {"ids": "KSFO", "format": "json", "taf": "false"}


def test_iata_is_converted_before_the_call():
    """ANC must be requested as PANC, not KANC."""
    seen = {}

    def handler(request):
        seen["ids"] = request.url.params.get("ids")
        return httpx.Response(200, json=[metar(icao="PANC")])

    result = provider(handler).get_conditions("ANC")
    assert seen["ids"] == "PANC"
    assert result["icao"] == "PANC"
    assert result["iata"] == "ANC"


def test_base_url_is_configurable():
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        return httpx.Response(200, json=[metar()])

    provider(handler, aviation_weather_base_url="https://example.test/wx").get_conditions("SFO")
    assert seen["host"] == "example.test"


# ---------------------------------------------------------------------------
# Successful parse
# ---------------------------------------------------------------------------
def test_successful_observation_is_structured(sfo):
    result = sfo.get_conditions("SFO")

    assert result["status"] == ConditionsStatus.OK.value
    assert result["observation_available"] is True
    assert result["iata"] == "SFO"
    assert result["icao"] == "KSFO"
    assert result["station_name"] == "San Francisco Intl, CA, US"
    assert result["flight_category"] == "VFR"
    assert result["flight_category_derived"] is False
    assert result["raw_metar"].startswith("KSFO ")

    assert result["visibility"] == {"statute_miles": 10.0, "or_greater": True,
                                    "display": "10+ sm"}
    assert result["wind"]["direction_degrees"] == 290
    assert result["wind"]["speed_knots"] == 18
    assert result["wind"]["gust_knots"] == 26
    assert result["wind"]["variable"] is False

    assert result["temperature_c"] == 17.2
    assert result["dewpoint_c"] == 12.8
    assert result["observed_at"].endswith("+00:00")
    assert 0 <= result["observation_age_minutes"] < 60


def test_altimeter_is_converted_from_hpa_to_inches():
    """1013.6 hPa is 29.93 inHg — the value in the raw METAR's A2993 group."""
    result = provider(responder([metar(altim=1013.6)])).get_conditions("SFO")
    assert result["altimeter_hpa"] == 1013.6
    assert result["altimeter_in_hg"] == 29.93


def test_present_weather_is_decoded():
    result = provider(responder([metar(wxString="-TSRA BR")])).get_conditions("SFO")
    assert result["weather"]["raw"] == "-TSRA BR"
    assert result["weather"]["phenomena"] == ["light thunderstorm rain", "mist"]


def test_no_present_weather_reads_cleanly(sfo):
    assert sfo.get_conditions("SFO")["weather"]["summary"] == "no significant weather"


def test_ceiling_is_the_lowest_broken_or_overcast_layer():
    clouds = [{"cover": "FEW", "base": 500}, {"cover": "OVC", "base": 1200},
              {"cover": "BKN", "base": 800}]
    result = provider(responder([metar(clouds=clouds)])).get_conditions("SFO")
    assert result["ceiling_feet_agl"] == 800
    assert len(result["cloud_layers"]) == 3
    assert result["cloud_layers"][0]["cover_text"] == "few"


def test_scattered_layers_are_not_a_ceiling():
    clouds = [{"cover": "FEW", "base": 900}, {"cover": "SCT", "base": 1100}]
    result = provider(responder([metar(clouds=clouds)])).get_conditions("SFO")
    assert result["ceiling_feet_agl"] is None


def test_variable_wind_is_handled():
    result = provider(responder([metar(wdir="VRB", wspd=4, wgst=None)])).get_conditions("SFO")
    assert result["wind"]["variable"] is True
    assert result["wind"]["direction_degrees"] is None
    assert result["wind"]["display"] == "variable at 4 kt"


def test_calm_wind_is_handled():
    result = provider(responder([metar(wdir=0, wspd=0, wgst=None)])).get_conditions("SFO")
    assert result["wind"]["display"] == "calm"


def test_missing_numeric_fields_do_not_crash():
    sparse = {"icaoId": "KBOS", "obsTime": int(time.time()),
              "rawOb": "KBOS 302054Z AUTO"}
    result = provider(responder([sparse])).get_conditions("BOS")
    assert result["status"] == "ok"
    assert result["temperature_c"] is None
    assert result["wind"]["display"] is None
    assert result["visibility"]["statute_miles"] is None
    assert result["summary"].startswith("KBOS:")


def test_the_matching_station_is_selected_from_a_multi_station_payload():
    payload = [metar(icao="KLAX", name="Los Angeles Intl, CA, US"), metar(icao="KSFO")]
    result = provider(responder(payload)).get_conditions("SFO")
    assert result["icao"] == "KSFO"
    assert result["station_name"] == "San Francisco Intl, CA, US"


def test_a_dict_wrapped_payload_is_unwrapped():
    result = provider(responder({"data": [metar()]})).get_conditions("SFO")
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Flight category
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("visibility,ceiling,expected", [
    (10.0, None, "VFR"),
    (10.0, 5000, "VFR"),
    (4.0, 5000, "MVFR"),
    (10.0, 2000, "MVFR"),
    (2.0, 5000, "IFR"),
    (10.0, 700, "IFR"),
    (0.5, 5000, "LIFR"),
    (10.0, 200, "LIFR"),
    (None, None, None),
])
def test_flight_category_rules(visibility, ceiling, expected):
    assert _derive_flight_category(visibility, ceiling) == expected


def test_category_is_derived_only_when_the_api_omits_it():
    supplied = provider(responder([metar(fltCat="MVFR")])).get_conditions("SFO")
    assert supplied["flight_category"] == "MVFR"
    assert supplied["flight_category_derived"] is False

    omitted = provider(responder([
        metar(fltCat=None, visib="0.5", clouds=[{"cover": "OVC", "base": 200}])
    ])).get_conditions("SFO")
    assert omitted["flight_category"] == "LIFR"
    assert omitted["flight_category_derived"] is True
    assert "Low IFR" in omitted["flight_category_meaning"]


# ---------------------------------------------------------------------------
# Graceful failure — the whole point of the integration
# ---------------------------------------------------------------------------
def test_timeout_degrades_instead_of_raising():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    result = provider(handler).get_conditions("SFO")
    assert result["status"] == ConditionsStatus.UNAVAILABLE.value
    assert result["observation_available"] is False
    assert "did not respond" in result["message"]
    assert result["used_in_scoring"] is False


def test_connection_error_degrades():
    def handler(request):
        raise httpx.ConnectError("no route to host", request=request)

    result = provider(handler).get_conditions("SFO")
    assert result["status"] == ConditionsStatus.UNAVAILABLE.value
    assert "unreachable" in result["message"]


@pytest.mark.parametrize("code", [400, 403, 404, 429, 500, 502, 503])
def test_http_errors_degrade(code):
    result = provider(responder([], status_code=code)).get_conditions("SFO")
    assert result["status"] == ConditionsStatus.UNAVAILABLE.value


def test_non_json_body_degrades():
    result = provider(lambda r: httpx.Response(200, text="<html>maintenance</html>")) \
        .get_conditions("SFO")
    assert result["status"] == ConditionsStatus.UNAVAILABLE.value


def test_unexpected_json_shape_degrades():
    result = provider(responder({"unexpected": "shape"})).get_conditions("SFO")
    assert result["status"] == ConditionsStatus.UNAVAILABLE.value


def test_empty_station_list_reports_no_report():
    result = provider(responder([])).get_conditions("SFO")
    assert result["status"] == ConditionsStatus.NO_REPORT.value
    assert result["observation_available"] is False
    assert "KSFO" in result["message"]


def test_unmappable_code_is_reported_as_unsupported():
    result = provider(responder([metar()])).get_conditions("ZZZZZ")
    assert result["status"] == ConditionsStatus.UNSUPPORTED.value
    assert result["icao"] is None


def test_live_weather_can_be_switched_off():
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("no request may be made when live weather is disabled")

    result = provider(handler, enable_live_weather=False).get_conditions("SFO")
    assert result["status"] == ConditionsStatus.DISABLED.value


def test_every_payload_carries_the_provenance_envelope():
    """Success or failure, the source labelling must always be present."""
    cases = [
        provider(responder([metar()])).get_conditions("SFO"),
        provider(responder([])).get_conditions("SFO"),
        provider(responder([], status_code=500)).get_conditions("SFO"),
        provider(responder([metar()])).get_conditions("!!!"),
        provider(responder([metar()]), enable_live_weather=False).get_conditions("SFO"),
    ]
    for result in cases:
        assert result["used_in_scoring"] is False
        assert result["data_kind"] == "live_operational_context"
        assert "AviationWeather.gov" in result["source"]
        assert "NOT an input to the Airport Expansion Score" in result["source_role"]
        assert result["status"] in {s.value for s in ConditionsStatus}
        assert result["message"]


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
def test_repeat_calls_are_served_from_cache():
    calls = []

    def handler(request):
        calls.append(request.url.params.get("ids"))
        return httpx.Response(200, json=[metar()])

    weather = provider(handler)
    first = weather.get_conditions("SFO")
    second = weather.get_conditions("SFO")

    assert len(calls) == 1, "the second call should not hit the network"
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["cache_age_seconds"] >= 0
    assert second["raw_metar"] == first["raw_metar"]


def test_different_airports_are_cached_separately():
    calls = []

    def handler(request):
        ids = request.url.params.get("ids")
        calls.append(ids)
        return httpx.Response(200, json=[metar(icao=ids)])

    weather = provider(handler)
    weather.get_conditions("SFO")
    weather.get_conditions("LAX")
    assert calls == ["KSFO", "KLAX"]


def test_a_zero_ttl_still_serves_the_process_cache():
    """TTL 0 means "no expiry" in TTLCache; make sure that is what happens."""
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=[metar()])

    weather = provider(handler, aviation_weather_cache_ttl_seconds=0)
    weather.get_conditions("SFO")
    weather.get_conditions("SFO")
    assert len(calls) == 1


def test_failures_are_not_cached():
    """A transient outage must not poison the cache for the whole TTL."""
    responses = [httpx.Response(503, json=[]), httpx.Response(200, json=[metar()])]

    def handler(request):
        return responses.pop(0)

    weather = provider(handler)
    assert weather.get_conditions("SFO")["status"] == "unavailable"
    assert weather.get_conditions("SFO")["status"] == "ok"


# ---------------------------------------------------------------------------
# Agent tool and HTTP endpoint
# ---------------------------------------------------------------------------
@pytest.fixture
def stub_provider(monkeypatch):
    """Swap the process-wide provider singleton for a mocked one."""
    def install(handler, **settings_kwargs):
        weather = provider(handler, **settings_kwargs)
        for module in ("app.agent.tools", "app.api.routes"):
            monkeypatch.setattr(f"{module}.get_weather_provider", lambda: weather)
        return weather
    return install


def test_tool_returns_conditions(dataset, stub_provider):
    from app.agent.tools import run_tool

    stub_provider(responder([metar()]))
    result = run_tool("get_airport_conditions", {"iata": "SFO"}, dataset, 2500.0)
    assert result["status"] == "ok"
    assert result["icao"] == "KSFO"
    assert result["used_in_scoring"] is False


def test_tool_is_registered_and_described():
    from app.agent.tools import TOOL_REGISTRY, anthropic_tool_definitions

    assert "get_airport_conditions" in TOOL_REGISTRY
    spec = next(d for d in anthropic_tool_definitions()
                if d["name"] == "get_airport_conditions")
    assert spec["input_schema"]["required"] == ["iata"]
    # The description must tell the model this is not an analytics input.
    assert "LIVE OPERATIONAL CONTEXT ONLY" in spec["description"]
    assert "BTS" in spec["description"]


def test_tool_rejects_an_airport_outside_the_dataset(dataset, stub_provider):
    from app.agent.tools import run_tool

    stub_provider(responder([metar()]))
    result = run_tool("get_airport_conditions", {"iata": "ZZZ"}, dataset, 2500.0)
    assert "error" in result
    assert "Unknown airport" in result["error"]


def test_tool_degrades_when_the_upstream_is_down(dataset, stub_provider):
    from app.agent.tools import run_tool

    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    stub_provider(handler)
    result = run_tool("get_airport_conditions", {"iata": "SFO"}, dataset, 2500.0)
    assert "error" not in result          # a graceful payload, not a tool failure
    assert result["status"] == "unavailable"


def test_endpoint_returns_conditions(client, stub_provider):
    stub_provider(responder([metar()]))
    body = client.get("/api/airports/SFO/conditions").json()
    assert body["status"] == "ok"
    assert body["icao"] == "KSFO"
    assert body["flight_category"] == "VFR"
    assert body["used_in_scoring"] is False
    assert body["data_kind"] == "live_operational_context"
    assert "AviationWeather.gov" in body["source"]


def test_endpoint_converts_iata_for_alaska(client, stub_provider):
    stub_provider(responder([metar(icao="PANC", name="Anchorage Intl, AK, US")]))
    body = client.get("/api/airports/anc/conditions").json()
    assert body["iata"] == "ANC"
    assert body["icao"] == "PANC"


def test_endpoint_stays_200_when_the_upstream_fails(client, stub_provider):
    """An outage degrades the payload, never the request."""
    stub_provider(responder([], status_code=503))
    response = client.get("/api/airports/LAX/conditions")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["observation_available"] is False


def test_endpoint_404s_for_an_unknown_airport(client, stub_provider):
    stub_provider(responder([metar()]))
    response = client.get("/api/airports/ZZZ/conditions")
    assert response.status_code == 404
    assert "Unknown airport" in response.json()["detail"]


def test_weather_outage_does_not_affect_analytics(client, stub_provider):
    """The load-bearing guarantee: weather is isolated from the investment path."""
    def dead(request):
        raise httpx.ConnectError("upstream down", request=request)

    stub_provider(dead)

    assert client.get("/api/airports/SFO/conditions").json()["status"] == "unavailable"
    assert client.get("/health").status_code == 200
    assert client.get("/api/airports/SFO/score").status_code == 200
    assert client.get("/api/airports/SFO/metrics").status_code == 200
    assert client.post("/api/rank", json={"region": "New England"}).status_code == 200
    assert client.post("/api/chat", json={"message": "Compare LAX and SNA."}).status_code == 200


def test_scores_are_identical_with_and_without_live_weather(client, stub_provider):
    """Conditions must not perturb a single analytic figure."""
    before = client.get("/api/airports/SFO/score").json()["expansion_score"]

    stub_provider(responder([metar(fltCat=None, visib="0.25", wxString="+SN",
                                   clouds=[{"cover": "OVC", "base": 100}])]))
    assert client.get("/api/airports/SFO/conditions").json()["flight_category"] == "LIFR"

    after = client.get("/api/airports/SFO/score").json()["expansion_score"]
    assert before == after


def test_system_prompt_separates_the_two_sources():
    from app.agent.prompts import SYSTEM_PROMPT

    assert "AviationWeather.gov" in SYSTEM_PROMPT
    assert "operational context only" in SYSTEM_PROMPT
    assert "not part of any score" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Deterministic fallback routing (the no-API-key path)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("question", [
    "What is the weather at SFO right now?",
    "Current conditions at SFO?",
    "Show me the METAR for SFO",
    "Is it raining at SFO?",
    "What is the flight category at SFO?",
])
def test_weather_questions_route_to_the_conditions_tool(dataset, stub_provider, question):
    from app.agent import fallback

    stub_provider(responder([metar()]))
    result = fallback.answer(question, dataset, 2500.0)
    assert [c["tool"] for c in result["tool_calls"]] == ["get_airport_conditions"]
    assert "AviationWeather.gov" in result["answer"]
    assert "not** an input to the Airport Expansion Score" in result["answer"]


@pytest.mark.parametrize("question,expected_tool", [
    ("What is the unmet flight demand at SFO right now?", "get_unmet_demand_proxy"),
    ("What percentage of flights from ANC are long-haul?", "get_long_haul_share"),
    ("Compare LAX and SNA congestion levels.", "compare_congestion"),
    ("Which airports in New England are strong candidates?", "rank_airports"),
])
def test_analytics_questions_are_not_hijacked_by_weather_routing(
    dataset, stub_provider, question, expected_tool
):
    """Adding a weather intent must not steal any existing analytics question."""
    from app.agent import fallback

    stub_provider(responder([metar()]))
    result = fallback.answer(question, dataset, 2500.0)
    assert [c["tool"] for c in result["tool_calls"]] == [expected_tool]


def test_conditions_answer_degrades_readably(dataset, stub_provider):
    from app.agent import fallback

    stub_provider(responder([], status_code=503))
    answer = fallback.answer("weather at SFO", dataset, 2500.0)["answer"]
    assert "No observation available" in answer
    assert "AviationWeather.gov" in answer

"""HTTP contract tests for the FastAPI surface."""

from __future__ import annotations

import pytest

NEW_ENGLAND = {"BOS", "PVD", "BDL", "MHT", "PWM", "BTV"}


def test_data_status_never_claims_demo_data_is_live(client):
    body = client.get("/api/data-status").json()
    assert body["status"] == "demo"
    assert body["is_demo"] is True
    assert body["label"].startswith("DEMO")


def test_list_airports(client):
    body = client.get("/api/airports").json()
    assert body["count"] >= 9
    assert {"SFO", "LAX", "SNA", "ANC", "BOS", "PVD", "BDL", "JFK", "EWR"} <= {
        a["iata"] for a in body["airports"]}


def test_list_airports_by_region(client):
    body = client.get("/api/airports", params={"region": "New England"}).json()
    assert {a["iata"] for a in body["airports"]} == NEW_ENGLAND
    assert body["region_resolution"]["kind"] == "region"


def test_unknown_region_is_a_400(client):
    response = client.get("/api/airports", params={"region": "Narnia"})
    assert response.status_code == 400
    assert "Narnia" in response.json()["detail"]


def test_regions_endpoint(client):
    regions = client.get("/api/regions").json()["regions"]
    new_england = next(r for r in regions if r["region"] == "New England")
    assert set(new_england["airports"]) == NEW_ENGLAND


def test_metrics_endpoint_shape(client):
    body = client.get("/api/airports/SFO/metrics").json()
    metrics = body["metrics"]
    assert metrics["iata"] == "SFO"
    assert metrics["passengers"] > 0
    assert 0 < metrics["load_factor"] < 1
    assert body["long_haul"]["long_haul_threshold_miles"] == 2500.0
    assert body["unmet_demand"]["is_proxy"] is True
    assert body["data_status"]["status"] == "demo"


def test_metrics_accepts_lowercase(client):
    assert client.get("/api/airports/sfo/metrics").status_code == 200


def test_score_endpoint_shape(client):
    body = client.get("/api/airports/AUS/score").json()
    assert 0 <= body["expansion_score"] <= 100
    assert len(body["components"]) == 5
    assert sum(c["points"] for c in body["components"]) == pytest.approx(
        body["expansion_score"], abs=0.05)
    assert body["weights"]["demand_pressure"] == 30.0


def test_unknown_airport_is_a_404(client):
    for path in ("/api/airports/ZZZ/metrics", "/api/airports/ZZZ/score"):
        response = client.get(path)
        assert response.status_code == 404
        assert "Unknown airport" in response.json()["detail"]


def test_compare_endpoint(client):
    body = client.post("/api/compare", json={"iatas": ["LAX", "SNA"]}).json()
    assert body["view"] == "full"
    assert body["result"]["iatas"] == ["LAX", "SNA"]
    assert body["result"]["higher_score"] in {"LAX", "SNA"}


def test_compare_congestion_view(client):
    body = client.post("/api/compare",
                       json={"iatas": ["LAX", "SNA"], "view": "congestion"}).json()
    assert {a["iata"] for a in body["result"]["airports"]} == {"LAX", "SNA"}


def test_compare_requires_two_distinct_airports(client):
    assert client.post("/api/compare", json={"iatas": ["LAX"]}).status_code == 422
    assert client.post("/api/compare", json={"iatas": ["LAX", "lax"]}).status_code == 422


def test_compare_unknown_airport_is_a_404(client):
    assert client.post("/api/compare",
                       json={"iatas": ["LAX", "ZZZ"]}).status_code == 404


def test_rank_endpoint_for_new_england(client):
    body = client.post("/api/rank",
                       json={"region": "New England", "limit": 5}).json()
    assert body["count"] == 5
    assert all(r["region"] == "New England" for r in body["results"])
    scores = [r["expansion_score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_rank_validates_the_sort_field(client):
    response = client.post("/api/rank", json={"sort_by": "vibes"})
    assert response.status_code == 400
    assert "Unsupported sort field" in response.json()["detail"]


def test_rank_validates_the_limit(client):
    assert client.post("/api/rank", json={"limit": 0}).status_code == 422
    assert client.post("/api/rank", json={"limit": 500}).status_code == 422


def test_chat_endpoint_answers_and_reports_provenance(client):
    body = client.post("/api/chat",
                       json={"message": "What percentage of flights from ANC "
                                        "are long-haul?"}).json()
    assert body["answer"]
    assert body["data_status"] == "demo"
    assert [c["tool"] for c in body["tool_calls"]] == ["get_long_haul_share"]


def test_chat_accepts_history_for_follow_ups(client):
    body = client.post("/api/chat", json={
        "message": "Which one is a better expansion candidate?",
        "history": [
            {"role": "user", "content": "Compare LAX and SNA."},
            {"role": "assistant", "content": "LAX scores higher."},
        ],
    }).json()
    assert body["tool_calls"]
    assert set(body["tool_calls"][0]["input"]["iatas"]) == {"LAX", "SNA"}


def test_chat_rejects_an_empty_message(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_overview_endpoint(client):
    body = client.get("/api/overview").json()
    assert body["airport_count"] >= 9
    assert body["provenance"]["status"] == "demo"
    assert body["long_haul_threshold_miles"] == 2500.0

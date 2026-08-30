"""/health must be fast, dependency-free and always 200."""

from __future__ import annotations

import httpx
import pytest

from app import __version__


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_health_makes_no_outbound_calls(client, monkeypatch):
    """Break every outbound HTTP path; /health must still succeed."""

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("/health must not make an external request")

    monkeypatch.setattr(httpx, "get", explode)
    monkeypatch.setattr(httpx, "request", explode)
    monkeypatch.setattr(httpx.Client, "send", explode)

    response = client.get("/health")
    assert response.status_code == 200


def test_root_lists_endpoints(client):
    body = client.get("/").json()
    assert body["docs_url"] == "/docs"
    assert "GET /health" in body["endpoints"]
    assert "POST /api/chat" in body["endpoints"]


def test_openapi_and_docs_available(client):
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200

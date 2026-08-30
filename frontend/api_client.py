"""Thin HTTP client for the FastAPI backend.

The frontend holds no analytics logic of its own — it renders whatever the API
returns. Every call has a timeout and converts transport failures into a single
:class:`APIError` the UI can display calmly.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_TIMEOUT = float(os.getenv("API_TIMEOUT_SECONDS", "60"))


class APIError(RuntimeError):
    """Any failure talking to the backend."""


class AirportAPIClient:
    """Client for the Airport Investment Intelligence API."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        raw = (base_url or os.getenv("API_BASE_URL", "http://localhost:8000")).strip()
        # Render's `fromService` injects a bare host ("api.onrender.com"), so
        # supply a scheme when one is missing rather than failing at request time.
        if not raw.startswith(("http://", "https://")):
            raw = ("http://" if raw.startswith(("localhost", "127.0.0.1")) else "https://") + raw
        self.base_url = raw.rstrip("/")
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(method, url, timeout=self.timeout, **kwargs)
        except httpx.TimeoutException as exc:
            raise APIError(f"The API did not respond within {self.timeout:.0f}s.") from exc
        except httpx.HTTPError as exc:
            raise APIError(f"Could not reach the API at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("detail", detail)
            except ValueError:
                pass
            raise APIError(f"API error {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise APIError("The API returned a non-JSON response.") from exc

    # -- endpoints ---------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def overview(self) -> dict[str, Any]:
        return self._request("GET", "/api/overview")

    def data_status(self, refresh: bool = False) -> dict[str, Any]:
        return self._request("GET", "/api/data-status",
                             params={"refresh": str(refresh).lower()})

    def airports(self, region: str | None = None) -> dict[str, Any]:
        params = {"region": region} if region else None
        return self._request("GET", "/api/airports", params=params)

    def regions(self) -> dict[str, Any]:
        return self._request("GET", "/api/regions")

    def metrics(self, iata: str) -> dict[str, Any]:
        return self._request("GET", f"/api/airports/{iata.upper()}/metrics")

    def score(self, iata: str) -> dict[str, Any]:
        return self._request("GET", f"/api/airports/{iata.upper()}/score")

    def compare(self, iatas: list[str], view: str = "full") -> dict[str, Any]:
        return self._request("POST", "/api/compare",
                             json={"iatas": iatas, "view": view})

    def rank(self, region: str | None = None, limit: int = 10,
             sort_by: str = "expansion_score") -> dict[str, Any]:
        return self._request("POST", "/api/rank",
                             json={"region": region, "limit": limit,
                                   "sort_by": sort_by})

    def chat(self, message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/chat",
                             json={"message": message, "history": history or []})

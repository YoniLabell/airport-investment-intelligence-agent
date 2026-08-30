"""Shared fixtures. Every test runs against the bundled demo dataset."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("USE_DEMO_DATA", "true")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("LONG_HAUL_MILES", "2500")

from app.config import Settings  # noqa: E402
from app.data.demo_provider import DemoDataProvider  # noqa: E402
from app.data.repository import DataRepository  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(use_demo_data=True, anthropic_api_key="", long_haul_miles=2500.0)


@pytest.fixture(scope="session")
def dataset():
    """The bundled demo dataset, loaded once for the whole session."""
    return DemoDataProvider().load()


@pytest.fixture(scope="session")
def repository(settings) -> DataRepository:
    return DataRepository(settings=settings)


@pytest.fixture(scope="session")
def client():
    """FastAPI test client pinned to the demo dataset."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

"""Application configuration, loaded from environment variables.

No secrets are ever hardcoded here. Everything sensitive (``ANTHROPIC_API_KEY``)
comes from the environment, optionally seeded by a local ``.env`` file that is
git-ignored. See ``.env.example``.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = Path(__file__).resolve().parent / "data" / "seed"


class Settings(BaseSettings):
    """Runtime settings. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", str(PROJECT_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- AI ---------------------------------------------------------------
    anthropic_api_key: str = Field(default="", description="Anthropic API key.")
    anthropic_model: str = Field(default="claude-opus-5")
    anthropic_max_tokens: int = Field(default=16000, ge=256, le=64000)
    anthropic_timeout_seconds: float = Field(default=45.0, gt=0)
    agent_max_tool_rounds: int = Field(default=6, ge=1, le=12)

    # --- Data -------------------------------------------------------------
    use_demo_data: bool = Field(
        default=False,
        description="Force the bundled demo dataset and skip any live fetch.",
    )
    bts_base_url: str = Field(
        default="https://transtats.bts.gov",
        description="Base URL of the upstream BTS/DOT data service.",
    )
    bts_t100_url: str = Field(
        default="",
        description=(
            "Direct URL to a BTS T-100 Segment CSV (or zipped CSV) extract. "
            "Leave empty to disable live fetching."
        ),
    )
    bts_local_extract_dir: str = Field(
        default="",
        description=(
            "Directory holding manually downloaded BTS T-100 Segment CSVs. "
            "Takes precedence over the network fetch."
        ),
    )
    data_timeout_seconds: float = Field(default=10.0, gt=0)
    cache_ttl_seconds: int = Field(default=6 * 60 * 60, ge=0)
    cache_dir: Path = Field(default=PROJECT_ROOT / ".cache")

    # --- Live operational context (AviationWeather.gov) -------------------
    # Separate from the analytics data above on purpose: this is current
    # conditions, never an input to any score or ranking.
    enable_live_weather: bool = Field(
        default=True,
        description="Fetch current METAR conditions from AviationWeather.gov.",
    )
    aviation_weather_base_url: str = Field(
        default="https://aviationweather.gov/api/data",
        description="Base URL of the NOAA/NWS Aviation Weather Center Data API.",
    )
    aviation_weather_timeout_seconds: float = Field(default=8.0, gt=0)
    aviation_weather_cache_ttl_seconds: int = Field(
        default=600,
        ge=0,
        description="METARs are issued about hourly; 10 minutes is plenty.",
    )

    # --- Analytics --------------------------------------------------------
    long_haul_miles: float = Field(
        default=2500.0,
        gt=0,
        description="Deterministic long-haul threshold, in statute miles.",
    )

    # --- Service wiring ---------------------------------------------------
    api_base_url: str = Field(default="http://localhost:8000")
    log_level: str = Field(default="INFO")
    cors_allow_origins: str = Field(default="*")

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_allow_origins.strip()
        if raw in {"", "*"}:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()

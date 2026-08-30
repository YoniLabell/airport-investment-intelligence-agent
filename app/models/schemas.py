"""Request and response schemas for the FastAPI surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    """Liveness payload. Computed without touching any external service."""

    status: Literal["ok"] = "ok"
    version: str
    service: str = "airport-investment-intelligence-api"


class RootResponse(BaseModel):
    name: str
    version: str
    description: str
    docs_url: str
    endpoints: list[str]


class DataStatusResponse(BaseModel):
    status: Literal["live", "cached", "demo"]
    source_name: str
    description: str
    fetched_at: str
    coverage_years: list[int]
    airport_count: int
    notes: str
    label: str
    is_demo: bool


class AirportSummary(BaseModel):
    iata: str
    name: str
    city: str
    state: str
    region: str


class AirportListResponse(BaseModel):
    count: int
    region_query: str | None = None
    region_resolution: dict[str, Any] | None = None
    airports: list[AirportSummary]
    data_status: DataStatusResponse


class MetricsResponse(BaseModel):
    iata: str
    metrics: dict[str, Any]
    long_haul: dict[str, Any]
    unmet_demand: dict[str, Any]
    data_status: DataStatusResponse


class ScoreResponse(BaseModel):
    iata: str
    expansion_score: float
    rating: str
    components: list[dict[str, Any]]
    weights: dict[str, float]
    methodology: str
    top_drivers: list[str]
    biggest_gaps: list[str]
    pillar_detail: dict[str, Any]
    latest_year: int
    base_year: int
    data_status: DataStatusResponse


class CompareRequest(BaseModel):
    iatas: list[str] = Field(..., min_length=2, max_length=6,
                             description="Two to six IATA codes.")
    view: Literal["full", "congestion"] = Field(
        default="full",
        description="'full' for the whole comparison, 'congestion' for the "
                    "throughput-per-capacity view.",
    )

    @field_validator("iatas")
    @classmethod
    def _normalize(cls, codes: list[str]) -> list[str]:
        cleaned = [c.strip().upper() for c in codes if c and c.strip()]
        if len(set(cleaned)) < 2:
            raise ValueError("Provide at least two distinct IATA codes.")
        return cleaned


class CompareResponse(BaseModel):
    view: str
    result: dict[str, Any]
    data_status: DataStatusResponse


class RankRequest(BaseModel):
    region: str | None = Field(
        default=None,
        description="Region name (e.g. 'New England'), US state name, or state code. "
                    "Omit for a nationwide ranking.",
    )
    limit: int = Field(default=10, ge=1, le=50)
    sort_by: str = Field(default="expansion_score")
    ascending: bool = False


class RankResponse(BaseModel):
    region_query: str | None
    region_resolution: dict[str, Any]
    sort_by: str
    sort_label: str
    count: int
    candidate_pool: int | None = None
    results: list[dict[str, Any]]
    note: str | None = None
    latest_year: int | None = None
    data_status: DataStatusResponse


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Prior turns, oldest first. Enables follow-up questions "
                    "such as 'which one is the better candidate?'.",
    )


class ChatResponse(BaseModel):
    answer: str
    used_llm: bool
    model: str | None = None
    degraded: bool = False
    degraded_reason: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    data_status: str
    data_source: str
    data_label: str


class RegionsResponse(BaseModel):
    regions: list[dict[str, Any]]
    data_status: DataStatusResponse


class ErrorResponse(BaseModel):
    detail: str

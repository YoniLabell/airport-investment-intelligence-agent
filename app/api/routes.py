"""API routes.

Everything numeric is delegated to :mod:`app.analytics`; this layer only
validates input, wires dependencies and shapes responses.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import __version__
from app.agent.service import AgentService, get_agent_service
from app.analytics.metrics import (
    UnknownAirportError,
    get_airport_metrics,
    long_haul_breakdown,
    resolve_iata,
)
from app.analytics.ranking import (
    SORTABLE_FIELDS,
    compare_airports,
    congestion_comparison,
    dataset_overview,
    rank_airports,
)
from app.analytics.regions import filter_airports, list_regions
from app.analytics.scoring import expansion_score, unmet_demand_proxy
from app.config import Settings, get_settings
from app.data.dataset import AirportDataset
from app.data.repository import DataRepository, get_repository
from app.services.aviation_weather import get_weather_provider
from app.logging_config import get_logger
from app.models.schemas import (
    AirportListResponse,
    ConditionsResponse,
    ChatRequest,
    ChatResponse,
    CompareRequest,
    CompareResponse,
    DataStatusResponse,
    HealthResponse,
    MetricsResponse,
    RankRequest,
    RankResponse,
    RegionsResponse,
    RootResponse,
    ScoreResponse,
)

log = get_logger(__name__)

router = APIRouter()

SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[DataRepository, Depends(get_repository)]
AgentDep = Annotated[AgentService, Depends(get_agent_service)]


def _dataset(repository: DataRepository) -> AirportDataset:
    """Load the active dataset, converting failures into a 503."""
    try:
        return repository.get_dataset()
    except Exception as exc:  # noqa: BLE001
        log.exception("dataset unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No airport dataset is available.",
        ) from exc


def _status(dataset: AirportDataset) -> dict[str, Any]:
    return dataset.provenance.to_dict()


def _not_found(exc: UnknownAirportError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


# ---------------------------------------------------------------------------
# Service endpoints
# ---------------------------------------------------------------------------
@router.get("/api", response_model=RootResponse, tags=["service"])
def api_root() -> RootResponse:
    """Service banner and endpoint index.

    ``GET /`` serves the dashboard itself (static HTML), so the machine-readable
    banner lives here.
    """
    return RootResponse(
        name="Airport Investment Intelligence API",
        version=__version__,
        description=(
            "Screening analytics for US airport terminal expansion and "
            "modernization candidates. Statistics are computed in Python; "
            "Claude explains them."
        ),
        docs_url="/docs",
        endpoints=[
            "GET /            (dashboard)",
            "GET /health",
            "GET /api/airports",
            "GET /api/airports/{iata}/metrics",
            "GET /api/airports/{iata}/score",
            "GET /api/airports/{iata}/conditions",
            "GET /api/regions",
            "GET /api/data-status",
            "POST /api/compare",
            "POST /api/rank",
            "POST /api/chat",
        ],
    )


@router.get("/health", response_model=HealthResponse, tags=["service"])
def health() -> HealthResponse:
    """Liveness probe.

    Deliberately does no I/O: no dataset load, no upstream call, no Anthropic
    request. Render's health check must stay fast and must never fail because
    an external dependency is having a bad day.
    """
    return HealthResponse(status="ok", version=__version__)


@router.get("/api/data-status", response_model=DataStatusResponse, tags=["data"])
def data_status(repository: RepositoryDep,
                refresh: bool = Query(False, description="Force a re-fetch.")
                ) -> DataStatusResponse:
    """Whether the active dataset is live, cached or demo."""
    if refresh:
        repository.reset()
    dataset = _dataset(repository)
    return DataStatusResponse(**_status(dataset))


# ---------------------------------------------------------------------------
# Airports
# ---------------------------------------------------------------------------
@router.get("/api/airports", response_model=AirportListResponse, tags=["airports"])
def airports(repository: RepositoryDep,
             region: str | None = Query(
                 None, description="Region name, US state name, or state code.")
             ) -> AirportListResponse:
    """List covered airports, optionally filtered by region or state."""
    dataset = _dataset(repository)
    subset, resolution = filter_airports(dataset.airports, region)
    if region and not resolution.matched:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"Unrecognised region '{region}'. Try a region name such as "
                    "'New England' or a US state name/code."),
        )
    return AirportListResponse(
        count=int(len(subset)),
        region_query=region,
        region_resolution=resolution.to_dict(),
        airports=[
            {"iata": r.iata, "name": r.name, "city": r.city,
             "state": r.state, "region": r.region}
            for r in subset.itertuples()
        ],
        data_status=DataStatusResponse(**_status(dataset)),
    )


@router.get("/api/regions", response_model=RegionsResponse, tags=["airports"])
def regions(repository: RepositoryDep) -> RegionsResponse:
    """Regions present in the dataset and their member airports."""
    dataset = _dataset(repository)
    return RegionsResponse(regions=list_regions(dataset.airports),
                           data_status=DataStatusResponse(**_status(dataset)))


@router.get("/api/airports/{iata}/metrics", response_model=MetricsResponse,
            tags=["airports"])
def airport_metrics(iata: str, repository: RepositoryDep,
                    settings: SettingsDep) -> MetricsResponse:
    """Full deterministic metric bundle for one airport."""
    dataset = _dataset(repository)
    threshold = settings.long_haul_miles
    try:
        return MetricsResponse(
            iata=iata.upper(),
            metrics=get_airport_metrics(dataset, iata, threshold),
            long_haul=long_haul_breakdown(dataset, iata, threshold),
            unmet_demand=unmet_demand_proxy(dataset, iata, threshold),
            data_status=DataStatusResponse(**_status(dataset)),
        )
    except UnknownAirportError as exc:
        raise _not_found(exc) from exc


@router.get("/api/airports/{iata}/score", response_model=ScoreResponse,
            tags=["scoring"])
def airport_score(iata: str, repository: RepositoryDep,
                  settings: SettingsDep) -> ScoreResponse:
    """Airport Expansion Score with its full component breakdown."""
    dataset = _dataset(repository)
    try:
        result = expansion_score(dataset, iata, settings.long_haul_miles)
    except UnknownAirportError as exc:
        raise _not_found(exc) from exc
    return ScoreResponse(data_status=DataStatusResponse(**_status(dataset)),
                         **{k: v for k, v in result.items()
                            if k in ScoreResponse.model_fields})


@router.get("/api/airports/{iata}/conditions", response_model=ConditionsResponse,
            tags=["conditions"])
def airport_conditions(iata: str, repository: RepositoryDep) -> ConditionsResponse:
    """Current weather at an airport, from AviationWeather.gov (NOAA/NWS).

    **Live operational context only.** This endpoint is completely separate from
    the investment analytics: nothing here feeds the Airport Expansion Score,
    the Unmet Demand Proxy or any ranking, which are computed from historical
    US DOT / BTS data.

    A 404 means the airport is not in the dataset. An upstream outage does *not*
    produce an error status — it returns 200 with ``status`` set to
    ``unavailable``, so a weather problem degrades one panel instead of the
    request.
    """
    dataset = _dataset(repository)
    try:
        code = resolve_iata(dataset, iata)
    except UnknownAirportError as exc:
        raise _not_found(exc) from exc
    return ConditionsResponse(**get_weather_provider().get_conditions(code))


# ---------------------------------------------------------------------------
# Comparison and ranking
# ---------------------------------------------------------------------------
@router.post("/api/compare", response_model=CompareResponse, tags=["scoring"])
def compare(request: CompareRequest, repository: RepositoryDep,
            settings: SettingsDep) -> CompareResponse:
    """Compare two or more airports, either fully or on congestion alone."""
    dataset = _dataset(repository)
    threshold = settings.long_haul_miles
    try:
        if request.view == "congestion":
            result = congestion_comparison(dataset, request.iatas, threshold)
        else:
            result = compare_airports(dataset, request.iatas, threshold)
    except UnknownAirportError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc
    return CompareResponse(view=request.view, result=result,
                           data_status=DataStatusResponse(**_status(dataset)))


@router.post("/api/rank", response_model=RankResponse, tags=["scoring"])
def rank(request: RankRequest, repository: RepositoryDep,
         settings: SettingsDep) -> RankResponse:
    """Rank airports by a chosen metric, optionally within a region or state."""
    dataset = _dataset(repository)
    if request.sort_by not in SORTABLE_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported sort field '{request.sort_by}'. Choose one of: "
                   f"{', '.join(sorted(SORTABLE_FIELDS))}.",
        )
    result = rank_airports(dataset, region=request.region, limit=request.limit,
                           sort_by=request.sort_by, ascending=request.ascending,
                           long_haul_miles=settings.long_haul_miles)
    return RankResponse(data_status=DataStatusResponse(**_status(dataset)),
                        **{k: v for k, v in result.items()
                           if k in RankResponse.model_fields})


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
@router.post("/api/chat", response_model=ChatResponse, tags=["agent"])
def chat(request: ChatRequest, agent: AgentDep) -> ChatResponse:
    """Ask the analyst agent a question.

    The agent picks deterministic tools, runs them, and asks Claude to explain
    the structured results. Claude never computes a statistic itself.
    """
    try:
        result = agent.answer(
            request.message,
            [m.model_dump() for m in request.history],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc
    return ChatResponse(**result)


@router.get("/api/overview", tags=["data"])
def overview(repository: RepositoryDep, settings: SettingsDep) -> dict[str, Any]:
    """Dataset coverage summary used by the dashboard header."""
    dataset = _dataset(repository)
    return dataset_overview(dataset, settings.long_haul_miles)

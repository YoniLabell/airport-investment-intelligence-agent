"""FastAPI application entry point.

Run locally with::

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.config import PROJECT_ROOT, get_settings
from app.logging_config import configure_logging, get_logger

FRONTEND_DIR = PROJECT_ROOT / "frontend"

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the dataset at start-up without letting a failure block boot.

    ``/health`` must answer even if the upstream data source is down, so any
    problem here is logged and deferred to the first data request.
    """
    log.info("starting Airport Investment Intelligence API v%s", __version__)
    try:
        from app.data.repository import get_repository

        provenance = get_repository().get_dataset().provenance
        log.info("dataset ready: %s (%d airports)",
                 provenance.label, provenance.airport_count)
    except Exception as exc:  # noqa: BLE001
        log.warning("dataset warm-up failed (%s); will retry on first request", exc)
    yield
    log.info("shutting down")


app = FastAPI(
    title="Airport Investment Intelligence API",
    version=__version__,
    description=(
        "Screening analytics for US airport terminal expansion and modernization. "
        "All statistics are computed deterministically in Python; the Claude agent "
        "selects tools and explains results but never calculates them.\n\n"
        "**This is an investment screening tool, not a financial valuation model.**"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)

# The dashboard is plain static HTML/CSS/JS with no build step, served by this
# same app. Mounting it last means the API routes above always win; anything
# else falls through to a file. Serving both from one origin also means the
# browser never makes a cross-origin request, so the UI needs no API base URL
# and no CORS preflight.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="dashboard")
    log.info("serving dashboard from %s", FRONTEND_DIR)
else:  # pragma: no cover - only when the frontend is deployed separately
    log.warning("frontend directory %s not found; API-only mode", FRONTEND_DIR)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail, return a generic message — never leak internals."""
    log.exception("unhandled error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check the service logs."},
    )

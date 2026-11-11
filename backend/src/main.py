"""NHTSA Manufacturer Communications API - FastAPI Application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.billing.router import router as billing_router
from src.communications.router import router as communications_router
from src.config import Settings, get_settings
from src.corpus.router import router as corpus_router
from src.db import close_pool
from src.jobs import batch_watch
from src.me.router import router as me_router
from src.public.router import router as public_router
from src.refresh import queue as ingest_queue
from src.refresh.router import router as refresh_router
from src.symptom_search.router import router as symptom_search_router
from src.usage.router import router as usage_router
from src.vehicles.router import read_router as vehicles_read_router
from src.vehicles.router import write_router as vehicles_write_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events.

    Nothing to open. The Postgres pool is created lazily on first use, so a
    missing DATABASE_URL surfaces as a failed request rather than a failed
    startup, and there is no second store to connect to any more.
    """
    if not get_settings().read_only:
        # Cars queued before a restart or a deploy are picked up again here.
        ingest_queue.ensure_worker()
        # A batch the nightly run could not wait for is collected here when it ends.
        batch_watch.ensure_running()
    yield
    await close_pool()


BACKEND_DIR = Path(__file__).resolve().parents[1]

# Paths the SPA catch-all must never answer with index.html.
_NOT_SPA = ("api", "docs", "redoc", "openapi.json")


def _dist_dir(settings: Settings) -> Path | None:
    """The built frontend directory, or None when unset or missing."""
    if not settings.frontend_dist:
        return None
    dist = Path(settings.frontend_dist)
    if not dist.is_absolute():
        dist = BACKEND_DIR / dist
    dist = dist.resolve()
    return dist if (dist / "index.html").is_file() else None


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. A parameter so tests can vary read-only mode and the dist."""
    settings = settings or get_settings()
    app = FastAPI(
        title="NHTSA Manufacturer Communications API",
        description=(
            "API for fetching, filtering, and viewing NHTSA manufacturer communications "
            "for specific vehicles. Supports real-time fetch progress via SSE."
        ),
        version="2.0.0",
        lifespan=lifespan,
    )

    # CORS configuration. In production the frontend is same-origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def www_to_apex(request: Request, call_next):
        """www.example.com answers with a permanent redirect to example.com, path and query kept."""
        host = request.headers.get("host", "")
        if host.lower().startswith("www."):
            target = request.url.replace(scheme="https", netloc=host[4:])
            return RedirectResponse(str(target), status_code=308)
        return await call_next(request)

    # Mount routers
    app.include_router(vehicles_read_router, prefix="/api")
    app.include_router(communications_router, prefix="/api")
    app.include_router(corpus_router, prefix="/api")
    app.include_router(billing_router, prefix="/api")
    app.include_router(me_router, prefix="/api")
    app.include_router(public_router, prefix="/api")
    app.include_router(symptom_search_router, prefix="/api")
    if not settings.read_only:
        app.include_router(vehicles_write_router, prefix="/api")
        app.include_router(refresh_router, prefix="/api")
        app.include_router(usage_router, prefix="/api")
    else:

        @app.exception_handler(StarletteHTTPException)
        async def hide_write_routes(request: Request, exc: StarletteHTTPException):
            """A write to a path that only has reads answers 404, as if it did not exist."""
            if exc.status_code == 405:
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return await http_exception_handler(request, exc)

    @app.get("/api/health", tags=["Health"])
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "service": "nhtsa-manu-comms-api",
            "readOnly": settings.read_only,
        }

    dist = _dist_dir(settings)
    if dist is None:

        @app.get("/", include_in_schema=False)
        async def root():
            """Redirect to API docs."""
            return {"message": "NHTSA Manufacturer Communications API", "docs": "/docs"}

    else:
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            """Serve the SPA for any path that is not the API or its docs."""
            if path.split("/", 1)[0] in _NOT_SPA:
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app()

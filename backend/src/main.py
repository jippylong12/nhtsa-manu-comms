"""NHTSA Manufacturer Communications API - FastAPI Application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.communications.router import router as communications_router
from src.config import get_settings
from src.corpus.router import router as corpus_router
from src.database import close_mongodb_connection, connect_to_mongodb
from src.db import close_pool
from src.vehicles.router import router as vehicles_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    # Startup
    await connect_to_mongodb()
    # The Postgres pool is created lazily on first corpus request, so a missing
    # DATABASE_URL never blocks the Mongo-backed endpoints from starting.
    yield
    # Shutdown
    await close_mongodb_connection()
    await close_pool()


app = FastAPI(
    title="NHTSA Manufacturer Communications API",
    description=(
        "API for fetching, filtering, and viewing NHTSA manufacturer communications "
        "for specific vehicles. Supports real-time fetch progress via SSE."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# CORS configuration
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(vehicles_router, prefix="/api")
app.include_router(communications_router, prefix="/api")
app.include_router(corpus_router, prefix="/api")


@app.get("/api/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "nhtsa-manu-comms-api"}


@app.get("/", include_in_schema=False)
async def root():
    """Redirect to API docs."""
    return {"message": "NHTSA Manufacturer Communications API", "docs": "/docs"}

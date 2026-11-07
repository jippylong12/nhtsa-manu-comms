"""Read-only mode: the early deploy cannot spend money or change data (JIP-1595)."""

import httpx
import pytest_asyncio

from src.config import Settings
from src.main import create_app


def _client(read_only: bool) -> httpx.AsyncClient:
    app = create_app(Settings(read_only=read_only, frontend_dist=""))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def read_only_client():
    async with _client(True) as client:
        yield client


@pytest_asyncio.fixture
async def writable_client():
    async with _client(False) as client:
        yield client


async def test_read_only_hides_every_write_and_spend_route(read_only_client):
    body = {"year": "2024", "make": "TESLA", "model": "MODEL 3"}
    assert (await read_only_client.post("/api/vehicles", json=body)).status_code == 404
    assert (await read_only_client.patch("/api/vehicles/1", json={"keywords": []})).status_code == 404
    assert (await read_only_client.delete("/api/vehicles/1")).status_code == 404
    assert (await read_only_client.post("/api/refresh/1")).status_code == 404
    assert (await read_only_client.get("/api/admin/usage")).status_code == 404


async def test_read_only_keeps_reads_and_reports_the_flag(read_only_client):
    assert (await read_only_client.get("/api/vehicles")).status_code == 200
    assert (await read_only_client.get("/api/corpus/communications")).status_code == 200
    health = (await read_only_client.get("/api/health")).json()
    assert health["readOnly"] is True
    assert health["status"] == "healthy"


async def test_writable_mode_behaves_as_before(writable_client):
    assert (await writable_client.get("/api/vehicles")).status_code == 200
    assert (await writable_client.get("/api/admin/usage")).status_code == 200
    assert (await writable_client.get("/api/health")).json()["readOnly"] is False
    # The routes exist: an invalid body is a 422, a missing vehicle a 404 with detail.
    assert (await writable_client.post("/api/vehicles", json={})).status_code == 422
    missing = await writable_client.delete("/api/vehicles/999999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Vehicle not found"

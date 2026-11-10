"""www.<domain> permanently redirects to the bare domain, keeping the path and query."""

import httpx
import pytest

from src.main import create_app


def _client(host: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url=f"http://{host}",
        follow_redirects=False,
    )


@pytest.mark.asyncio
async def test_www_redirects_to_the_bare_domain_with_path_and_query():
    async with _client("www.notjustmycar.com") as c:
        r = await c.get("/app/vehicles/7?b=11027376")
    assert r.status_code == 308
    assert r.headers["location"] == "https://notjustmycar.com/app/vehicles/7?b=11027376"


@pytest.mark.asyncio
async def test_the_bare_domain_is_served_not_redirected():
    async with _client("notjustmycar.com") as c:
        r = await c.get("/api/health")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_a_post_to_www_keeps_its_method():
    async with _client("www.notjustmycar.com") as c:
        r = await c.post("/api/billing/webhook", content=b"{}")
    assert r.status_code == 308  # 308, not 301/302: the method and body are preserved

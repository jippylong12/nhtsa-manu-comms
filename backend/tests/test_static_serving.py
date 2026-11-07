"""The API serves the built frontend with SPA fallback (JIP-1595)."""

from fastapi.testclient import TestClient

from src.config import Settings
from src.main import create_app


def _dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html><body>SPA</body></html>")
    (tmp_path / "assets" / "app.js").write_text("console.log('app')")
    return tmp_path


def _client(dist) -> TestClient:
    return TestClient(create_app(Settings(frontend_dist=str(dist))))


def test_spa_routes_serve_index_html(tmp_path):
    client = _client(_dist(tmp_path))
    for path in ("/", "/app", "/app/vehicles/1", "/app/vehicles/7"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "SPA" in response.text, path


def test_assets_are_served_as_files(tmp_path):
    client = _client(_dist(tmp_path))
    response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log('app')"


def test_api_routes_are_not_shadowed_by_the_spa(tmp_path):
    client = _client(_dist(tmp_path))
    assert client.get("/api/health").json()["status"] == "healthy"
    nope = client.get("/api/nope")
    assert nope.status_code == 404
    assert nope.headers["content-type"].startswith("application/json")
    assert client.get("/openapi.json").json()["info"]["title"]
    assert "swagger" in client.get("/docs").text.lower()


def test_without_a_dist_directory_root_is_the_json_message(tmp_path):
    client = _client(tmp_path / "missing")
    body = client.get("/").json()
    assert body["docs"] == "/docs"
    assert client.get("/app/vehicles/1").status_code == 404

"""Symptom-search endpoint: validation, scoping errors, rate limiting, shape.

Mounts only the symptom-search router on a bare app so the test never triggers
the Mongo lifespan, and monkeypatches the service so it never touches the DB or
the LLM. The service's own logic is covered in test_symptom_compose / retrieval.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth import deps
from src.symptom_search import router as router_mod
from src.symptom_search import service

app = FastAPI()
app.include_router(router_mod.router, prefix="/api")
client = TestClient(app)

_CANNED = {
    "answer": "Your issue matches [10000001].",
    "refused": False,
    "confidence": 0.81,
    "model": "gemini-3.1-flash-lite",
    "answer_generated": True,
    "matches": [
        {
            "nhtsa_id": "10000001",
            "communication_number": "NUM-1",
            "communication_type": "TSB",
            "communication_date": None,
            "llm_summary": "A bulletin.",
            "symptoms": ["x"],
            "systems": ["electrical"],
            "components": ["module"],
            "remedy": "reprogram",
            "relevance_score": 0.03,
            "cosine_sim": 0.81,
            "cited": True,
        }
    ],
    "cited_ids": ["10000001"],
    "cost": {
        "embed_tokens": 12,
        "embed_cost_usd": 0.0000018,
        "llm_model": "gemini-3.1-flash-lite",
        "llm_tokens_in": 400,
        "llm_tokens_out": 60,
        "llm_cost_usd": 0.00019,
        "total_usd": 0.0001918,
    },
}


@pytest.fixture(autouse=True)
def _stub_service(monkeypatch):
    async def _exists(vehicle_id):
        return vehicle_id == 218944

    async def _search(vehicle_id, description, model=None, user_id=None):
        return _CANNED

    # Sign in as the admin fake: the entitlement gate has its own tests.
    async def _admin():
        return {"id": 1, "is_admin": True}

    app.dependency_overrides[deps.current_user] = _admin

    monkeypatch.setattr(service, "vehicle_exists", _exists)
    monkeypatch.setattr(service, "search", _search)
    # Ensure a clean limiter per test.
    monkeypatch.setattr(router_mod._limiter, "allow", lambda key: True)
    yield
    app.dependency_overrides.clear()


def test_happy_path_returns_camelcase_payload():
    r = client.post("/api/symptom-search", json={"vehicleId": 218944, "description": "no heat"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("Your issue")
    assert body["matches"][0]["nhtsaId"] == "10000001"  # camelCase serialisation
    assert body["cost"]["totalUsd"] == pytest.approx(0.0001918)
    assert body["citedIds"] == ["10000001"]


def test_unknown_vehicle_is_404():
    r = client.post("/api/symptom-search", json={"vehicleId": 999999, "description": "no heat"})
    assert r.status_code == 404


def test_missing_description_is_422():
    r = client.post("/api/symptom-search", json={"vehicleId": 218944})
    assert r.status_code == 422


def test_blank_description_is_400():
    r = client.post("/api/symptom-search", json={"vehicleId": 218944, "description": "   "})
    assert r.status_code == 400


def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(router_mod._limiter, "allow", lambda key: False)
    monkeypatch.setattr(router_mod._limiter, "retry_after", lambda key: 42)
    r = client.post("/api/symptom-search", json={"vehicleId": 218944, "description": "no heat"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "42"

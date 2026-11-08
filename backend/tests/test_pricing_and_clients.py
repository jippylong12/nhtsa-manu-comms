"""Prices for the models the ledger sees, and the bounded Anthropic client."""

import anthropic
import pytest

from src import anthropic_client
from src.config import Settings
from src.usage.pricing import RATES, cost_of

MODEL_IDS = [
    "gemini-3.5-flash-lite",
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
]


@pytest.mark.parametrize("model", MODEL_IDS)
def test_every_priced_model_has_a_rate(model):
    assert model in RATES


def test_haiku_batch_cost_is_half_the_list_price():
    expected = (2188 * 0.10 + 270 * 0.50) / 1e6 * 0.5
    assert cost_of("claude-haiku-5-5", 2188, 270, batch=True) == pytest.approx(expected)


def test_the_default_extraction_model_is_claude_haiku_5_5(monkeypatch):
    monkeypatch.delenv("EXTRACTION_MODEL", raising=False)
    assert Settings(_env_file=None).extraction_model == "claude-haiku-5-5"


def test_the_default_compose_model_is_claude_haiku_5_5(monkeypatch):
    monkeypatch.delenv("COMPOSE_MODEL", raising=False)
    assert Settings(_env_file=None).compose_model == "claude-haiku-5-5"


def test_symptom_search_per_query_price_is_haiku():
    """The per-query figures the UI shows must come from the model compose calls."""
    from src.symptom_search import cost

    assert cost.PRICE_LLM_IN == RATES["claude-haiku-5-5"].input_per_token
    assert cost.PRICE_LLM_OUT == RATES["claude-haiku-5-5"].output_per_token


def test_a_missing_anthropic_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    monkeypatch.setattr(anthropic_client, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="CLAUDE_API_KEY"):
        anthropic_client.client()


def test_claude_api_key_name_is_accepted(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "x")
    assert Settings(_env_file=None).anthropic_api_key == "x"


def test_anthropic_api_key_name_is_accepted(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert Settings(_env_file=None).anthropic_api_key == "y"


def test_the_anthropic_client_carries_the_configured_timeout(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "dummy")
    settings = Settings(_env_file=None)
    monkeypatch.setattr(anthropic_client, "get_settings", lambda: settings)
    built = anthropic_client.client()
    assert isinstance(built, anthropic.Anthropic)
    assert built.timeout == float(settings.anthropic_timeout_seconds)

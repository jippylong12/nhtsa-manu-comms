"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignore unrecognised env vars rather than raising. Pydantic's
        # extra_forbidden error message embeds the offending *value*, so a
        # stray or mistyped key name in .env would otherwise print a live
        # secret into logs and tracebacks.
        extra="ignore",
    )

    # Postgres (the store; Railway + pgvector)
    database_url: str = ""
    postgres_pool_max: int = 10
    # Where the test suite points instead of `database_url`. Set, the whole
    # suite runs against a local Postgres; unset, it falls back to
    # `database_url`. Read only by tests/conftest.py — see docs/database.md.
    test_database_url: str = ""

    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # NHTSA API
    nhtsa_api_base_url: str = "https://api.nhtsa.gov"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Built frontend served by the API (single-service deploy). Relative paths resolve
    # against backend/. Missing directory or empty string: the API serves JSON only.
    frontend_dist: str = "../frontend/dist"

    # Read-only mode (READ_ONLY=true): the write, refresh and usage routes are not
    # mounted and /api/health reports readOnly. The early deploy runs this way.
    read_only: bool = False

    # Document pipeline
    pdf_archive_dir: str = ".pdf_archive"
    pdf_download_concurrency: int = 3
    # Below this, extraction is treated as garbled and the document is routed to
    # the vision fallback. The A1 spike measured a corpus minimum of 475
    # chars/page across 174 documents, so 200 is a safety net that fires on
    # roughly nothing rather than a routine path.
    low_yield_chars_per_page: int = 200

    # Gemini (embeddings; gemini_model is kept for the eval harness and bake-off arms)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_embedding_model: str = "gemini-embedding-001"
    # Per-request ceiling for every Gemini call (see src/gemini.py). Without
    # one, a stalled TLS read blocks forever rather than failing: a single
    # hung embedding call once held a test run open for 40+ minutes. Generous
    # enough for a 50-document embedding batch or a vision extraction, which
    # are the slowest calls made here.
    #
    # `gt=0` is load-bearing, not decoration. The SDK treats a falsy timeout as
    # "no timeout" (`if timeout:` in google.genai._api_client), so setting this
    # to 0 — the obvious way to write "no limit" — would silently restore the
    # unbounded hang. Rejecting it at startup makes that a loud error instead.
    gemini_timeout_seconds: int = Field(default=120, gt=0)
    # Anthropic (extraction bake-off; later the extraction model itself). Marcus saved
    # the key in backend/.env as CLAUDE_API_KEY; accept that name and the SDK's usual one.
    anthropic_api_key: str = Field(
        "", validation_alias=AliasChoices("CLAUDE_API_KEY", "ANTHROPIC_API_KEY")
    )
    # Per-request ceiling for every Anthropic call (see src/anthropic_client.py).
    anthropic_timeout_seconds: int = Field(default=300, gt=0)
    # The model the LLM extraction stage calls (src/jobs/llm.py).
    extraction_model: str = "claude-haiku-5-5"
    # The model symptom-search compose calls (src/symptom_search/compose.py). Haiku at
    # list price is a third of Flash-Lite on this prompt; see src/symptom_search/cost.py.
    compose_model: str = "claude-haiku-5-5"
    # Per-call ceiling for the compose call only. It runs inside a user-facing search
    # request, so it must fail in seconds; the shared anthropic_timeout_seconds is sized
    # for the extraction job's PDF calls.
    compose_timeout_seconds: int = Field(default=30, gt=0)
    # Live extraction calls in flight at once.
    anthropic_extraction_concurrency: int = Field(default=6, gt=0)
    # 1536, not the model's native 3072: pgvector cannot build an hnsw or
    # ivfflat index above 2000 dimensions. See migrations/0001.
    embedding_dimensions: int = 1536

    # Email digest
    resend_api_key: str = ""
    digest_from_email: str = ""
    digest_to_email: str = ""


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

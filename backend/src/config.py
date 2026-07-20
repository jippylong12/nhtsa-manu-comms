"""Application configuration using Pydantic Settings."""

from functools import lru_cache

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

    # MongoDB (legacy store, still backing the existing endpoints)
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "nhtsa_comms"

    # Postgres (canonical Phase A store, Railway + pgvector)
    database_url: str = ""
    postgres_pool_max: int = 10

    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # NHTSA API
    nhtsa_api_base_url: str = "https://api.nhtsa.gov"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Gemini (structured extraction + embeddings)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_embedding_model: str = "gemini-embedding-001"
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

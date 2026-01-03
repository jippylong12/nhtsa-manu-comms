"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # MongoDB
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_database: str = "nhtsa_comms"

    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # NHTSA API
    nhtsa_api_base_url: str = "https://api.nhtsa.gov"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

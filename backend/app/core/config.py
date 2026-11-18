"""Application settings, loaded from environment variables (prefix FL_) or a .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FL_", env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"), extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://fraudlens:fraudlens@localhost:5440/fraudlens"
    # Secret used to sign access tokens. MUST be overridden outside development.
    secret_key: str = "dev-only-insecure-secret-change-me"
    access_token_minutes: int = 480
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5180", "http://127.0.0.1:5180"])

    storage_backend: str = "local"  # "local" | "s3"
    storage_dir: Path = BACKEND_DIR / "var" / "storage"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_prefix: str = "fraudlens/"

    max_import_mb: int = 200
    max_evidence_mb: int = 50
    datasets_dir: Path = PROJECT_DIR / "datasets"

    # Bootstrap admin created on first start if no users exist.
    bootstrap_admin_email: str = "admin@fraudlens.local"
    bootstrap_admin_password: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

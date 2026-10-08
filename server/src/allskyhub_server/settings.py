"""Hub configuration from environment variables (prefix ``ALLSKYHUB_SERVER_``).

In production the variables come from an ``EnvironmentFile`` of the systemd unit; in development
from ``.dev/env`` (see ``tools/dev-postgres.sh``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALLSKYHUB_SERVER_", extra="ignore")

    env: Literal["dev", "prod"] = "prod"
    database_url: str = Field(description="postgresql://user:pass@host:port/db")
    base_url: str = "http://localhost:8000"

    data_dir: Path = Path("/var/lib/allskyhub-server")
    # SPEC §6.5: the hub asks for a new latest image at most this often (live view: every frame).
    latest_image_interval_s: int = Field(default=300, ge=10)
    # Thumbnails for the per-night gallery are fetched more often than full images.
    thumb_interval_s: int = Field(default=60, ge=10)
    # Archive retention (privacy policy): full images and thumbnails.
    keep_full_days: int = Field(default=7, ge=1)
    keep_thumb_days: int = Field(default=30, ge=1)
    # Night products (SPEC §6.5): a long 1080p timelapse can reach a few hundred MB.
    max_product_mb: int = Field(default=512, ge=1)
    # Push (roadmap #6): the Firebase service account JSON. Unset: push is off, nothing is
    # sent to Google and the privacy policy does not mention it.
    fcm_service_account_file: Path | None = None
    max_image_mb: int = Field(default=25, ge=1)

    session_cookie_name: str = "allskyhub_session"
    session_cookie_secure: bool = True
    # Trust X-Real-IP from the reverse proxy (only when listening behind nginx).
    trust_proxy_headers: bool = False

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("database_url")
    @classmethod
    def _plain_postgres_url(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("database_url must start with postgresql://")
        return v

    @property
    def async_database_url(self) -> str:
        """URL for SQLAlchemy with asyncpg."""
        return "postgresql+asyncpg://" + self.database_url.split("://", 1)[1]

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def push_enabled(self) -> bool:
        return self.fcm_service_account_file is not None

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment

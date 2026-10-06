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
    # Anyone may create an account (public hub); self-hosters can turn it off.
    allow_signup: bool = True

    data_dir: Path = Path("/var/lib/allskyhub-server")
    # SPEC §6.5: the hub asks for a new latest image at most this often (live view: every frame).
    latest_image_interval_s: int = Field(default=300, ge=10)
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
    def image_dir(self) -> Path:
        return self.data_dir / "images"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment

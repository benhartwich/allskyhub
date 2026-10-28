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
    # The same for cameras with Plus (roadmap N7); the privacy policy names both.
    keep_plus_full_days: int = Field(default=90, ge=1)
    keep_plus_thumb_days: int = Field(default=365, ge=1)
    # Roadmap N7: Paddle Billing for Plus. Off (no checkout, nothing sent to Paddle) until
    # the API key, client-side token, webhook secret and both price IDs are set.
    paddle_env: Literal["sandbox", "live"] = "sandbox"
    paddle_api_key: str = ""
    paddle_client_token: str = ""
    paddle_webhook_secret: str = ""
    paddle_price_month: str = ""
    paddle_price_year: str = ""
    # Roadmap C8: timelapses and keograms over several nights, made with ffmpeg.
    ffmpeg_path: str = "ffmpeg"
    keep_render_days: int = Field(default=7, ge=1)
    # Night products (SPEC §6.5): a long 1080p timelapse can reach a few hundred MB.
    max_product_mb: int = Field(default=512, ge=1)
    # Push (roadmap #6): the Firebase service account JSON. Unset: push is off, nothing is
    # sent to Google and the privacy policy does not mention it.
    fcm_service_account_file: Path | None = None
    # The app's own Firebase registration (Firebase console → project settings → your apps):
    # public identifiers, also inside every app build, handed to the app at runtime so the
    # builds need no Firebase files. Empty: the app does not register for push.
    fcm_sender_id: str = ""
    fcm_android_app_id: str = ""
    fcm_android_api_key: str = ""
    fcm_ios_app_id: str = ""
    fcm_ios_api_key: str = ""
    # E-mail (roadmap #15): off while smtp_host is empty. Submission with STARTTLS.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = "allskyhub <noreply@allskyhub.org>"
    max_image_mb: int = Field(default=25, ge=1)
    # Admin page (/admin): comma-separated e-mail addresses of accounts that may see the
    # server's state and start a deploy. Empty: no admin page.
    admin_emails: str = ""
    # Where the admin page looks for new versions and their CI (GitHub, public API).
    deploy_repo: str = "benhartwich/allskyhub"
    # deploy/android: root's Android builds. Root-owned, the hub only reads (android-test/).
    android_dir: Path = Path("/var/lib/allskyhub-android")
    # Which CI checks must be green before a deploy (name prefixes, comma-separated): the
    # hub's tests, not the Raspberry Pi image build, which takes much longer.
    deploy_checks: str = "check"

    session_cookie_name: str = "allskyhub_session"
    session_cookie_secure: bool = True
    # Trust X-Real-IP from the reverse proxy (only when listening behind nginx).
    trust_proxy_headers: bool = False
    # Roadmap H3 (SPEC §7.8): fetch aircraft from adsb.lol for the cameras that need them.
    adsb: bool = True
    # Roadmap H6 (SPEC §7.9): CelesTrak's orbital elements, to name satellites among meteors.
    satellites: bool = True
    # Roadmap H8 (SPEC §7.10): flashes from Meteosat's Lightning Imager, from the EUMETSAT
    # Data Store (a free account; key and secret of its API). Off while either is empty.
    eumetsat_key: str = ""
    eumetsat_secret: str = ""
    # Reporter sign-up on /melden/konto (app "Himmelshelligkeit melden"), behind Cloudflare
    # Turnstile. Off while either is empty (Benjamin enters them, 2026-10-28).
    turnstile_site_key: str = ""
    turnstile_secret: str = ""

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("database_url")
    @classmethod
    def _plain_postgres_url(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("database_url must start with postgresql://")
        return v

    @property
    def reporter_signup(self) -> bool:
        return bool(self.turnstile_site_key and self.turnstile_secret)

    @property
    def lightning_li(self) -> bool:
        return bool(self.eumetsat_key and self.eumetsat_secret)

    @property
    def billing(self) -> bool:
        return all(
            (
                self.paddle_api_key,
                self.paddle_client_token,
                self.paddle_webhook_secret,
                self.paddle_price_month,
                self.paddle_price_year,
            )
        )

    @property
    def async_database_url(self) -> str:
        """URL for SQLAlchemy with asyncpg."""
        return "postgresql+asyncpg://" + self.database_url.split("://", 1)[1]

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_host)

    @property
    def admins(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.admin_emails.split(",") if e.strip())

    @property
    def deploy_check_names(self) -> tuple[str, ...]:
        return tuple(c.strip() for c in self.deploy_checks.split(",") if c.strip())

    @property
    def push_enabled(self) -> bool:
        return self.fcm_service_account_file is not None

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def key_dir(self) -> Path:
        """The hub's Ed25519 keys for local logins (SPEC §6.2 step 6, §7.4)."""
        return self.data_dir / "keys"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment

"""Database models. Schema changes go through Alembic (``server/migrations``)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from allskyhub_server.ids import uuid7

# Deterministic constraint names so Alembic migrations stay stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _tz() -> DateTime:
    return DateTime(timezone=True)


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Stored lower-cased (see auth.accounts.normalize_email).
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class WebSession(Base):
    """Server-side session; the cookie carries a token whose SHA-256 is stored here."""

    __tablename__ = "web_session"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    last_seen_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())


class RateLimit(Base):
    """Fixed-window counters (auth.ratelimit)."""

    __tablename__ = "rate_limit"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[dt.datetime] = mapped_column(_tz(), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class Device(Base):
    """A camera (SPEC §6.2). Known from its first registration; paired while ``owner_id`` is set."""

    __tablename__ = "device"

    # SPEC §6.2: derived from the public key, 26 base32 characters.
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    profile: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[str] = mapped_column(String(64))
    # Unpairing bumps it: tokens of an earlier pairing stop working at once.
    auth_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    paired_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Latest `status` and `frame` bodies (SPEC §6.3), as sent.
    last_status: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_frame: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # When the latest full image and thumbnail were stored (SPEC §6.5).
    latest_image_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    latest_thumb_at: Mapped[dt.datetime | None] = mapped_column(_tz())


class DeviceNonce(Base):
    """Single-use challenge (SPEC §6.2), stored as SHA-256."""

    __tablename__ = "device_nonce"

    nonce_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(26))
    expires_at: Mapped[dt.datetime] = mapped_column(_tz(), index=True)


class PairingCode(Base):
    """SPEC §6.2 step 2: one open code per device at a time."""

    __tablename__ = "pairing_code"
    __table_args__ = (
        # Open codes are unique; claimed ones may repeat later.
        Index(
            "uq_pairing_code_open_code",
            "code",
            unique=True,
            postgresql_where=text("claimed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(6))
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())
    claimed_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class DeviceToken(Base):
    """Bearer token of a paired device (SPEC §6.2 step 5), stored as SHA-256."""

    __tablename__ = "device_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    auth_generation: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())


class AppToken(Base):
    """Bearer token of a signed-in app (M3), stored as SHA-256."""

    __tablename__ = "app_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    # Shown in a future "signed-in devices" list, e.g. "Pixel 8".
    label: Mapped[str] = mapped_column(String(100), default="", server_default="")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    last_used_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())


class Invitation(Base):
    """Accounts exist only by invitation; the link carries a token stored here as SHA-256."""

    __tablename__ = "invitation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())
    used_at: Mapped[dt.datetime | None] = mapped_column(_tz())


class Frame(Base):
    """A frame the hub asked the device for (SPEC §4.4 metadata) and what it holds of it."""

    __tablename__ = "frame"
    __table_args__ = (UniqueConstraint("device_id", "night_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    night_id: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128))
    captured_at: Mapped[dt.datetime] = mapped_column(_tz())
    mode: Mapped[str] = mapped_column(String(8))
    exposure_us: Mapped[int] = mapped_column(BigInteger)
    gain: Mapped[float] = mapped_column(Float)
    mean: Mapped[float] = mapped_column(Float)
    sun_elevation: Mapped[float] = mapped_column(Float)
    has_full: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    has_thumb: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Retention counts from here, not from the device clock (SPEC §4.4: it may be wrong).
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now(), index=True)


class Product(Base):
    """A night product the device announced (SPEC §5.2, §6.3) and what the hub holds of it."""

    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("device_id", "night_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    night_id: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(BigInteger)
    duration_s: Mapped[float | None] = mapped_column(Float)
    # The device has a thumbnail to offer (always JPEG).
    thumbnail: Mapped[bool] = mapped_column(Boolean)
    has_full: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    has_thumb: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    full_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Retention counts from the first announcement (privacy policy).
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now(), index=True)

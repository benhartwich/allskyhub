"""Database models. Schema changes go through Alembic (``server/migrations``)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    func,
)
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

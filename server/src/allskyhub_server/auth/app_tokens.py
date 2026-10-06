"""Bearer tokens for the app (M3): 90 days, extended while in use, revocable."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth.tokens import hash_token, new_token
from allskyhub_server.models import AppToken, User

LIFETIME = dt.timedelta(days=90)
TOUCH_INTERVAL = dt.timedelta(hours=1)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def issue(db: AsyncSession, user_id: uuid.UUID, label: str) -> str:
    """The caller commits."""
    token = new_token()
    db.add(
        AppToken(
            token_hash=hash_token(token),
            user_id=user_id,
            label=label.strip()[:100],
            expires_at=_now() + LIFETIME,
        )
    )
    await db.flush()
    return token


async def user_for(db: AsyncSession, token: str) -> User | None:
    now = _now()
    row = (
        await db.execute(
            select(AppToken, User)
            .join(User, User.id == AppToken.user_id)
            .where(AppToken.token_hash == hash_token(token), AppToken.expires_at > now)
        )
    ).one_or_none()
    if row is None:
        return None
    app_token, user = row
    if not user.is_active:
        return None
    if now - app_token.last_used_at > TOUCH_INTERVAL:
        await db.execute(
            update(AppToken)
            .where(AppToken.token_hash == app_token.token_hash)
            .values(last_used_at=now, expires_at=now + LIFETIME)
        )
        await db.commit()
    return user


async def revoke(db: AsyncSession, token: str) -> None:
    await db.execute(delete(AppToken).where(AppToken.token_hash == hash_token(token)))
    await db.commit()

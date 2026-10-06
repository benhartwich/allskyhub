"""Invitations: the only way to get an account.

The operator creates one on the command line (``allskyhub-server invite``) and passes the link
on. The link is valid for a few days and only once; the account gets the invited address.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth.accounts import AccountError, create_user, normalize_email
from allskyhub_server.auth.tokens import hash_token, new_token
from allskyhub_server.models import Invitation, User

DEFAULT_TTL = dt.timedelta(days=7)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def create(db: AsyncSession, email: str, ttl: dt.timedelta = DEFAULT_TTL) -> str:
    """Return the token for a new invitation; earlier open ones for that address stop working.
    The caller commits."""
    email = normalize_email(email)
    if "@" not in email or len(email) > 254:
        raise AccountError("Keine gültige E-Mail-Adresse.")
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        raise AccountError("Für diese E-Mail-Adresse gibt es schon ein Konto.")
    await db.execute(
        update(Invitation)
        .where(Invitation.email == email, Invitation.used_at.is_(None))
        .values(expires_at=_now())
    )
    token = new_token()
    db.add(Invitation(token_hash=hash_token(token), email=email, expires_at=_now() + ttl))
    await db.flush()
    return token


async def open_invitation(db: AsyncSession, token: str) -> Invitation | None:
    """Unknown, expired and used invitations look the same."""
    if not token:
        return None
    return await db.scalar(
        select(Invitation).where(
            Invitation.token_hash == hash_token(token),
            Invitation.used_at.is_(None),
            Invitation.expires_at > _now(),
        )
    )


async def accept(db: AsyncSession, token: str, password: str) -> User:
    """Create the invited account. The caller commits."""
    invitation = await db.scalar(
        select(Invitation)
        .where(
            Invitation.token_hash == hash_token(token),
            Invitation.used_at.is_(None),
            Invitation.expires_at > _now(),
        )
        .with_for_update()
    )
    if invitation is None:
        raise AccountError("Die Einladung ist ungültig oder abgelaufen.")
    user = await create_user(db, invitation.email, password)
    invitation.used_at = _now()
    await db.flush()
    return user

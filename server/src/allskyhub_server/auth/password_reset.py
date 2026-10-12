"""'Passwort vergessen' (roadmap #15): a single-use link by e-mail, valid one hour.

Asking never reveals whether an address has an account: the page always says the same.
Setting the new password signs out every web session and app of the account.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth.accounts import MIN_PASSWORD_LENGTH, AccountError, normalize_email
from allskyhub_server.auth.passwords import hash_secret_async
from allskyhub_server.models import AppToken, PasswordReset, User, WebSession

TTL = dt.timedelta(hours=1)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def create(db: AsyncSession, email: str) -> tuple[User, str] | None:
    """A new link for the account, or None when there is none. The caller commits."""
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        return None
    token = secrets.token_urlsafe(32)
    db.add(PasswordReset(token_hash=_hash(token), user_id=user.id, expires_at=_now() + TTL))
    return user, token


async def open_reset(db: AsyncSession, token: str) -> PasswordReset | None:
    if not token or len(token) > 100:
        return None
    reset = await db.get(PasswordReset, _hash(token))
    if reset is None or reset.used_at is not None or reset.expires_at <= _now():
        return None
    return reset


async def use(db: AsyncSession, token: str, password: str) -> User:
    """Set the new password; raises AccountError. The caller commits."""
    reset = await open_reset(db, token)
    if reset is None:
        raise AccountError("Der Link ist abgelaufen oder wurde schon benutzt.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Das Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.")
    user = await db.get(User, reset.user_id)
    if user is None:
        raise AccountError("Der Link ist abgelaufen oder wurde schon benutzt.")
    user.password_hash = await hash_secret_async(password)
    reset.used_at = _now()
    # Other open links of this account become useless too.
    await db.execute(
        delete(PasswordReset).where(
            PasswordReset.user_id == user.id, PasswordReset.token_hash != reset.token_hash
        )
    )
    await db.execute(delete(WebSession).where(WebSession.user_id == user.id))
    await db.execute(delete(AppToken).where(AppToken.user_id == user.id))
    await db.flush()
    return user


async def purge(db: AsyncSession, now: dt.datetime) -> None:
    """Expired and used links go; the caller commits."""
    await db.execute(delete(PasswordReset).where(PasswordReset.expires_at < now))

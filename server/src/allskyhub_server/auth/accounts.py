"""User accounts: sign-up and password login."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth.passwords import hash_secret_async, verify_secret_async
from allskyhub_server.models import User

MIN_PASSWORD_LENGTH = 10


class AccountError(Exception):
    """A user-facing reason (German UI text)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def create_user(db: AsyncSession, email: str, password: str) -> User:
    """Create an account. The caller commits."""
    email = normalize_email(email)
    if "@" not in email or len(email) > 254:
        raise AccountError("Bitte eine gültige E-Mail-Adresse angeben.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Das Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.")
    user = User(email=email, password_hash=await hash_secret_async(password))
    try:
        async with db.begin_nested():
            db.add(user)
    except IntegrityError:
        raise AccountError("Für diese E-Mail-Adresse gibt es schon ein Konto.") from None
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    """Unknown account, inactive account and wrong password are indistinguishable."""
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    ok = await verify_secret_async(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        return None
    return user


async def change_password(
    db: AsyncSession,
    user: User,
    current: str,
    new: str,
    *,
    keep_session: uuid.UUID | None = None,
    keep_app_token: bytes | None = None,
) -> None:
    """Self-service (roadmap #10): needs the current password; signs out every other web
    session and app. The caller commits."""
    from allskyhub_server.auth.passwords import hash_secret_async
    from allskyhub_server.models import AppToken, WebSession

    if not await verify_secret_async(user.password_hash, current):
        raise AccountError("Das aktuelle Passwort stimmt nicht.")
    if len(new) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"Das neue Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.")
    user.password_hash = await hash_secret_async(new)
    sessions = delete(WebSession).where(WebSession.user_id == user.id)
    if keep_session is not None:
        sessions = sessions.where(WebSession.id != keep_session)
    await db.execute(sessions)
    tokens = delete(AppToken).where(AppToken.user_id == user.id)
    if keep_app_token is not None:
        tokens = tokens.where(AppToken.token_hash != keep_app_token)
    await db.execute(tokens)
    await db.flush()


async def delete_account(db: AsyncSession, user: User) -> list[str]:
    """Delete the account; its cameras are unpaired (archive rows go with them). Returns the
    device ids, so the caller can delete their files and close their connections after the
    commit. The caller commits."""
    from allskyhub_server.devices import pairing
    from allskyhub_server.models import Device, Invitation

    devices = (await db.scalars(select(Device).where(Device.owner_id == user.id))).all()
    for device in devices:
        await pairing.unpair(db, device)
    await db.execute(delete(Invitation).where(Invitation.email == user.email))
    await db.delete(user)
    await db.flush()
    return [d.id for d in devices]

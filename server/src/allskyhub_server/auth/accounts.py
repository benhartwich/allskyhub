"""User accounts: sign-up and password login."""

from __future__ import annotations

from sqlalchemy import select
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

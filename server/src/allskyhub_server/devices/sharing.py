"""Sharing a camera with other accounts (roadmap C10).

The owner adds an existing account by its e-mail address as ``viewer`` or ``admin``.
Accounts exist only by invitation, so the hub reveals nothing about addresses it does
not know beyond "no account with this address" to the camera's owner.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth.accounts import normalize_email
from allskyhub_server.i18n import tr
from allskyhub_server.models import CameraMember, Device, User

ROLES = {"viewer": "ansehen", "admin": "verwalten"}
MAX_MEMBERS = 20


class SharingError(Exception):
    """German message for the page."""


@dataclass(frozen=True)
class Member:
    user_id: uuid.UUID
    email: str
    role: str


async def members(db: AsyncSession, device_id: str) -> list[Member]:
    rows = await db.execute(
        select(User.id, User.email, CameraMember.role)
        .join(CameraMember, CameraMember.user_id == User.id)
        .where(CameraMember.device_id == device_id)
        .order_by(User.email)
    )
    return [Member(uid, email, role) for uid, email, role in rows.all()]


async def add(db: AsyncSession, device: Device, email: str, role: str) -> None:
    """Add or change a member; the caller commits."""
    if role not in ROLES:
        raise SharingError(tr("Unbekannte Rolle.", "Unknown role."))
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise SharingError(
            tr("Es gibt kein Konto mit dieser Adresse.", "There is no account with this address.")
        )
    if user.id == device.owner_id:
        raise SharingError(tr("Das ist dein eigenes Konto.", "This is your own account."))
    count = await db.scalar(
        select(func.count()).select_from(CameraMember).where(CameraMember.device_id == device.id)
    )
    if (count or 0) >= MAX_MEMBERS:
        raise SharingError(
            tr(
                f"Höchstens {MAX_MEMBERS} Personen pro Kamera.",
                f"At most {MAX_MEMBERS} people per camera.",
            )
        )
    await db.execute(
        pg_insert(CameraMember)
        .values(device_id=device.id, user_id=user.id, role=role)
        .on_conflict_do_update(index_elements=["device_id", "user_id"], set_={"role": role})
    )


async def remove(db: AsyncSession, device_id: str, user_id: uuid.UUID) -> None:
    """Owner removes a member, or a member leaves; the caller commits."""
    await db.execute(
        delete(CameraMember).where(
            CameraMember.device_id == device_id, CameraMember.user_id == user_id
        )
    )

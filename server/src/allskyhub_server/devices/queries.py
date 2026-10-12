"""Devices of an account, shared by the web UI and the app API.

Besides its owner a camera can be shared (roadmap C10): an ``admin`` may also change its
settings and mark detections, a ``viewer`` only looks. Pairing, removing, the public page
and the local login stay with the owner.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.models import CameraMember, Device

Role = Literal["owner", "admin", "viewer"]
_RANK: dict[str, int] = {"viewer": 1, "admin": 2, "owner": 3}


async def owned(db: AsyncSession, user_id: uuid.UUID) -> Sequence[Device]:
    return (
        await db.scalars(
            select(Device).where(Device.owner_id == user_id).order_by(Device.paired_at)
        )
    ).all()


async def owned_one(db: AsyncSession, user_id: uuid.UUID, device_id: str) -> Device | None:
    """None for unknown devices and devices of other accounts alike."""
    device = await db.get(Device, device_id)
    if device is None or device.owner_id != user_id:
        return None
    return device


async def role_of(db: AsyncSession, user_id: uuid.UUID, device: Device) -> Role | None:
    if device.owner_id is None:
        return None
    if device.owner_id == user_id:
        return "owner"
    role = await db.scalar(
        select(CameraMember.role).where(
            CameraMember.device_id == device.id, CameraMember.user_id == user_id
        )
    )
    return role if role in ("admin", "viewer") else None  # type: ignore[return-value]


async def visible_one(
    db: AsyncSession, user_id: uuid.UUID, device_id: str, need: Role = "viewer"
) -> tuple[Device, Role] | None:
    """The device and the user's role, if it is at least ``need``; else None (404)."""
    device = await db.get(Device, device_id)
    if device is None:
        return None
    role = await role_of(db, user_id, device)
    if role is None or _RANK[role] < _RANK[need]:
        return None
    return device, role


async def visible(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[Device, Role]]:
    """Own cameras first, then the shared ones."""
    own: list[tuple[Device, Role]] = [(d, "owner") for d in await owned(db, user_id)]
    rows = await db.execute(
        select(Device, CameraMember.role)
        .join(CameraMember, CameraMember.device_id == Device.id)
        .where(CameraMember.user_id == user_id, Device.owner_id.is_not(None))
        .order_by(Device.name)
    )
    shared: list[tuple[Device, Role]] = [
        (d, "admin" if r == "admin" else "viewer") for d, r in rows.all()
    ]
    return own + shared

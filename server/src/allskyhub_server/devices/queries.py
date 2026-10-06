"""Devices of an account, shared by the web UI and the app API."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.models import Device


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

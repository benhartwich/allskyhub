"""Offline code for the camera's local UI (SPEC §7.4 B, §6.5 `local_access`).

The hub makes a 12-character code, shows it once, and keeps only an Argon2id verifier
until the camera has acknowledged it. The code is unrelated to the account password, so a
stolen camera allows no offline attack on that.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import secrets

from argon2 import PasswordHasher
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import (
    ARGON2_MIN_MEMORY_KIB,
    ARGON2_MIN_TIME,
    OFFLINE_CODE_LENGTH,
    PAIRING_CODE_ALPHABET,
    Ack,
    Command,
    CommandName,
    LocalAccessArgs,
)
from allskyhub_server.devices.connections import ConnectionRegistry, DeviceOfflineError
from allskyhub_server.models import Device

log = logging.getLogger(__name__)

REPLY_TIMEOUT_S = 10.0
# Exactly the minimum the camera accepts: it verifies on a Raspberry Pi.
_hasher = PasswordHasher(
    time_cost=ARGON2_MIN_TIME, memory_cost=ARGON2_MIN_MEMORY_KIB, parallelism=1
)


def new_code() -> str:
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(OFFLINE_CODE_LENGTH))


def shown(code: str) -> str:
    """XXXX-XXXX-XXXX."""
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


async def create(db: AsyncSession, device: Device) -> str:
    """A new code replaces the old one. The caller commits, then calls ``deliver``."""
    code = new_code()
    device.local_access_verifier = await asyncio.to_thread(_hasher.hash, code)
    device.local_access_pending = True
    device.local_access_set_at = dt.datetime.now(dt.UTC)
    return code


def remove(device: Device) -> None:
    """The camera is told to forget the code. The caller commits, then calls ``deliver``."""
    device.local_access_verifier = None
    device.local_access_pending = True
    device.local_access_set_at = None


async def deliver(
    maker: async_sessionmaker[AsyncSession], registry: ConnectionRegistry, device_id: str
) -> bool:
    """Send the pending verifier (or its removal) if the camera is online; True once it has
    acknowledged. Called after a change and after every `hello`."""
    async with maker() as db:
        device = await db.get(Device, device_id)
        if device is None or not device.local_access_pending or device.owner_id is None:
            return False
        verifier = device.local_access_verifier
    args = LocalAccessArgs(verifier=verifier)
    cmd = Command(name=CommandName.LOCAL_ACCESS, args=args.model_dump(mode="json"))
    try:
        reply = await registry.command(device_id, cmd, REPLY_TIMEOUT_S)
    except (DeviceOfflineError, TimeoutError):
        return False
    if not isinstance(reply, Ack):
        log.warning("local_access refused", extra={"device_id": device_id, "code": reply.code})
        return False
    async with maker() as db:
        # Only clear the flag if nothing changed meanwhile.
        await db.execute(
            update(Device)
            .where(
                Device.id == device_id, Device.local_access_verifier.is_not_distinct_from(verifier)
            )
            .values(local_access_pending=False)
        )
        await db.commit()
    return True

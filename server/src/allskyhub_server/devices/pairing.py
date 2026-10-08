"""Device identity, pairing and tokens (SPEC §6.2).

* ``new_nonce``: a single-use challenge for one device id.
* ``register``: proves possession of the key; returns the open pairing code until a user
  claims it, then ``paired``.
* ``claim``: a signed-in user binds the device with the code.
* ``issue_token``: a paired device gets a bearer token; ``device_for_token`` checks it.
* ``unpair``: the owner removes the device; its tokens stop working at once.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_protocol import (
    PAIRING_CODE_ALPHABET,
    PAIRING_CODE_LENGTH,
    Purpose,
    b64url_decode,
    device_id_from_public_key,
    normalize_pairing_code,
    signing_payload,
)
from allskyhub_server.auth.tokens import hash_token, new_token
from allskyhub_server.models import (
    Device,
    DeviceNonce,
    DeviceToken,
    EventRecord,
    Frame,
    PairingCode,
    Product,
    SkySample,
)

NONCE_TTL = dt.timedelta(minutes=5)
CODE_TTL = dt.timedelta(minutes=15)
TOKEN_TTL = dt.timedelta(hours=1)
_CODE_ATTEMPTS = 20


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class PairingError(Exception):
    """Base of the errors below; the message is for logs and API details, never secret."""


class InvalidProofError(PairingError):
    """Unknown or expired nonce, malformed key or a signature that does not verify."""


class NotPairedError(PairingError):
    """A token was asked for by a device that has no owner."""


class CodeInvalidError(PairingError):
    """Unknown, expired and used codes look the same (SPEC §6.2 step 3)."""


class PairedElsewhereError(PairingError):
    """The device already belongs to another account."""


async def new_nonce(db: AsyncSession, device_id: str) -> str:
    """SPEC §6.2: a challenge valid for 5 minutes. The caller commits."""
    await db.execute(delete(DeviceNonce).where(DeviceNonce.expires_at <= _now()))
    nonce = secrets.token_urlsafe(24)
    db.add(
        DeviceNonce(
            nonce_hash=hash_token(nonce), device_id=device_id, expires_at=_now() + NONCE_TTL
        )
    )
    await db.flush()
    return nonce


async def _consume_nonce(db: AsyncSession, device_id: str, nonce: str) -> None:
    row = await db.scalar(
        delete(DeviceNonce)
        .where(
            DeviceNonce.nonce_hash == hash_token(nonce),
            DeviceNonce.device_id == device_id,
            DeviceNonce.expires_at > _now(),
        )
        .returning(DeviceNonce.nonce_hash)
    )
    if row is None:
        raise InvalidProofError("unknown or expired nonce")


def _verify(public_key: bytes, purpose: Purpose, device_id: str, nonce: str, sig: str) -> None:
    try:
        key = Ed25519PublicKey.from_public_bytes(public_key)
        key.verify(b64url_decode(sig), signing_payload(purpose, device_id, nonce))
    except (InvalidSignature, ValueError):
        raise InvalidProofError("signature does not verify") from None


@dataclass(frozen=True)
class Registration:
    device_id: str
    paired: bool
    code: str | None = None
    expires_in: int | None = None


async def register(
    db: AsyncSession,
    *,
    public_key_b64: str,
    profile: str,
    agent_version: str,
    nonce: str,
    signature: str,
) -> Registration:
    """SPEC §6.2 step 2. The caller commits (also on InvalidProofError: the nonce is spent)."""
    try:
        public_key = b64url_decode(public_key_b64)
        device_id = device_id_from_public_key(public_key)
    except ValueError:
        raise InvalidProofError("malformed public key") from None
    await _consume_nonce(db, device_id, nonce)
    _verify(public_key, Purpose.REGISTER, device_id, nonce, signature)

    device = await db.get(Device, device_id, with_for_update=True)
    if device is None:
        device = Device(
            id=device_id, public_key=public_key, profile=profile, agent_version=agent_version
        )
        db.add(device)
    device.profile = profile
    device.agent_version = agent_version
    await db.flush()
    if device.owner_id is not None:
        return Registration(device_id, paired=True)

    now = _now()
    open_code = await db.scalar(
        select(PairingCode).where(
            PairingCode.device_id == device_id,
            PairingCode.claimed_at.is_(None),
            PairingCode.expires_at > now,
        )
    )
    if open_code is None:
        open_code = await _new_code(db, device_id)
    return Registration(
        device_id,
        paired=False,
        code=open_code.code,
        expires_in=max(1, int((open_code.expires_at - now).total_seconds())),
    )


async def _new_code(db: AsyncSession, device_id: str) -> PairingCode:
    # Expired open codes would block their numbers (unique index on open codes).
    await db.execute(
        delete(PairingCode).where(
            PairingCode.claimed_at.is_(None), PairingCode.expires_at <= _now()
        )
    )
    for _ in range(_CODE_ATTEMPTS):
        code = "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))
        row = PairingCode(device_id=device_id, code=code, expires_at=_now() + CODE_TTL)
        try:
            async with db.begin_nested():
                db.add(row)
        except IntegrityError:
            continue
        return row
    raise PairingError("no free pairing code")  # pragma: no cover - 31^6 codes


async def claim(db: AsyncSession, user_id: uuid.UUID, code: str, name: str) -> Device:
    """SPEC §6.2 step 3: bind the device to ``user_id``. The caller commits."""
    pairing = await db.scalar(
        select(PairingCode)
        .where(
            PairingCode.code == normalize_pairing_code(code),
            PairingCode.claimed_at.is_(None),
            PairingCode.expires_at > _now(),
        )
        .with_for_update()
    )
    if pairing is None:
        raise CodeInvalidError("Der Code ist ungültig oder abgelaufen.")
    device = await db.get(Device, pairing.device_id, with_for_update=True)
    if device is None:  # pragma: no cover - the foreign key guarantees it
        raise CodeInvalidError("Der Code ist ungültig oder abgelaufen.")
    if device.owner_id is not None and device.owner_id != user_id:
        raise PairedElsewhereError("Diese Kamera ist mit einem anderen Konto gekoppelt.")
    now = _now()
    pairing.claimed_at = now
    pairing.claimed_by = user_id
    device.owner_id = user_id
    device.name = name.strip()[:100] or "Allsky-Kamera"
    device.paired_at = now
    await db.flush()
    return device


async def unpair(db: AsyncSession, device: Device) -> None:
    """The owner removes the device (SPEC §6.2 step 4). The caller commits."""
    device.owner_id = None
    device.paired_at = None
    device.auth_generation += 1
    await db.execute(delete(DeviceToken).where(DeviceToken.device_id == device.id))
    # The archive belongs to the pairing: a later owner must not see it (privacy policy).
    await db.execute(delete(Frame).where(Frame.device_id == device.id))
    await db.execute(delete(Product).where(Product.device_id == device.id))
    await db.execute(delete(EventRecord).where(EventRecord.device_id == device.id))
    await db.execute(delete(SkySample).where(SkySample.device_id == device.id))
    device.latest_image_at = None
    device.latest_thumb_at = None
    device.last_frame = None
    device.last_status = None
    device.public_slug = None  # a new owner decides again
    await db.flush()


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_in: int


async def issue_token(db: AsyncSession, device_id: str, nonce: str, signature: str) -> IssuedToken:
    """SPEC §6.2 step 5. The caller commits (also on errors: the nonce is spent)."""
    await _consume_nonce(db, device_id, nonce)
    device = await db.get(Device, device_id)
    if device is None:
        raise InvalidProofError("unknown device")
    _verify(device.public_key, Purpose.TOKEN, device_id, nonce, signature)
    if device.owner_id is None:
        raise NotPairedError("device is not paired")
    await db.execute(delete(DeviceToken).where(DeviceToken.expires_at <= _now()))
    token = new_token()
    db.add(
        DeviceToken(
            token_hash=hash_token(token),
            device_id=device_id,
            auth_generation=device.auth_generation,
            expires_at=_now() + TOKEN_TTL,
        )
    )
    await db.flush()
    return IssuedToken(token, int(TOKEN_TTL.total_seconds()))


@dataclass(frozen=True)
class TokenInfo:
    device_id: str
    expires_at: dt.datetime


async def device_for_token(db: AsyncSession, token: str) -> TokenInfo | None:
    """A valid token of a currently paired device, or None."""
    row = (
        await db.execute(
            select(DeviceToken.device_id, DeviceToken.expires_at)
            .join(Device, Device.id == DeviceToken.device_id)
            .where(
                DeviceToken.token_hash == hash_token(token),
                DeviceToken.expires_at > _now(),
                DeviceToken.auth_generation == Device.auth_generation,
                Device.owner_id.is_not(None),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    device_id, expires_at = row
    return TokenInfo(device_id, expires_at)

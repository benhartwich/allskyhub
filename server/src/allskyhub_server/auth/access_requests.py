"""Access requests: there is no open sign-up; people ask on /zugang and an admin decides.

Inviting sends the usual invitation link (auth/invitations.py) by mail; declining only
marks the request. The page answers the same whether the address already has an account
or an open request, so it cannot be used to look addresses up. Requests are deleted
``KEEP`` after the decision and ``KEEP_OPEN`` after they came in (privacy policy).
"""

from __future__ import annotations

import datetime as dt
import uuid
from urllib.parse import urlencode

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.auth import invitations
from allskyhub_server.auth.accounts import AccountError, normalize_email
from allskyhub_server.mail.send import Mail
from allskyhub_server.models import AccessRequest, User

STAGES = {
    "camera": "Ich habe schon eine Allsky-Kamera",
    "building": "Ich baue gerade eine",
    "planning": "Ich plane eine",
    "curious": "Ich schaue mich erst um",
}
INVITE_TTL = dt.timedelta(days=14)
KEEP = dt.timedelta(days=30)
KEEP_OPEN = dt.timedelta(days=180)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def create(
    db: AsyncSession, email: str, name: str, place: str, stage: str, message: str
) -> AccessRequest | None:
    """The new request, or None when the address has an account or an open request already
    (the caller answers the same either way). The caller commits."""
    email = normalize_email(email)
    if "@" not in email or len(email) > 254:
        raise AccountError("Keine gültige E-Mail-Adresse.")
    if not name.strip():
        raise AccountError("Bitte gib deinen Namen an.")
    if stage not in STAGES:
        raise AccountError("Bitte wähle aus, wie weit du bist.")
    if await db.scalar(select(User.id).where(User.email == email)) is not None:
        return None
    open_one = await db.scalar(
        select(AccessRequest.id).where(
            AccessRequest.email == email, AccessRequest.decision.is_(None)
        )
    )
    if open_one is not None:
        return None
    request = AccessRequest(
        email=email,
        name=name.strip()[:100],
        place=place.strip()[:100],
        stage=stage,
        message=message.strip()[:1000],
    )
    db.add(request)
    await db.flush()
    return request


async def open_requests(db: AsyncSession) -> list[AccessRequest]:
    return list(
        await db.scalars(
            select(AccessRequest)
            .where(AccessRequest.decision.is_(None))
            .order_by(AccessRequest.created_at)
        )
    )


async def _open(db: AsyncSession, request_id: uuid.UUID) -> AccessRequest:
    request = await db.get(AccessRequest, request_id, with_for_update=True)
    if request is None or request.decision is not None:
        raise AccountError("Diese Anfrage ist schon erledigt.")
    return request


async def invite(
    db: AsyncSession, request_id: uuid.UUID, base_url: str
) -> tuple[AccessRequest, str, Mail]:
    """Mark the request invited; return the invitation link and the mail with it. The
    caller commits and sends it."""
    request = await _open(db, request_id)
    token = await invitations.create(db, request.email, INVITE_TTL)
    request.decision = "invited"
    request.decided_at = _now()
    link = f"{base_url.rstrip('/')}/invite?{urlencode({'token': token})}"
    start = f"{base_url.rstrip('/')}/start"
    mail = Mail(
        to=request.email,
        subject="allskyhub: dein Zugang",
        body=(
            f"Hallo {request.name},\n\n"
            "danke für dein Interesse an allskyhub. Mit diesem Link legst du dein Passwort "
            f"fest (14 Tage gültig, nur einmal):\n\n{link}\n\n"
            "Danach führen dich die ersten Schritte durch Hardware, Zusammenbau und "
            f"Einrichtung:\n\n{start}\n\n"
            "Klare Nächte!\nallskyhub\n"
        ),
    )
    return request, link, mail


async def decline(db: AsyncSession, request_id: uuid.UUID) -> AccessRequest:
    """The caller commits."""
    request = await _open(db, request_id)
    request.decision = "declined"
    request.decided_at = _now()
    return request


async def purge(db: AsyncSession, now: dt.datetime) -> None:
    await db.execute(
        delete(AccessRequest).where(
            AccessRequest.decided_at.is_not(None), AccessRequest.decided_at < now - KEEP
        )
    )
    await db.execute(delete(AccessRequest).where(AccessRequest.created_at < now - KEEP_OPEN))

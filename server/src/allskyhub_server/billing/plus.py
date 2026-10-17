"""What Plus a camera has, and the free trial month (roadmap N7).

A camera has Plus while ``Device.plus_until`` lies ahead or an admin gifted it
(``plus_gift``). ``plus_until`` is the later of the trial's end and the end of what its
Paddle subscriptions paid for; :func:`refresh` keeps it so. Retention (maintenance.py)
adds a grace period after it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allskyhub_server.models import Device, Subscription, User

TRIAL = dt.timedelta(days=30)
# Paddle renews at the end of the period; its webhook may come a little later.
RENEWAL_SLACK = dt.timedelta(days=1)
PRICE_MONTH = "4,99 €"
PRICE_YEAR = "49 €"
# Subscriptions that still pay for the current period.
RUNNING = ("trialing", "active", "past_due")


# What the camera page says after a Plus action (``?plus=<code>``).
MESSAGES = {
    "danke": "Danke! Plus ist in wenigen Sekunden aktiv, sobald Paddle die Zahlung bestätigt.",
    "trial_used": "Den kostenlosen Probemonat hat dein Konto schon genutzt.",
    "has_plus": "Diese Kamera hat Plus schon.",
}


class PlusError(Exception):
    """A Plus action that is not possible; ``code`` is a key of ``MESSAGES``."""

    def __init__(self, code: str) -> None:
        super().__init__(MESSAGES[code])
        self.code = code


@dataclass(frozen=True)
class PlusState:
    kind: Literal["gift", "paid", "trial", "ended", "none"]
    until: dt.datetime | None = None
    # A running subscription: it renews (or ends at ``until`` when cancelled).
    renews: bool = False
    past_due: bool = False
    subscription_id: str | None = None

    @property
    def active(self) -> bool:
        return self.kind in ("gift", "paid", "trial")


def paid_until(sub: Subscription) -> dt.datetime | None:
    """The end of what a subscription has paid for (None: nothing)."""
    if sub.status in RUNNING and sub.period_end is not None:
        return sub.period_end + RENEWAL_SLACK
    return sub.ended_at


async def running_subscription(db: AsyncSession, device_id: str) -> Subscription | None:
    return await db.scalar(
        select(Subscription)
        .where(Subscription.device_id == device_id, Subscription.status.in_(RUNNING))
        .order_by(Subscription.event_at.desc())
        .limit(1)
    )


async def refresh(db: AsyncSession, device: Device) -> None:
    """Set ``plus_until`` from the trial and the camera's subscriptions. The caller commits."""
    ends = [device.plus_trial_until] if device.plus_trial_until else []
    for sub in await db.scalars(select(Subscription).where(Subscription.device_id == device.id)):
        end = paid_until(sub)
        if end is not None:
            ends.append(end)
    device.plus_until = max(ends) if ends else None


async def state(db: AsyncSession, device: Device, now: dt.datetime) -> PlusState:
    if device.plus_gift:
        return PlusState("gift")
    sub = await running_subscription(db, device.id)
    if sub is not None:
        return PlusState(
            "paid",
            until=sub.cancel_at or sub.period_end,
            renews=sub.cancel_at is None,
            past_due=sub.status == "past_due",
            subscription_id=sub.id,
        )
    if device.plus_until is not None and device.plus_until > now:
        trial = device.plus_trial_until is not None and device.plus_until == device.plus_trial_until
        return PlusState("trial" if trial else "paid", until=device.plus_until)
    if device.plus_until is not None:
        return PlusState("ended", until=device.plus_until)
    return PlusState("none")


async def start_trial(db: AsyncSession, user: User, device: Device, now: dt.datetime) -> None:
    """The free month, once per account and only for a camera without Plus. Commits."""
    if user.plus_trial_at is not None:
        raise PlusError("trial_used")
    if (await state(db, device, now)).active:
        raise PlusError("has_plus")
    user.plus_trial_at = now
    device.plus_trial_until = now + TRIAL
    await refresh(db, device)
    await db.commit()

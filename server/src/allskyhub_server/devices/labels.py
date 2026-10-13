"""Labels of detections, kept in step with the camera (SPEC §6.4 Labels, §6.5).

A label is `confirmed` or `false_positive` with a reason; the newer `at` wins wherever
it was set. Labels set on the hub go to the camera with `label_event`, again after every
`hello` until it acknowledges; an older camera answers `unsupported` and the hub keeps the
label for itself. A false positive is never deleted and counts nowhere, so the night's
numbers are computed again whenever a label changes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Ack, Command, CommandName, EventLabel
from allskyhub_server.devices import stats
from allskyhub_server.devices.connections import ConnectionRegistry, DeviceOfflineError
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import EventRecord, Frame, Product

log = logging.getLogger(__name__)

LABELS = ("confirmed", "false_positive")
REASONS = {
    "aircraft": "Flugzeug",
    "satellite": "Satellit",
    "artifact": "Bildfehler",
    "other": "Sonstiges",
}
REPLY_TIMEOUT_S = 10.0
# Codes after which the hub stops sending a label: the camera does not know the event
# (any more), cannot store labels at all (older agent), or finds the label invalid.
FINAL_ERRORS = ("not_found", "unsupported", "invalid_args")


async def _event(
    db: AsyncSession, device_id: str, night_id: str, event_id: str
) -> EventRecord | None:
    return await db.scalar(
        select(EventRecord).where(
            EventRecord.device_id == device_id,
            EventRecord.night_id == night_id,
            EventRecord.event_id == event_id,
        )
    )


async def set_on_hub(
    db: AsyncSession,
    device_id: str,
    night_id: str,
    event_id: str,
    label: str | None,
    reason: str | None = None,
) -> EventRecord:
    """The owner's verdict on the hub (None clears it); the camera gets it with
    ``deliver``. The caller commits."""
    if label is not None and label not in LABELS:
        raise HTTPException(400, "Unknown label")
    if reason is not None and (label != "false_positive" or reason not in REASONS):
        raise HTTPException(400, "Unknown reason")
    event = await _event(db, device_id, night_id, event_id)
    if event is None:
        raise HTTPException(404, "Not found")
    event.label = label
    event.label_reason = reason
    event.labelled_at = dt.datetime.now(dt.UTC)
    event.label_auto = False
    event.label_detail = None
    event.label_pending = True
    await stats.refresh_night(db, device_id, night_id)
    await db.flush()
    return event


def stored(event: EventRecord) -> EventLabel | None:
    """The event's label as the protocol has it, None if it never had one."""
    if event.labelled_at is None:
        return None
    return EventLabel(
        night_id=event.night_id,
        event_id=event.event_id,
        label=event.label,  # pyright: ignore[reportArgumentType]
        reason=event.label_reason,  # pyright: ignore[reportArgumentType]
        at=event.labelled_at,
        auto=event.label_auto,
        detail=event.label_detail if event.label_auto else None,
    )


async def set_auto(
    db: AsyncSession, event: EventRecord, reason: str, detail: str, at: dt.datetime
) -> bool:
    """SPEC §6.4, §7.8, §7.9: an automatic false positive where the owner never set a
    label (their removal stays too); it replaces an older automatic one (the camera's trail
    verdict), unless it says the same. The camera gets it with ``deliver``. The caller
    commits."""
    if event.labelled_at is not None and not event.label_auto:
        return False
    if (event.label, event.label_reason, event.label_detail) == (
        "false_positive",
        reason,
        detail[:100],
    ):
        return False
    event.label = "false_positive"
    event.label_reason = reason
    event.labelled_at = at
    event.label_auto = True
    event.label_detail = detail[:100]
    event.label_pending = True
    await stats.refresh_night(db, event.device_id, event.night_id)
    return True


async def from_device(db: AsyncSession, device_id: str, body: EventLabel) -> bool:
    """`event_label` from the camera: taken if it wins over the stored one
    (``EventLabel.replaces``, the same rule on both sides). Commits."""
    event = await _event(db, device_id, body.night_id, body.event_id)
    if event is None:
        return False
    if not body.replaces(stored(event)):
        return False
    event.label = body.label
    event.label_reason = body.reason
    event.labelled_at = body.at
    event.label_auto = body.auto
    event.label_detail = body.detail
    event.label_pending = False
    await stats.refresh_night(db, device_id, body.night_id)
    await db.commit()
    return True


async def deliver(
    maker: async_sessionmaker[AsyncSession], registry: ConnectionRegistry, device_id: str
) -> int:
    """Send every label still pending, oldest first; returns how many the camera took.
    Called after a change and after every `hello`; stops when the camera goes away."""
    async with maker() as db:
        pending = list(
            await db.scalars(
                select(EventRecord)
                .where(EventRecord.device_id == device_id, EventRecord.label_pending.is_(True))
                .order_by(EventRecord.labelled_at)
                .limit(500)
            )
        )
        done = 0
        for event in pending:
            sent_at = event.labelled_at
            args = stored(event)
            if args is None:  # pending without a label: never set that way
                event.label_pending = False
                await db.commit()
                continue
            cmd = Command(
                name=CommandName.LABEL_EVENT,
                args=args.model_dump(mode="json"),
            )
            try:
                reply = await registry.command(device_id, cmd, REPLY_TIMEOUT_S)
            except (DeviceOfflineError, TimeoutError):
                break
            if not isinstance(reply, Ack) and reply.code not in FINAL_ERRORS:
                extra = {"device_id": device_id, "code": reply.code}
                log.warning("label_event refused", extra=extra)
                break
            await db.refresh(event)
            if event.labelled_at == sent_at:  # not changed again meanwhile
                event.label_pending = False
                await db.commit()
            done += isinstance(reply, Ack)
    return done


_tasks: set[asyncio.Task[int]] = set()


def deliver_later(
    maker: async_sessionmaker[AsyncSession], registry: ConnectionRegistry, device_id: str
) -> None:
    """``deliver`` in its own task, so a web or app request never waits for the camera."""
    task = asyncio.create_task(deliver(maker, registry, device_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def delete_night(db: AsyncSession, store: ImageStore, device_id: str, night_id: str) -> None:
    """SPEC §6.3 `night_deleted`: the hub's copies of that night's frames, products and
    events go too; the night's numbers (statistics) stay. Commits."""
    for model in (Frame, Product, EventRecord):
        await db.execute(
            delete(model).where(model.device_id == device_id, model.night_id == night_id)
        )
    await db.commit()
    await asyncio.to_thread(store.delete_night, device_id, night_id)

"""App API, what the web pages around a camera manage: sharing it (roadmap C10), the
public page (roadmap #9, the same model as SPEC §6.8), statistics (C7) and the CSV exports
for science (C11).

Sharing and the public page belong to the owner; statistics and exports are for everyone
the camera is shared with.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from allskyhub_protocol import PublicState, PublicUpdate
from allskyhub_server.api.app import AppUser
from allskyhub_server.devices import public, queries, science, sharing, stats
from allskyhub_server.devices.queries import Role
from allskyhub_server.models import Device, User
from allskyhub_server.web.deps import DbSession

router = APIRouter(prefix="/api/v1")


async def _own(db: DbSession, user: User, device_id: str, need: Role = "viewer") -> Device:
    found = await queries.visible_one(db, user.id, device_id, need)
    if found is None:
        raise HTTPException(404, "Not found")
    return found[0]


class MemberItem(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str


class MemberRequest(BaseModel):
    # Like the rest of the app API: fields a newer app sends are ignored.
    model_config = ConfigDict(extra="ignore")

    email: str = Field(max_length=254)
    role: Literal["viewer", "admin"] = "viewer"


@router.get("/cameras/{device_id}/members")
async def members(db: DbSession, user: AppUser, device_id: str) -> list[MemberItem]:
    device = await _own(db, user, device_id, "owner")
    return [MemberItem(**m.__dict__) for m in await sharing.members(db, device.id)]


@router.post("/cameras/{device_id}/members")
async def share(
    db: DbSession, user: AppUser, device_id: str, body: MemberRequest
) -> list[MemberItem]:
    """Add an existing account or change its role; 400 with a German sentence."""
    device = await _own(db, user, device_id, "owner")
    try:
        await sharing.add(db, device, body.email, body.role)
    except sharing.SharingError as exc:
        raise HTTPException(400, str(exc)) from None
    await db.commit()
    return [MemberItem(**m.__dict__) for m in await sharing.members(db, device.id)]


@router.delete("/cameras/{device_id}/members/{user_id}", status_code=204)
async def unshare(db: DbSession, user: AppUser, device_id: str, user_id: uuid.UUID) -> Response:
    """The owner removes someone, or a member leaves (their own ``user_id``)."""
    device = await _own(db, user, device_id, "viewer" if user_id == user.id else "owner")
    await sharing.remove(db, device.id, user_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/account/me")
async def me(user: AppUser) -> MemberItem:
    """The signed-in account, so a member can leave a camera (``DELETE …/members/{id}``)."""
    return MemberItem(user_id=user.id, email=user.email, role="owner")


@router.get("/cameras/{device_id}/public/profile")
async def public_profile(
    request: Request, db: DbSession, user: AppUser, device_id: str
) -> PublicState:
    device = await _own(db, user, device_id, "owner")
    return public.state(device, request.app.state.settings.base_url)


@router.put("/cameras/{device_id}/public/profile")
async def public_profile_put(
    request: Request, db: DbSession, user: AppUser, device_id: str
) -> PublicState:
    """Like ``PUT /device/v1/public`` (SPEC §6.8): switch the page and/or replace the whole
    profile; 422 with a German sentence."""
    device = await _own(db, user, device_id, "owner")
    try:
        body = PublicUpdate.model_validate_json(await request.body())
    except ValidationError as exc:
        raise HTTPException(422, public.describe_error(exc)) from None
    if body.profile is not None:
        device.public_profile = body.profile.model_dump()
    if body.enabled is True:
        public.enable(device)
    elif body.enabled is False:
        public.disable(device)
    await db.commit()
    return public.state(device, request.app.state.settings.base_url)


class PeriodItem(BaseModel):
    label: str
    nights: int
    clear_nights: int
    clear_hours: float
    best_sqm: float | None
    meteors: int
    aurora: int
    storms: int


class YearStats(BaseModel):
    year: int
    months: list[PeriodItem]
    total: PeriodItem
    # Meteor showers by name, most meteors first.
    showers: dict[str, int]
    # Years with data, newest first.
    years: list[int]


def _period(p: stats.Period) -> PeriodItem:
    return PeriodItem(**p.__dict__)


@router.get("/cameras/{device_id}/stats")
async def statistics(db: DbSession, user: AppUser, device_id: str, year: int = 0) -> YearStats:
    """Roadmap C7: clear nights, sky brightness and detections per month."""
    device = await _own(db, user, device_id)
    this_year = dt.datetime.now(dt.UTC).year
    found = await stats.year(db, device.id, year if 2000 <= year <= this_year + 1 else this_year)
    return YearStats(
        year=found.year,
        months=[_period(m) for m in found.months],
        total=_period(found.total),
        showers=found.showers,
        years=found.years,
    )


class ExportItem(BaseModel):
    name: str
    title: str
    filename: str


@router.get("/cameras/{device_id}/exports")
async def exports(db: DbSession, user: AppUser, device_id: str) -> list[ExportItem]:
    await _own(db, user, device_id)
    return [
        ExportItem(name=name, title=title, filename=filename)
        for name, (title, filename) in science.EXPORTS.items()
    ]


@router.get("/cameras/{device_id}/exports/{name}.csv")
async def export_csv(db: DbSession, user: AppUser, device_id: str, name: str) -> Response:
    """Roadmap C11: the same CSV files as the web export page."""
    device = await _own(db, user, device_id)
    if name not in science.EXPORTS:
        raise HTTPException(404, "Not found")
    body, filename = await science.export(db, device, name)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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
from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import discord, public, queries, science, sharing, stats
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


class AppPublicState(PublicState):
    """SPEC §6.8 plus the hub's own start-page switch (not part of the profile)."""

    on_home: bool = False


class AppPublicUpdate(PublicUpdate):
    on_home: bool | None = None


def _app_state(request: Request, device: Device) -> AppPublicState:
    state = public.state(device, request.app.state.settings.base_url)
    return AppPublicState(**state.model_dump(), on_home=device.public_on_home)


class NetworkState(BaseModel):
    """Audit L2: the camera takes part in the network of other owners' cameras."""

    network: bool


@router.get("/cameras/{device_id}/network")
async def network_get(db: DbSession, user: AppUser, device_id: str) -> NetworkState:
    return NetworkState(network=(await _own(db, user, device_id, "owner")).network)


@router.put("/cameras/{device_id}/network")
async def network_put(
    db: DbSession, user: AppUser, device_id: str, body: NetworkState
) -> NetworkState:
    device = await _own(db, user, device_id, "owner")
    device.network = body.network
    await db.commit()
    return NetworkState(network=device.network)


@router.get("/cameras/{device_id}/public/profile")
async def public_profile(
    request: Request, db: DbSession, user: AppUser, device_id: str
) -> AppPublicState:
    device = await _own(db, user, device_id, "owner")
    return _app_state(request, device)


@router.put("/cameras/{device_id}/public/profile")
async def public_profile_put(
    request: Request, db: DbSession, user: AppUser, device_id: str
) -> AppPublicState:
    """Like ``PUT /device/v1/public`` (SPEC §6.8): switch the page and/or replace the whole
    profile; 422 with a German sentence."""
    device = await _own(db, user, device_id, "owner")
    try:
        body = AppPublicUpdate.model_validate_json(await request.body())
    except ValidationError as exc:
        raise HTTPException(422, public.describe_error(exc)) from None
    if body.profile is not None:
        device.public_profile = body.profile.model_dump()
    if body.enabled is True:
        public.enable(device)
    elif body.enabled is False:
        public.disable(device)
    if body.on_home is not None:
        device.public_on_home = body.on_home
    await db.commit()
    return _app_state(request, device)


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


class DiscordState(BaseModel):
    """Roadmap H5. ``webhook`` is shown without its secret part."""

    configured: bool
    webhook: str | None
    kinds: list[str]
    summary: bool
    broken: bool
    # Every kind that can go to the channel, with its German label.
    available: dict[str, str]


class DiscordRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Empty keeps the stored address.
    webhook: str = Field(default="", max_length=300)
    kinds: list[str] = Field(default_factory=list[str], max_length=10)
    summary: bool = True


def _discord_state(device: Device) -> DiscordState:
    config = discord.settings_of(device)
    return DiscordState(
        configured=config is not None,
        webhook=discord.masked(config.webhook) if config else None,
        kinds=list(config.kinds) if config else ["meteor", "aurora", "nlc"],
        summary=config.summary if config else True,
        broken=config.broken if config else False,
        available=discord.KINDS,
    )


@router.get("/cameras/{device_id}/discord")
async def discord_get(db: DbSession, user: AppUser, device_id: str) -> DiscordState:
    return _discord_state(await _own(db, user, device_id, "owner"))


@router.put("/cameras/{device_id}/discord")
async def discord_put(
    db: DbSession, user: AppUser, device_id: str, body: DiscordRequest
) -> DiscordState:
    """400 with a German sentence for an address that is not a Discord webhook."""
    device = await _own(db, user, device_id, "owner")
    old = discord.settings_of(device)
    try:
        config = discord.DiscordSettings.model_validate(
            {
                "webhook": body.webhook.strip() or (old.webhook if old else ""),
                "kinds": [k for k in body.kinds if k in discord.KINDS],
                "summary": body.summary,
            }
        )
    except ValidationError:
        raise HTTPException(400, discord.BAD_WEBHOOK) from None
    device.discord = config.model_dump()
    await db.commit()
    return _discord_state(device)


@router.delete("/cameras/{device_id}/discord")
async def discord_delete(db: DbSession, user: AppUser, device_id: str) -> DiscordState:
    device = await _own(db, user, device_id, "owner")
    device.discord = None
    await db.commit()
    return _discord_state(device)


@router.post("/cameras/{device_id}/discord/test", status_code=204)
async def discord_test(request: Request, db: DbSession, user: AppUser, device_id: str) -> Response:
    """409 when no webhook is set or Discord does not know it, 502 when unreachable."""
    device = await _own(db, user, device_id, "owner")
    try:
        await ratelimit.hit(
            request.app.state.engine, f"discord-test:{device.id}", ratelimit.DISCORD_TEST
        )
    except ratelimit.RateLimitedError as exc:
        raise HTTPException(
            429,
            "Bitte ein paar Minuten warten, bevor du noch eine Testnachricht schickst.",
            {"Retry-After": str(exc.retry_after)},
        ) from None
    poster: discord.Discord = request.app.state.notifier.discord
    result = await poster.test(device) if discord.settings_of(device) else "gone"
    if result == "gone":
        raise HTTPException(409, "Discord kennt diesen Webhook nicht (mehr).")
    if result != "ok":
        raise HTTPException(502, "Discord war nicht erreichbar.")
    return Response(status_code=204)

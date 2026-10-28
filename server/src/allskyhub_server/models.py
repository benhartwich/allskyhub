"""Database models. Schema changes go through Alembic (``server/migrations``)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from allskyhub_server.ids import uuid7

# Deterministic constraint names so Alembic migrations stay stable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _tz() -> DateTime:
    return DateTime(timezone=True)


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Stored lower-cased (see auth.accounts.normalize_email).
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # How long detections are kept (privacy policy): 30 (default), 90 or 365 days. Above 30
    # the hub also keeps each event's full picture for that long.
    event_keep_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    # Push notifications (roadmap #6), each off until the user switches it on.
    notify_events: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notify_offline: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Roadmap C2: Kp high enough for the camera's latitude while its sky is clear.
    notify_aurora: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Roadmap C3: a summary of each camera's night in the morning.
    notify_summary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Roadmap C1: in the evening, when the night ahead looks clear.
    notify_tonight: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # A fireball that may have dropped meteorites, seen by the network near the user's cameras.
    notify_fireball: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Roadmap #15: the notifications above (all but single detections) also by e-mail.
    notify_by_mail: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Roadmap N7: when this account started its one free Plus month.
    plus_trial_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # "owner" (cameras, the default) or "reporter": signed up on /melden/konto only to
    # report the sky brightness from the app; cannot pair cameras (Benjamin, 2026-10-28).
    kind: Mapped[str] = mapped_column(String(16), default="owner", server_default="owner")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class EmailConfirmation(Base):
    """A reporter's sign-up link (SHA-256 of the token); the account stays inactive until
    it is opened."""

    __tablename__ = "email_confirmation"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())


class SkyReport(Base):
    """The sky brightness as a person saw it (app "Himmelshelligkeit melden"): the
    faintest stars in a constellation, at a place rounded to 0.1°."""

    __tablename__ = "sky_report"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[dt.datetime] = mapped_column(_tz(), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    constellation: Mapped[str] = mapped_column(String(3))
    limit_mag: Mapped[float] = mapped_column(Float)
    sqm: Mapped[float] = mapped_column(Float)
    clouds: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class WebSession(Base):
    """Server-side session; the cookie carries a token whose SHA-256 is stored here."""

    __tablename__ = "web_session"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    last_seen_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())


class RateLimit(Base):
    """Fixed-window counters (auth.ratelimit)."""

    __tablename__ = "rate_limit"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[dt.datetime] = mapped_column(_tz(), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class Device(Base):
    """A camera (SPEC §6.2). Known from its first registration; paired while ``owner_id`` is set."""

    __tablename__ = "device"

    # SPEC §6.2: derived from the public key, 26 base32 characters.
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    profile: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[str] = mapped_column(String(64))
    # Unpairing bumps it: tokens of an earlier pairing stop working at once.
    auth_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    paired_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Latest `status` and `frame` bodies (SPEC §6.3), as sent.
    last_status: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_frame: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # When the latest full image and thumbnail were stored (SPEC §6.5).
    latest_image_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    latest_thumb_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # When an "offline" push was sent for the current outage (roadmap #6); cleared on reconnect.
    offline_notified_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Roadmap C2/C3: last aurora alarm, and the night whose summary went out.
    aurora_notified_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    summary_night: Mapped[str | None] = mapped_column(String(8))
    # Roadmap C1: the night whose evening notice was decided (sent or not).
    tonight_night: Mapped[str | None] = mapped_column(String(8))
    # Roadmap #15: when the owner got an e-mail about this outage (long offline).
    offline_mailed_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Roadmap C14: health problems (devices/health.py keys) already reported to the owner.
    health_notified: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # SPEC §6.9: the settings schema from the newest valid `hello`; None for old agents.
    settings_schema: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Opt-in public sky page (/sky/<slug>): a random slug, never the device id; switching the
    # page off drops it, so old links stop working.
    public_slug: Mapped[str | None] = mapped_column(String(32), unique=True)
    # What the public page shows and its texts (devices/public.py PublicProfile); None = defaults.
    public_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The public page also listed on the hub's start page; the owner's choice, kept on the
    # hub only (not part of the profile of SPEC §6.8).
    public_on_home: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The camera takes part in the network of other owners' cameras: meteors seen together
    # are triangulated with the exact places (devices/meteor_pairs.py), which tells the other
    # owner a lot about where this camera stands. The owner's choice, kept on the hub only.
    network: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Roadmap N7, "Plus": the longer archive (maintenance.py) until ``plus_until``; a gift
    # (``plus_gift``, set by an admin) never ends. Paid and trial periods come from Paddle.
    plus_until: Mapped[dt.datetime | None] = mapped_column(_tz())
    plus_gift: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # The free trial month (billing/plus.py), once per account; ``plus_until`` covers it too.
    plus_trial_until: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Roadmap H5: Discord webhook and what goes there (devices/discord.py), and the night
    # whose summary was posted.
    discord: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    discord_night: Mapped[str | None] = mapped_column(String(8))
    # Roadmap H9 (SPEC §7.8 By day): list the aircraft crossing the sky by day.
    day_aircraft: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Roadmap H5: the owner's YouTube channel (devices/youtube.py): refresh token, privacy.
    youtube: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Roadmap #17: the public page under the owner's own domain, set up by a root helper
    # (deploy/domains): state "pending", "active", "failed" or "removing".
    custom_domain: Mapped[str | None] = mapped_column(String(253), unique=True)
    custom_domain_state: Mapped[str | None] = mapped_column(String(10))
    custom_domain_error: Mapped[str | None] = mapped_column(String(300))
    # SPEC §7.4 B: the offline code's Argon2id verifier (never the code itself), whether the
    # device still has to get it (`local_access`, §6.5), and when the owner made it.
    local_access_verifier: Mapped[str | None] = mapped_column(String(512))
    local_access_pending: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    local_access_set_at: Mapped[dt.datetime | None] = mapped_column(_tz())


class DeviceNonce(Base):
    """Single-use challenge (SPEC §6.2), stored as SHA-256."""

    __tablename__ = "device_nonce"

    nonce_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(26))
    expires_at: Mapped[dt.datetime] = mapped_column(_tz(), index=True)


class PairingCode(Base):
    """SPEC §6.2 step 2: one open code per device at a time."""

    __tablename__ = "pairing_code"
    __table_args__ = (
        # Open codes are unique; claimed ones may repeat later.
        Index(
            "uq_pairing_code_open_code",
            "code",
            unique=True,
            postgresql_where=text("claimed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(6))
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())
    claimed_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class DeviceToken(Base):
    """Bearer token of a paired device (SPEC §6.2 step 5), stored as SHA-256."""

    __tablename__ = "device_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    auth_generation: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())


class WidgetToken(Base):
    """Roadmap N6: a home-screen widget's read-only key for one camera (latest image and a
    few lines), stored as SHA-256. Separate from the app's token, so a widget, which keeps
    its key outside the app's secure storage, can never do more than show the camera."""

    __tablename__ = "widget_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class AppToken(Base):
    """Bearer token of a signed-in app (M3), stored as SHA-256."""

    __tablename__ = "app_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    # Shown in a future "signed-in devices" list, e.g. "Pixel 8".
    label: Mapped[str] = mapped_column(String(100), default="", server_default="")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    last_used_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())


class Invitation(Base):
    """Accounts exist only by invitation; the link carries a token stored here as SHA-256."""

    __tablename__ = "invitation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(_tz())
    used_at: Mapped[dt.datetime | None] = mapped_column(_tz())


class AccessRequest(Base):
    """Someone asks for an account on the public page (/zugang); an admin invites or declines."""

    __tablename__ = "access_request"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(String(254), index=True)
    name: Mapped[str] = mapped_column(String(100))
    place: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # How far along: access_requests.STAGES.
    stage: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    # "invited" or "declined"; null while open.
    decision: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[dt.datetime | None] = mapped_column(_tz())


class Frame(Base):
    """A frame the hub asked the device for (SPEC §4.4 metadata) and what it holds of it."""

    __tablename__ = "frame"
    __table_args__ = (UniqueConstraint("device_id", "night_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    night_id: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128))
    captured_at: Mapped[dt.datetime] = mapped_column(_tz())
    mode: Mapped[str] = mapped_column(String(8))
    exposure_us: Mapped[int] = mapped_column(BigInteger)
    gain: Mapped[float] = mapped_column(Float)
    mean: Mapped[float] = mapped_column(Float)
    sun_elevation: Mapped[float] = mapped_column(Float)
    has_full: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    has_thumb: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Retention counts from here, not from the device clock (SPEC §4.4: it may be wrong).
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now(), index=True)


class Product(Base):
    """A night product the device announced (SPEC §5.2, §6.3) and what the hub holds of it."""

    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("device_id", "night_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    night_id: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(BigInteger)
    duration_s: Mapped[float | None] = mapped_column(Float)
    # The device has a thumbnail to offer (always JPEG).
    thumbnail: Mapped[bool] = mapped_column(Boolean)
    has_full: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    has_thumb: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    full_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Roadmap H5: the video on the owner's YouTube channel, once uploaded.
    youtube_id: Mapped[str | None] = mapped_column(String(32))
    # Retention counts from the first announcement (privacy policy).
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now(), index=True)


class AircraftPass(Base):
    """Roadmap H9 (SPEC §7.8 By day): an aircraft at least 20° up seen from a camera by day,
    from ADS-B positions alone; under the night id of the date it started (like the day
    products, §5.2)."""

    __tablename__ = "aircraft_pass"
    __table_args__ = (Index("ix_aircraft_pass_device_night", "device_id", "night_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    night_id: Mapped[str] = mapped_column(String(8))
    icao: Mapped[str] = mapped_column(String(8))
    callsign: Mapped[str | None] = mapped_column(String(10))
    registration: Mapped[str | None] = mapped_column(String(12))
    type_code: Mapped[str | None] = mapped_column(String(8))
    start: Mapped[dt.datetime] = mapped_column(_tz())
    end: Mapped[dt.datetime] = mapped_column(_tz())
    top_at: Mapped[dt.datetime] = mapped_column(_tz())
    top_alt_deg: Mapped[float] = mapped_column(Float)
    top_az_deg: Mapped[float] = mapped_column(Float)
    start_alt_deg: Mapped[float] = mapped_column(Float)
    start_az_deg: Mapped[float] = mapped_column(Float)
    end_alt_deg: Mapped[float] = mapped_column(Float)
    end_az_deg: Mapped[float] = mapped_column(Float)
    alt_m: Mapped[float | None] = mapped_column(Float)
    min_km: Mapped[float] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now(), index=True)


class EventRecord(Base):
    """A detection the device reported (SPEC §6.4); upserted by the device's stable id."""

    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("device_id", "event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(48))
    night_id: Mapped[str] = mapped_column(String(8), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    start: Mapped[dt.datetime] = mapped_column(_tz(), index=True)
    end: Mapped[dt.datetime] = mapped_column(_tz())
    confidence: Mapped[float] = mapped_column(Float)
    has_image: Mapped[bool] = mapped_column(Boolean)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    has_full: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    has_thumb: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # SPEC §6.4 Original frame (roadmap L28): the whole frame the event was found in, and its
    # size in pixels (the picture's window, ``data.crop_*``, is outlined in it).
    has_original: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    original_w: Mapped[int | None] = mapped_column(Integer)
    original_h: Mapped[int | None] = mapped_column(Integer)
    # The owner's verdict: "confirmed" or "false_positive" (e.g. "kein Meteor", an aircraft);
    # exported for tuning the detector.
    label: Mapped[str | None] = mapped_column(String(16))
    # SPEC §6.4 Labels: why it is a false positive (aircraft, satellite, artifact, other);
    # ``labelled_at`` is the label's `at` (also of a removal), the newer one wins.
    label_reason: Mapped[str | None] = mapped_column(String(16))
    labelled_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Set on the hub and not yet acknowledged by the camera (`label_event`, SPEC §6.5).
    label_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # SPEC §6.4: set by a matcher (aircraft, §7.8), never over a label, and what it found.
    label_auto: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    label_detail: Mapped[str | None] = mapped_column(String(100))
    # Roadmap H5: already posted to the owner's Discord channel.
    discord_posted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # SPEC §7.10 (lightning): when the hub compared it with the Lightning Imager's flashes
    # (null: unchecked), and the nearest flash within reach then (null: none).
    li_checked_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    li_km: Mapped[float | None] = mapped_column(Float)
    li_bearing_deg: Mapped[float | None] = mapped_column(Float)
    # Retention counts from the first report (privacy policy).
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now(), index=True)


class LiFlash(Base):
    """A flash Meteosat's Lightning Imager saw over Europe (SPEC §7.10), kept 48 hours."""

    __tablename__ = "li_flash"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(_tz(), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)


class LiGranule(Base):
    """A Lightning Imager product the hub has read: its time span counts as covered, also
    when it held no flash over Europe (SPEC §7.10)."""

    __tablename__ = "li_granule"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    start: Mapped[dt.datetime] = mapped_column(_tz(), index=True)
    end: Mapped[dt.datetime] = mapped_column(_tz())
    flashes: Mapped[int] = mapped_column(Integer)
    read_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class PushToken(Base):
    """An app installation that receives push notifications (FCM registration token)."""

    __tablename__ = "push_token"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(4096), unique=True)
    platform: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class SkySample(Base):
    """One sky measurement from ``status.sky`` (SPEC §6.3): cloud cover, sky brightness, stars.

    One row per measured frame (``at``); the device repeats the last one between frames."""

    __tablename__ = "sky_sample"
    __table_args__ = (UniqueConstraint("device_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    night_id: Mapped[str] = mapped_column(String(8), index=True)
    at: Mapped[dt.datetime] = mapped_column(_tz())
    cloud_cover: Mapped[float | None] = mapped_column(Float)
    sqm_mag: Mapped[float | None] = mapped_column(Float)
    stars: Mapped[int | None] = mapped_column(Integer)
    # Retention counts from arrival at the hub (privacy policy, like thumbnails).
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now(), index=True)


class NightStat(Base):
    """One night of a camera in numbers (roadmap C7), kept after the samples and events
    expire, so statistics reach back years. Removed with the pairing."""

    __tablename__ = "night_stat"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.id", ondelete="CASCADE"), primary_key=True
    )
    night_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    clear_hours: Mapped[float | None] = mapped_column(Float)
    best_sqm: Mapped[float | None] = mapped_column(Float)
    meteors: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Radiant-matched meteors per shower (German names), plus "sporadisch".
    showers: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")
    aurora: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    storms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[dt.datetime] = mapped_column(
        _tz(), server_default=func.now(), onupdate=func.now()
    )


class RenderJob(Base):
    """A timelapse or a keogram over several nights, made on the hub from the archive's
    thumbnails (roadmap C8). The file lives with the camera's images."""

    __tablename__ = "render_job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "timelapse" or "keogram"
    start: Mapped[dt.datetime] = mapped_column(_tz())
    end: Mapped[dt.datetime] = mapped_column(_tz())
    fps: Mapped[int] = mapped_column(Integer, default=25, server_default="25")
    state: Mapped[str] = mapped_column(String(10), default="queued", server_default="queued")
    error: Mapped[str | None] = mapped_column(String(300))
    frames: Mapped[int | None] = mapped_column(Integer)
    size: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(_tz())


class CameraMember(Base):
    """A camera shared with another account (roadmap C10): ``viewer`` or ``admin``."""

    __tablename__ = "camera_member"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class MeteorPair(Base):
    """One meteor seen by two cameras and its trajectory (roadmap C4). ``a`` is the device
    with the smaller id. Kept after the events expire; removed with either pairing."""

    __tablename__ = "meteor_pair"
    __table_args__ = (UniqueConstraint("device_a", "event_a", "device_b", "event_b"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_a: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    event_a: Mapped[str] = mapped_column(String(64))
    device_b: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    event_b: Mapped[str] = mapped_column(String(64))
    at: Mapped[dt.datetime] = mapped_column(_tz())
    high_lat: Mapped[float] = mapped_column(Float)
    high_lon: Mapped[float] = mapped_column(Float)
    high_km: Mapped[float] = mapped_column(Float)
    low_lat: Mapped[float] = mapped_column(Float)
    low_lon: Mapped[float] = mapped_column(Float)
    low_km: Mapped[float] = mapped_column(Float)
    length_km: Mapped[float] = mapped_column(Float)
    convergence_deg: Mapped[float] = mapped_column(Float)
    # The brighter of the two events' `peak` (SPEC §6.4), for fireballs (devices/fireball.py),
    # and whether the fireball alarm for this meteor went out.
    peak: Mapped[float | None] = mapped_column(Float)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[dt.datetime] = mapped_column(_tz(), server_default=func.now())


class StatusSample(Base):
    """The camera's state every few minutes (SPEC §6.3 status), for the charts of exposure,
    gain, temperatures and disk. Kept like thumbnails; removed with the pairing."""

    __tablename__ = "status_sample"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    at: Mapped[dt.datetime] = mapped_column(_tz())
    mode: Mapped[str | None] = mapped_column(String(8))
    exposure_us: Mapped[int | None] = mapped_column(BigInteger)
    gain: Mapped[float | None] = mapped_column(Float)
    sensor_temp_c: Mapped[float | None] = mapped_column(Float)
    cpu_temp_c: Mapped[float | None] = mapped_column(Float)
    disk_free_pct: Mapped[float | None] = mapped_column(Float)
    # SPEC §7.11 weather sensor and dew heater (roadmap L30), null without them.
    air_temp_c: Mapped[float | None] = mapped_column(Float)
    humidity_pct: Mapped[float | None] = mapped_column(Float)
    dewpoint_c: Mapped[float | None] = mapped_column(Float)
    pressure_hpa: Mapped[float | None] = mapped_column(Float)
    heater_on: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (Index("ix_status_sample_device_id_at", "device_id", "at"),)


class PasswordReset(Base):
    """A "Passwort vergessen" link (roadmap #15): stored as SHA-256, single use, one hour."""

    __tablename__ = "password_reset"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[dt.datetime] = mapped_column(_tz(), index=True)
    used_at: Mapped[dt.datetime | None] = mapped_column(_tz())


class Subscription(Base):
    """A Plus subscription at Paddle (roadmap N7, billing/paddle.py), as its webhooks last
    described it. Removing the camera keeps the row (``device_id`` None) until Paddle has
    cancelled it."""

    __tablename__ = "subscription"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # Paddle's sub_…
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("device.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL"), index=True
    )
    customer_id: Mapped[str] = mapped_column(String(64))
    # Paddle's status: trialing, active, past_due, paused or canceled.
    status: Mapped[str] = mapped_column(String(16))
    price_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    period_end: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Set while a cancellation at the end of the period is scheduled.
    cancel_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    ended_at: Mapped[dt.datetime | None] = mapped_column(_tz())
    # Paddle's occurred_at of the event this row reflects; older events are ignored.
    event_at: Mapped[dt.datetime] = mapped_column(_tz())

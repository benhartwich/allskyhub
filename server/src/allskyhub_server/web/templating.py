"""Jinja2 environment: static files with content hashes, date filters."""

from __future__ import annotations

import datetime as dt
import hashlib
from functools import cache
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import allskyhub_design
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from jinja2.runtime import Context

from allskyhub_server.devices import sky_now as sky_now_module
from allskyhub_server.devices import sky_overlay as overlay
from allskyhub_server.devices.event_text import compass

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=TEMPLATE_DIR)


# The shared look (packages/design) is served under /static/design/.
DESIGN_DIR = allskyhub_design.static_dir()


def _static_file(path: str) -> Path:
    if path.startswith("design/"):
        return DESIGN_DIR / path.removeprefix("design/")
    return STATIC_DIR / path


@cache
def _static_digest(path: str) -> str:
    return hashlib.sha256(_static_file(path).read_bytes()).hexdigest()[:10]


def asset(path: str) -> str:
    """URL of a static file with its content hash, so browsers never keep an old copy."""
    return f"/static/{path}?v={_static_digest(path)}"


# Until accounts have a time zone setting, times are shown in Central European time.
UI_ZONE = ZoneInfo("Europe/Berlin")


def localtime(value: dt.datetime | None) -> str:
    if value is None:
        return "–"
    return value.astimezone(UI_ZONE).strftime("%d.%m.%Y %H:%M")


def age(value: dt.datetime | None) -> str:
    """Age for status lines: "vor 3 Min."."""
    if value is None:
        return "nie"
    seconds = int((dt.datetime.now(dt.UTC) - value).total_seconds())
    if seconds < 60:
        return "gerade eben"
    if seconds < 3600:
        return f"vor {seconds // 60} Min."
    if seconds < 86400:
        return f"vor {seconds // 3600} Std."
    return f"vor {seconds // 86400} Tagen"


_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def night_title(night_id: str) -> str:
    """ "Di 06.10. → Mi 07.10.": a night spans two dates (SPEC §4.5, id = evening)."""
    evening = dt.date(int(night_id[:4]), int(night_id[4:6]), int(night_id[6:8]))
    morning = evening + dt.timedelta(days=1)
    return " → ".join(
        f"{_WEEKDAYS[d.weekday()]} {d.day:02d}.{d.month:02d}." for d in (evening, morning)
    )


def clock(value: dt.datetime | None) -> str:
    return "–" if value is None else value.astimezone(UI_ZONE).strftime("%H:%M")


def when(value: dt.datetime) -> str:
    """ "14.10.2026, 22:14" in the hub's time zone."""
    return value.astimezone(UI_ZONE).strftime("%d.%m.%Y, %H:%M")


def stamp(value: str | dt.datetime | None) -> str:
    """ "heute 14:02", "gestern 22:10" or "05.11. 14:02" in the hub's time zone; an ISO
    text too (the apps page keeps its times as text)."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    local = value.astimezone(UI_ZONE)
    today = dt.datetime.now(UI_ZONE).date()
    day = local.date()
    if day == today:
        return f"heute {local:%H:%M}"
    if day == today - dt.timedelta(days=1):
        return f"gestern {local:%H:%M}"
    return f"{local:%d.%m.} {local:%H:%M}"


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def filesize(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB".replace(".", ",")
    return f"{-(-size // 1024)} KB"


def sky_overlay(device: Any) -> Any:
    """Roadmap H1: constellations for the device's latest image, None without a lens."""
    if device.latest_image_at is None:
        return None
    at = overlay.image_time(device.last_frame, device.latest_image_at)
    return overlay.build(device.last_status, at)


def air_overlay(device: Any) -> Any:
    """Roadmap H3: aircraft over the device's latest image, None without a lens."""
    from allskyhub_server.devices import aircraft

    if device.latest_image_at is None or aircraft.current is None:
        return None
    at = overlay.image_time(device.last_frame, device.latest_image_at)
    return overlay.build_aircraft(device.last_status, at, aircraft.current.tracks, device.id)


def sky_now(device: Any) -> Any:
    """Drafts 2 and 4: the sky tiles of the camera page, None before the first measurement."""
    return sky_now_module.sky_now(device.last_status)


def orientation_badge(device: Any) -> Any:
    """Draft 2: how well the camera knows its orientation, None before the first solve."""
    return sky_now_module.orientation(device.last_status)


# SPEC §7.11 status.heater reasons.
HEATER_REASONS = {
    "off": "aus", "on": "an (dauernd)", "dew": "an: Taugefahr", "humidity": "an: hohe Luftfeuchte",
    "night": "an: Nacht (ohne Sensor)", "too_warm": "aus: zu warm",
}  # fmt: skip


def heater_text(heater: object) -> str:
    if not isinstance(heater, dict):
        return ""
    h = cast(dict[str, Any], heater)
    reason = h.get("reason")
    text = HEATER_REASONS.get(reason) if isinstance(reason, str) else None
    return text or ("an" if h.get("on") else "aus")


@pass_context
def t(context: Context, de: str, en: str) -> str:
    """German or English by the page's language (``en`` from web/render.py, web/lang.py):
    ``{{ t('Anmelden', 'Sign in') }}`` on the pages that have both."""
    return en if context.get("en") else de


cast(dict[str, Any], templates.env.globals)["t"] = t
cast(dict[str, Any], templates.env.globals)["asset"] = asset
# The app in the stores (Benjamin, 2026-10-28; iOS released 2026-11-07).
PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=org.allskyhub.app"
APP_STORE_ID = "6826841463"
APP_STORE_URL = f"https://apps.apple.com/app/id{APP_STORE_ID}"
cast(dict[str, Any], templates.env.globals)["play_store_url"] = PLAY_STORE_URL
cast(dict[str, Any], templates.env.globals)["app_store_url"] = APP_STORE_URL
cast(dict[str, Any], templates.env.globals)["app_store_id"] = APP_STORE_ID
cast(dict[str, Any], templates.env.globals)["heater_text"] = heater_text
cast(dict[str, Any], templates.env.globals)["air_overlay"] = air_overlay
cast(dict[str, Any], templates.env.globals)["sky_overlay"] = sky_overlay
cast(dict[str, Any], templates.env.globals)["sky_now"] = sky_now
cast(dict[str, Any], templates.env.globals)["orientation_badge"] = orientation_badge
_filters = cast(dict[str, Any], templates.env.filters)
_filters["age"] = age
_filters["night_title"] = night_title
_filters["clock"] = clock
_filters["when"] = when
_filters["stamp"] = stamp
_filters["compass"] = compass
_filters["mmss"] = mmss
_filters["filesize"] = filesize
cast(dict[str, Any], templates.env.filters)["localtime"] = localtime

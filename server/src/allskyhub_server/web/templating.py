"""Jinja2 environment: static files with content hashes, date filters."""

from __future__ import annotations

import datetime as dt
import hashlib
from functools import cache
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=TEMPLATE_DIR)


@cache
def _static_digest(path: str) -> str:
    return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:10]


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


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def filesize(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB".replace(".", ",")
    return f"{-(-size // 1024)} KB"


cast(dict[str, Any], templates.env.globals)["asset"] = asset
_filters = cast(dict[str, Any], templates.env.filters)
_filters["age"] = age
_filters["night_title"] = night_title
_filters["clock"] = clock
_filters["mmss"] = mmss
_filters["filesize"] = filesize
cast(dict[str, Any], templates.env.filters)["localtime"] = localtime

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


cast(dict[str, Any], templates.env.globals)["asset"] = asset
cast(dict[str, Any], templates.env.filters)["localtime"] = localtime

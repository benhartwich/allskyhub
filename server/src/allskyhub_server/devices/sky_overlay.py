"""Constellations over a camera's image on the hub (roadmap H1): reads the lens and the
place from the camera's status; the drawing itself is shared with the camera
(``allskyhub_sky``)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import ValidationError

from allskyhub_protocol import Orientation
from allskyhub_sky import Overlay, overlay, usable


def orientation(status: dict[str, Any] | None) -> Orientation | None:
    raw = (status or {}).get("orientation")
    if not isinstance(raw, dict):
        return None
    try:
        found = Orientation.model_validate(raw)
    except ValidationError:
        return None
    return found if usable(found.lens) else None


def build(status: dict[str, Any] | None, at: dt.datetime) -> Overlay | None:
    """The overlay for an image taken ``at``; None without a usable lens or place."""
    found = orientation(status)
    settings: dict[str, Any] = (status or {}).get("settings") or {}
    lat, lon = settings.get("latitude"), settings.get("longitude")
    if found is None or found.lens is None:
        return None
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return overlay(found.lens, found.north_deg, found.mirrored, float(lat), float(lon), at)


def image_time(device_last_frame: dict[str, Any] | None, fallback: dt.datetime) -> dt.datetime:
    """When the shown image was taken (``captured_at`` of the last frame), else ``fallback``."""
    raw = (device_last_frame or {}).get("captured_at")
    if isinstance(raw, str):
        try:
            at = dt.datetime.fromisoformat(raw)
        except ValueError:
            return fallback
        return at if at.tzinfo else at.replace(tzinfo=dt.UTC)
    return fallback

"""What's new in each release (SPEC §8 What's new): `news.json` next to this module, newest
first, one entry per version with a few short points in German and English.

It comes with the bundle, so a camera shows what its running release brought; the release
workflow takes the GitHub release notes from the same entry.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from allskyhub_protocol.updates import VERSION_PATTERN, version_key

log = logging.getLogger(__name__)

_POINT = Field(min_length=1, max_length=300)


class News(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=VERSION_PATTERN, max_length=32)
    de: list[str] = Field(min_length=1, max_length=8)
    en: list[str] = Field(min_length=1, max_length=8)


_LIST = TypeAdapter(list[News])


def load_news(text: str | None = None) -> list[News]:
    """All entries, newest first; an unreadable file means none (it never stops the UI)."""
    try:
        if text is None:
            text = (
                resources.files("allskyhub_agent.update").joinpath("news.json").read_text("utf-8")
            )
        entries = _LIST.validate_python(json.loads(text))
    except (OSError, ValueError, ValidationError) as exc:
        log.warning("news.json: %s", exc)
        return []
    return sorted(entries, key=lambda e: version_key(e.version), reverse=True)


def news_for(
    version: str, entries: list[News] | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    """What the running `version` and the ones before it brought, newest first."""
    if entries is None:
        entries = load_news()
    upto = version_key(version)
    return [e.model_dump() for e in entries if version_key(e.version) <= upto][:limit]

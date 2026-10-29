"""Fixed-window rate limits in PostgreSQL (no Redis).

Counts are committed in their own transaction, so rejected and failed attempts count too.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class Limit:
    count: int
    window_s: int


class RateLimitedError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


_HIT = text(
    """
    WITH w AS (
        SELECT to_timestamp(floor(extract(epoch FROM now()) / :win) * :win) AS start
    )
    INSERT INTO rate_limit (key, window_start, count)
    SELECT :key, w.start, 1 FROM w
    ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limit.count + 1
    RETURNING count, extract(epoch FROM (window_start + make_interval(secs => :win) - now()))
    """
)


async def hit(engine: AsyncEngine, key: str, limits: Sequence[Limit]) -> None:
    """Count one attempt for ``key``; raise ``RateLimitedError`` if any limit is exceeded."""
    retry_after = 0
    async with engine.begin() as conn:
        for limit in limits:
            row = (
                await conn.execute(_HIT, {"key": f"{key}|{limit.window_s}", "win": limit.window_s})
            ).one()
            count, remaining = int(row[0]), float(row[1])
            if count > limit.count:
                retry_after = max(retry_after, math.ceil(remaining))
    if retry_after:
        raise RateLimitedError(max(retry_after, 1))


LOGIN_PER_ACCOUNT = (Limit(10, 900),)
# Audit L5: password guesses for one address count per address and IP, so whoever knows an
# address cannot lock its owner out from one place; this cap holds across all places.
LOGIN_PER_ACCOUNT_ALL = (Limit(100, 900), Limit(500, 86400))
LOGIN_PER_IP = (Limit(50, 900),)
INVITE_PER_IP = (Limit(20, 3600),)
# Roadmap #15: "Passwort vergessen" mails, per address and per IP.
RESET_PER_EMAIL = (Limit(3, 3600),)
RESET_PER_IP = (Limit(10, 3600),)
# Access requests on /zugang: per IP, and a cap across all, so a bot cannot flood the admins.
ACCESS_PER_IP = (Limit(3, 3600), Limit(10, 86400))
ACCESS_ALL = (Limit(50, 86400),)
# Device API (SPEC §6.6): a registering device calls challenge + register every ~5 s.
DEVICE_CHALLENGE_PER_IP = (Limit(120, 60), Limit(2000, 3600))
DEVICE_REGISTER_PER_IP = (Limit(60, 60),)
DEVICE_TOKEN_PER_IP = (Limit(30, 60),)
# SPEC §6.2 step 3: codes have 31^6 values; this keeps guessing hopeless.
CLAIM_PER_USER = (Limit(10, 600), Limit(30, 86400))
CLAIM_PER_IP = (Limit(30, 600),)
# Roadmap H5: test posts to a Discord webhook.
DISCORD_TEST = (Limit(5, 600),)
MAP_SEARCH_PER_IP = (Limit(20, 60), Limit(200, 3600))
# Public APKs (/download/<app>.apk, /app/android/…) are some 100 MB each: a script must not
# burn the server's bandwidth (review of #354). The cap across all is ~50 GB a day.
APK_PER_IP = (Limit(10, 3600),)
APK_ALL = (Limit(500, 86400),)


async def login(engine: AsyncEngine, email: str, ip: str) -> None:
    """Count one login attempt (web and app) for ``email`` (normalised) from ``ip``."""
    await hit(engine, f"login:ip:{ip}", LOGIN_PER_IP)
    await hit(engine, f"login:acct:{email}|{ip}", LOGIN_PER_ACCOUNT)
    await hit(engine, f"login:acct:{email}", LOGIN_PER_ACCOUNT_ALL)

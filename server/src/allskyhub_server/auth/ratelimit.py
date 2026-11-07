"""Fixed-window rate limits in PostgreSQL (no Redis).

Counts are committed in their own transaction, so rejected and failed attempts count too.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class Limit:
    count: int
    window_s: int


# The database's clock unless set (``:now`` in the queries): tests fix it, so no window
# ends in the middle of one (tests/conftest.py; fixed-window flakes 2026-11-07).
clock: Callable[[], dt.datetime] | None = None


def _now() -> dt.datetime | None:
    return clock() if clock is not None else None


class RateLimitedError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


_HIT = text(
    """
    WITH c AS (SELECT COALESCE(CAST(:now AS timestamptz), now()) AS now),
    w AS (
        SELECT to_timestamp(floor(extract(epoch FROM c.now) / :win) * :win) AS start, c.now
        FROM c
    )
    INSERT INTO rate_limit (key, window_start, count)
    SELECT :key, w.start, 1 FROM w
    ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limit.count + 1
    RETURNING count,
        extract(epoch FROM (window_start + make_interval(secs => :win) - (SELECT now FROM c)))
    """
)


async def hit(engine: AsyncEngine, key: str, limits: Sequence[Limit]) -> None:
    """Count one attempt for ``key``; raise ``RateLimitedError`` if any limit is exceeded."""
    retry_after = 0
    async with engine.begin() as conn:
        for limit in limits:
            row = (
                await conn.execute(
                    _HIT, {"key": f"{key}|{limit.window_s}", "win": limit.window_s, "now": _now()}
                )
            ).one()
            count, remaining = int(row[0]), float(row[1])
            if count > limit.count:
                retry_after = max(retry_after, math.ceil(remaining))
    if retry_after:
        raise RateLimitedError(max(retry_after, 1))


_PEEK = text(
    """
    WITH c AS (SELECT COALESCE(CAST(:now AS timestamptz), now()) AS now)
    SELECT count, extract(epoch FROM (window_start + make_interval(secs => :win) - c.now))
    FROM rate_limit, c
    WHERE key = :key
      AND window_start = to_timestamp(floor(extract(epoch FROM c.now) / :win) * :win)
    """
)


async def blocked(engine: AsyncEngine, key: str, limits: Sequence[Limit]) -> int:
    """Seconds until ``key`` may try again (0: now), without counting an attempt; for
    checks before the expensive part of a request (SPEC §10.2)."""
    retry_after = 0
    async with engine.connect() as conn:
        for limit in limits:
            row = (
                await conn.execute(
                    _PEEK, {"key": f"{key}|{limit.window_s}", "win": limit.window_s, "now": _now()}
                )
            ).one_or_none()
            if row is not None and int(row[0]) >= limit.count:
                retry_after = max(retry_after, max(math.ceil(float(row[1])), 1))
    return retry_after


LOGIN_PER_ACCOUNT = (Limit(10, 900),)
# Audit L5: password guesses for one address count per address and IP, so whoever knows an
# address cannot lock its owner out from one place; this cap holds across all places.
LOGIN_PER_ACCOUNT_ALL = (Limit(100, 900), Limit(500, 86400))
LOGIN_PER_IP = (Limit(50, 900),)
INVITE_PER_IP = (Limit(20, 3600),)
# Roadmap #15: "Passwort vergessen" mails, per address and per IP.
# The warning to an account's owner about many failed sign-ins: at most every 6 hours.
LOCKOUT_MAIL = (Limit(1, 6 * 3600),)
# Owner sign-up on /konto/anlegen (open_signup), like the reporters' one.
OWNER_SIGNUP_PER_IP = (Limit(5, 3600), Limit(20, 86400))
SIGNUP_MAIL_PER_EMAIL = (Limit(3, 86400),)
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
# Security review 2026-11-04: every own-domain request stops nginx for a few seconds
# (certbot standalone), for every site on the server.
DOMAIN_PER_USER = (Limit(3, 3600), Limit(10, 86400))
# Roadmap H5: test posts to a Discord webhook.
DISCORD_TEST = (Limit(5, 600),)
MAP_SEARCH_PER_IP = (Limit(20, 60), Limit(200, 3600))
# SPEC §10.2: wrong station logins per IP, like password guesses (LOGIN_PER_IP).
STATION_AUTH_PER_IP = (Limit(30, 900),)
# Public APKs (/download/<app>.apk, /app/android/…) are some 100 MB each: a script must not
# burn the server's bandwidth (review of #354). The cap across all is ~50 GB a day.
APK_PER_IP = (Limit(10, 3600),)
APK_ALL = (Limit(500, 86400),)


class AccountLockedError(RateLimitedError):
    """Too many attempts at one account from many places together: someone may be trying
    passwords, so its owner hears of it (auth/lockout.py). Typos from one place only hit
    the per-place limit and stay quiet (allsky's review of #392)."""


async def login(engine: AsyncEngine, email: str, ip: str) -> None:
    """Count one login attempt (web and app) for ``email`` (normalised) from ``ip``."""
    await hit(engine, f"login:ip:{ip}", LOGIN_PER_IP)
    await hit(engine, f"login:acct:{email}|{ip}", LOGIN_PER_ACCOUNT)
    try:
        await hit(engine, f"login:acct:{email}", LOGIN_PER_ACCOUNT_ALL)
    except RateLimitedError as exc:
        raise AccountLockedError(exc.retry_after) from None

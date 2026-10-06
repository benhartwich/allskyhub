"""Test helpers: CSRF tokens from pages, sign-up and login."""

from __future__ import annotations

import re
import uuid

import httpx

PASSWORD = "correct horse battery"  # noqa: S105
_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html: str) -> str:
    m = _CSRF_RE.search(html)
    assert m, "no csrf token in page"
    return m.group(1)


async def signup(client: httpx.AsyncClient, email: str | None = None) -> str:
    """Create an account (and session); return its email."""
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.org"
    page = await client.get("/signup")
    r = await client.post(
        "/signup",
        data={
            "email": email,
            "password": PASSWORD,
            "password2": PASSWORD,
            "csrf_token": csrf_from(page.text),
        },
    )
    assert r.status_code == 303, r.text
    return email

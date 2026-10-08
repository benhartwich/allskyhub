"""Firebase Cloud Messaging, HTTP v1 API, with a service account (no Google SDK needed).

The service account signs a JWT (RS256) that is exchanged for an OAuth access token; the
token is cached until shortly before it expires.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an URL, not a secret


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    # Opens the right screen in the app, e.g. {"camera": id, "event": id, "night": night}.
    data: dict[str, str]


class Sender:
    """Sends to FCM registration tokens; tells which tokens are no longer valid."""

    def __init__(self, service_account_file: Path, client: httpx.AsyncClient | None = None):
        info = cast(dict[str, Any], json.loads(service_account_file.read_text()))
        self.project_id = str(info["project_id"])
        self._email = str(info["client_email"])
        key = serialization.load_pem_private_key(str(info["private_key"]).encode(), None)
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("service account key must be RSA")
        self._key = key
        self._client = client or httpx.AsyncClient(timeout=10)
        self._token: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    def _assertion(self, now: int) -> str:
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64(
            json.dumps(
                {"iss": self._email, "scope": SCOPE, "aud": TOKEN_URL, "iat": now,
                 "exp": now + 3600}
            ).encode()
        )  # fmt: skip
        signing_input = f"{header}.{claims}".encode()
        signature = self._key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{_b64(signature)}"

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            now = int(time.time())
            r = await self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self._assertion(now),
                },
            )
            r.raise_for_status()
            body = r.json()
            self._token = str(body["access_token"])
            self._expires = now + float(body.get("expires_in", 3600))
            return self._token

    async def send(self, tokens: list[str], message: Message) -> list[str]:
        """Send to every token; returns the tokens FCM reports as unregistered or invalid."""
        if not tokens:
            return []
        access = await self._access_token()
        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        dead: list[str] = []
        for token in tokens:
            payload = {
                "message": {
                    "token": token,
                    "notification": {"title": message.title, "body": message.body},
                    "data": message.data,
                    "android": {"priority": "high"},
                }
            }
            try:
                r = await self._client.post(
                    url, json=payload, headers={"Authorization": f"Bearer {access}"}
                )
            except httpx.HTTPError:
                log.warning("push: FCM not reachable")
                continue
            # Only an unregistered token is dead; INVALID_ARGUMENT may also mean a bad payload.
            if r.status_code == 404 or "UNREGISTERED" in r.text:
                dead.append(token)
            elif r.status_code >= 400:
                log.warning("push: FCM answered %s", r.status_code)
        return dead

"""The camera's public page from its local UI (SPEC §6.8, roadmap L10).

The hub stores the page; the local UI reads and changes it through the device API with
the device's own token. The web server's threads call this synchronously; a token is
kept until shortly before it expires. While the hub cannot be reached the last state
read is kept, so the local UI can still show it (read-only).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, cast

import httpx
from pydantic import ValidationError

from allskyhub_agent.hub.identity import DeviceIdentity
from allskyhub_protocol import (
    ChallengeRequest,
    ChallengeResponse,
    PublicState,
    Purpose,
    TokenRequest,
    TokenResponse,
)

_TOKEN_MARGIN_S = 60.0


class HubUnreachable(Exception):
    """The hub did not answer, or the camera is not paired."""


class PublicRejected(Exception):
    """The hub refused the change; `detail` is its German sentence (SPEC §6.8)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PublicPage:
    def __init__(
        self,
        hub_url: str,
        identity: DeviceIdentity,
        client: Callable[[], httpx.Client] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = hub_url.rstrip("/")
        self._id = identity
        self._client = client or (lambda: httpx.Client(base_url=self._base, timeout=15.0))
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._token: tuple[str, float] | None = None  # value, valid until (monotonic)
        self.last: PublicState | None = None

    def get(self) -> PublicState:
        return self._call("GET", None)

    def put(self, body: dict[str, Any]) -> PublicState:
        """Raises PublicRejected with the hub's sentence for a refused change."""
        return self._call("PUT", body)

    def _call(self, method: str, body: dict[str, Any] | None) -> PublicState:
        with self._lock:
            try:
                with self._client() as http:
                    r = self._request(http, method, body)
                    if r.status_code == 401:
                        self._token = None  # expired or revoked: one new token
                        r = self._request(http, method, body)
            except (httpx.HTTPError, ValidationError) as exc:
                raise HubUnreachable(str(exc)) from None
            if r.status_code == 422:
                detail: object = _detail(r)
                raise PublicRejected(str(detail or "Ungültige Eingabe."))
            if r.status_code != 200:
                raise HubUnreachable(f"hub answered {r.status_code}")
            try:
                state = PublicState.from_hub(r.content)
            except ValidationError as exc:
                raise HubUnreachable(f"unexpected answer: {exc.error_count()} errors") from None
            except ValueError:
                raise HubUnreachable("unexpected answer: not JSON") from None
            self.last = state
            return state

    def _request(
        self, http: httpx.Client, method: str, body: dict[str, Any] | None
    ) -> httpx.Response:
        token = self._valid_token(http)
        return http.request(
            method, "/device/v1/public", json=body, headers={"Authorization": f"Bearer {token}"}
        )

    def _valid_token(self, http: httpx.Client) -> str:
        now = self._monotonic()
        if self._token is not None and now < self._token[1]:
            return self._token[0]
        r = http.post(
            "/device/v1/challenge",
            json=ChallengeRequest(device_id=self._id.device_id).model_dump(),
        )
        r.raise_for_status()
        nonce = ChallengeResponse.model_validate_json(r.content).nonce
        body = TokenRequest(
            device_id=self._id.device_id,
            nonce=nonce,
            signature=self._id.sign(Purpose.TOKEN, nonce),
        )
        r = http.post("/device/v1/token", json=body.model_dump())
        if r.status_code == 403:
            raise HubUnreachable("not paired")
        r.raise_for_status()
        resp = TokenResponse.model_validate_json(r.content)
        self._token = (resp.access_token, now + max(0.0, resp.expires_in - _TOKEN_MARGIN_S))
        return resp.access_token


def _detail(r: httpx.Response) -> object:
    try:
        data: object = r.json()
    except ValueError:
        return None
    return cast("dict[str, object]", data).get("detail") if isinstance(data, dict) else None

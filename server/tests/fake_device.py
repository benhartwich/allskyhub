"""A fake camera for tests: Ed25519 key, challenge signing, register and token (SPEC §6.2)."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from allskyhub_protocol import (
    Purpose,
    RegisterResponse,
    TokenResponse,
    b64url,
    device_id_from_public_key,
    signing_payload,
)


@dataclass
class FakeDevice:
    key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    profile: str = "zwo-asi678mc"
    agent_version: str = "0.1.0"

    @property
    def public_key(self) -> bytes:
        return self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def device_id(self) -> str:
        return device_id_from_public_key(self.public_key)

    def sign(self, purpose: Purpose, nonce: str) -> str:
        return b64url(self.key.sign(signing_payload(purpose, self.device_id, nonce)))

    async def nonce(self, client: httpx.AsyncClient) -> str:
        r = await client.post("/device/v1/challenge", json={"device_id": self.device_id})
        assert r.status_code == 200, r.text
        return str(r.json()["nonce"])

    def register_body(self, nonce: str, signature: str | None = None) -> dict[str, str]:
        return {
            "public_key": b64url(self.public_key),
            "profile": self.profile,
            "agent_version": self.agent_version,
            "nonce": nonce,
            "signature": signature or self.sign(Purpose.REGISTER, nonce),
        }

    async def register(self, client: httpx.AsyncClient) -> RegisterResponse:
        nonce = await self.nonce(client)
        r = await client.post("/device/v1/register", json=self.register_body(nonce))
        assert r.status_code == 200, r.text
        return RegisterResponse.model_validate_json(r.content)

    async def token_response(self, client: httpx.AsyncClient) -> httpx.Response:
        nonce = await self.nonce(client)
        body = {
            "device_id": self.device_id,
            "nonce": nonce,
            "signature": self.sign(Purpose.TOKEN, nonce),
        }
        return await client.post("/device/v1/token", json=body)

    async def token(self, client: httpx.AsyncClient) -> str:
        r = await self.token_response(client)
        assert r.status_code == 200, r.text
        return TokenResponse.model_validate_json(r.content).access_token

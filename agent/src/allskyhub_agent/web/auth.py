"""Local login for the web UI of a paired camera (SPEC §7.4).

Two ways to open a session: a hub-signed assertion (single sign-on with the allskyhub
account) or the camera's offline code, of which the hub sends only an Argon2id verifier
(SPEC §6.5 `local_access`). Sessions, the verifier and the hub keys are kept in one JSON
file in the data directory (mode 0600); only hashes of the session cookies are stored.
Unpairing deletes all of it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from cryptography.exceptions import InvalidKey, InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from pydantic import ValidationError

from allskyhub_protocol import (
    OFFLINE_CODE_LENGTH,
    PAIRING_CODE_ALPHABET,
    HubKey,
    LocalAccessArgs,
    b64url_decode,
    local_login_signing_payload,
    normalize_offline_code,
    split_local_login_assertion,
)

SESSION_COOKIE = "ash_session"
NONCE_COOKIE = "ash_login_nonce"
SESSION_TTL_S = 30 * 24 * 3600
NONCE_TTL_S = 300.0
MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60.0

Method = Literal["hub", "code"]
CodeResult = Literal["ok", "wrong", "locked", "no_code"]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_verifier(phc: str) -> None:
    """Raise ValueError unless `phc` is an Argon2id verifier the device accepts (SPEC §6.5)."""
    try:
        LocalAccessArgs(verifier=phc)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


class LocalAuth:
    """Thread-safe: the web server reads it, the hub client writes keys and the verifier."""

    def __init__(
        self,
        path: Path,
        device_id: str,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = path
        self._device_id = device_id
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._argon = threading.Lock()  # one Argon2 check at a time
        self._nonces: dict[str, float] = {}
        self._failures: deque[float] = deque()
        self._sessions: dict[str, dict[str, Any]] = {}
        # Kept across restarts: a paired camera that cannot reach the hub stays locked.
        self._paired = False
        self._verifier: str | None = None
        self._hub_keys: list[HubKey] = []
        self._load()

    # --- persistence ---------------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        d = cast(dict[str, Any], data)
        self._paired = d.get("paired") is True
        sessions = d.get("sessions")
        if isinstance(sessions, dict):
            self._sessions = {
                str(k): cast(dict[str, Any], v)
                for k, v in cast(dict[str, Any], sessions).items()
                if isinstance(v, dict)
            }
        verifier = d.get("verifier")
        if isinstance(verifier, str):
            with contextlib.suppress(ValueError):
                check_verifier(verifier)
                self._verifier = verifier
        keys = d.get("hub_keys")
        if isinstance(keys, list):
            for k in cast(list[Any], keys):
                if isinstance(k, dict):
                    kd = cast(dict[str, Any], k)
                    kid, pub = kd.get("kid"), kd.get("public_key")
                    if not (isinstance(kid, str) and isinstance(pub, str)):
                        continue
                    with contextlib.suppress(ValidationError):
                        self._hub_keys.append(HubKey(kid=kid, public_key=pub))

    def _save(self) -> None:
        data = {
            "paired": self._paired,
            "sessions": self._sessions,
            "verifier": self._verifier,
            "hub_keys": [k.model_dump() for k in self._hub_keys],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        tmp.replace(self._path)

    # --- written by the hub client -------------------------------------------------------
    def set_hub_keys(self, keys: list[HubKey]) -> None:
        """SPEC §6.2 step 6: every token answer replaces the list."""
        with self._lock:
            if keys != self._hub_keys:
                self._hub_keys = list(keys)
                self._save()

    def set_verifier(self, phc: str | None) -> None:
        """SPEC §6.5 `local_access`; ValueError for a verifier that is too weak."""
        if phc is not None:
            check_verifier(phc)
        with self._lock:
            self._verifier = phc
            # Sessions opened with the old code end with it.
            self._sessions = {h: s for h, s in self._sessions.items() if s.get("method") != "code"}
            self._save()

    def mark_paired(self) -> None:
        with self._lock:
            if not self._paired:
                self._paired = True
                self._save()

    def clear(self) -> None:
        """Unpaired (SPEC §7.4): forget sessions, the verifier and the hub keys."""
        with self._lock:
            if not (self._paired or self._sessions or self._verifier or self._hub_keys):
                return
            self._paired = False
            self._sessions, self._verifier, self._hub_keys = {}, None, []
            self._nonces.clear()
            self._save()

    @property
    def paired(self) -> bool:
        """Whether the local UI needs a login (SPEC §7.4)."""
        with self._lock:
            return self._paired

    @property
    def has_code(self) -> bool:
        with self._lock:
            return self._verifier is not None

    # --- login A: hub assertion ----------------------------------------------------------
    def new_nonce(self) -> str:
        nonce = secrets.token_urlsafe(32)
        now = self._monotonic()
        with self._lock:
            self._nonces = {n: t for n, t in self._nonces.items() if now - t < NONCE_TTL_S}
            self._nonces[nonce] = now
        return nonce

    def check_assertion(
        self, assertion: str, cookie_nonce: str | None, time_trusted: bool
    ) -> str | None:
        """The user id (`sub`) if the assertion is valid for this camera (SPEC §7.4 A step 5)."""
        try:
            payload, claims, signature = split_local_login_assertion(assertion)
        except ValueError:
            return None
        if claims.aud != self._device_id:
            return None
        if cookie_nonce is None or not secrets.compare_digest(claims.nonce, cookie_nonce):
            return None
        with self._lock:
            key = next((k for k in self._hub_keys if k.kid == claims.kid), None)
        if key is None:
            return None
        try:
            pub = Ed25519PublicKey.from_public_bytes(b64url_decode(key.public_key))
            pub.verify(signature, local_login_signing_payload(payload))
        except (ValueError, InvalidSignature, UnsupportedAlgorithm):
            return None
        if time_trusted and claims.exp < self._clock():
            return None
        now = self._monotonic()
        with self._lock:
            made = self._nonces.pop(claims.nonce, None)  # each nonce once
        if made is None or now - made >= NONCE_TTL_S:
            return None
        return claims.sub

    # --- login B: offline code -----------------------------------------------------------
    def check_code(self, code: str) -> CodeResult:
        now = self._monotonic()
        with self._lock:
            while self._failures and now - self._failures[0] >= FAILURE_WINDOW_S:
                self._failures.popleft()
            if len(self._failures) >= MAX_FAILURES:
                return "locked"
            verifier = self._verifier
            if verifier is None:
                return "no_code"
            # Counted as a failure before the hash runs, so parallel guesses cannot each
            # start an Argon2 run (19 MiB each) past the limit; taken back if it fits.
            self._failures.append(now)
        c = normalize_offline_code(code)
        ok = len(c) == OFFLINE_CODE_LENGTH and all(ch in PAIRING_CODE_ALPHABET for ch in c)
        if ok:
            with self._argon:  # one at a time: the Pi's memory belongs to capture
                try:
                    Argon2id.verify_phc_encoded(c.encode(), verifier)
                except (InvalidKey, ValueError):
                    ok = False
        if ok:
            with self._lock, contextlib.suppress(ValueError):
                self._failures.remove(now)
            return "ok"
        return "wrong"

    # --- sessions ------------------------------------------------------------------------
    def open_session(self, method: Method, sub: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[_hash(token)] = {"method": method, "sub": sub, "at": int(self._clock())}
            self._save()
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            s = self._sessions.get(_hash(token))
        if s is None:
            return False
        at = s.get("at")
        return isinstance(at, int) and self._clock() - at < SESSION_TTL_S

    def close_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            if self._sessions.pop(_hash(token), None) is not None:
                self._save()

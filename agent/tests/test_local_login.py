"""Local login of a paired camera (SPEC §7.4) and the hub's part in it (SPEC §6.2, §6.5)."""

from __future__ import annotations

import http.client
import json
import os
import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.web.auth import FAILURE_WINDOW_S, NONCE_TTL_S, LocalAuth, check_verifier
from allskyhub_agent.web.server import WebServer
from allskyhub_protocol import (
    HubKey,
    LocalLoginClaims,
    b64url,
    encode_local_login_claims,
    local_login_signing_payload,
)

DEVICE = "k44i7rl6cuaek5yufvr7oh3c5i"
CODE = "ABCD-EFGH-JKMN"


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def _verifier(code: str = "ABCDEFGHJKMN", memory: int = 19456, iterations: int = 2) -> str:
    kdf = Argon2id(
        salt=os.urandom(16), length=32, iterations=iterations, lanes=1, memory_cost=memory
    )
    return kdf.derive_phc_encoded(code.encode())


@pytest.fixture(scope="module")
def verifier() -> str:
    return _verifier()


class Hub:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes_raw()
        self.hub_key = HubKey(kid="k1", public_key=b64url(raw))

    def assertion(
        self,
        nonce: str,
        aud: str = DEVICE,
        iat: int = 1_800_000_000,
        exp: int | None = None,
        kid: str = "k1",
    ) -> str:
        claims = LocalLoginClaims(
            kid=kid,
            aud=aud,
            sub="user-1",
            nonce=nonce,
            iat=iat,
            exp=iat + 60 if exp is None else exp,
        )
        payload = encode_local_login_claims(claims)
        sig = self.key.sign(local_login_signing_payload(payload))
        return f"{payload}.{b64url(sig)}"


def make_auth(tmp_path: Path) -> tuple[LocalAuth, Clock, Clock]:
    wall, mono = Clock(), Clock()
    auth = LocalAuth(tmp_path / "local_auth.json", DEVICE, clock=wall, monotonic=mono)
    auth.mark_paired()
    return auth, wall, mono


# --- LocalAuth ------------------------------------------------------------------------


def test_weak_or_foreign_verifier_is_refused() -> None:
    # SPEC §6.5: other algorithms and parameters below m=19456, t=2 are invalid_args.
    with pytest.raises(ValueError, match="too weak"):
        check_verifier(_verifier(memory=8192))
    with pytest.raises(ValueError, match="Argon2id"):
        check_verifier("$argon2i$v=19$m=19456,t=2,p=1$c2FsdA$aGFzaA")
    # ... and above m=65536, t=10, p=4: one try must not take the Pi's memory or minutes.
    with pytest.raises(ValueError, match="too costly"):
        check_verifier("$argon2id$v=19$m=1048576,t=2,p=1$c2FsdHNhbHQ$aGFzaGhhc2g")
    with pytest.raises(ValueError, match="too costly"):
        check_verifier("$argon2id$v=19$m=19456,t=50,p=1$c2FsdHNhbHQ$aGFzaGhhc2g")


def test_offline_code_any_case_with_or_without_hyphens(tmp_path: Path, verifier: str) -> None:
    auth, _, _ = make_auth(tmp_path)
    assert auth.check_code(CODE) == "no_code"
    auth.set_verifier(verifier)
    assert auth.check_code("abcd-efgh-jkmn") == "ok"
    assert auth.check_code("ABCDEFGHJKMN") == "ok"
    assert auth.check_code("ABCD-EFGH-JKMP") == "wrong"


def test_five_failures_lock_the_code_for_15_minutes(tmp_path: Path, verifier: str) -> None:
    auth, _, mono = make_auth(tmp_path)
    auth.set_verifier(verifier)
    for _ in range(5):
        assert auth.check_code("WRONG") == "wrong"
    assert auth.check_code(CODE) == "locked"  # even the right code
    mono.t += FAILURE_WINDOW_S
    assert auth.check_code(CODE) == "ok"


def test_parallel_guesses_cannot_pass_the_limit(
    tmp_path: Path, verifier: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security review 2026-10-13: each guess is counted before Argon2 runs, and only one
    Argon2 runs at a time, so a burst of requests cannot fill the Pi's memory."""
    from cryptography.exceptions import InvalidKey

    from allskyhub_agent.web import auth as auth_module

    auth, _, _ = make_auth(tmp_path)
    auth.set_verifier(verifier)
    release = threading.Event()
    running: list[int] = []
    peak: list[int] = [0]

    def slow(*_: object) -> None:
        running.append(1)
        peak[0] = max(peak[0], len(running))
        release.wait(5)
        running.pop()
        raise InvalidKey

    class SlowArgon:
        verify_phc_encoded = staticmethod(slow)

    monkeypatch.setattr(auth_module, "Argon2id", SlowArgon)
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(auth.check_code(CODE)))
               for _ in range(12)]  # fmt: skip
    for t in threads:
        t.start()
    time.sleep(0.2)
    release.set()
    for t in threads:
        t.join(5)
    assert results.count("wrong") == 5
    assert results.count("locked") == 7
    assert peak[0] == 1


def test_new_verifier_ends_code_sessions_only(tmp_path: Path, verifier: str) -> None:
    auth, _, _ = make_auth(tmp_path)
    auth.set_verifier(verifier)
    by_code, by_hub = auth.open_session("code"), auth.open_session("hub", "user-1")
    auth.set_verifier(None)
    assert not auth.valid(by_code)
    assert auth.valid(by_hub)


def test_state_survives_a_restart_in_a_private_file(tmp_path: Path, verifier: str) -> None:
    auth, wall, mono = make_auth(tmp_path)
    auth.set_verifier(verifier)
    auth.set_hub_keys([Hub().hub_key])
    token = auth.open_session("code")
    path = tmp_path / "local_auth.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token not in path.read_text()  # only a hash is stored
    again = LocalAuth(path, DEVICE, clock=wall, monotonic=mono)
    assert again.paired
    assert again.has_code
    assert again.valid(token)


def test_sessions_expire_after_30_days(tmp_path: Path) -> None:
    auth, wall, _ = make_auth(tmp_path)
    token = auth.open_session("hub", "user-1")
    wall.t += 30 * 24 * 3600
    assert not auth.valid(token)


def test_unpairing_forgets_everything(tmp_path: Path, verifier: str) -> None:
    auth, _, _ = make_auth(tmp_path)
    auth.set_verifier(verifier)
    auth.set_hub_keys([Hub().hub_key])
    token = auth.open_session("code")
    auth.clear()
    assert not auth.paired
    assert not auth.has_code
    assert not auth.valid(token)
    assert json.loads((tmp_path / "local_auth.json").read_text())["hub_keys"] == []


def test_valid_assertion_gives_the_user(tmp_path: Path) -> None:
    auth, _, _ = make_auth(tmp_path)
    hub = Hub()
    auth.set_hub_keys([hub.hub_key])
    nonce = auth.new_nonce()
    assert auth.check_assertion(hub.assertion(nonce), nonce, time_trusted=True) == "user-1"
    # Each nonce once.
    assert auth.check_assertion(hub.assertion(nonce), nonce, time_trusted=True) is None


@pytest.mark.parametrize(
    "case", ["other_device", "cookie", "unknown_nonce", "kid", "signature", "expired", "old_nonce"]
)
def test_invalid_assertions_are_refused(tmp_path: Path, case: str) -> None:
    auth, wall, mono = make_auth(tmp_path)
    hub = Hub()
    auth.set_hub_keys([hub.hub_key])
    nonce = auth.new_nonce()
    cookie = nonce
    assertion = hub.assertion(nonce)
    if case == "other_device":
        assertion = hub.assertion(nonce, aud="a" * 26)
    elif case == "cookie":
        cookie = auth.new_nonce()
    elif case == "unknown_nonce":
        nonce = cookie = b64url(os.urandom(32))
        assertion = hub.assertion(nonce)
    elif case == "kid":
        assertion = hub.assertion(nonce, kid="k2")
    elif case == "signature":
        assertion = Hub().assertion(nonce)
    elif case == "expired":
        wall.t += 61
    elif case == "old_nonce":
        mono.t += NONCE_TTL_S
    assert auth.check_assertion(assertion, cookie, time_trusted=True) is None


def test_expiry_is_not_checked_without_trusted_time(tmp_path: Path) -> None:
    # SPEC §7.4 A step 5: without NTP the nonce alone keeps the assertion fresh.
    auth, wall, _ = make_auth(tmp_path)
    hub = Hub()
    auth.set_hub_keys([hub.hub_key])
    nonce = auth.new_nonce()
    wall.t -= 86400  # the clock is a day behind
    assert auth.check_assertion(hub.assertion(nonce), nonce, time_trusted=False) == "user-1"


# --- web server -----------------------------------------------------------------------


class Client:
    def __init__(self, port: int) -> None:
        self.port = port
        self.cookies: dict[str, str] = {}

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"cam.local:{self.port}", **(headers or {})}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        data = None
        if body is not None:
            data = urlencode(body).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        r.read()
        for name, value in r.getheaders():
            if name.lower() == "set-cookie":
                k, v = value.split(";", 1)[0].split("=", 1)
                if "Max-Age=0" in value:
                    self.cookies.pop(k, None)
                else:
                    self.cookies[k] = v
        return r


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[WebServer, LocalAuth, PairingState]]:
    pairing = PairingState(DEVICE, "https://hub.example", "sim", "0.1.0")
    auth = LocalAuth(tmp_path / "local_auth.json", DEVICE)
    pairing.watch(auth.mark_paired, auth.clear)
    web = WebServer(LiveState(), "127.0.0.1", 0, pairing, auth=auth)
    web.start()
    yield web, auth, pairing
    web.stop()


def test_unpaired_camera_needs_no_login(server: tuple[WebServer, LocalAuth, PairingState]) -> None:
    web, _, _ = server
    assert Client(web.port).request("GET", "/api/status").status == 200


def test_paired_camera_needs_a_session(server: tuple[WebServer, LocalAuth, PairingState]) -> None:
    web, _, pairing = server
    pairing.set_paired()
    c = Client(web.port)
    assert c.request("GET", "/api/status").status == 401
    assert c.request("GET", "/api/setup").status == 200  # the app's onboarding
    r = c.request("GET", "/")
    assert r.status == 303
    assert r.getheader("Location") == f"http://cam.local:{web.port}/local-login"
    assert c.request("GET", "/local-login").status == 200


def test_offline_code_login_and_logout(
    server: tuple[WebServer, LocalAuth, PairingState], verifier: str
) -> None:
    web, auth, pairing = server
    pairing.set_paired()
    auth.set_verifier(verifier)
    c = Client(web.port)
    r = c.request("POST", "/local-login/code", {"code": "abcd-efgh-jkmp"})
    assert r.getheader("Location", "").endswith("/local-login?e=wrong")
    c.request("POST", "/local-login/code", {"code": CODE})
    assert "ash_session" in c.cookies
    assert c.request("GET", "/api/status").status == 200
    c.request("POST", "/local-login/logout")
    assert c.request("GET", "/api/status").status == 401


def test_single_sign_on_round_trip(server: tuple[WebServer, LocalAuth, PairingState]) -> None:
    web, auth, pairing = server
    pairing.set_paired()
    hub = Hub()
    auth.set_hub_keys([hub.hub_key])
    c = Client(web.port)
    r = c.request("GET", "/local-login/start")
    target = urlsplit(r.getheader("Location", ""))
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        f"https://hub.example/cameras/{DEVICE}/local-login"
    )
    q = parse_qs(target.query)
    assert q["return"] == [f"http://cam.local:{web.port}/"]
    nonce = q["nonce"][0]
    assert c.cookies["ash_login_nonce"] == nonce
    assert c.request("GET", "/local-login/callback").status == 200
    # The callback page posts the assertion from the fragment, same-origin (SPEC §7.4 A 3).
    r = c.request("POST", "/local-login/callback", {"assertion": hub.assertion(nonce)})
    assert r.getheader("Location") == f"http://cam.local:{web.port}/"
    assert "ash_login_nonce" not in c.cookies
    assert c.request("GET", "/api/status").status == 200


def test_reverse_proxy_on_loopback_sets_base_and_secure(
    server: tuple[WebServer, LocalAuth, PairingState], verifier: str
) -> None:
    web, auth, pairing = server
    pairing.set_paired()
    auth.set_verifier(verifier)
    c = Client(web.port)
    proxy = {"Host": "cam.example.org", "X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/hub"}
    r = c.request("GET", "/", headers=proxy)
    assert r.getheader("Location") == "https://cam.example.org/hub/local-login"
    r = c.request("POST", "/local-login/code", {"code": CODE}, headers=proxy)
    cookie = next(v for k, v in r.getheaders() if k == "Set-Cookie" and "ash_session" in v)
    assert "Secure" in cookie
    assert "Path=/hub/" in cookie
    assert "HttpOnly" in cookie


def test_unpairing_locks_out_again(
    server: tuple[WebServer, LocalAuth, PairingState], verifier: str
) -> None:
    web, auth, pairing = server
    pairing.set_paired()
    auth.set_verifier(verifier)
    c = Client(web.port)
    c.request("POST", "/local-login/code", {"code": CODE})
    pairing.set_unpaired("ABC234", 900, 0.0)
    assert not auth.has_code
    pairing.set_paired()
    assert c.request("GET", "/api/status").status == 401


def test_page_files_and_design_are_open(server: tuple[WebServer, LocalAuth, PairingState]) -> None:
    """The login page needs its look before anyone is logged in (SPEC §7.4)."""
    web, _, pairing = server
    pairing.set_paired()
    c = Client(web.port)
    for path in (
        "/app.css",
        "/theme.js",
        "/favicon.svg",
        "/static/design/tokens.css",
        "/static/design/fonts/space-grotesk.woff2",
    ):
        assert c.request("GET", path).status == 200, path
    assert c.request("GET", "/static/design/../auth.py").status == 404
    assert c.request("GET", "/files/20261012/x.jpg").status == 303  # stored files need a login

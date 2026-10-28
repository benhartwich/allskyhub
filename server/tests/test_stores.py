"""App store accounts and release builds from the admin page (admin/stores.py)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import pkcs12
from fastapi import FastAPI

from allskyhub_server.admin import appstore, stores
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, csrf_from, make_account

ISSUER = "69a6de70-03db-47e3-e053-5b8c7c11a4d1"


def _play_json() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "allskyhub-store",
            "client_email": "codemagic@allskyhub-store.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    ).encode()


def _p8() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_uploads_are_checked() -> None:
    _, meta = stores.parse_play(_play_json())
    assert meta == {
        "client_email": "codemagic@allskyhub-store.iam.gserviceaccount.com",
        "project_id": "allskyhub-store",
    }
    with pytest.raises(stores.StoreError):
        stores.parse_play(b"{nope")
    with pytest.raises(stores.StoreError):
        stores.parse_play(json.dumps({"type": "authorized_user"}).encode())

    secret, meta = stores.parse_asc(_p8(), "2x9r4hxf34", ISSUER.upper())
    assert meta == {"key_id": "2X9R4HXF34", "issuer_id": ISSUER}
    assert secret.startswith(b"-----BEGIN PRIVATE KEY-----")
    with pytest.raises(stores.StoreError, match="Key-ID"):
        stores.parse_asc(_p8(), "short", ISSUER)
    with pytest.raises(stores.StoreError, match="Issuer"):
        stores.parse_asc(_p8(), "2X9R4HXF34", "nope")
    with pytest.raises(stores.StoreError, match=r"\.p8"):
        stores.parse_asc(b"not a key", "2X9R4HXF34", ISSUER)

    assert stores.parse_codemagic("  abcdefghijklmnopqrstuvwxyz0123  ")[0] == (
        b"abcdefghijklmnopqrstuvwxyz0123"
    )
    with pytest.raises(stores.StoreError):
        stores.parse_codemagic("has spaces in it, sure")


def test_the_upload_keystore_opens_with_its_password() -> None:
    secret, meta = stores.make_android_upload()
    data = json.loads(secret)
    p12 = pkcs12.load_pkcs12(base64.b64decode(data["p12"]), data["password"].encode())
    assert p12.key is not None
    assert p12.cert is not None
    assert p12.cert.friendly_name == b"upload"  # the alias Gradle asks for
    assert meta["valid_until"] > "2055"
    assert len(meta["sha256"].split(":")) == 32


def test_es256_tokens_verify() -> None:
    p8 = _p8()
    token = stores.jwt_es256(p8, "2X9R4HXF34", {"iss": ISSUER})
    head, body, sig = token.split(".")

    def pad(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    assert json.loads(pad(head)) == {"alg": "ES256", "kid": "2X9R4HXF34", "typ": "JWT"}
    raw = pad(sig)
    key = serialization.load_pem_private_key(p8, password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    der = encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:]))
    key.public_key().verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))


class FakeServices:
    """Google, Apple and Codemagic as far as the setup talks to them."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, Any]] = []
        self.patched: list[str] = []
        self.imported: dict[str, Any] = {}
        self.bundle = False
        self.builds = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body: Any = json.loads(request.content) if request.content.startswith(b"{") else None
        self.seen.append((request.method, url, body))
        path = request.url.path
        if url == "https://oauth2.googleapis.com/token":
            assert b"jwt-bearer" in request.content
            return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3600})
        if path == "/androidpublisher/v3/applications/org.allskyhub.app/edits":
            return httpx.Response(200, json={"id": "edit-1"})
        if path.endswith("/edits/edit-1"):
            return httpx.Response(204)
        if request.url.host == "api.appstoreconnect.apple.com":
            assert request.headers["authorization"].startswith("Bearer ey")
            if path == "/v1/bundleIds" and request.method == "GET":
                return httpx.Response(200, json={"data": []})
            if path == "/v1/bundleIds":
                self.bundle = True
                return httpx.Response(201, json={"data": {"id": "B1"}})
            if path == "/v1/apps":
                return httpx.Response(200, json={"data": []})
        if request.url.host in ("codemagic.io", "api.codemagic.io"):
            assert request.headers["x-auth-token"] == "abcdefghijklmnopqrstuvwxyz0123"
            if path == "/api/v3/user/apps":
                return httpx.Response(200, json={"data": [{"id": "x", "name": "other"}]})
            if path == "/api/v3/user/teams":
                return httpx.Response(200, json={"data": [{"id": "T1", "name": "Sternwarte"}]})
            if path == "/apps":
                assert body == {
                    "repositoryUrl": "https://github.com/benhartwich/allskyhub.git",
                    "teamId": "T1",
                }
                return httpx.Response(200, json={"_id": "APP1", "appName": "allskyhub"})
            if path == "/api/v3/apps/APP1/variable-groups" and request.method == "GET":
                return httpx.Response(200, json={"data": []})
            if path == "/api/v3/apps/APP1/variable-groups":
                return httpx.Response(201, json={"data": {"id": "G1", "name": "allskyhub_store"}})
            if path == "/api/v3/variable-groups/G1/variables" and request.method == "GET":
                old = [
                    {"id": "V1", "name": "ALLSKYHUB_KEY_ALIAS", "value": None, "secure": True},
                    {"id": "V2", "name": "CERTIFICATE_PRIVATE_KEY", "value": None, "secure": True},
                ]
                return httpx.Response(200, json={"data": old})
            if path == "/api/v3/variable-groups/G1/variables/V1":
                assert request.method == "DELETE"  # Android keys left Codemagic
                self.patched.append("deleted")
                return httpx.Response(204)
            if path == "/api/v3/variable-groups/G1/variables/V2":
                self.patched.append(body["value"][:31])
                return httpx.Response(200, json={"data": {}})
            if path == "/api/v3/variable-groups/G1/variables":
                self.imported = body
                return httpx.Response(201, json={"data": []})
            if path == "/api/v3/apps/APP1/builds":
                self.builds += 1
                assert body == {"workflow_id": "ios-release", "branch": "main"}
                return httpx.Response(202, json={"data": {"id": "BUILD1"}})
            if path == "/api/v3/builds/BUILD1":
                return httpx.Response(200, json={"data": {"id": "BUILD1", "status": "building"}})
        return httpx.Response(404, json={"message": f"unexpected {path}"})


async def test_setup_registers_everything(tmp_path: Path) -> None:
    fake = FakeServices()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub", client)

    report = await hub.setup("admin@example.org")
    assert [s.state for s in report.steps] == ["ok", "ok", "warn", "warn", "warn"]
    assert not fake.seen  # nothing uploaded yet: no calls at all
    assert oct((tmp_path / "store").stat().st_mode & 0o777) == "0o700"
    assert oct((tmp_path / "store" / "android_upload.secret").stat().st_mode & 0o777) == "0o600"
    fingerprint = hub.vault.meta("android_upload")
    assert fingerprint
    assert fingerprint["sha256"]

    hub.vault.put("play", *stores.parse_play(_play_json()), by="admin@example.org")
    hub.vault.put("asc", *stores.parse_asc(_p8(), "2X9R4HXF34", ISSUER), by="admin@example.org")
    hub.vault.put(
        "codemagic",
        *stores.parse_codemagic("abcdefghijklmnopqrstuvwxyz0123"),
        by="admin@example.org",
    )
    report = await hub.setup("admin@example.org")
    states = {s.title: s.state for s in report.steps}
    assert states == {
        "Google-Play-Dienstkonto": "ok",
        "Bundle-ID": "ok",
        "App-Store-Connect-API-Schlüssel": "warn",  # the app record is made by hand
        "Codemagic-API-Token": "ok",
        "Codemagic-Gruppe allskyhub_store": "ok",
    }, report.steps
    assert report.ok
    assert fake.bundle
    assert (
        "DELETE",
        stores.PLAY + "/applications/org.allskyhub.app/edits/edit-1",
        None,
    ) in fake.seen
    assert fake.patched == ["deleted", "-----BEGIN RSA PRIVATE KEY-----"]
    assert fake.imported["secure"] is True
    names = {v["name"] for v in fake.imported["variables"]}
    assert names == {
        "APP_STORE_CONNECT_PRIVATE_KEY",
        "APP_STORE_CONNECT_KEY_IDENTIFIER",
        "APP_STORE_CONNECT_ISSUER_ID",
    }
    # The reports name variables, never their values.
    text = " ".join(s.text for s in report.steps)
    assert "PRIVATE KEY-----" not in text
    assert (hub.vault.meta("codemagic") or {})["app_id"] == "APP1"

    assert await hub.build("ios", "admin@example.org") == "BUILD1"
    builds = await hub.recent_builds()
    assert builds[0]["status"] == "building"
    assert stores.build_url(builds[0]) == "https://codemagic.io/app/APP1/build/BUILD1"


async def test_a_play_app_without_any_upload_is_a_warning(tmp_path: Path) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(404, json={"error": {"message": "Package not found"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    hub = stores.Stores(tmp_path, "benhartwich/allskyhub", client)
    hub.vault.put("play", *stores.parse_play(_play_json()), by="a")
    report = await hub.setup("a")
    play = next(s for s in report.steps if s.title == "Google-Play-Dienstkonto")
    assert play.state == "warn"
    assert "von Hand" in play.text


async def test_admin_uploads_keys_and_never_sees_them_again(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    monkeypatch.setattr(app.state, "stores", stores.Stores(tmp_path, "benhartwich/allskyhub"))
    page = await client.get("/admin")
    assert "Apps: Android und iOS" in page.text
    token = csrf_from(page.text)
    play = _play_json()

    r = await client.post(
        "/admin/stores/play",
        data={"csrf_token": token, "password": "wrong password"},
        files={"file": ("key.json", play, "application/json")},
    )
    assert r.status_code == 400
    assert not (tmp_path / "play.secret").exists()

    r = await client.post(
        "/admin/stores/play",
        data={"csrf_token": token, "password": PASSWORD},
        files={"file": ("key.json", play, "application/json")},
    )
    assert r.status_code == 200, r.text
    assert "codemagic@allskyhub-store.iam.gserviceaccount.com" in r.text
    assert "PRIVATE KEY" not in r.text
    assert (tmp_path / "play.secret").read_bytes() == play

    r = await client.post(
        "/admin/stores/asc",
        data={"csrf_token": token, "password": PASSWORD, "key_id": "nope", "issuer_id": ISSUER},
        files={"file": ("AuthKey.p8", _p8(), "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "Key-ID" in r.text

    r = await client.post(
        "/admin/stores/codemagic",
        data={"csrf_token": token, "password": PASSWORD, "token": "abcdefghijklmnopqrstuvwxyz0123"},
    )
    assert r.status_code == 200
    assert "abcdefghijklmnopqrstuvwxyz0123" not in r.text

    r = await client.post("/admin/stores/other", data={"csrf_token": token, "password": PASSWORD})
    assert r.status_code == 404

    # Building waits for the setup.
    r = await client.post("/admin/stores-build", data={"csrf_token": token, "platform": "ios"})
    assert r.status_code == 400
    assert "einrichten" in r.text


async def test_others_cannot_upload(app: FastAPI, client: httpx.AsyncClient) -> None:
    await make_account(client)
    page = await client.get("/account")
    r = await client.post(
        "/admin/stores/codemagic",
        data={"csrf_token": csrf_from(page.text), "password": PASSWORD, "token": "x" * 20},
    )
    assert r.status_code == 404


def _play_answers(seen: list[tuple[str, str]], track_status: int = 200) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        path = request.url.path
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        if path.endswith("/applications/org.allskyhub.app/edits"):
            return httpx.Response(200, json={"id": "E1"})
        if path.endswith("/edits/E1/bundles"):
            assert path.startswith("/upload/")
            assert request.url.params["uploadType"] == "media"
            assert request.content == b"bundle"
            return httpx.Response(200, json={"versionCode": 412})
        if path.endswith("/edits/E1/tracks/internal"):
            release = json.loads(request.content)["releases"][0]
            assert release["versionCodes"] == ["412"]
            if release["status"] == "completed" and track_status == 400:
                msg = "Only releases with status draft may be created on draft app."
                return httpx.Response(400, json={"error": {"message": msg}})
            return httpx.Response(200, json={})
        if path.endswith("/edits/E1:commit"):
            return httpx.Response(200, json={"id": "E1"})
        return httpx.Response(404, json={"error": {"message": "Package not found"}})

    return httpx.MockTransport(answer)


async def test_play_upload(tmp_path: Path) -> None:
    aab = tmp_path / "allskyhub-412.aab"
    aab.write_bytes(b"bundle")
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub")
    assert (await hub.play_upload(aab))[0] == 1  # no service account yet
    hub.vault.put("play", *stores.parse_play(_play_json()), by="a")

    seen: list[tuple[str, str]] = []
    hub._client = httpx.AsyncClient(transport=_play_answers(seen))  # pyright: ignore[reportPrivateUsage]
    code, text = await hub.play_upload(aab)
    assert code == 0
    assert text == "Version 412 ist im internen Test."
    assert seen[-1] == (
        "POST",
        "/androidpublisher/v3/applications/org.allskyhub.app/edits/E1:commit",
    )

    # A draft app (never published) takes draft releases only.
    hub._client = httpx.AsyncClient(transport=_play_answers(seen, 400))  # pyright: ignore[reportPrivateUsage]
    code, text = await hub.play_upload(aab)
    assert code == 0
    assert "Entwurf" in text

    # Google does not know the app yet: the first bundle goes by hand.
    def unknown(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(404, json={"error": {"message": "Package not found"}})

    hub._client = httpx.AsyncClient(transport=httpx.MockTransport(unknown))  # pyright: ignore[reportPrivateUsage]
    code, text = await hub.play_upload(aab)
    assert code == 3
    assert "von Hand" in text


async def test_android_release_through_the_spool(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    out = tmp_path / "out"  # the root helper's own folder
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub", release_out=out)
    monkeypatch.setattr(app.state, "stores", hub)
    token = csrf_from((await client.get("/admin")).text)
    build = {"csrf_token": token, "platform": "android"}

    r = await client.post("/admin/stores-build", data=build)
    assert r.status_code == 400  # no upload key yet
    hub.ensure_keys(stores.Report(), email)
    r = await client.post("/admin/stores-build", data=build)
    assert r.status_code == 200, r.text
    spool = tmp_path / "android-release"
    assert (spool / "request").read_text() == f"{email}\n"
    assert hub.android_job().busy
    assert "angefordert" in r.text
    assert "Android: Release bauen" not in r.text  # one at a time
    assert (await client.post("/admin/stores-build", data=build)).status_code == 400

    assert [p.name for p in spool.iterdir()] == ["request"]  # all the hub writes there

    # The root helper took the request, built and could not upload yet; it answers in
    # its own folder, never in the hub's.
    (spool / "request").unlink()
    out.mkdir()
    (out / "state").write_text("built\n")
    (out / "log").write_text("allskyhub-412.aab gebaut.\nGoogle kennt die App noch nicht.\n")
    (out / "allskyhub-412.aab").write_bytes(b"bundle")
    latest = {"build": 412, "version": "1.0.0", "commit": "abc1234", "size": 6}
    (out / "latest.json").write_text(json.dumps({**latest, "file": "allskyhub-412.aab"}))
    # An Android build from Codemagic's time is labelled as Android, not iOS.
    old = {"id": "CM1", "app_id": "A1", "platform": "android", "at": "2026-10-16T03:33:00+00:00"}
    hub.vault.add_build({**old, "by": email})
    page = await client.get("/admin")
    assert 'build/CM1">Android</a> <span class="muted small">(Codemagic)' in page.text
    assert "gebaut, erstes Hochladen von Hand" in page.text
    assert "Google kennt die App noch nicht." in page.text
    assert 'href="/admin/stores-android/allskyhub-412.aab"' in page.text
    r = await client.get("/admin/stores-android/allskyhub-412.aab")
    assert r.status_code == 200
    assert r.content == b"bundle"
    assert (await client.get("/admin/stores-android/latest.json")).status_code == 404
    assert (await client.get("/admin/stores-android/..%2Fstore%2Fplay.secret")).status_code == 404


def _png(width: int, height: int) -> bytes:
    """Enough of a PNG for its size: signature and IHDR."""
    ihdr = width.to_bytes(4) + height.to_bytes(4) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + len(ihdr).to_bytes(4) + b"IHDR" + ihdr + b"\x00" * 4


def test_screenshots_must_fit_play(tmp_path: Path) -> None:
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub")
    repo = hub.screenshots()  # the repository's, made from the app (app/test/store)
    assert [p.parent.name for p in repo] == ["screenshots"] * len(repo)
    assert 2 <= len(repo) <= 8
    for path in repo:
        size, _ = stores.image_size(path.read_bytes())
        assert size is not None
        short, long = sorted(size)
        assert short >= 320
        assert long <= min(3840, 2 * short), path.name
    with pytest.raises(stores.StoreError, match="2 bis 8"):
        hub.put_screenshots([_png(1080, 1920)])
    with pytest.raises(stores.StoreError, match="Bild 2 passt nicht"):
        hub.put_screenshots([_png(1080, 1920), _png(1080, 2400)])  # longer than 2:1
    with pytest.raises(stores.StoreError, match="kein PNG"):
        hub.put_screenshots([_png(1080, 1920), b"GIF89a"])
    hub.put_screenshots([_png(1080, 1920)] * 3)
    assert [p.name for p in hub.screenshots()] == ["01.png", "02.png", "03.png"]
    hub.put_screenshots([_png(1080, 1920)] * 2)
    assert len(hub.screenshots()) == 2  # replaced, not added


async def test_push_listing(tmp_path: Path) -> None:
    seen: list[tuple[str, str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((request.method, path, request.headers.get("content-type", "")))
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t"})
        if path.endswith("/edits"):
            return httpx.Response(200, json={"id": "E1"})
        if path.endswith("/edits/E1/details"):
            assert json.loads(request.content)["contactEmail"] == stores.CONTACT_EMAIL
            return httpx.Response(200, json={})
        if path.endswith(("/listings/de-DE", "/listings/en-US")) and request.method == "PUT":
            texts = json.loads(request.content)
            assert len(texts["title"]) <= 30
            assert len(texts["shortDescription"]) <= 80
            assert len(texts["fullDescription"]) <= 4000
            return httpx.Response(200, json=texts)
        if request.method == "DELETE":
            return httpx.Response(200, json={"deleted": []})
        if "/listings/" in path and request.method == "POST":
            assert path.startswith("/upload/")
            return httpx.Response(200, json={"image": {"id": "I"}})
        if path.endswith("/edits/E1:commit"):
            return httpx.Response(200, json={})
        return httpx.Response(500, json={"error": {"message": f"unexpected {path}"}})

    hub = stores.Stores(
        tmp_path / "store",
        "benhartwich/allskyhub",
        httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )
    hub.vault.put("play", *stores.parse_play(_play_json()), by="a")
    real = hub.listing
    bare = tmp_path / "listing"  # a listing without screenshots
    for f in [
        "icon.png",
        *(
            f"{lang}/{n}"
            for lang in stores.LANGUAGES
            for n in ("title.txt", "short_description.txt", "full_description.txt", "feature.png")
        ),
    ]:
        (bare / f).parent.mkdir(parents=True, exist_ok=True)
        (bare / f).write_bytes((real / f).read_bytes())
    hub.listing = bare
    report = await hub.push_listing()
    assert [(s.title, s.state) for s in report.steps] == [
        ("Store-Eintrag de-DE", "ok"),
        ("Store-Eintrag en-US", "ok"),
        ("Screenshots", "warn"),
    ], report.steps
    assert "allskyhub – Allsky-Kamera" in report.steps[0].text

    hub.listing = real  # with the repository's screenshots
    seen.clear()
    report = await hub.push_listing()
    assert report.ok
    assert len(report.steps) == 2
    uploads = [(p.rsplit("/", 1)[1], t) for m, p, t in seen if m == "POST" and "/listings/" in p]
    shots = len(hub.screenshots())
    assert uploads.count(("phoneScreenshots", "image/png")) == 2 * shots  # per language

    hub.put_screenshots([_png(1080, 1920)] * 2)  # uploaded ones take their place
    seen.clear()
    await hub.push_listing()
    uploads = [(p.rsplit("/", 1)[1], t) for m, p, t in seen if m == "POST" and "/listings/" in p]
    assert uploads.count(("phoneScreenshots", "image/png")) == 4
    assert ("featureGraphic", "image/png") in uploads
    assert ("icon", "image/png") in uploads


async def test_listing_runs_in_the_background(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A push takes longer than nginx waits (60 s): the page answers at once and shows
    the result once it is there."""
    gate = asyncio.Event()

    async def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            await gate.wait()
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(404, json={"error": {"message": "Package not found"}})

    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    hub = stores.Stores(
        tmp_path / "store", "benhartwich/allskyhub",
        httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )  # fmt: skip
    hub.vault.put("play", *stores.parse_play(_play_json()), by="a")
    monkeypatch.setattr(app.state, "stores", hub)
    token = csrf_from((await client.get("/admin")).text)

    r = await client.post("/admin/stores-listing", data={"csrf_token": token})
    assert r.status_code == 200
    assert "gestartet" in r.text
    assert "läuft …" in (await client.get("/admin")).text
    again = await client.post("/admin/stores-listing", data={"csrf_token": token})
    assert again.status_code == 409

    gate.set()
    for _ in range(100):
        state = hub.listing_state()
        if state and not state.get("running"):
            break
        await asyncio.sleep(0.01)
    page = (await client.get("/admin")).text
    assert "läuft …" not in page
    assert "erst das erste App Bundle von Hand hochladen" in page

    # Running in the file but no task here: the hub restarted during a push.
    hub.vault.write_json("listing.json", {"at": "2026-10-16T07:39:00+00:00", "running": True})
    assert "neu gestartet" in (await client.get("/admin")).text


async def test_a_failing_listing_push_says_so(tmp_path: Path) -> None:
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub")

    async def boom() -> stores.Report:
        raise RuntimeError("bug")

    hub.push_listing = boom  # type: ignore[method-assign]
    assert hub.start_listing("a")
    state = hub.listing_state()
    for _ in range(100):
        state = hub.listing_state()
        if state and not state.get("running"):
            break
        await asyncio.sleep(0.01)
    assert state
    assert state["steps"][0]["text"] == "Interner Fehler, siehe Log des Hubs."


async def test_push_appstore(tmp_path: Path) -> None:
    """Texts, screenshots and the build to App Store Connect (admin/appstore.py)."""
    calls: list[tuple[str, str]] = []
    created: dict[str, list[dict[str, Any]]] = {}
    shots: dict[str, int] = {}
    version_state = ["PREPARE_FOR_SUBMISSION"]
    beta_contact: list[dict[str, Any]] = []
    busy_review: list[bool] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        calls.append((method, path))
        if request.url.host == "upload.example":
            return httpx.Response(200)
        assert request.headers["authorization"].startswith("Bearer ")
        body: Any = json.loads(request.content) if request.content else None
        if method == "POST" and path == "/v1/betaAppReviewSubmissions" and busy_review:
            detail = "Another build in the same train is already in beta review."
            return httpx.Response(422, json={"errors": [{"detail": detail}]})
        if method == "POST" and path.endswith("/relationships/builds"):
            assert body["data"] == [{"type": "builds", "id": "B1"}]
            return httpx.Response(204)
        if method == "POST":
            kind = path.rsplit("/", 1)[1]
            created.setdefault(kind, []).append(body["data"].get("attributes", {}))
            n = len(created[kind])
            data: dict[str, Any] = {
                "id": f"{kind}-{n}",
                "attributes": body["data"].get("attributes", {}),
            }
            if kind == "betaGroups":
                data["attributes"] = {
                    **data["attributes"],
                    "publicLink": "https://tf.example/join/X",
                }
            if kind == "appScreenshots":
                size = body["data"]["attributes"]["fileSize"]
                data["attributes"] = {
                    "uploadOperations": [
                        {
                            "method": "PUT",
                            "url": f"https://upload.example/{n}",
                            "offset": 0,
                            "length": size,
                            "requestHeaders": [{"name": "Content-Type", "value": "image/png"}],
                        }
                    ]
                }
            return httpx.Response(201, json={"data": data})
        if method == "PATCH" and path == "/v1/ageRatingDeclarations/AGE":
            # Only the questions Apple has; none that it does not know.
            assert body["data"]["attributes"] == {"violenceRealistic": "NONE", "gambling": False}
        if method == "PATCH" and path == "/v1/appInfos/INFO":
            rel = body["data"]["relationships"]
            assert rel["primaryCategory"]["data"]["id"] == "WEATHER"
        if method == "PATCH" and path == "/v1/betaAppReviewDetails/APP":
            # Only what the beta details lack; their own contact e-mail stays.
            beta_contact.append(body["data"]["attributes"])
            if version_state[0] == "REJECTED":
                detail = "'s3cret-pass' is not a valid value for 'demoAccountPassword'"
                return httpx.Response(
                    422, json={"errors": [{"code": "ENTITY_ERROR", "detail": detail}]}
                )
        if method == "PATCH" and "/appScreenshots/" in path:
            assert body["data"]["attributes"]["uploaded"] is True
            assert len(body["data"]["attributes"]["sourceFileChecksum"]) == 32
            shots[path] = 1
        if method in ("PATCH", "DELETE"):
            return httpx.Response(
                204 if method == "DELETE" or "relationships" in path else 200, json={"data": {}}
            )
        found: list[dict[str, Any]] = []
        if path == "/v1/apps":
            assert request.url.params["filter[bundleId]"] == stores.PACKAGE
            found = [{"id": "APP"}]
        elif path.endswith("/appInfos"):
            found = [{"id": "INFO", "attributes": {"appStoreState": "PREPARE_FOR_SUBMISSION"}}]
        elif path.endswith("/appInfoLocalizations"):
            found = [{"id": "INFO-DE", "attributes": {"locale": "de-DE"}}]
        elif path.endswith("/appStoreVersions"):
            found = [
                {
                    "id": "V1",
                    "attributes": {
                        "versionString": "1.0",
                        "appVersionState": version_state[0],
                    },
                }
            ]
        elif path.endswith("/appStoreVersionLocalizations"):
            found = [{"id": "VL-DE", "attributes": {"locale": "de-DE"}}]
        elif path.endswith("/appScreenshotSets"):
            found = []
        elif path.endswith("/appScreenshots"):
            found = [{"id": "OLD"}]
        elif path.endswith("/appStoreReviewDetail"):
            attrs = {
                "contactEmail": "ben@example.org",
                "contactPhone": "+49 1",
                "demoAccountName": "reviewer@example.org",
                "demoAccountPassword": "s3cret-pass",
                "demoAccountRequired": True,
            }
            return httpx.Response(200, json={"data": {"id": "RD", "attributes": attrs}})
        elif path.endswith("/betaAppReviewDetail"):
            attrs = {"contactEmail": "beta@example.org", "demoAccountName": None}
            return httpx.Response(200, json={"data": {"id": "APP", "attributes": attrs}})
        elif path.endswith("/ageRatingDeclaration"):
            attrs = {"violenceRealistic": None, "gambling": None, "somethingNew": None}
            return httpx.Response(200, json={"data": {"id": "AGE", "attributes": attrs}})
        elif path == "/v1/builds":
            found = [
                {"id": "B1", "attributes": {"version": "300", "usesNonExemptEncryption": None}}
            ]
        return httpx.Response(200, json={"data": found})

    hub = stores.Stores(
        tmp_path / "store",
        "benhartwich/allskyhub",
        httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )
    report = await appstore.push_report(hub)
    assert [(s.title, s.state) for s in report.steps] == [("App Store", "warn")]  # no key yet

    hub.vault.put("asc", *stores.parse_asc(_p8(), "2X9R4HXF34", ISSUER), by="a")
    report = await appstore.push_report(hub)
    assert [(s.title, s.state) for s in report.steps] == [
        ("Name und Untertitel", "ok"),
        ("Kategorie", "ok"),
        ("Altersfreigabe", "ok"),
        ("Texte und Screenshots de-DE", "ok"),
        ("Texte und Screenshots en-US", "ok"),
        ("Build", "ok"),
        ("TestFlight-Link", "ok"),
        ("TestFlight-Prüfung", "ok"),
        ("Noch in App Store Connect", "warn"),
    ], report.steps
    # The public TestFlight group, made once, with the build in it and sent to review.
    assert report.steps[6].text == "https://tf.example/join/X"
    assert created["betaGroups"][0]["publicLinkEnabled"] is True
    assert ("POST", "/v1/betaGroups/betaGroups-1/relationships/builds") in calls
    assert [a["locale"] for a in created["betaAppLocalizations"]] == ["de-DE", "en-US"]
    assert [a["locale"] for a in created["betaBuildLocalizations"]] == ["de-DE", "en-US"]
    assert len(created["betaAppReviewSubmissions"]) == 1
    # German existed and was changed; English was made.
    assert ("PATCH", "/v1/appInfoLocalizations/INFO-DE") in calls
    assert [a["locale"] for a in created["appInfoLocalizations"]] == ["en-US"]
    assert created["appInfoLocalizations"][0]["name"] == "allskyhub – All-Sky Camera"
    for a in [*created["appInfoLocalizations"]]:
        assert len(a["name"]) <= 30
        assert len(a["subtitle"]) <= 30
    assert [a["locale"] for a in created["appStoreVersionLocalizations"]] == ["en-US"]
    english = created["appStoreVersionLocalizations"][0]
    assert len(english["keywords"]) <= 100
    assert len(english["promotionalText"]) <= 170
    assert "German for now" not in english["description"]
    assert {a["screenshotDisplayType"] for a in created["appScreenshotSets"]} == {"APP_IPHONE_67"}
    per_language = len(appstore.screenshots("de-DE"))
    assert per_language >= 3
    assert len(created["appScreenshots"]) == len(shots) == 2 * per_language
    assert calls.count(("DELETE", "/v1/appScreenshots/OLD")) == 2
    assert ("PATCH", "/v1/builds/B1") in calls  # export compliance answered
    assert ("PATCH", "/v1/appStoreVersions/V1/relationships/build") in calls
    assert "appStoreVersions" not in created  # Apple's own 1.0 is used

    # While Apple reviews 1.0 it stays as it is (no new version: Apple says 409), but the
    # build still goes to the public TestFlight group.
    version_state[0] = "WAITING_FOR_REVIEW"
    calls.clear()
    report = await appstore.push_report(hub)
    assert [(s.title, s.state) for s in report.steps] == [
        ("Name und Untertitel", "ok"),
        ("Kategorie", "ok"),
        ("Altersfreigabe", "ok"),
        ("Version", "warn"),
        ("Build", "ok"),
        ("TestFlight-Link", "ok"),
        ("TestFlight-Prüfung", "ok"),
        ("Noch in App Store Connect", "warn"),
    ], report.steps
    assert "appStoreVersions" not in created
    assert not [c for c in calls if "appStoreVersion" in c[1] and c[0] != "GET"]
    assert ("POST", "/v1/betaGroups/betaGroups-2/relationships/builds") in calls
    # The App Review's contact and test account filled the empty beta fields, both times.
    assert beta_contact[-1] == {
        "contactPhone": "+49 1",
        "demoAccountName": "reviewer@example.org",
        "demoAccountPassword": "s3cret-pass",
        "demoAccountRequired": True,
    }
    assert len(beta_contact) == 2
    assert all("s3cret" not in s.text for s in report.steps)

    # An earlier build of the same version still in beta review: no error, just wait.
    busy_review.append(True)
    report = await appstore.push_report(hub)
    step = next(s for s in report.steps if s.title == "TestFlight-Prüfung")
    assert (step.state, "früherer Build" in step.text) == ("ok", True), step
    busy_review.clear()

    # Apple refusing a copied value: its answer may quote it, so the report does not.
    version_state[0] = "REJECTED"
    report = await appstore.push_report(hub)
    step = next(s for s in report.steps if s.title == "TestFlight-Prüfung")
    assert step.state == "warn"
    assert "HTTP 422" in step.text
    assert all("s3cret" not in s.text for s in report.steps)


async def test_a_further_app_shares_the_keys(tmp_path: Path) -> None:
    """Benjamin, 2026-10-28: bortle uses the same Apple key, Codemagic token and
    distribution certificate; only bundle id, repository and variable group differ."""
    seen: list[tuple[str, str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body: Any = json.loads(request.content) if request.content.startswith(b"{") else None
        path = request.url.path
        seen.append((request.method, path, body))
        if request.url.host == "api.appstoreconnect.apple.com":
            if path == "/v1/bundleIds" and request.method == "GET":
                assert request.url.params["filter[identifier]"] == "eu.bortle.app"
                return httpx.Response(200, json={"data": []})
            if path == "/v1/bundleIds":
                return httpx.Response(201, json={"data": {"id": "B2"}})
            if path == "/v1/apps":
                return httpx.Response(200, json={"data": []})
        if path == "/api/v3/user/apps":
            return httpx.Response(200, json={"data": [{"id": "APP1", "name": "allskyhub"}]})
        if path == "/api/v3/user/teams":
            return httpx.Response(200, json={"data": [{"id": "T1", "name": "Sternwarte"}]})
        if path == "/apps":
            return httpx.Response(200, json={"_id": "APP2"})
        if path == "/api/v3/apps/APP2/variable-groups" and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path == "/api/v3/apps/APP2/variable-groups":
            return httpx.Response(201, json={"data": {"id": "G2"}})
        if path == "/api/v3/variable-groups/G2/variables" and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        if path == "/api/v3/variable-groups/G2/variables":
            return httpx.Response(201, json={"data": []})
        if path == "/api/v3/apps/APP2/builds":
            return httpx.Response(202, json={"data": {"id": "BUILD2"}})
        return httpx.Response(404, json={"message": f"unexpected {path}"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    hub = stores.Stores(tmp_path, "benhartwich/allskyhub", client)
    hub.vault.put("asc", *stores.parse_asc(_p8(), "2X9R4HXF34", ISSUER), by="a")
    hub.vault.put("codemagic", *stores.parse_codemagic("abcdefghijklmnopqrstuvwxyz0123"), by="a")
    hub.vault.update_meta("codemagic", app_id="APP1")
    bortle = stores.OTHER_APPS["bortle"]

    report = await hub.setup_other(bortle, "a")
    states = {s.title: s.state for s in report.steps}
    assert states == {
        "Schlüssel des iOS-Zertifikats": "ok",
        "Bundle-ID": "ok",
        "App-Store-Connect-API-Schlüssel": "warn",
        "Codemagic-API-Token": "ok",
        "Codemagic-Gruppe bortle_store": "ok",
    }, report.steps
    assert ("POST", "/v1/bundleIds", {"data": {"type": "bundleIds", "attributes": {
        "identifier": "eu.bortle.app", "name": "bortle", "platform": "IOS"}}}) in seen  # fmt: skip
    assert ("POST", "/apps", {"repositoryUrl": "https://github.com/benhartwich/bortle-app.git",
                              "teamId": "T1"}) in seen  # fmt: skip
    assert ("POST", "/api/v3/apps/APP2/variable-groups", {"name": "bortle_store"}) in seen
    names = {
        v["name"]
        for m, p, b in seen
        if m == "POST" and p == "/api/v3/variable-groups/G2/variables"
        for v in b["variables"]
    }
    assert names == {
        "APP_STORE_CONNECT_PRIVATE_KEY",
        "APP_STORE_CONNECT_KEY_IDENTIFIER",
        "APP_STORE_CONNECT_ISSUER_ID",
        "CERTIFICATE_PRIVATE_KEY",
    }
    meta = hub.vault.meta("codemagic") or {}
    assert (meta["app_id"], meta["bortle_app_id"]) == ("APP1", "APP2")  # allskyhub's stays
    assert "Neue App, Bundle-ID eu.bortle.app, Name bortle" in " ".join(
        s.text for s in report.steps
    )

    assert await hub.build("ios", "a", app=bortle) == "BUILD2"
    assert ("POST", "/api/v3/apps/APP2/builds", {"workflow_id": "ios-release",
                                                 "branch": "main"}) in seen  # fmt: skip
    assert hub.vault.builds()[0]["app"] == "bortle"


async def test_admin_page_offers_the_further_app(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    monkeypatch.setattr(app.state, "stores", stores.Stores(tmp_path, "benhartwich/allskyhub"))
    page = await client.get("/admin")
    assert "Weitere App: bortle (iOS)" in page.text
    assert "bortle: iOS-Release bauen" not in page.text  # only after the setup
    token = csrf_from(page.text)
    r = await client.post(
        "/admin/stores-other", data={"csrf_token": token, "app": "nope", "action": "setup"}
    )
    assert r.status_code == 400
    r = await client.post(
        "/admin/stores-other", data={"csrf_token": token, "app": "bortle", "action": "build"}
    )
    assert r.status_code == 400
    assert "einrichten" in r.text


async def test_push_capability_on_and_old_profiles_gone(tmp_path: Path) -> None:
    """Stage 5: push for the bundle id; profiles made before it lack it and go."""
    seen: list[tuple[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((request.method, path))
        if path == "/v1/bundleIds":
            attrs = {"identifier": "org.allskyhub.app"}
            return httpx.Response(200, json={"data": [{"id": "B1", "attributes": attrs}]})
        if path == "/v1/bundleIds/B1/bundleIdCapabilities":
            caps = [{"attributes": {"capabilityType": "IN_APP_PURCHASE"}}]
            return httpx.Response(200, json={"data": caps})
        if path == "/v1/bundleIdCapabilities":
            body = json.loads(request.content)
            assert body["data"]["attributes"]["capabilityType"] == "PUSH_NOTIFICATIONS"
            return httpx.Response(201, json={"data": {"id": "C1"}})
        if path == "/v1/bundleIds/B1/profiles":
            return httpx.Response(200, json={"data": [{"id": "P1"}, {"id": "P2"}]})
        if path.startswith("/v1/profiles/"):
            return httpx.Response(204)
        if path == "/v1/apps":
            return httpx.Response(200, json={"data": [{"id": "A1"}]})
        return httpx.Response(404, json={"message": path})

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    hub = stores.Stores(tmp_path, "benhartwich/allskyhub", client)
    hub.vault.put("asc", *stores.parse_asc(_p8(), "2X9R4HXF34", ISSUER), by="a")
    report = await hub.setup("a")
    step = next(s for s in report.steps if s.title == "Push-Benachrichtigungen")
    assert step.state == "ok"
    assert "2 alte Profile gelöscht" in step.text
    assert ("DELETE", "/v1/profiles/P1") in seen
    assert ("DELETE", "/v1/profiles/P2") in seen

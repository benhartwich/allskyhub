"""The last German-only bits on English pages (owner decision 2026-11-27: every page in
German and English): the camera's local sign-in and offline code, the reporter sign-up,
the camera page's sky tiles, the admin's store and GitHub messages, the sky layers'
cache and the binding German text of the privacy policy and the terms."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from allskyhub_server import i18n
from allskyhub_server.admin import deploy, stores
from allskyhub_server.devices import sky_now
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import Device
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, app_state, csrf_from, home_csrf, make_account
from tests.test_english_owner import EN, english
from tests.test_reports import _Turnstile  # pyright: ignore[reportPrivateUsage]
from tests.test_sky_history import NIGHT as SKY_NIGHT
from tests.test_sky_history import _setup as sky_setup  # pyright: ignore[reportPrivateUsage]
from tests.test_sky_now import ORIENTATION, SKY
from tests.ws_helpers import paired_token

NONCE = "n0nce-" + "B" * 30


@contextmanager
def in_english() -> Generator[None]:
    """The request's language set to English, as i18n.LanguageMiddleware would."""
    token = i18n._english.set(True)  # pyright: ignore[reportPrivateUsage]
    try:
        yield
    finally:
        i18n._english.reset(token)  # pyright: ignore[reportPrivateUsage]


# --- 1. Local sign-in, offline code, reporter sign-up ---------------------------------


async def test_local_login_and_offline_code_in_english(client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    url = f"/cameras/{dev.device_id}/local-login"
    query = {"return": "https://cam.example.org/", "nonce": NONCE}
    page = await client.get(url, params={**query, **EN})
    english(page, "Sign in at the camera", "wants to sign you in with your allskyhub account",
            "This address is not in a home network", "Sign in at https://cam.example.org",
            "Cancel",
            absent=("Anmelden", "Abbrechen", "Adresse", "Heimnetz", "Bestätige"))  # fmt: skip
    bad = await client.get(url, params={**query, "nonce": "short", **EN})
    assert bad.status_code == 400
    assert "Invalid sign-in request from the camera." in bad.text

    shown = await client.post(
        f"/cameras/{dev.device_id}/local-access",
        params=EN,
        data={"csrf_token": await home_csrf(client)},
    )
    english(shown, "Offline code", "It is shown only now.", "has nothing to do with your password",
            "Done", absent=("Offline-Code", "Fertig", "Passwort", "Kamera"))  # fmt: skip

    # German stays German.
    german = await client.get(url, params={**query, "lang": "de"})
    assert "An der Kamera anmelden" in german.text
    assert "nicht in einem Heimnetz" in german.text
    bad = await client.get(url, params={**query, "nonce": "short"})
    assert "Ungültige Anmeldeanfrage der Kamera." in bad.text


@pytest.fixture
def reporter_signup(
    app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> MemoryMailer:
    monkeypatch.setattr(settings, "turnstile_site_key", "site")
    monkeypatch.setattr(settings, "turnstile_secret", "secret")
    app.state.turnstile = _Turnstile()
    mailer: MemoryMailer = app.state.mailer
    return mailer


async def test_reporter_signup_in_english(
    client: httpx.AsyncClient, reporter_signup: MemoryMailer
) -> None:
    german_words = ("Konto", "Melder-Konto", "Passwort", "Datenschutzerklärung", "bestätigen")
    form = await client.get("/melden/konto", params=EN)
    english(form, "Create a reporter account", "For reporting sky brightness",
            "Password (at least 10 characters)", "I have read the", 'data-language="en"',
            absent=german_words)  # fmt: skip
    data = {"csrf_token": csrf_from(form.text), "email": "nordheim@example.org",
            "password": PASSWORD, "cf-turnstile-response": "ok"}  # fmt: skip
    # The cookie keeps English for the form's answers.
    english(await client.post("/melden/konto", data=data), "Please confirm the privacy policy.")
    bot = await client.post("/melden/konto", data={**data, "privacy": "true",
                                                   "cf-turnstile-response": "nope"})  # fmt: skip
    english(bot, "The check against bots did not work.")
    sent = await client.post("/melden/konto", data={**data, "privacy": "true"})
    english(sent, "Almost done: an email with a link is on its way.", absent=german_words)
    mail = reporter_signup.sent[-1]
    assert mail.subject == "allskyhub: your reporter account"
    assert "this link confirms your reporter account" in mail.body
    link = re.search(r"/melden/bestaetigen\?\S+", mail.body)
    assert link
    assert "lang=en" in link[0]

    asked = await client.get(link[0])
    english(asked, "Confirm your reporter account", "One more click", absent=german_words)
    token = re.search(r'name="token" value="([^"]+)"', asked.text)
    assert token
    done = await client.post(
        "/melden/bestaetigen", data={"csrf_token": csrf_from(asked.text), "token": token[1]}
    )
    english(done, "Account confirmed", "Take part → Report the sky brightness",
            absent=german_words)  # fmt: skip
    gone = await client.get("/melden/bestaetigen", params={"token": "nope"})
    english(gone, "Invalid link", "Create again", absent=("Neu", "Link ungültig"))

    german = await client.get("/melden/konto", params={"lang": "de"})
    assert "Melder-Konto anlegen" in german.text
    assert 'data-language="de"' in german.text


# --- 2. The camera page's sky tiles ----------------------------------------------------


def test_sky_now_in_english() -> None:
    status = {"sky": SKY, "orientation": {**ORIENTATION, "north_deg": 0.0}}
    with in_english():
        now = sky_now.sky_now(status)
        measured = sky_now.flat({"flat": {"source": "twilight", "max_gain": 1.4}})
        night = sky_now.flat({"flat": {"source": "night"}})
        none = sky_now.flat(
            {"flat": {"source": None, "reason": "Objektiv korrigiert schon selbst"}}
        )
        off = sky_now.flat({"settings": {"flat_correction": "off"}})
    assert now is not None
    assert {c[0] for c in now.compass} == {"N", "E", "S", "W"}
    assert now.cells[0].title == "N, 15–40°: 90 % cloudy"
    assert now.cells[2].title.startswith("E, ")
    assert now.cells[5].title == "SW, 15–40°: cannot be judged"
    assert measured is not None
    assert (measured.text, measured.detail) == (
        "Flat measured",
        "twilight",
    )
    assert night is not None
    assert (night.text, night.detail) == (
        "Flat estimated", "from the night images, for the picture only")  # fmt: skip
    # The agent's reason is its own German text (SPEC §6.3) and stays as sent.
    assert none is not None
    assert (none.text, none.detail) == ("no flat", "Objektiv korrigiert schon selbst")
    assert off is not None
    assert off.text == "Flat off"
    # German unchanged.
    german = sky_now.sky_now(status)
    assert german is not None
    assert {c[0] for c in german.compass} == {"N", "O", "S", "W"}
    assert german.cells[2].title.startswith("O, ")


async def test_live_sky_tiles_in_english(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    flat = {"source": "twilight", "max_gain": 1.42}
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"sky": SKY, "orientation": ORIENTATION, "flat": flat},
                    latest_image_at=dt.datetime.now(dt.UTC))
        )  # fmt: skip
        await db.commit()
    live = (await client.get(f"/cameras/{dev.device_id}/live", params=EN)).text
    assert "Flat measured" in live
    assert "twilight" in live
    assert "N, 15–40°: 90 % cloudy" in live
    assert ">E</text>" in live
    for german in ("bewölkt", "Dämmerung", "Flat gemessen", ">O</text>", "beurteilbar"):
        assert german not in live, german


# --- 3. Store messages shown at once ---------------------------------------------------


def test_store_messages_follow_the_language(tmp_path: Path) -> None:
    with pytest.raises(stores.StoreError, match=r"^Das ist keine JSON-Datei\.$"):
        stores.parse_play(b"nope")
    with in_english():
        with pytest.raises(stores.StoreError, match=r"^This is no JSON file\.$"):
            stores.parse_play(b"nope")
        with pytest.raises(stores.StoreError, match="Google Play takes 2 to 8 screenshots"):
            stores.Stores(tmp_path / "store", "benhartwich/allskyhub").put_screenshots([])
        busy = stores.Stores(tmp_path / "busy", "benhartwich/allskyhub")
        busy.release_dir.mkdir(parents=True)
        (busy.release_dir / "request").write_text("someone\n")
        app = next(a for a in stores.OTHER_APPS.values() if a.test_apk)
        with pytest.raises(stores.StoreError, match="An Android build is already running"):
            busy.request_test_apk("someone", app)


async def test_store_upload_error_in_english(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.test_admin import FakeGitHub

    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    monkeypatch.setitem(
        app_state(app), "stores", stores.Stores(tmp_path / "store", "benhartwich/allskyhub")
    )
    monkeypatch.setitem(app_state(app), "github", FakeGitHub())
    page = await client.get("/admin", params=EN)
    r = await client.post(
        "/admin/stores/github",
        data={"csrf_token": csrf_from(page.text), "password": PASSWORD, "token": "nope"},
        follow_redirects=True,
    )
    assert r.status_code == 400
    assert "This does not look like a GitHub token" in r.text
    assert "Das sieht nicht" not in r.text


# --- 4. GitHub's failure, kept as a code -------------------------------------------------


async def test_github_failure_is_kept_as_a_code() -> None:
    def limited(request: httpx.Request) -> httpx.Response:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793191305"}
        return httpx.Response(403, headers=headers)

    github = deploy.GitHub(
        "benhartwich/allskyhub", httpx.AsyncClient(transport=httpx.MockTransport(limited))
    )
    with pytest.raises(deploy.DeployError, match=r"Abrufgrenze .* wieder ab 12:41 UTC"):
        await github.upstream(None)
    failed = github._failed  # pyright: ignore[reportPrivateUsage]
    assert failed is not None
    assert (failed[1].code, failed[1].with_token) == ("limit", False)
    # The next admin within the minute reads it in English, from the kept code.
    with in_english(), pytest.raises(deploy.DeployError, match=r"limit .* again from 12:41"):
        await github.upstream(None)
    with pytest.raises(deploy.DeployError, match="Abrufgrenze"):
        await github.upstream(None)

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    github = deploy.GitHub(
        "benhartwich/allskyhub", httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    with in_english(), pytest.raises(deploy.DeployError, match="GitHub cannot be reached"):
        await github.upstream(None)
    with pytest.raises(deploy.DeployError, match="GitHub ist gerade nicht erreichbar"):
        await github.upstream(None)


# --- 5. Sky layers' cache, the binding German text ------------------------------------------


async def test_sky_layers_vary_by_cookie(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await sky_setup(app, client)
    r = await client.get(f"/cameras/{device_id}/himmel/{SKY_NIGHT}/f0.jpg.json")
    assert r.status_code == 200
    assert r.headers["vary"] == "Cookie"
    assert r.headers["cache-control"] == "private, max-age=300"
    r = await client.get(
        f"/cameras/{device_id}/himmel/{SKY_NIGHT}/zeit", params={"at": "2026-10-15T23:00:00+00:00"}
    )
    assert r.status_code == 200
    assert r.headers["vary"] == "Cookie"


@pytest.mark.parametrize(
    ("path", "german"),
    [
        ("/datenschutz", "<h1>Datenschutzerklärung</h1>"),
        ("/agb", "4,99 € im Monat oder 49 € im Jahr"),
    ],
)
async def test_german_original_keeps_the_language(
    client: httpx.AsyncClient, path: str, german: str
) -> None:
    page = await client.get(path, params=EN)
    assert f'href="{path}?original=1"' in page.text
    assert f'href="{path}?lang=de"' not in page.text
    original = await client.get(path, params={"original": "1"})
    assert '<html lang="de">' in original.text
    assert german in original.text
    assert "This is a translation" not in original.text
    assert "ash_lang" not in original.headers.get("set-cookie", "")
    # The visitor's language stays English.
    again = await client.get(path)
    assert '<html lang="en">' in again.text

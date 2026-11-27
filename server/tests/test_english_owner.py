"""The signed-in pages in English too (owner decision 2026-11-27: every page in German and
English): the camera page and its parts, account, pairing, public page, streams, Plus,
the admin pages, the privacy policy and the terms, with ``?lang=en`` (web/lang.py)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.admin import stores
from allskyhub_server.auth import accounts
from allskyhub_server.devices import image_url
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import AircraftPass, Device, Frame, Product
from allskyhub_server.settings import Settings
from allskyhub_server.web import templating
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, app_state, csrf_from, home_csrf, make_account
from tests.test_admin import FakeGitHub
from tests.test_station_pages import _jpeg, _new  # pyright: ignore[reportPrivateUsage]
from tests.test_storms import STORM, flash
from tests.test_web_events import EVENT, record
from tests.test_web_events import NIGHT as EVENT_NIGHT
from tests.ws_helpers import JPEG, paired_token

EN = {"lang": "en"}


def english(page: httpx.Response, *sentences: str, absent: tuple[str, ...] = ()) -> str:
    """The page is English, has the sentences and none of the German words."""
    assert page.status_code in (200, 400, 404), page.status_code
    text = page.text
    assert '<html lang="en"' in text
    for sentence in sentences:
        assert sentence in text, sentence
    for word in absent:
        assert not re.search(rf"\b{re.escape(word)}\b", text), word
    return text


async def _camera(app: FastAPI, client: httpx.AsyncClient) -> str:
    """A paired camera at a made-up place (50.04/10.39) with a full status."""
    dev = FakeDevice()
    await paired_token(client, dev)
    now = dt.datetime.now(dt.UTC)
    status = {
        "time_trusted": False, "disk_free_pct": 40, "sensor_temp_c": 21.5, "mode": "night",
        "exposure_us": 20_000_000, "gain": 200,
        "env": {"temperature_c": 4.2, "humidity_pct": 91, "dewpoint_c": 2.9},
        "heater": {"on": True, "reason": "dew"},
        "lightning_sensor": {"strikes_30min": 3, "nearest_km": 12},
        "settings": {"latitude": 50.04, "longitude": 10.39, "timezone": "Europe/Berlin"},
    }  # fmt: skip
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                last_status=status,
                last_seen_at=now - dt.timedelta(minutes=3),
                last_frame={"captured_at": now.isoformat(), "sun_elevation": -20.0},
                latest_image_at=now - dt.timedelta(minutes=3),
            )
        )
        await db.commit()
    return dev.device_id


async def test_camera_page_in_english(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    base = f"/cameras/{device_id}"
    await client.post(
        f"{base}/public", data={"enabled": "true", "csrf_token": await home_csrf(client)}
    )
    english(
        await client.get(base, params=EN),
        "My cameras", "Readings", "Statistics", "Gallery", "Health", "Satellites",
        "Settings", "Apply settings", "Latitude", "Time zone", "Detect automatically",
        "Device", "Paired", "Sharing", "Email address", "Camera network", "Aircraft by day",
        "What goes to the channel", "Thunderstorms (once per storm)", "Create an offline code",
        "Public page", "Save visibility", "For streams (OBS)", "Remove camera",
        "Try it free for a month",
        absent=("Einstellungen", "Speichern", "Gerät", "Teilen", "Kameranetz", "Entfernen",
                "Zustand", "Satelliten", "Öffentliche", "Galerie", "Statistik"),
    )  # fmt: skip
    english_live = (await client.get(f"{base}/live", params=EN)).text
    for sentence in ("Current sky image", "Last seen", "Exposure", "At the housing",
                     "dew point", "Dome heater", "on: risk of dew", "3 strikes in 30 min",
                     "Free storage", "not synchronised", "Sun altitude", "3 min ago"):  # fmt: skip
        assert sentence in english_live, sentence
    for word in ("Zuletzt", "Belichtung", "Taupunkt", "Kuppelheizung", "Blitze", "Uhr"):
        assert word not in english_live, word


async def test_camera_messages_in_english(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    base = f"/cameras/{device_id}"
    csrf = await home_csrf(client)
    wrong = await client.post(f"{base}/settings", params=EN,
                              data={"csrf_token": csrf, "latitude": "x"})  # fmt: skip
    assert wrong.status_code == 400
    english(wrong, "Please give latitude and longitude as numbers (both or neither).")
    form = {"csrf_token": csrf, "email": "nobody@example.org"}
    nobody = await client.post(f"{base}/members", params=EN, data=form)
    assert nobody.status_code == 400
    assert "Es gibt kein Konto" not in nobody.text
    english(await client.get(base, params={**EN, "plus": "trial_used"}),
            "Your account has already used the free trial month.")  # fmt: skip
    # German stays as it was.
    german = await client.get(base, params={"lang": "de", "plus": "trial_used"})
    assert '<html lang="de">' in german.text
    assert "Den kostenlosen Probemonat hat dein Konto schon genutzt." in german.text
    assert "Einstellungen übernehmen" in german.text


async def test_owner_pages_in_english(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    base = f"/cameras/{device_id}"
    csrf = await home_csrf(client)
    await client.post(f"{base}/public", data={"enabled": "true", "csrf_token": csrf})
    english(
        await client.get("/", params=EN),
        "My cameras", "Pair a camera", "Last seen 3 min ago", "SharedSky stations", "The app",
        "Download on the",
        absent=("Kameras", "Zuletzt", "Laden"),
    )  # fmt: skip
    english(
        await client.get("/account", params=EN),
        "Signed in as", "Keeping events", "30 days (default)", "1 year", "Change password",
        "Delete account", "Yes, delete my account for good",
        absent=("Konto", "Passwort", "Tage", "Speichern"),
    )  # fmt: skip
    english(
        await client.get("/cameras/pair", params=EN),
        "Pair a camera", "Pairing code", "Camera name",
        absent=("Kopplungscode", "Koppeln"),
    )  # fmt: skip
    english(
        await client.get(f"{base}/public/edit", params=EN),
        "Public page", "Texts", "Sections", "Current image", "Aurora outlook (Kp index)",
        "Your own domain", "Use domain", "On your own website", "camera map",
        absent=("Abschnitte", "Speichern", "Objektiv", "Kamerakarte", "Startseite"),
    )  # fmt: skip
    english(
        await client.get(f"{base}/stream", params=EN),
        "For streams (OBS)", "No address yet", "Allow satellites and weather balloons",
        "Create an address",
        absent=("Adresse", "Satelliten"),
    )  # fmt: skip
    form = {"csrf_token": csrf, "place_layers": "true"}
    made = await client.post(f"{base}/stream", params=EN, data=form, follow_redirects=True)
    text = english(made, "Your address", "Layers", "Constellations", "Red light", "Copy",
                   absent=("Ebenen", "Sternbilder", "Kopieren"))  # fmt: skip
    token = re.search(r"/obs/([A-Za-z0-9_-]+)\?ebenen", text)
    assert token
    obs = await client.get(f"/obs/{token.group(1)}/rahmen", params=EN)
    assert "Uhr" not in obs.text
    english(await client.get("/cameras/nope", params=EN), "Not found.", "To the start page")
    assert (await client.get("/cameras/nope", params={"lang": "de"})).status_code == 404


async def test_filters_follow_the_language(client: httpx.AsyncClient) -> None:
    """age, stamp, night_title, filesize and heater_text read the request's language."""
    from allskyhub_server import i18n

    token = i18n._english.set(True)  # pyright: ignore[reportPrivateUsage]
    try:
        assert templating.night_title("20261006") == "Tue 06.10. → Wed 07.10."
        assert templating.filesize(3 * 1024 * 1024 // 2) == "1.5 MB"
        assert templating.heater_text({"on": True, "reason": "humidity"}) == "on: high humidity"
        assert templating.age(None) == "never"
        assert templating.event_titles()["aurora"] == "Aurora"
    finally:
        i18n._english.reset(token)  # pyright: ignore[reportPrivateUsage]
    assert templating.night_title("20261006") == "Di 06.10. → Mi 07.10."
    assert templating.filesize(3 * 1024 * 1024 // 2) == "1,5 MB"
    assert templating.event_titles()["aurora"] == "Polarlicht"


NIGHT = "20261006"


async def _seed(app: FastAPI, device_id: str) -> str:
    """A frame, a keogram, a meteor, a storm and an aircraft by day; the pass's id."""
    store: ImageStore = app.state.images
    store.save_frame(device_id, NIGHT, "a.jpg", FrameVariant.THUMB, JPEG)
    store.save_frame(device_id, NIGHT, "a.jpg", FrameVariant.FULL, JPEG)
    keogram = store.product_path(device_id, NIGHT, "keogram.jpg", FrameVariant.FULL)
    keogram.parent.mkdir(parents=True, exist_ok=True)
    keogram.write_bytes(JPEG)
    top = dt.datetime(2026, 10, 7, 11, 0, tzinfo=dt.UTC)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(
            Frame(device_id=device_id, night_id=NIGHT, name="a.jpg",
                  captured_at=dt.datetime(2026, 10, 6, 20, 15, tzinfo=dt.UTC), mode="night",
                  exposure_us=30_000_000, gain=100, mean=0.2, sun_elevation=-20,
                  has_full=True, has_thumb=True)
        )  # fmt: skip
        db.add(
            Product(device_id=device_id, night_id=NIGHT, kind="keogram", name="keogram.jpg",
                    content_type="image/jpeg", size=300_000, thumbnail=True, has_full=True)
        )  # fmt: skip
        db.add(record(device_id))
        db.add_all([flash(device_id, m, a) for m, a in ((0, 0.3), (10, 0.6), (20, 0.1))])
        found = AircraftPass(
            device_id=device_id, night_id="20261007", icao="3c6444", callsign="DLH4AB",
            registration="D-AIUA", type_code="A320", start=top - dt.timedelta(minutes=2),
            end=top + dt.timedelta(minutes=2), top_at=top, top_alt_deg=84.2, top_az_deg=95.0,
            start_alt_deg=21.0, start_az_deg=2.0, end_alt_deg=22.5, end_az_deg=181.0,
            alt_m=10_500, min_km=1.1,
        )  # fmt: skip
        db.add(found)
        await db.commit()
        return str(found.id)


def _english(text: str, *, has: list[str], lacks: list[str]) -> None:
    assert '<html lang="en">' in text
    for words in has:
        assert words in text, words
    for words in lacks:
        assert words not in text, words


async def test_gallery_pages_in_english(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    pass_id = await _seed(app, dev.device_id)
    base = f"/cameras/{dev.device_id}"

    async def page(path: str, **params: str) -> str:
        r = await client.get(base + path, params={**EN, **params})
        assert r.status_code == 200, path
        return r.text

    _english(
        await page("/gallery"),
        has=["Gallery", "Timelapses &amp; keograms", "1 image", "1 product", "events"],
        lacks=["Galerie", "Produkt", "Ereignis", "Himmelsdaten"],
    )
    _english(
        await page(f"/nights/{NIGHT}"),
        has=["← Gallery", "Image at 22:15", "View the sky over these images"],
        lacks=["Aufnahme", " Uhr", "Ereignisse", "Himmel über"],
    )
    _english(
        await page(f"/products/{NIGHT}/keogram.jpg"),
        has=["<h1>Keogram</h1>"],
        lacks=["Keogramm", "Wird von der Kamera"],
    )
    _english(
        await page("/events"),
        has=["<h1>Events</h1>", "Overflights", "are hidden", "Show all"],
        lacks=["Ereignisse", "ausgeblendet", "Alle zeigen", "Überflüge"],
    )
    _english(
        await page(f"/events/{EVENT_NIGHT}/{EVENT}"),
        has=["Is the detection right?", ">Real<", "Not a meteor, but:", "Image artefact",
             "Fireball?", "Duration", "Report the fireball to the IMO"],
        # The rows (devices/event_text.event_rows) are the other branch's: German there still.
        lacks=["Stimmt die Erkennung", "Bildfehler", "sondern", "Feuerkugel", "Zeit (UTC)"],
    )  # fmt: skip
    _english(
        await page(f"/storms/{STORM}"),
        has=["<h1>Thunderstorm</h1>", "3 flashes", "Lightning satellite", "Not checked yet."],
        lacks=["Gewitter", "Blitze", "Blitzsatellit", "geprüft"],
    )
    _english(
        await page("/ueberfluege", nacht="20261007"),
        has=["<h1>Overflights</h1>", "By day: 1 aircraft", "Highest point", "km away",
             "Sky map of the day", "84° "],
        lacks=["Am Tag", "Flugzeug", "Höchster Punkt", "entfernt", "Himmelskarte"],
    )  # fmt: skip
    _english(
        await page(f"/flugzeuge/{pass_id}"),
        has=["highest point 84°", "1.1 km away", "altitude 10.5 km", "View the flight on adsb"],
        lacks=["höchster Punkt", " Uhr", "entfernt", "Flughöhe"],
    )
    _english(
        await page("/stats"),
        has=["Statistics", "Export data"],
        lacks=["Statistik", "Daten exportieren", "Nächte"],
    )
    _english(
        await page("/export"),
        has=["Data for science", "Meteors per hour", "Sky brightness per night"],
        lacks=["Wissenschaft", "Meteore pro Stunde", "Himmelshelligkeit"],
    )
    _english(
        await page("/renders"),
        has=["Timelapses & keograms", "Make timelapse", "Keogram over nights", "No jobs yet."],
        lacks=["Zeitraffer", "erstellen", "Aufträge"],
    )
    _english(
        await page("/ziel"),
        has=["Target clear?", "Please choose", "Planets"],
        lacks=["Ziel frei", "Bitte wählen", "Planeten"],
    )
    assert "I do not know this object." in await page("/ziel", objekt="X99")
    _english(
        await page("/himmel"),
        has=[
            "<h1>Sky</h1>",
            "Constellations",
            "images of this night.",
            "Night 06.10.2026 · 1 images",
        ],
        lacks=["Himmel</h1>", "Sternbilder", "Ereignisse zeigen"],
    )
    _english(
        await page("/flugzeuge"),
        has=["<h1>Aircraft</h1>", "The camera has not reported its location yet."],
        lacks=["Flugzeuge", "Standort"],
    )
    _english(
        await page("/satelliten/3d"),
        has=["Satellites 3D", "does not know its location yet"],
        lacks=["Satelliten 3D", "Standort"],
    )
    fragment = await page("/satelliten")
    assert "Visible now" in fragment
    assert "Tonight" in fragment
    assert "Jetzt sichtbar" not in fragment
    assert "Standort" not in fragment

    # German stays as it was.
    german = (await client.get(f"{base}/gallery", params={"lang": "de"})).text
    assert "<h1>Galerie</h1>" in german
    assert "1 Bild" in german
    storm = (await client.get(f"{base}/storms/{STORM}")).text
    assert "Noch nicht geprüft." in storm
    assert "3 Blitze" in storm


def _text(html: str) -> str:
    """What a reader sees: no tags, scripts or styles."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def _no_german(html: str, *words: str) -> None:
    seen = _text(html)
    assert not re.findall(r"\w*[äöüÄÖÜß]\w*", seen)
    for word in words:
        assert not re.search(rf"\b{re.escape(word)}\b", seen), word


async def _admin(
    client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> str:
    email = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", email)
    return email


async def test_admin_page_in_english(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await paired_token(new_client, FakeDevice())  # a camera for the list
    await _admin(client, settings, monkeypatch)
    monkeypatch.setitem(app_state(app), "github", FakeGitHub())
    page = await client.get("/admin", params={"lang": "en"})
    assert page.status_code == 200
    assert '<html lang="en">' in page.text
    for english in ("Running", "New since the running version", "Deploy now", "Image storage",
                    "Accounts", "Paired cameras", "Free space", "Access requests",
                    "Push and reporter sign-up", "Aurora photos to approve", "Store access",
                    "Check and set up", "Give Plus", "Google Play service account",
                    "Password to confirm", "nothing sent yet"):  # fmt: skip
        assert english in page.text, english
    _no_german(page.text, "Kamera", "Kameras", "Konto", "Konten", "Passwort", "Speichern",
               "Hochladen", "nie", "und", "Bildspeicher", "Zugangsanfragen", "fehlt",
               "verwalten", "Jetzt")  # fmt: skip
    # German stays German.
    german = await client.get("/admin", params={"lang": "de"})
    assert "Jetzt deployen" in german.text
    assert "Gekoppelte Kameras" in german.text

    # A message after a form, in English too.
    await client.get("/admin", params={"lang": "en"})
    r = await client.post(
        "/admin/plus",
        data={"csrf_token": csrf_from(page.text), "device_id": "nope"},
        follow_redirects=True,
    )
    assert "No paired camera with this ID." in r.text


async def test_accounts_in_english(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, settings, monkeypatch)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        other = await accounts.create_user(db, "nordheim@example.org", PASSWORD)
        await db.commit()
        other_id = other.id
    listed = await client.get("/admin/users", params={"lang": "en"})
    assert listed.status_code == 200
    for english in ("Accounts", "Search", "Filter", "Last active", "camera account",
                    "Invite a new account", "Send invitation", "shown"):  # fmt: skip
        assert english in listed.text, english
    _no_german(listed.text, "Konten", "Suchen", "Kameras", "angezeigt", "aktiv", "Einladung")

    page = await client.get(f"/admin/users/{other_id}")  # the cookie keeps English
    assert page.status_code == 200
    for english in ("Overview", "Last on the web", "No cameras.", "Sign out everywhere",
                    "Lock", "Delete account", "Delete for good", "Your password"):  # fmt: skip
        assert english in page.text, english
    _no_german(page.text, "Überblick", "Konto", "Passwort", "Sperren", "nie", "Kameras")

    r = await client.post(
        f"/admin/users/{other_id}/sign-out",
        data={"csrf_token": csrf_from(page.text), "password": "wrong"},
        follow_redirects=True,
    )
    assert "Your password is wrong." in r.text


async def test_apps_page_in_english(
    app: FastAPI,
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hub = stores.Stores(tmp_path / "store", "benhartwich/allskyhub", release_out=tmp_path / "out")
    monkeypatch.setitem(app_state(app), "stores", hub)
    await _admin(client, settings, monkeypatch)
    page = await client.get("/admin/apps", params={"lang": "en"})
    assert page.status_code == 200
    for english in ("What needs doing?", "Built", "Not set up yet.", "Set up", "Access",
                    "Since the last build:", "More: build again, store listings, logs",
                    "Build Android release"):  # fmt: skip
        assert english in page.text, english
    _no_german(page.text, "Gebaut", "Einrichten", "Zugänge", "Mehr", "unbekannt", "Eingereicht")
    card = await client.get("/admin/apps/card/allskyhub")
    assert "Not set up yet." in card.text


async def test_stations_and_late_in_english(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await make_account(new_client)
    fields = {"name": "Allsky Nordheim", "url": "https://cam.example.org/allsky/image.jpg",
              "latitude": "48.30", "longitude": "4.29", "elevation_m": "450",
              "timezone": "Europe/Paris", "proof_text": "Settings screenshot."}  # fmt: skip
    page = await new_client.get("/stationen/neu", params={"software": "image-url"})
    r = await new_client.post(
        "/stationen/neu",
        data={"csrf_token": csrf_from(page.text), "software": "image-url", "terms": "true",
              **fields},
        files={"proof": ("settings.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=True,
    )  # fmt: skip
    station_id = r.url.path.rsplit("/", 1)[1]
    assert _new  # the same station form as test_station_pages
    admin = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", f"{admin},{owner}x")

    async def resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})

    app.state.image_fetcher = image_url.Fetcher(
        resolve=resolve, transport=httpx.MockTransport(handler)
    )
    listed = await client.get("/admin/stationen", params={"lang": "en"})
    assert listed.status_code == 200
    for english in ("Review stations", "Owner", "Requested", "waiting for review"):
        assert english in listed.text, english
    _no_german(listed.text, "Besitzer", "Angefragt", "Stand", "Prüfung")
    detail = await client.get(f"/admin/stationen/{station_id}")
    for english in ("Request", "Proof", "The image at the address, fetched now", "Given place",
                    "Decide", "Approve", "Reject", "Reason", "Your password"):  # fmt: skip
        assert english in detail.text, english
    _no_german(detail.text, "Antrag", "Nachweis", "Entscheiden", "Freigeben", "Grund", "Passwort")
    no_reason = await client.post(
        f"/admin/stationen/{station_id}/ablehnen",
        data={"csrf_token": csrf_from(detail.text), "password": PASSWORD},
        follow_redirects=True,
    )
    assert "Please give a reason." in no_reason.text
    done = await client.post(
        f"/admin/stationen/{station_id}/freigeben",
        data={"csrf_token": csrf_from(detail.text), "password": PASSWORD},
        follow_redirects=True,
    )
    assert "Station approved." in done.text

    late = await client.get("/admin/nachgereicht", params={"lang": "en"})
    assert late.status_code == 200
    for english in ("Fireballs sent later", "Nothing sent later yet.", "Owner", "Check"):
        assert english in late.text, english
    _no_german(late.text, "Nachgereichte", "Besitzer", "Probe", "Noch")


async def test_privacy_policy_in_english(client: httpx.AsyncClient) -> None:
    page = (await client.get("/datenschutz?lang=en")).text
    assert '<html lang="en">' in page
    assert "<title>Privacy · allskyhub</title>" in page
    assert "This is a translation; the German version is binding." in page
    for english in (
        "<h1>Privacy policy</h1>",
        "<h2>Controller</h2>",
        "<h2>Storage period and deletion</h2>",
        "<h2>Recipients and hosting</h2>",
        "<h2>Your rights</h2>",
        "Art. 6(1)(f) GDPR",
        "netcup GmbH",
        "Open-Meteo",
    ):
        assert english in page
    for german in (
        "Datenschutzerklärung</h1>",
        "Verantwortlicher",
        "Welche Daten wir verarbeiten",
        "Speicherdauer und Löschung",
        "Deine Rechte",
        "Rechtsgrundlage",
        "DSGVO",
    ):
        assert german not in page


async def test_privacy_policy_stays_german_by_default(client: httpx.AsyncClient) -> None:
    page = (await client.get("/datenschutz")).text
    assert '<html lang="de">' in page
    assert "<h1>Datenschutzerklärung</h1>" in page
    assert "Art. 6 Abs. 1 lit. f DSGVO" in page
    assert "the German version is binding" not in page


async def test_terms_in_english(client: httpx.AsyncClient) -> None:
    page = await client.get("/agb", params=EN)
    english(
        page,
        "This is a translation; the German version is binding.",
        "Terms and conditions for allskyhub Plus",
        "Right of withdrawal for consumers",
        "€4.99 a month or €49 a year",
        absent=("Widerrufsrecht", "Haftung", "Kündigung", "Umsatzsteuer"),
    )
    german = await client.get("/agb", params={"lang": "de"})
    assert '<html lang="de">' in german.text
    assert "7. Widerrufsrecht für Verbraucher" in german.text
    assert "4,99 € im Monat oder 49 € im Jahr" in german.text
    assert "This is a translation" not in german.text

"""The App Store listing (Benjamin, 2026-10-25), like the Play listing in stores.py.

Name, subtitle, keywords and promotional text (app/store/appstore), the description of
the Play listing (app/store/play), the iPhone screenshots and the newest processed build
go to the iOS version in preparation, through the App Store Connect API with the hub's
key. The build also goes to an external TestFlight group with a public link. Apple's API
does not take the privacy answers, the review contact with the test account or the
price; those stay for App Store Connect itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx

from allskyhub_server.admin.stores import (
    ASC,
    CONTACT_EMAIL,
    LISTING,
    PACKAGE,
    UPLOAD_TIMEOUT,
    Report,
    StoreError,
    Stores,
    _dict,  # pyright: ignore[reportPrivateUsage]
    _list,  # pyright: ignore[reportPrivateUsage]
    _message,  # pyright: ignore[reportPrivateUsage]
    _now,  # pyright: ignore[reportPrivateUsage]
)

log = logging.getLogger(__name__)

APPSTORE = LISTING.parent / "appstore"
LOCALES = ("de-DE", "en-US")  # the first is the primary language chosen when creating the app
VERSION = "1.0.0"  # app/pubspec.yaml, for a first version Apple has not made itself
# 1290 x 2796 (app/test/store, STORE_DEVICE=iphone): Apple's 6.7" and 6.9" iPhone set.
DISPLAY_TYPE = "APP_IPHONE_67"
SUPPORT_URL = "https://allskyhub.org"
PRIVACY_URL = "https://allskyhub.org/datenschutz"
# Versions whose metadata can still be changed.
EDITABLE = frozenset(
    {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED"}
)
# Versions Apple has finished with; after them a new version can be made.
RELEASED = frozenset(
    {
        "READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "REMOVED_FROM_SALE",
        "DEVELOPER_REMOVED_FROM_SALE", "REPLACED_WITH_NEW_VERSION",
    }
)  # fmt: skip
PRIMARY_CATEGORY = "WEATHER"
SECONDARY_CATEGORY = "EDUCATION"  # astronomy
_NONE = (
    "alcoholTobaccoOrDrugUseOrReferences", "contests", "gamblingSimulated",
    "gunsOrOtherWeapons", "horrorOrFearThemes", "matureOrSuggestiveThemes",
    "medicalOrTreatmentInformation", "profanityOrCrudeHumor",
    "sexualContentGraphicAndNudity", "sexualContentOrNudity", "violenceCartoonOrFantasy",
    "violenceRealistic", "violenceRealisticProlongedGraphicOrSadistic",
)  # fmt: skip
_NO = (
    "gambling", "unrestrictedWebAccess", "lootBox", "messagingAndChat", "parentalControls",
    "ageAssurance", "userGeneratedContent", "advertising", "healthOrWellnessTopics",
)  # fmt: skip
AGE_RATING: dict[str, Any] = {**dict.fromkeys(_NONE, "NONE"), **dict.fromkeys(_NO, False)}
TITLE = "App Store"
# The external TestFlight group with the public link (Benjamin, 2026-10-26).
BETA_GROUP = "Öffentlich"
WHAT_TO_TEST = {
    "de-DE": "Anmelden, eigene oder freigegebene Kameras ansehen, Bilder, Ereignisse und "
    "die Vorhersage für heute Nacht. Rückmeldungen gern über TestFlight.",
    "en-US": "Sign in, look at your own or shared cameras, pictures, events and tonight's "
    "forecast. Feedback is welcome through TestFlight.",
}
STATE_FILE = "appstore.json"

Auth = Callable[[], dict[str, str]]


class _Api:
    """App Store Connect calls; a fresh token for each (the token lives 20 minutes)."""

    def __init__(self, client: httpx.AsyncClient, auth: Auth) -> None:
        self.client = client
        self.auth = auth

    async def call(
        self,
        method: str,
        path: str,
        what: str,
        *,
        body: Any = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        r = await self.client.request(
            method, f"{ASC}{path}", json=body, params=params, headers=self.auth()
        )
        if r.status_code >= 300:
            raise StoreError(f"{what}: {_message(r)}")
        return _dict(r.json()) if r.content else {}

    async def items(self, path: str, what: str, **params: str) -> list[dict[str, Any]]:
        return _list((await self.call("GET", path, what, params=params or None)).get("data"))

    async def upsert(
        self,
        existing: str | None,
        kind: str,
        attributes: dict[str, Any],
        create: dict[str, Any],
        what: str,
    ) -> str:
        """PATCH the object ``existing`` or POST a new one with ``create`` (locale and
        relationships); returns its id."""
        if existing is not None:
            body = {"data": {"type": kind, "id": existing, "attributes": attributes}}
            await self.call("PATCH", f"/{kind}/{existing}", what, body=body)
            return existing
        body = {
            "data": {
                "type": kind,
                "attributes": {**attributes, **create.get("attributes", {})},
                "relationships": create["relationships"],
            }
        }
        return str(_dict((await self.call("POST", f"/{kind}", what, body=body)).get("data"))["id"])


def _rel(kind: str, id_: str) -> dict[str, Any]:
    return {"data": {"type": kind, "id": id_}}


def _state(item: dict[str, Any]) -> str:
    attrs = _dict(item.get("attributes"))
    return str(
        attrs.get("appVersionState") or attrs.get("state") or attrs.get("appStoreState") or ""
    )


def texts(locale: str, root: Path = APPSTORE, play: Path = LISTING) -> dict[str, str]:
    def read(folder: Path, name: str) -> str:
        return (folder / name).read_text().strip()

    own = root / locale
    return {
        "name": read(own, "name.txt"),
        "subtitle": read(own, "subtitle.txt"),
        "keywords": read(own, "keywords.txt"),
        "promotionalText": read(own, "promotional_text.txt"),
        "description": read(play / locale, "full_description.txt"),
    }


def screenshots(locale: str, root: Path = APPSTORE) -> list[Path]:
    folder = root / "screenshots" / locale
    return sorted(folder.glob("*.png")) if folder.is_dir() else []


async def push(
    client: httpx.AsyncClient,
    auth: Auth,
    report: Report,
    root: Path = APPSTORE,
    play: Path = LISTING,
) -> None:
    api = _Api(client, auth)
    apps = await api.items("/apps", "App", **{"filter[bundleId]": PACKAGE})
    if not apps:
        raise StoreError(
            f"Die App fehlt in App Store Connect: Apps → + → Neue App, Bundle-ID {PACKAGE}."
        )
    app_id = str(apps[0]["id"])

    # Name, subtitle and privacy policy belong to the app info, not to a version.
    infos = await api.items(f"/apps/{app_id}/appInfos", "App-Infos")
    info = next(
        (i for i in infos if _state(i) not in ("READY_FOR_SALE", "READY_FOR_DISTRIBUTION")),
        infos[0] if infos else None,
    )
    if info is None:
        raise StoreError("App-Infos: Apple liefert keine.")
    known = {
        str(_dict(x.get("attributes")).get("locale")): str(x["id"])
        for x in await api.items(f"/appInfos/{info['id']}/appInfoLocalizations", "Sprachen")
    }
    for locale in LOCALES:
        t = texts(locale, root, play)
        await api.upsert(
            known.get(locale),
            "appInfoLocalizations",
            {"name": t["name"], "subtitle": t["subtitle"], "privacyPolicyUrl": PRIVACY_URL},
            {
                "attributes": {"locale": locale},
                "relationships": {"appInfo": _rel("appInfos", str(info["id"]))},
            },
            f"Name {locale}",
        )
    report.add("Name und Untertitel", "ok", ", ".join(LOCALES))
    await _soft(report, "Kategorie", _category(api, str(info["id"])))
    await _soft(report, "Altersfreigabe", _age_rating(api, str(info["id"])))

    versions = await api.items(
        f"/apps/{app_id}/appStoreVersions", "Versionen", **{"filter[platform]": "IOS"}
    )
    version = next((v for v in versions if _state(v) in EDITABLE), None)
    # A version Apple is reviewing (or that waits for release) stays as it is; a new one
    # can only be made once the last one is out (Apple answers 409 otherwise).
    busy = next((v for v in versions if _state(v) not in EDITABLE | RELEASED), None)
    if version is None and busy is not None:
        report.add(
            "Version",
            "warn",
            f"Version {_dict(busy.get('attributes')).get('versionString', '')} ist bei Apple "
            f"({_state(busy)}); Texte, Screenshots und Build bleiben dort unverändert.",
        )
    elif version is None:
        body = {
            "data": {
                "type": "appStoreVersions",
                "attributes": {"platform": "IOS", "versionString": VERSION},
                "relationships": {"app": _rel("apps", app_id)},
            }
        }
        version = _dict(
            (await api.call("POST", "/appStoreVersions", "Version", body=body)).get("data")
        )
    if version is not None:
        await _version_texts(api, report, version, root, play)

    builds = await api.items(
        "/builds",
        "Builds",
        **{
            "filter[app]": app_id,
            "filter[processingState]": "VALID",
            "filter[expired]": "false",
            "sort": "-uploadedDate",
            "limit": "1",
        },
    )
    if builds:
        build = builds[0]
        if _dict(build.get("attributes")).get("usesNonExemptEncryption") is None:
            body = {
                "data": {
                    "type": "builds",
                    "id": build["id"],
                    "attributes": {"usesNonExemptEncryption": False},
                }
            }
            await api.call("PATCH", f"/builds/{build['id']}", "Verschlüsselung", body=body)
        number = _dict(build.get("attributes")).get("version")
        if version is not None:
            await api.call(
                "PATCH",
                f"/appStoreVersions/{version['id']}/relationships/build",
                "Build",
                body=_rel("builds", str(build["id"])),
            )
            report.add("Build", "ok", f"Build {number} gewählt.")
        else:
            report.add("Build", "ok", f"Build {number} nur für TestFlight.")
        await _soft(report, "TestFlight-Link", _public_group(api, app_id, str(build["id"])))
        await _soft(
            report, "TestFlight-Prüfung", _beta_review(api, app_id, str(build["id"]), root, play)
        )
    else:
        report.add(
            "Build",
            "warn",
            "Noch kein fertig verarbeiteter Build in TestFlight; "
            "nach dem iOS-Release noch einmal übertragen.",
        )
    report.add(
        "Noch in App Store Connect",
        "warn",
        "App-Datenschutz, Preis und Länder, Kontakt und Testkonto für die Prüfung; dann "
        "„Zur Prüfung hinzufügen“.",
    )


async def _version_texts(
    api: _Api, report: Report, version: dict[str, Any], root: Path, play: Path
) -> None:
    """Texts and screenshots of the version in preparation, in both languages."""
    version_id = str(version["id"])
    number = _dict(version.get("attributes")).get("versionString", VERSION)

    known = {
        str(_dict(x.get("attributes")).get("locale")): str(x["id"])
        for x in await api.items(
            f"/appStoreVersions/{version_id}/appStoreVersionLocalizations", "Texte"
        )
    }
    for locale in LOCALES:
        t = texts(locale, root, play)
        localization = await api.upsert(
            known.get(locale),
            "appStoreVersionLocalizations",
            {
                "description": t["description"],
                "keywords": t["keywords"],
                "promotionalText": t["promotionalText"],
                "supportUrl": SUPPORT_URL,
                "marketingUrl": SUPPORT_URL,
            },
            {
                "attributes": {"locale": locale},
                "relationships": {"appStoreVersion": _rel("appStoreVersions", version_id)},
            },
            f"Texte {locale}",
        )
        shots = screenshots(locale, root)
        await _screenshots(api, localization, shots, locale)
        report.add(
            f"Texte und Screenshots {locale}",
            "ok",
            f"Version {number}, {len(shots)} iPhone-Screenshots.",
        )


async def _soft(report: Report, title: str, step: Coroutine[Any, Any, str]) -> None:
    """A step whose failure leaves the rest standing; it can be done by hand."""
    try:
        report.add(title, "ok", await step)
    except StoreError as exc:
        report.add(title, "warn", f"{exc} Bitte in App Store Connect setzen.")


async def _category(api: _Api, info: str) -> str:
    body = {
        "data": {
            "type": "appInfos",
            "id": info,
            "relationships": {
                "primaryCategory": _rel("appCategories", PRIMARY_CATEGORY),
                "secondaryCategory": _rel("appCategories", SECONDARY_CATEGORY),
            },
        }
    }
    await api.call("PATCH", f"/appInfos/{info}", "Kategorie", body=body)
    return f"{PRIMARY_CATEGORY}, {SECONDARY_CATEGORY}"


async def _age_rating(api: _Api, info: str) -> str:
    """Every question answered "none"/"no" (as for Google Play: no violence, no
    purchases, no web browser, no chat, no feed of other people's content). Only the
    questions Apple's declaration has right now are sent."""
    found = _dict(
        (await api.call("GET", f"/appInfos/{info}/ageRatingDeclaration", "Altersfreigabe")).get(
            "data"
        )
    )
    present = _dict(found.get("attributes"))
    answers = {k: v for k, v in AGE_RATING.items() if k in present}
    body = {"data": {"type": "ageRatingDeclarations", "id": found["id"], "attributes": answers}}
    await api.call("PATCH", f"/ageRatingDeclarations/{found['id']}", "Altersfreigabe", body=body)
    return f"{len(answers)} Fragen mit „keine“ beantwortet."


async def _public_group(api: _Api, app_id: str, build: str) -> str:
    """The external group with the public link, the build added to it. The link works
    once Apple's beta review has passed the build (:func:`_beta_review`)."""
    groups = await api.items(f"/apps/{app_id}/betaGroups", "TestFlight-Gruppen")
    group = next(
        (
            g
            for g in groups
            if _dict(g.get("attributes")).get("name") == BETA_GROUP
            and not _dict(g.get("attributes")).get("isInternalGroup")
        ),
        None,
    )
    if group is None:
        body = {
            "data": {
                "type": "betaGroups",
                "attributes": {
                    "name": BETA_GROUP,
                    "publicLinkEnabled": True,
                    "publicLinkLimitEnabled": False,
                    "feedbackEnabled": True,
                },
                "relationships": {"app": _rel("apps", app_id)},
            }
        }
        group = _dict(
            (await api.call("POST", "/betaGroups", "TestFlight-Gruppe", body=body)).get("data")
        )
    elif not _dict(group.get("attributes")).get("publicLinkEnabled"):
        body = {
            "data": {
                "type": "betaGroups",
                "id": group["id"],
                "attributes": {"publicLinkEnabled": True},
            }
        }
        group = (
            _dict(
                (
                    await api.call(
                        "PATCH", f"/betaGroups/{group['id']}", "Öffentlicher Link", body=body
                    )
                ).get("data")
            )
            or group
        )
    await api.call(
        "POST",
        f"/betaGroups/{group['id']}/relationships/builds",
        "Build in die TestFlight-Gruppe",
        body={"data": [{"type": "builds", "id": build}]},
    )
    link = _dict(group.get("attributes")).get("publicLink")
    if not link:
        raise StoreError(f"Gruppe „{BETA_GROUP}“ ohne öffentlichen Link.")
    return str(link)


async def _beta_review(api: _Api, app_id: str, build: str, root: Path, play: Path) -> str:
    """Test information (description, what to test) and the build to Apple's beta review,
    which external testers need. The review contact and test account are Benjamin's to
    enter (TestFlight → Testinformationen); without them Apple refuses the submission."""
    known = {
        str(_dict(x.get("attributes")).get("locale")): str(x["id"])
        for x in await api.items(f"/apps/{app_id}/betaAppLocalizations", "Testinformationen")
    }
    for locale in LOCALES:
        await api.upsert(
            known.get(locale),
            "betaAppLocalizations",
            {
                "description": texts(locale, root, play)["description"],
                "feedbackEmail": CONTACT_EMAIL,
                "marketingUrl": SUPPORT_URL,
                "privacyPolicyUrl": PRIVACY_URL,
            },
            {"attributes": {"locale": locale}, "relationships": {"app": _rel("apps", app_id)}},
            f"Testinformationen {locale}",
        )
    known = {
        str(_dict(x.get("attributes")).get("locale")): str(x["id"])
        for x in await api.items(f"/builds/{build}/betaBuildLocalizations", "Was testen")
    }
    for locale in LOCALES:
        await api.upsert(
            known.get(locale),
            "betaBuildLocalizations",
            {"whatsNew": WHAT_TO_TEST[locale]},
            {"attributes": {"locale": locale}, "relationships": {"build": _rel("builds", build)}},
            f"Was testen {locale}",
        )
    submitted = await api.items(
        "/betaAppReviewSubmissions", "Beta-Prüfung", **{"filter[build]": build}
    )
    if submitted:
        state = str(_dict(submitted[0].get("attributes")).get("betaReviewState") or "")
        return {"APPROVED": "Freigegeben, der Link ist aktiv."}.get(
            state, f"Bei Apple in Prüfung ({state or 'eingereicht'})."
        )
    body = {
        "data": {
            "type": "betaAppReviewSubmissions",
            "relationships": {"build": _rel("builds", build)},
        }
    }
    try:
        await api.call("POST", "/betaAppReviewSubmissions", "Beta-Prüfung", body=body)
    except StoreError as exc:
        raise StoreError(
            f"{exc} Unter TestFlight → Testinformationen Kontakt und Testkonto eintragen, "
            "dann noch einmal übertragen."
        ) from None
    return "Zur Beta-Prüfung eingereicht; der Link wird danach aktiv."


async def _screenshots(api: _Api, localization: str, shots: list[Path], locale: str) -> None:
    """Replace the iPhone screenshots of one language (create, upload in parts, commit)."""
    sets = await api.items(
        f"/appStoreVersionLocalizations/{localization}/appScreenshotSets", "Screenshot-Sätze"
    )
    found = next(
        (
            s
            for s in sets
            if _dict(s.get("attributes")).get("screenshotDisplayType") == DISPLAY_TYPE
        ),
        None,
    )
    if found is None:
        body = {
            "data": {
                "type": "appScreenshotSets",
                "attributes": {"screenshotDisplayType": DISPLAY_TYPE},
                "relationships": {
                    "appStoreVersionLocalization": _rel(
                        "appStoreVersionLocalizations", localization
                    )
                },
            }
        }
        found = _dict(
            (await api.call("POST", "/appScreenshotSets", "Screenshot-Satz", body=body)).get("data")
        )
    set_id = str(found["id"])
    for old in await api.items(f"/appScreenshotSets/{set_id}/appScreenshots", "Screenshots"):
        await api.call("DELETE", f"/appScreenshots/{old['id']}", "Alter Screenshot")
    for path in shots:
        data = await asyncio.to_thread(path.read_bytes)
        what = f"Screenshot {locale} {path.name}"
        body = {
            "data": {
                "type": "appScreenshots",
                "attributes": {"fileName": path.name, "fileSize": len(data)},
                "relationships": {"appScreenshotSet": _rel("appScreenshotSets", set_id)},
            }
        }
        shot = _dict((await api.call("POST", "/appScreenshots", what, body=body)).get("data"))
        for op in _list(_dict(shot.get("attributes")).get("uploadOperations")):
            start, length = int(op.get("offset", 0)), int(op.get("length", len(data)))
            headers = {str(h["name"]): str(h["value"]) for h in _list(op.get("requestHeaders"))}
            r = await api.client.request(
                str(op.get("method", "PUT")),
                str(op["url"]),
                headers=headers,
                content=data[start : start + length],
            )
            if r.status_code >= 300:
                raise StoreError(f"{what}: Hochladen {_message(r)}")
        checksum = hashlib.md5(data).hexdigest()  # noqa: S324 - Apple asks for MD5
        body = {
            "data": {
                "type": "appScreenshots",
                "id": shot["id"],
                "attributes": {"uploaded": True, "sourceFileChecksum": checksum},
            }
        }
        await api.call("PATCH", f"/appScreenshots/{shot['id']}", what, body=body)


async def push_report(stores: Stores) -> Report:
    report = Report()
    if not stores.vault.has("asc"):
        report.add(TITLE, "warn", "Kein App-Store-Connect-Schlüssel hinterlegt.")
        return report
    client = stores.client or httpx.AsyncClient(timeout=UPLOAD_TIMEOUT)
    try:
        await push(client, stores.asc_auth, report)
    except StoreError as exc:
        report.add(TITLE, "fail", str(exc))
    except httpx.HTTPError as exc:
        report.add(TITLE, "fail", f"Apple nicht erreichbar ({type(exc).__name__}).")
    finally:
        if client is not stores.client:
            await client.aclose()
    return report


def start(stores: Stores, by: str) -> bool:
    """Push in the background, like the Play listing; False while one still runs."""
    task = stores.jobs.get(TITLE)
    if task is not None and not task.done():
        return False
    stores.vault.write_json(STATE_FILE, {"at": _now(), "by": by, "running": True})

    async def run() -> Report:
        try:
            report = await push_report(stores)
        except Exception:  # a bug, not Apple: say so instead of "restarted"
            log.exception("app store listing push failed")
            report = Report()
            report.add(TITLE, "fail", "Interner Fehler, siehe Log des Hubs.")
        steps = [{"title": x.title, "state": x.state, "text": x.text} for x in report.steps]
        stores.vault.write_json(STATE_FILE, {"at": _now(), "by": by, "steps": steps})
        log.info(
            "app store listing pushed by %s: %s", by, [(x.title, x.state) for x in report.steps]
        )
        return report

    stores.jobs[TITLE] = asyncio.create_task(run())
    return True


def state(stores: Stores) -> dict[str, Any] | None:
    found = stores.vault.read_json(STATE_FILE)
    task = stores.jobs.get(TITLE)
    if found and found.get("running") and (task is None or task.done()):
        text = "Abgebrochen (der Hub wurde neu gestartet), bitte noch einmal übertragen."
        return {
            **found,
            "running": False,
            "steps": [{"title": TITLE, "state": "fail", "text": text}],
        }
    return found

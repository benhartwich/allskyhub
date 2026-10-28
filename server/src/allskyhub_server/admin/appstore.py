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
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from allskyhub_server.admin.stores import (
    ASC,
    CONTACT_EMAIL,
    LISTING,
    PACKAGE,
    UPLOAD_TIMEOUT,
    OtherApp,
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


def app_version(pubspec: Path = LISTING.parents[1] / "pubspec.yaml", default: str = "1.0.0") -> str:
    """The app's version (``version: 1.1.0+1`` in app/pubspec.yaml), for the App Store
    version: Apple ties it to the build's CFBundleShortVersionString, and each update
    needs a new one."""
    try:
        for line in pubspec.read_text().splitlines():
            if line.startswith("version:"):
                found = line.split(":", 1)[1].strip().split("+", 1)[0]
                if re.fullmatch(r"\d+(\.\d+){0,2}", found):
                    return found
    except OSError:
        pass
    return default


VERSION = app_version()
# 1290 x 2796 (app/test/store, STORE_DEVICE=iphone): Apple's 6.7" and 6.9" iPhone set.
DISPLAY_TYPE = "APP_IPHONE_67"
SUPPORT_URL = "https://allskyhub.org"
PRIVACY_URL = "https://allskyhub.org/datenschutz"
# Versions whose metadata can still be changed.
EDITABLE = frozenset(
    {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED"}
)
# A withdrawn review, and a version still under its old number: step titles that lead to
# the listing push, never to submitting; and how long Apple gets to hand the version back.
WITHDRAWN = "Zurückgezogen"
OUTDATED = "Versionsnummer"
TO_PUSH = frozenset({WITHDRAWN, OUTDATED})
WITHDRAW_TRIES = 10
WITHDRAW_PAUSE = 3.0
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
# Contact and test account, named alike in the App Review and the beta review details.
REVIEW_FIELDS = (
    "contactFirstName", "contactLastName", "contactPhone", "contactEmail",
    "demoAccountName", "demoAccountPassword", "demoAccountRequired", "notes",
)  # fmt: skip
# The external TestFlight group with the public link (Benjamin, 2026-10-26).
BETA_GROUP = "Öffentlich"
WHAT_TO_TEST = {
    "de-DE": "Anmelden, eigene oder freigegebene Kameras ansehen, Bilder, Ereignisse und "
    "die Vorhersage für heute Nacht. Rückmeldungen gern über TestFlight.",
    "en-US": "Sign in, look at your own or shared cameras, pictures, events and tonight's "
    "forecast. Feedback is welcome through TestFlight.",
}
STATE_FILE = "appstore.json"


@dataclass(frozen=True)
class Profile:
    """What differs between the apps on the same accounts (stores.OTHER_APPS)."""

    key: str
    package: str
    support_url: str
    privacy_url: str
    contact_email: str
    categories: tuple[str, str]
    version: str
    what_to_test: dict[str, str]

    @property
    def state_file(self) -> str:
        return STATE_FILE if self.key == "allskyhub" else f"appstore-{self.key}.json"

    @property
    def job(self) -> str:
        return TITLE if self.key == "allskyhub" else f"{TITLE} {self.key}"


ALLSKYHUB = Profile(
    key="allskyhub",
    package=PACKAGE,
    support_url=SUPPORT_URL,
    privacy_url=PRIVACY_URL,
    contact_email=CONTACT_EMAIL,
    categories=(PRIMARY_CATEGORY, SECONDARY_CATEGORY),
    version=VERSION,
    what_to_test=WHAT_TO_TEST,
)


def profile(app: OtherApp | None) -> Profile:
    if app is None:
        return ALLSKYHUB
    return Profile(
        key=app.key,
        package=app.package,
        support_url=app.website,
        privacy_url=app.privacy_url,
        contact_email=app.contact_email,
        categories=app.categories,
        version=app.version,
        what_to_test=app.what_to_test,
    )


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
    p: Profile = ALLSKYHUB,
) -> None:
    api = _Api(client, auth)
    apps = await api.items("/apps", "App", **{"filter[bundleId]": p.package})
    if not apps:
        raise StoreError(
            f"Die App fehlt in App Store Connect: Apps → + → Neue App, Bundle-ID {p.package}."
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
            {"name": t["name"], "subtitle": t["subtitle"], "privacyPolicyUrl": p.privacy_url},
            {
                "attributes": {"locale": locale},
                "relationships": {"appInfo": _rel("appInfos", str(info["id"]))},
            },
            f"Name {locale}",
        )
    report.add("Name und Untertitel", "ok", ", ".join(LOCALES))
    await _soft(report, "Kategorie", _category(api, str(info["id"]), p))
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
                "attributes": {"platform": "IOS", "versionString": p.version},
                "relationships": {"app": _rel("apps", app_id)},
            }
        }
        version = _dict(
            (await api.call("POST", "/appStoreVersions", "Version", body=body)).get("data")
        )
    if version is not None:
        await _soft(report, "Versionsnummer", _version_string(api, version, p.version))
        await _version_texts(api, report, version, root, play, p)

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
        reviewed = version or busy
        await _soft(
            report,
            "TestFlight-Prüfung",
            _beta_review(
                api,
                app_id,
                str(build["id"]),
                root,
                play,
                str(reviewed["id"]) if reviewed is not None else None,
                p,
            ),
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
        "hier „zur Prüfung einreichen“.",
    )


async def _version_string(api: _Api, version: dict[str, Any], wanted: str) -> str:
    """The version in preparation renamed to the app's (pubspec), e.g. "1.0" → "0.3.0"."""
    attrs = _dict(version.get("attributes"))
    now = str(attrs.get("versionString", ""))
    if now == wanted:
        return wanted
    body = {
        "data": {
            "type": "appStoreVersions",
            "id": version["id"],
            "attributes": {"versionString": wanted},
        }
    }
    await api.call("PATCH", f"/appStoreVersions/{version['id']}", "Versionsnummer", body=body)
    attrs["versionString"] = wanted
    return f"{now} → {wanted}"


async def _version_texts(
    api: _Api,
    report: Report,
    version: dict[str, Any],
    root: Path,
    play: Path,
    p: Profile = ALLSKYHUB,
) -> None:
    """Texts and screenshots of the version in preparation, in both languages."""
    version_id = str(version["id"])
    number = _dict(version.get("attributes")).get("versionString", p.version)

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
                "supportUrl": p.support_url,
                "marketingUrl": p.support_url,
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


async def _category(api: _Api, info: str, p: Profile = ALLSKYHUB) -> str:
    primary, secondary = p.categories
    body = {
        "data": {
            "type": "appInfos",
            "id": info,
            "relationships": {
                "primaryCategory": _rel("appCategories", primary),
                "secondaryCategory": _rel("appCategories", secondary),
            },
        }
    }
    await api.call("PATCH", f"/appInfos/{info}", "Kategorie", body=body)
    return f"{primary}, {secondary}"


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


async def _beta_review(
    api: _Api,
    app_id: str,
    build: str,
    root: Path,
    play: Path,
    version: str | None,
    p: Profile = ALLSKYHUB,
) -> str:
    """Test information (description, what to test) and the build to Apple's beta review,
    which external testers need. The review contact and test account are Benjamin's to
    enter; the ones he gave for the App Review of ``version`` are taken over where the
    beta ones are empty (:func:`_beta_contact`); without them Apple refuses."""
    if version is not None:
        await _beta_contact(api, app_id, version)
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
                "feedbackEmail": p.contact_email,
                "marketingUrl": p.support_url,
                "privacyPolicyUrl": p.privacy_url,
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
            {"whatsNew": p.what_to_test[locale]},
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
        if "already in beta review" in str(exc):
            # One build per version at a time; once that one is through, later builds of
            # the same version usually pass without a full review.
            return (
                "Ein früherer Build ist noch in der Beta-Prüfung; der Link gilt nach dessen "
                "Freigabe. Danach noch einmal übertragen, dann kommt dieser Build nach."
            )
        raise StoreError(
            f"{exc} Unter TestFlight → Testinformationen Kontakt und Testkonto eintragen, "
            "dann noch einmal übertragen."
        ) from None
    return "Zur Beta-Prüfung eingereicht; der Link wird danach aktiv."


async def _beta_contact(api: _Api, app_id: str, version: str) -> None:
    """Copy the App Review contact and test account into the beta review details, field
    by field where those are still empty. The values pass through, never into a report
    or a log."""
    review = _dict(
        _dict(
            (
                await api.call(
                    "GET", f"/appStoreVersions/{version}/appStoreReviewDetail", "Prüfkontakt"
                )
            ).get("data")
        ).get("attributes")
    )
    beta = _dict(
        (await api.call("GET", f"/apps/{app_id}/betaAppReviewDetail", "Beta-Prüfkontakt")).get(
            "data"
        )
    )
    have = _dict(beta.get("attributes"))
    missing = {
        k: review[k]
        for k in REVIEW_FIELDS
        if review.get(k) not in (None, "") and have.get(k) in (None, "")
    }
    if not missing or not beta.get("id"):
        return
    body = {"data": {"type": "betaAppReviewDetails", "id": beta["id"], "attributes": missing}}
    r = await api.client.request(
        "PATCH", f"{ASC}/betaAppReviewDetails/{beta['id']}", json=body, headers=api.auth()
    )
    if r.status_code >= 300:
        # Not Apple's text: it can quote the refused value (a phone number, the password).
        raise StoreError(
            f"Beta-Prüfkontakt: Apple lehnt ab (HTTP {r.status_code}), bitte in TestFlight → "
            "Testinformationen prüfen."
        )


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


async def push_report(stores: Stores, app: OtherApp | None = None) -> Report:
    report = Report()
    if not stores.vault.has("asc"):
        report.add(TITLE, "warn", "Kein App-Store-Connect-Schlüssel hinterlegt.")
        return report
    client = stores.client or httpx.AsyncClient(timeout=UPLOAD_TIMEOUT)
    try:
        if app is None:
            await push(client, stores.asc_auth, report)
        else:
            # A further app's texts and screenshots: store/ of its repository's main.
            store = await stores.fetch_listing(client, app)
            # The version of its repository's pubspec.yaml, fetched with store/.
            p = profile(app)
            p = replace(p, version=app_version(store.parent / "pubspec.yaml", p.version))
            await push(client, stores.asc_auth, report, store / "appstore", store / "play", p)
    except StoreError as exc:
        report.add(TITLE, "fail", str(exc))
    except httpx.HTTPError as exc:
        report.add(TITLE, "fail", f"Apple nicht erreichbar ({type(exc).__name__}).")
    finally:
        if client is not stores.client:
            await client.aclose()
    return report


async def submit(client: httpx.AsyncClient, auth: Auth, report: Report, p: Profile) -> None:
    """The version in preparation, with the build the listing push chose, to Apple's App
    Store review (a review submission with that version as its item). Apple names what is
    still missing (privacy, price, review contact) in its answer."""
    api = _Api(client, auth)
    apps = await api.items("/apps", "App", **{"filter[bundleId]": p.package})
    if not apps:
        raise StoreError(f"Die App {p.package} fehlt in App Store Connect.")
    app_id = str(apps[0]["id"])
    versions = await api.items(
        f"/apps/{app_id}/appStoreVersions", "Versionen", **{"filter[platform]": "IOS"}
    )
    version = next((v for v in versions if _state(v) in EDITABLE), None)
    if version is None:
        busy = next((v for v in versions if _state(v) not in RELEASED), None)
        if busy is not None:
            name = _dict(busy.get("attributes")).get("versionString", "")
            if _state(busy) == "WAITING_FOR_REVIEW" and name != p.version:
                back = await _withdraw(api, app_id, str(busy["id"]))
                text = (
                    f"Version {name} wartete noch mit einem älteren Build auf die Prüfung und "
                    f"ist zurückgezogen. Die Übertragung von {p.version} läuft, danach "
                    "einreichen."
                )
                if not back:
                    text += " (Apple gibt die Version erst gleich frei; wenn die Übertragung "
                    text += "sie noch nicht ändern konnte, einfach noch einmal übertragen.)"
                report.add(WITHDRAWN, "warn", text)
                return
            report.add("Prüfung", "ok", f"Version {name} ist schon bei Apple ({_state(busy)}).")
            return
        raise StoreError("Keine Version in Vorbereitung; erst „App Store übertragen“.")
    name = _dict(version.get("attributes")).get("versionString", "")
    if name != p.version:  # its build is likely the old one too; the push renames and picks
        report.add(
            OUTDATED,
            "warn",
            f"Version {name} ist noch nicht {p.version}; erst „App Store übertragen“, "
            "dann einreichen.",
        )
        return
    build = _dict(
        (await api.call("GET", f"/appStoreVersions/{version['id']}/build", "Build")).get("data")
    )
    if not build:
        raise StoreError(
            f"Version {name} hat noch keinen Build; erst iOS-Release bauen, dann übertragen."
        )
    number = _dict(build.get("attributes")).get("version", "")
    open_ = await api.items(
        f"/apps/{app_id}/reviewSubmissions",
        "Einreichungen",
        **{"filter[platform]": "IOS", "filter[state]": "READY_FOR_REVIEW"},
    )
    if open_:
        submission = str(open_[0]["id"])
    else:
        body = {
            "data": {
                "type": "reviewSubmissions",
                "attributes": {"platform": "IOS"},
                "relationships": {"app": _rel("apps", app_id)},
            }
        }
        submission = str(
            _dict(
                (await api.call("POST", "/reviewSubmissions", "Einreichung", body=body)).get("data")
            )["id"]
        )
    items = await api.items(f"/reviewSubmissions/{submission}/items", "Einreichung")
    has = any(
        _dict(_dict(_dict(i.get("relationships")).get("appStoreVersion")).get("data")).get("id")
        == version["id"]
        for i in items
    )
    if not has:
        body = {
            "data": {
                "type": "reviewSubmissionItems",
                "relationships": {
                    "reviewSubmission": _rel("reviewSubmissions", submission),
                    "appStoreVersion": _rel("appStoreVersions", str(version["id"])),
                },
            }
        }
        await api.call("POST", "/reviewSubmissionItems", "Version zur Einreichung", body=body)
    body = {
        "data": {"type": "reviewSubmissions", "id": submission, "attributes": {"submitted": True}}
    }
    await api.call("PATCH", f"/reviewSubmissions/{submission}", "Einreichen", body=body)
    report.add("Prüfung", "ok", f"Version {name} mit Build {number} ist zur Prüfung eingereicht.")


async def _withdraw(api: _Api, app_id: str, version: str) -> bool:
    """Takes a version that still waits for Apple's review back out of the queue (Benjamin,
    2026-10-28: 1.0 waited with an old build while 1.1.0 was ready), and waits until Apple
    hands it back for editing (False if it has not yet), so the listing push that follows
    can rename it and pick the build."""
    waiting = await api.items(
        f"/apps/{app_id}/reviewSubmissions",
        "Einreichungen",
        **{"filter[platform]": "IOS", "filter[state]": "WAITING_FOR_REVIEW"},
    )
    if not waiting:
        raise StoreError("Apple nennt keine wartende Einreichung zum Zurückziehen.")
    for submission in waiting:
        body = {
            "data": {
                "type": "reviewSubmissions",
                "id": submission["id"],
                "attributes": {"canceled": True},
            }
        }
        await api.call("PATCH", f"/reviewSubmissions/{submission['id']}", "Zurückziehen", body=body)
    for _ in range(WITHDRAW_TRIES):
        found = _dict(
            (await api.call("GET", f"/appStoreVersions/{version}", "Version")).get("data")
        )
        if _state(found) in EDITABLE:
            return True
        await asyncio.sleep(WITHDRAW_PAUSE)
    return False  # withdrawn all the same; Apple hands it back a little later


async def submit_report(stores: Stores, app: OtherApp | None = None) -> Report:
    report = Report()
    if not stores.vault.has("asc"):
        report.add(TITLE, "warn", "Kein App-Store-Connect-Schlüssel hinterlegt.")
        return report
    client = stores.client or httpx.AsyncClient(timeout=UPLOAD_TIMEOUT)
    try:
        p = profile(app)
        if app is not None:  # its pubspec, as the last listing push fetched it
            pubspec = stores.vault.root / "listings" / app.key / "pubspec.yaml"
            p = replace(p, version=app_version(pubspec, p.version))
        await submit(client, stores.asc_auth, report, p)
    except StoreError as exc:
        report.add("Prüfung", "fail", str(exc))
    except httpx.HTTPError as exc:
        report.add("Prüfung", "fail", f"Apple nicht erreichbar ({type(exc).__name__}).")
    finally:
        if client is not stores.client:
            await client.aclose()
    return report


def start(stores: Stores, by: str, app: OtherApp | None = None) -> bool:
    """Push in the background, like the Play listing; False while one still runs."""
    p = profile(app)
    task = stores.jobs.get(p.job)
    if task is not None and not task.done():
        return False
    stores.vault.write_json(p.state_file, {"at": _now(), "by": by, "running": True})

    async def run() -> Report:
        try:
            report = await push_report(stores, app)
        except Exception:  # a bug, not Apple: say so instead of "restarted"
            log.exception("app store listing push failed")
            report = Report()
            report.add(TITLE, "fail", "Interner Fehler, siehe Log des Hubs.")
        steps = [{"title": x.title, "state": x.state, "text": x.text} for x in report.steps]
        stores.vault.write_json(p.state_file, {"at": _now(), "by": by, "steps": steps})
        log.info(
            # With the texts: the public link and Apple's answers, nothing secret.
            "app store listing %s pushed by %s: %s",
            p.key,
            by,
            [(x.title, x.state, x.text) for x in report.steps],
        )
        return report

    stores.jobs[p.job] = asyncio.create_task(run())
    return True


def state(stores: Stores, app: OtherApp | None = None) -> dict[str, Any] | None:
    p = profile(app)
    found = stores.vault.read_json(p.state_file)
    task = stores.jobs.get(p.job)
    if found and found.get("running") and (task is None or task.done()):
        text = "Abgebrochen (der Hub wurde neu gestartet), bitte noch einmal übertragen."
        return {
            **found,
            "running": False,
            "steps": [{"title": TITLE, "state": "fail", "text": text}],
        }
    return found

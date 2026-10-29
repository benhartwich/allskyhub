"""The admin's app page (/admin/apps): one card per app, one row per store, each with
where it stands and the one next step (Benjamin, 2026-10-28: the store part of the admin
page was too hard to follow).

It reads what the hub keeps (the Android job, Codemagic's build states, the last listing
push and review submission) and what the stores say now (live-<app>.json, refreshed in
the background at most every ten minutes): a release submitted by hand in the Play
Console or App Store Connect counts too (Benjamin, 2026-10-29: rows said "failed" for
apps that were in review). The rows are pure; the routes act.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from allskyhub_server.admin import appstore
from allskyhub_server.admin.stores import (
    OTHER_APPS,
    PACKAGE,
    AndroidJob,
    OtherApp,
    Report,
    StoreError,
    Stores,
    build_url,
)

log = logging.getLogger(__name__)

# Codemagic's build states that are over; anything else is still on its way.
CM_DONE = frozenset({"finished"})
CM_FAILED = frozenset({"failed", "canceled", "timeout", "skipped"})
ACTIONS = (
    "setup", "build-android", "build-ios", "play-listing", "appstore",
    "review-play", "review-appstore", "apk", "build-test-apk",
)  # fmt: skip
# What the stores say, kept this long before the page asks again.
LIVE_MAX_AGE = dt.timedelta(minutes=10)
LIVE_JOB = "live"
# Apple's states of a version on its way and in the store.
APPLE_REVIEW = {
    "WAITING_FOR_REVIEW": "wartet bei Apple auf die Prüfung",
    "IN_REVIEW": "wird von Apple geprüft",
    "PENDING_DEVELOPER_RELEASE": "ist von Apple freigegeben und wartet auf die Veröffentlichung",
    "PENDING_APPLE_RELEASE": "ist von Apple freigegeben",
    "PROCESSING_FOR_APP_STORE": "ist freigegeben und kommt gleich in den App Store",
    "READY_FOR_SALE": "ist im App Store",
    "READY_FOR_DISTRIBUTION": "ist im App Store",
}
APPLE_REJECTED = {"REJECTED", "METADATA_REJECTED", "INVALID_BINARY"}


@dataclass(frozen=True)
class Row:
    store: str  # "Android" or "iOS"
    tone: str  # done, todo, wait, fail
    status: str
    action: str | None = None  # one of ACTIONS
    label: str = ""
    link: str | None = None  # a build on Codemagic, or the App Bundle
    link_label: str = ""


@dataclass
class Card:
    key: str  # "allskyhub" or OtherApp.key
    title: str
    package: str
    version: str
    rows: list[Row] = field(default_factory=lambda: list[Row]())
    android: AndroidJob | None = None
    results: list[tuple[str, dict[str, Any]]] = field(
        default_factory=lambda: list[tuple[str, dict[str, Any]]]()
    )
    live_at: str = ""  # when Apple and Google were last asked
    apk: dict[str, Any] | None = None  # the APK for /download/<key>.apk (Stores.apk)
    test: AndroidJob | None = None  # the server's test APK (OtherApp.test_apk)

    @property
    def waiting(self) -> bool:
        return any(r.tone == "wait" for r in self.rows) or bool(self.test and self.test.busy)


def live_file(key: str) -> str:
    return f"live-{key}.json"


def live_of(stores: Stores, key: str) -> dict[str, Any]:
    return stores.vault.read_json(live_file(key)) or {}


def _stale(stores: Stores) -> bool:
    now = dt.datetime.now(dt.UTC)
    for key in ("allskyhub", *OTHER_APPS):
        try:
            at = dt.datetime.fromisoformat(str(live_of(stores, key).get("at", "")))
        except ValueError:
            return True
        if now - at > LIVE_MAX_AGE:
            return True
    return False


async def refresh_live(stores: Stores) -> None:
    """Ask Apple and Google where each app stands; what one of them cannot answer is
    left out (the rows then go by the hub's own records)."""
    client = stores.client or httpx.AsyncClient(timeout=30)
    try:
        for app in (None, *OTHER_APPS.values()):
            key = app.key if app else "allskyhub"
            found: dict[str, Any] = {"at": _now()}
            if stores.vault.has("asc"):
                try:
                    found["apple"] = await appstore.live(
                        client, stores.asc_auth, appstore.profile(app)
                    )
                except (StoreError, httpx.HTTPError, KeyError, ValueError) as exc:
                    log.info("live state %s at Apple: %s", key, exc)
            # Not while an App Bundle goes up: Google lets a new edit expire the upload's
            # (vinventory build 25, 2026-10-29: "This edit has expired").
            # Nor while the hub has an edit of its own open (listing, submission).
            busy = stores.android_busy() or stores.play_edits.locked()
            if stores.vault.has("play") and not busy:
                try:
                    found["play"] = await stores.play_tracks(app.package if app else PACKAGE)
                except (StoreError, httpx.HTTPError, KeyError, ValueError) as exc:
                    log.info("live state %s at Google: %s", key, exc)
            stores.vault.write_json(live_file(key), found)
    finally:
        if client is not stores.client:
            await client.aclose()


def start_live(stores: Stores, *, force: bool = False) -> bool:
    """Refresh in the background when the last answers are older than LIVE_MAX_AGE."""
    task = stores.jobs.get(LIVE_JOB)
    if (task is not None and not task.done()) or not (force or _stale(stores)):
        return False

    async def run() -> Report:
        try:
            await refresh_live(stores)
        except Exception:  # a bug: the page still works from the hub's own records
            log.exception("live store state failed")
        return Report()

    stores.jobs[LIVE_JOB] = asyncio.create_task(run())
    return True


def review_file(key: str, store: str) -> str:
    return f"review-{key}-{store}.json"


def save_review(stores: Stores, key: str, store: str, by: str, report: Report) -> None:
    steps = [{"title": s.title, "state": s.state, "text": s.text} for s in report.steps]
    stores.vault.write_json(review_file(key, store), {"at": _now(), "by": by, "steps": steps})


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _state(steps: list[dict[str, Any]]) -> str:
    """The worst state of a result's steps: ok, warn or fail."""
    states = {str(s.get("state")) for s in steps}
    return "fail" if "fail" in states else "warn" if "warn" in states else "ok"


def _text(steps: list[dict[str, Any]]) -> str:
    return " ".join(str(s.get("text", "")) for s in steps if s.get("state") != "ok") or " ".join(
        str(s.get("text", "")) for s in steps
    )


def version_of(stores: Stores, app: OtherApp | None) -> str:
    """The version the stores get: app/pubspec.yaml here, a further app's from its
    repository (the copy of the last listing fetch), else its fixed fallback."""
    if app is None:
        return appstore.VERSION
    return appstore.listing_version(stores, app)


def _production(play: dict[str, Any] | None, code: str) -> dict[str, Any] | None:
    """The production release holding that version code, if Google has one."""
    releases: list[dict[str, Any]] = (play or {}).get("production") or []
    return next((r for r in releases if code in (r.get("codes") or [])), None)


def android_row(
    version: str,
    has_key: bool,
    job: AndroidJob,
    review: dict[str, Any] | None,
    aab: str,
    play: dict[str, Any] | None = None,
) -> Row:
    latest = job.latest or {}
    if not has_key:
        return Row("Android", "todo", "Noch nicht eingerichtet.", "setup", "Einrichten")
    if job.busy:
        return Row("Android", "wait", "Der Server baut gerade das App Bundle …")
    number, built = latest.get("build"), str(latest.get("version", ""))
    release = _production(play, str(number)) if latest and built == version else None
    if release is not None:  # also when submitted by hand in the Play Console
        status = release.get("status")
        if status == "draft":
            return Row("Android", "todo", f"Build {number} ({built}) liegt als Entwurf in der "
                       "Produktion: in der Play Console einführen.")  # fmt: skip
        if status == "halted":
            return Row("Android", "fail", f"Build {number} ({built}): die Einführung ist "
                       "angehalten (Play Console).")  # fmt: skip
        return Row("Android", "done", f"Build {number} ({built}) ist in der Produktion: bei "
                   "Google in Prüfung oder schon live.")  # fmt: skip
    if job.state == "failed":
        return Row("Android", "fail", "Der letzte Build ist fehlgeschlagen (Protokoll unten).",
                   "build-android", "Neu bauen")  # fmt: skip
    if not latest:
        return Row("Android", "todo", "Noch kein Build.", "build-android", "Release bauen")
    if built != version:
        return Row("Android", "todo", f"Build {number} ist Version {built}, nicht {version}.",
                   "build-android", "Release bauen")  # fmt: skip
    if job.state == "built":
        return Row("Android", "todo",
                   f"Build {number} ist gebaut. Google kennt die App noch nicht: dieses erste "
                   "App Bundle einmal von Hand in der Play Console hochladen.",
                   link=aab, link_label="App Bundle herunterladen")  # fmt: skip
    if review and str(review.get("at", "")) > str(latest.get("built_at", "")):
        steps: list[dict[str, Any]] = review.get("steps") or []
        state = _state(steps)
        if state == "fail":
            return Row("Android", "fail", f"Einreichen ging nicht: {_text(steps)}",
                       "review-play", "Erneut einreichen")  # fmt: skip
        if state == "warn":  # a draft to roll out in the Play Console, or an account missing
            return Row("Android", "todo", _text(steps), "review-play", "Erneut einreichen")
        return Row("Android", "done", _text(steps))
    return Row("Android", "todo", f"Build {number} (Version {built}) ist im internen Test.",
               "review-play", "Zur Prüfung einreichen")  # fmt: skip


def ios_row(
    codemagic: bool,
    build: dict[str, Any] | None,
    pushed: dict[str, Any] | None,
    review: dict[str, Any] | None,
    apple: list[dict[str, str]] | None = None,
    version: str = "",
) -> Row:
    if not codemagic:
        return Row("iOS", "todo", "Noch nicht eingerichtet.", "setup", "Einrichten")
    # Where Apple has the current version (also when submitted in App Store Connect).
    there = next((v for v in apple or [] if v.get("version") == version), None)
    state = (there or {}).get("state", "")
    if state in APPLE_REVIEW:
        return Row("iOS", "done", f"Version {version} {APPLE_REVIEW[state]}.")
    if state in APPLE_REJECTED:
        return Row("iOS", "fail", f"Apple hat Version {version} abgelehnt; die Begründung "
                   "steht in App Store Connect (App-Prüfung).", "review-appstore",
                   "Erneut einreichen")  # fmt: skip
    if build is None:
        return Row("iOS", "todo", "Noch kein Build.", "build-ios", "Release bauen")
    link, status = build_url(build), str(build.get("status") or "")
    if status in CM_FAILED:
        return Row("iOS", "fail", f"Der letzte Build bei Codemagic: {status}.", "build-ios",
                   "Neu bauen", link, "Bei Codemagic ansehen")  # fmt: skip
    if status not in CM_DONE:
        return Row("iOS", "wait", f"Codemagic baut ({status or 'gestartet'}) …",
                   link=link, link_label="Bei Codemagic ansehen")  # fmt: skip
    built_at = str(build.get("at", ""))
    if pushed and pushed.get("running"):
        return Row("iOS", "wait", "Die Übertragung zum App Store läuft …")
    steps: list[dict[str, Any]] = (pushed or {}).get("steps") or []
    chosen = next((s for s in steps if s.get("title") == "Build"), None)
    if not pushed or str(pushed.get("at", "")) < built_at or chosen is None:
        return Row("iOS", "todo", "Der Build ist fertig. Jetzt Texte, Screenshots und Build "
                   "zum App Store übertragen.", "appstore", "App Store übertragen")  # fmt: skip
    if chosen.get("state") != "ok":
        return Row("iOS", "todo", "Apple verarbeitet den Build noch (dauert meist 10–30 "
                   "Minuten); danach noch einmal übertragen.", "appstore",
                   "App Store übertragen")  # fmt: skip
    if review and str(review.get("at", "")) > str(pushed.get("at", "")):
        rsteps: list[dict[str, Any]] = review.get("steps") or []
        if any(s.get("title") in appstore.TO_PUSH for s in rsteps):  # the new build next
            return Row("iOS", "todo", _text(rsteps), "appstore", "App Store übertragen")
        if _state(rsteps) == "fail":
            return Row("iOS", "fail", f"Einreichen ging nicht: {_text(rsteps)}",
                       "review-appstore", "Erneut einreichen")  # fmt: skip
        if _state(rsteps) == "warn":
            return Row("iOS", "todo", _text(rsteps), "review-appstore", "Erneut einreichen")
        return Row("iOS", "done", _text(rsteps))
    return Row("iOS", "todo", f"{chosen.get('text', '')} Bereit für die Prüfung.",
               "review-appstore", "Zur Prüfung einreichen")  # fmt: skip


async def cards(stores: Stores) -> list[Card]:
    builds = await stores.recent_builds(limit=10)
    out: list[Card] = []
    for app in (None, *OTHER_APPS.values()):
        key = app.key if app else "allskyhub"
        version = version_of(stores, app)
        job = stores.android_job(app)
        card = Card(key, app.title if app else "allskyhub",
                    app.package if app else "org.allskyhub.app", version, android=job)  # fmt: skip
        latest_file = str((job.latest or {}).get("file", ""))
        live = live_of(stores, key)
        card.live_at = str(live.get("at", ""))
        card.rows.append(
            android_row(
                version,
                stores.vault.has(app.android_upload if app else "android_upload"),
                job,
                stores.vault.read_json(review_file(key, "play")),
                f"/admin/stores-android/{latest_file}",
                live.get("play"),
            )
        )
        codemagic = (
            stores.other_app_id(app)
            if app
            else (stores.vault.meta("codemagic") or {}).get("app_id")
        )
        ios_build = next(
            (b for b in builds
             if b.get("platform") == "ios" and str(b.get("app") or "allskyhub") == key),
            None,
        )  # fmt: skip
        pushed = appstore.state(stores, app)
        card.rows.append(
            ios_row(
                bool(codemagic),
                ios_build,
                pushed,
                stores.vault.read_json(review_file(key, "appstore")),
                live.get("apple"),
                version,
            )
        )
        listing = stores.listing_state(app)
        found = stores.apk(key)
        card.apk = found[1] if found else None
        card.test = stores.test_job(app) if app and app.test_apk else None
        results = (("Google-Play-Eintrag", listing), ("App Store", pushed),
                   ("APK", stores.apk_state(app)))  # fmt: skip
        for name, result in results:
            if result:
                card.results.append((name, result))
        out.append(card)
    return out

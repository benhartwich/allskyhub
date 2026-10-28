"""The admin's app page (/admin/apps): one card per app, one row per store, each with
where it stands and the one next step (Benjamin, 2026-10-28: the store part of the admin
page was too hard to follow).

Pure: it reads what the hub already keeps (the Android job, Codemagic's build states,
the last listing push and review submission) and decides; the routes act.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from allskyhub_server.admin import appstore
from allskyhub_server.admin.stores import (
    OTHER_APPS,
    AndroidJob,
    OtherApp,
    Report,
    Stores,
    build_url,
)

# Codemagic's build states that are over; anything else is still on its way.
CM_DONE = frozenset({"finished"})
CM_FAILED = frozenset({"failed", "canceled", "timeout", "skipped"})
ACTIONS = (
    "setup", "build-android", "build-ios", "play-listing", "appstore",
    "review-play", "review-appstore",
)  # fmt: skip


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

    @property
    def waiting(self) -> bool:
        return any(r.tone == "wait" for r in self.rows)


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
    pubspec = stores.vault.root / "listings" / app.key / "pubspec.yaml"
    return appstore.app_version(pubspec, app.version)


def android_row(
    version: str, has_key: bool, job: AndroidJob, review: dict[str, Any] | None, aab: str
) -> Row:
    latest = job.latest or {}
    if not has_key:
        return Row("Android", "todo", "Noch nicht eingerichtet.", "setup", "Einrichten")
    if job.busy:
        return Row("Android", "wait", "Der Server baut gerade das App Bundle …")
    if job.state == "failed":
        return Row("Android", "fail", "Der letzte Build ist fehlgeschlagen (Protokoll unten).",
                   "build-android", "Neu bauen")  # fmt: skip
    if not latest:
        return Row("Android", "todo", "Noch kein Build.", "build-android", "Release bauen")
    number, built = latest.get("build"), str(latest.get("version", ""))
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
) -> Row:
    if not codemagic:
        return Row("iOS", "todo", "Noch nicht eingerichtet.", "setup", "Einrichten")
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
        if any(s.get("title") == appstore.WITHDRAWN for s in rsteps):  # the new build next
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
        card.rows.append(
            android_row(
                version,
                stores.vault.has(app.android_upload if app else "android_upload"),
                job,
                stores.vault.read_json(review_file(key, "play")),
                f"/admin/stores-android/{latest_file}",
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
            )
        )
        listing = stores.listing_state(app)
        for name, result in (("Google-Play-Eintrag", listing), ("App Store", pushed)):
            if result:
                card.results.append((name, result))
        out.append(card)
    return out

"""The admin's app page (/admin/apps): one card per app, one row per store, each with
where it stands and the one next step (Benjamin, 2026-10-28: the store part of the admin
page was too hard to follow).

It reads what the hub keeps (the Android job, Codemagic's build states, the last listing
push and review submission) and what the stores say now (live-<app>.json, refreshed in
the background at most every ten minutes): a release submitted by hand in the Play
Console or App Store Connect counts too (Benjamin, 2026-10-29: rows said "failed" for
apps that were in review). The steps are pure; the routes act.

Each platform shows its whole way as steps (Benjamin, 2026-11-05: "die anzeige ist so
schlecht und verwirrend"): iOS built → sent to App Store Connect → processed by Apple →
submitted → Apple's verdict; Android built → internal test → submitted → Google. One
step is the current one and carries the one button; a store's verdict on an older build
or version is only a note and never hides where the new build stands.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from allskyhub_server.admin import appstore
from allskyhub_server.admin.deploy import API as GITHUB
from allskyhub_server.admin.deploy import github_headers
from allskyhub_server.admin.stores import (
    APP_PATHS,
    OTHER_APPS,
    PACKAGE,
    AndroidJob,
    OtherApp,
    Report,
    StoreError,
    Stores,
    build_url,
    github_token,
)

log = logging.getLogger(__name__)

# Codemagic's build states that are over; anything else is still on its way.
CM_DONE = frozenset({"finished"})
CM_FAILED = frozenset({"failed", "canceled", "timeout", "skipped"})
ACTIONS = (
    "setup", "build-android", "build-ios", "play-listing", "appstore",
    "review-play", "review-appstore", "apk", "build-test-apk", "build-screenshots",
    "take-screenshots",
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
# main's commits read per app and path at most (GitHub's page); more show as "100+".
COMMITS_MAX = 100
COMMITS_DEFAULT_DAYS = 30  # how far back when no build is known


@dataclass(frozen=True)
class Step:
    """One stage of a platform's way: done, current (the next thing to do), wait (the hub
    or Codemagic is busy), store (Apple or Google have it), fail, or todo (later)."""

    name: str
    state: str
    text: str = ""
    at: str = ""  # ISO time, shown in the hub's zone
    link: str | None = None
    link_label: str = ""
    action: str | None = None  # one of ACTIONS
    label: str = ""


@dataclass(frozen=True)
class Steps:
    store: str  # "Android" or "iOS"
    version: str
    steps: tuple[Step, ...]

    @property
    def row(self) -> Row:
        """The one line for the overview and the commits line (_pending.html): the
        current step, else the last one reached."""
        tones = {"current": "todo", "wait": "wait", "fail": "fail"}
        now = next((s for s in self.steps if s.state in tones), None)
        if now is not None:
            return Row(self.store, tones[now.state], now.text, now.action, now.label,
                       now.link, now.link_label)  # fmt: skip
        reached = [s for s in self.steps if s.state in ("done", "store")]
        last = reached[-1] if reached else self.steps[0]
        return Row(self.store, "done", last.text, None, "", last.link, last.link_label)


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
    steps: list[Steps] = field(default_factory=lambda: list[Steps]())
    android: AndroidJob | None = None
    results: list[tuple[str, dict[str, Any]]] = field(
        default_factory=lambda: list[tuple[str, dict[str, Any]]]()
    )
    live_at: str = ""  # when Apple and Google were last asked
    # main's commits that change the app and are newer than the platform's last build:
    # {"android": n, "ios": n}; None where unknown (no build, GitHub not asked yet).
    pending: dict[str, int | None] = field(default_factory=dict[str, int | None])
    apk: dict[str, Any] | None = None  # the APK for /download/<key>.apk (Stores.apk)
    test: AndroidJob | None = None  # the server's test APK (OtherApp.test_apk)
    screenshots: bool = False  # its codemagic.yaml can make store screenshots
    # The demo account for the screenshot run: None when the app needs none, "" when it
    # is not entered yet, else its login (OtherApp.review_vars).
    review_login: str | None = None
    review_at: str = ""  # when the demo account was saved

    @property
    def waiting(self) -> bool:
        """Something of this app is on its way: the card then polls itself."""
        return (
            any(r.tone == "wait" for r in self.rows)
            or bool(self.test and self.test.busy)
            or any(result.get("running") for _, result in self.results)
        )


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
            try:
                found["ios_built"] = await stores.last_ios_build(app)
            except (StoreError, httpx.HTTPError, KeyError, ValueError) as exc:
                log.info("last iOS build of %s: %s", key, exc)
            try:
                found["commits"] = await main_commits(client, stores, app, _since(stores, app))
            except (StoreError, httpx.HTTPError, KeyError, ValueError) as exc:
                log.info("main's commits of %s: %s", key, exc)
            stores.vault.write_json(live_file(key), found)
    finally:
        if client is not stores.client:
            await client.aclose()


# Which app needs a new build (Benjamin, 2026-10-29: "welche Apps einen neuen Stand haben
# und entsprechend neu gebaut und eingereicht werden müssen"): main's commits that touch
# the app's folders, against each platform's last build.


def _when(text: object) -> dt.datetime | None:
    try:
        when = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def _ios_build(stores: Stores, key: str) -> dict[str, Any] | None:
    """The newest iOS build started here (Codemagic builds main as it is then)."""
    return next(
        (b for b in stores.vault.builds()
         if b.get("platform") == "ios" and str(b.get("app") or "allskyhub") == key),
        None,
    )  # fmt: skip


def _ios_at(stores: Stores, key: str, live: dict[str, Any]) -> str:
    """The start of the last iOS build: Codemagic's own record, else the hub's."""
    return str(live.get("ios_built") or (_ios_build(stores, key) or {}).get("at", ""))


def _since(stores: Stores, app: OtherApp | None) -> str:
    """From the older of the two last builds on, a day earlier for clock skew."""
    key = app.key if app else "allskyhub"
    bases = [
        _when((stores.android_job(app).latest or {}).get("built_at")),
        _when(_ios_at(stores, key, live_of(stores, key))),
    ]
    known = [b for b in bases if b is not None]
    start = (
        min(known) - dt.timedelta(days=1)
        if len(known) == len(bases)
        else dt.datetime.now(dt.UTC) - dt.timedelta(days=COMMITS_DEFAULT_DAYS)
    )
    return start.isoformat(timespec="seconds")


async def main_commits(
    client: httpx.AsyncClient, stores: Stores, app: OtherApp | None, since: str
) -> list[dict[str, str]]:
    """main's commits since ``since`` that touch the app, newest first (sha, date, title)."""
    repo = app.repo if app else stores.repo
    paths = app.app_paths if app else APP_PATHS
    headers = github_headers(github_token(stores.vault))
    found: dict[str, dict[str, str]] = {}
    for path in paths or ("",):
        params = {"sha": "main", "since": since, "per_page": str(COMMITS_MAX)}
        if path:
            params["path"] = path
        r = await client.get(f"{GITHUB}/repos/{repo}/commits", params=params, headers=headers)
        if r.status_code != 200:
            raise StoreError(f"GitHub antwortet HTTP {r.status_code}.")
        body: Any = r.json()
        for c in cast(list[Any], body) if isinstance(body, list) else []:
            if not isinstance(c, dict):
                continue
            item = cast(dict[str, Any], c)
            commit = cast(dict[str, Any], item.get("commit") or {})
            committer = cast(dict[str, Any], commit.get("committer") or {})
            sha = str(item.get("sha", ""))
            if sha:
                found[sha] = {
                    "sha": sha,
                    "date": str(committer.get("date", "")),
                    "title": str(commit.get("message", "")).split("\n", 1)[0][:120],
                }
    return sorted(found.values(), key=lambda c: c["date"], reverse=True)


def pending(commits: list[dict[str, str]] | None, sha: str, at: str) -> int | None:
    """How many of main's app commits a build of ``sha`` (or, without one, started at
    ``at``) does not have; None when that is not known."""
    if commits is None:
        return None
    if sha:
        for i, c in enumerate(commits):
            if c["sha"].startswith(sha):
                return i
    base = _when(at)
    if base is None:
        return None
    return sum(1 for c in commits if (d := _when(c["date"])) is not None and d > base)


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


def _steps(store: str, version: str, names: tuple[str, ...], *done: Step) -> Steps:
    """The steps given, then the rest of ``names`` still to come."""
    rest = tuple(Step(n, "todo") for n in names[len(done) :])
    return Steps(store, version, (*done, *rest))


ANDROID = ("Gebaut", "Interner Test", "Eingereicht", "Google")


def android_steps(
    version: str,
    has_key: bool,
    job: AndroidJob,
    review: dict[str, Any] | None,
    aab: str,
    play: dict[str, Any] | None = None,
) -> Steps:
    latest = job.latest or {}

    def way(*done: Step) -> Steps:
        return _steps("Android", version, ANDROID, *done)

    if not has_key:
        return way(Step("Gebaut", "current", "Noch nicht eingerichtet.", action="setup",
                        label="Einrichten"))  # fmt: skip
    if job.busy:
        return way(Step("Gebaut", "wait", "Der Server baut gerade das App Bundle …"))
    number, built = latest.get("build"), str(latest.get("version", ""))
    at = str(latest.get("built_at", ""))
    made = Step("Gebaut", "done", f"Build {number} (Version {built})", at, aab or None,
                "App Bundle ↓")  # fmt: skip
    release = _production(play, str(number)) if latest and built == version else None
    if release is not None:  # also when submitted by hand in the Play Console
        status = release.get("status")
        earlier = (made, Step("Interner Test", "done"), Step("Eingereicht", "done"))
        if status == "draft":
            draft = (f"Build {number} ({built}) liegt als Entwurf in der Produktion: in der "
                     "Play Console einführen.")  # fmt: skip
            return way(*earlier, Step("Google", "current", draft))
        if status == "halted":
            return way(*earlier, Step("Google", "fail", f"Build {number} ({built}): die "
                       "Einführung ist angehalten (Play Console)."))  # fmt: skip
        return way(*earlier, Step("Google", "store", f"Build {number} ({built}) ist in der "
                   "Produktion: bei Google in Prüfung oder schon live."))  # fmt: skip
    if job.state == "failed":
        return way(Step("Gebaut", "fail", "Der letzte Build ist fehlgeschlagen (Protokoll "
                        "unten).", action="build-android", label="Neu bauen"))  # fmt: skip
    if not latest:
        return way(Step("Gebaut", "current", "Noch kein Build.", action="build-android",
                        label="Release bauen"))  # fmt: skip
    if built != version:
        other = f"Build {number} ist Version {built}, nicht {version}."
        return way(Step("Gebaut", "current", other, at, action="build-android",
                        label="Release bauen"))  # fmt: skip
    if job.state == "built":
        return way(made, Step("Interner Test", "current", f"Build {number} ist gebaut. "
                   "Google kennt die App noch nicht: dieses erste App Bundle einmal von Hand "
                   "in der Play Console hochladen.", link=aab, link_label="App Bundle "
                   "herunterladen"))  # fmt: skip
    tested = Step("Interner Test", "done", "Google Play: interner Test")
    google = Step("Google", "todo", _older_release(play, str(number)))
    if review and str(review.get("at", "")) > at:
        steps: list[dict[str, Any]] = review.get("steps") or []
        state, sent_at = _state(steps), str(review.get("at", ""))
        if state == "fail":
            return way(made, tested, Step("Eingereicht", "fail", f"Einreichen ging nicht: "
                       f"{_text(steps)}", sent_at, action="review-play",
                       label="Erneut einreichen"), google)  # fmt: skip
        if state == "warn":  # a draft to roll out in the Play Console, or an account missing
            return way(made, tested, Step("Eingereicht", "current", _text(steps), sent_at,
                       action="review-play", label="Erneut einreichen"), google)  # fmt: skip
        return way(made, tested, Step("Eingereicht", "done", _text(steps), sent_at), google)
    return way(made, tested, Step("Eingereicht", "current", f"Build {number} (Version "
               f"{built}) ist im internen Test.", action="review-play",
               label="Zur Prüfung einreichen"), google)  # fmt: skip


def _older_release(play: dict[str, Any] | None, code: str) -> str:
    """A note on what Google has in production now, when it is another build."""
    releases: list[dict[str, Any]] = (play or {}).get("production") or []
    other = next((r for r in releases if code not in (r.get("codes") or [])), None)
    if other is None:
        return ""
    found: list[Any] = other.get("codes") or []
    codes = ", ".join(str(c) for c in found)
    return f"zuletzt: Build {codes} in der Produktion ({other.get('status', '')})"


def android_row(
    version: str,
    has_key: bool,
    job: AndroidJob,
    review: dict[str, Any] | None,
    aab: str,
    play: dict[str, Any] | None = None,
) -> Row:
    return android_steps(version, has_key, job, review, aab, play).row


IOS = ("Gebaut", "Übertragen", "Bei Apple", "Eingereicht", "Apple")
APPLE_SHORT = {"REJECTED": "abgelehnt", "METADATA_REJECTED": "abgelehnt (Texte)",
               "INVALID_BINARY": "ungültiger Build", "DEVELOPER_REJECTED": "zurückgezogen",
               "READY_FOR_SALE": "im App Store", "READY_FOR_DISTRIBUTION": "im App Store",
               "PREPARE_FOR_SUBMISSION": "in Vorbereitung"}  # fmt: skip


def _apple_note(apple: list[dict[str, str]] | None, version: str) -> str:
    """What Apple last said about another version (a note, never the current step)."""
    other = next((v for v in apple or [] if v.get("version") != version), None)
    if other is None:
        return ""
    state = other.get("state", "")
    return f"zuletzt: {other.get('version', '')} {APPLE_SHORT.get(state, state.lower())}"


def ios_steps(
    codemagic: bool,
    build: dict[str, Any] | None,
    pushed: dict[str, Any] | None,
    review: dict[str, Any] | None,
    apple: list[dict[str, str]] | None = None,
    version: str = "",
) -> Steps:
    def way(*done: Step) -> Steps:
        return _steps("iOS", version, IOS, *done)

    if not codemagic:
        return way(Step("Gebaut", "current", "Noch nicht eingerichtet.", action="setup",
                        label="Einrichten"))  # fmt: skip
    # Where Apple has the current version (also when submitted in App Store Connect).
    there = next((v for v in apple or [] if v.get("version") == version), None)
    state = (there or {}).get("state", "")
    note = _apple_note(apple, version)
    link = build_url(build) if build else None
    status = str((build or {}).get("status") or "")
    built_at = str((build or {}).get("at", ""))
    steps: list[dict[str, Any]] = (pushed or {}).get("steps") or []
    pushed_at = str((pushed or {}).get("at", ""))
    chosen = next((s for s in steps if s.get("title") == "Build"), None)
    fresh = bool(pushed) and pushed_at >= built_at and chosen is not None  # this build pushed
    number = _build_number(str((chosen or {}).get("text", ""))) if fresh else ""
    made = Step("Gebaut", "done", f"Build {number}" if number else "bei Codemagic", built_at,
                link, "Codemagic ↗")  # fmt: skip
    if state in APPLE_REVIEW:
        apple_state = "done" if state in ("READY_FOR_SALE", "READY_FOR_DISTRIBUTION") else "store"
        return Steps("iOS", version, (
            made if build else Step("Gebaut", "done"), Step("Übertragen", "done"),
            Step("Bei Apple", "done"), Step("Eingereicht", "done"),
            Step("Apple", apple_state, f"Version {version} {APPLE_REVIEW[state]}."),
        ))  # fmt: skip
    rsteps: list[dict[str, Any]] = (review or {}).get("steps") or []
    sent_at = str((review or {}).get("at", ""))
    rejected = state in APPLE_REJECTED
    if build is None:
        if rejected:  # rejected, and no build started here: build anew
            return way(Step("Gebaut", "current", f"Apple hat Version {version} abgelehnt; "
                            "für einen neuen Versuch neu bauen.", action="build-ios",
                            label="Release bauen"))  # fmt: skip
        return way(Step("Gebaut", "current", "Noch kein Build.", action="build-ios",
                        label="Release bauen"))  # fmt: skip
    if status in CM_FAILED:
        failed = f"Der letzte Build bei Codemagic: {status}."
        return way(Step("Gebaut", "fail", failed, built_at, link, "Bei Codemagic ansehen",
                        "build-ios", "Neu bauen"))  # fmt: skip
    if status not in CM_DONE:
        return way(Step("Gebaut", "wait", f"Codemagic baut ({status or 'gestartet'}) …",
                        built_at, link, "Bei Codemagic ansehen"))  # fmt: skip
    verdict = Step("Apple", "todo", f"zuletzt: {version} abgelehnt" if rejected else note)
    if pushed and pushed.get("running"):
        return way(made, Step("Übertragen", "wait", "Die Übertragung zum App Store läuft …"),
                   Step("Bei Apple", "todo"), Step("Eingereicht", "todo"), verdict)  # fmt: skip
    if not fresh:
        return way(made, Step("Übertragen", "current", "Der Build ist fertig. Jetzt Texte, "
                   "Screenshots und Build zum App Store übertragen.", action="appstore",
                   label="App Store übertragen"), Step("Bei Apple", "todo"),
                   Step("Eingereicht", "todo"), verdict)  # fmt: skip
    sent = Step("Übertragen", "done", "Texte, Screenshots und Build", pushed_at)
    if (chosen or {}).get("state") != "ok":
        return way(made, sent, Step("Bei Apple", "current", "Apple verarbeitet den Build noch "
                   "(dauert meist 10–30 Minuten); danach noch einmal übertragen.",
                   action="appstore", label="App Store übertragen"),
                   Step("Eingereicht", "todo"), verdict)  # fmt: skip
    ready = Step("Bei Apple", "done", f"{(chosen or {}).get('text', '')} In TestFlight.".strip())
    if review and sent_at > pushed_at:  # submitted after this push
        if any(s.get("title") in appstore.TO_PUSH for s in rsteps):  # the new build next
            return way(made, Step("Übertragen", "current", _text(rsteps), sent_at,
                       action="appstore", label="App Store übertragen"),
                       Step("Bei Apple", "todo"), Step("Eingereicht", "todo"), verdict)  # fmt: skip
        if _state(rsteps) == "fail":
            return way(made, sent, ready, Step("Eingereicht", "fail", f"Einreichen ging nicht: "
                       f"{_text(rsteps)}", sent_at, action="review-appstore",
                       label="Erneut einreichen"), verdict)  # fmt: skip
        if _state(rsteps) == "warn":
            return way(made, sent, ready, Step("Eingereicht", "current", _text(rsteps), sent_at,
                       action="review-appstore", label="Erneut einreichen"), verdict)  # fmt: skip
        if rejected:  # what was submitted here came back
            return way(made, sent, ready, Step("Eingereicht", "done", _text(rsteps), sent_at),
                       Step("Apple", "fail", f"Apple hat Version {version} abgelehnt; die "
                            "Begründung steht in App Store Connect (App-Prüfung).",
                            action="review-appstore", label="Erneut einreichen"))  # fmt: skip
        return way(made, sent, ready, Step("Eingereicht", "done", _text(rsteps), sent_at),
                   Step("Apple", "todo", note))  # fmt: skip
    # This build went to Apple after the last submission: a rejection is only a note now.
    return way(made, sent, ready, Step("Eingereicht", "current", "Bereit für die Prüfung.",
               action="review-appstore",
               label="Erneut einreichen" if rejected else "Zur Prüfung einreichen"),
               verdict)  # fmt: skip


def _build_number(text: str) -> str:
    """ "Build 57 gewählt." → "57"."""
    found = re.search(r"Build (\S+?)[ .]", text)
    return found[1] if found else ""


def ios_row(
    codemagic: bool,
    build: dict[str, Any] | None,
    pushed: dict[str, Any] | None,
    review: dict[str, Any] | None,
    apple: list[dict[str, str]] | None = None,
    version: str = "",
) -> Row:
    return ios_steps(codemagic, build, pushed, review, apple, version).row


async def cards(stores: Stores, only: str | None = None) -> list[Card]:
    """Every app's card, or only the one with the key ``only`` (a card polling itself)."""
    builds = await stores.recent_builds(limit=10)
    out: list[Card] = []
    for app in (None, *OTHER_APPS.values()):
        key = app.key if app else "allskyhub"
        if only is not None and key != only:
            continue
        version = version_of(stores, app)
        job = stores.android_job(app)
        card = Card(key, app.title if app else "allskyhub",
                    app.package if app else "org.allskyhub.app", version, android=job)  # fmt: skip
        # allskyhub's own come from sample data (app/test/store): the owner's screens
        # need an account a simulator run would not have.
        card.screenshots = bool(app and app.screenshots)
        if app and app.review_vars:
            saved = stores.vault.meta(app.review) or {}
            card.review_login = str(saved.get("login", ""))
            card.review_at = str(saved.get("at", ""))
        latest_file = str((job.latest or {}).get("file", ""))
        live = live_of(stores, key)
        card.live_at = str(live.get("at", ""))
        commits = cast(list[dict[str, str]] | None, live.get("commits"))
        card.pending = {
            "android": pending(commits, str((job.latest or {}).get("commit", "")),
                               str((job.latest or {}).get("built_at", ""))),
            "ios": pending(commits, "", _ios_at(stores, key, live)),
        }  # fmt: skip
        card.steps.append(
            android_steps(
                version,
                stores.vault.has(app.android_upload if app else "android_upload"),
                job,
                stores.vault.read_json(review_file(key, "play")),
                f"/admin/stores-android/{latest_file}" if latest_file else "",
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
        card.steps.append(
            ios_steps(
                bool(codemagic),
                ios_build,
                pushed,
                stores.vault.read_json(review_file(key, "appstore")),
                live.get("apple"),
                version,
            )
        )
        card.rows = [s.row for s in card.steps]
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

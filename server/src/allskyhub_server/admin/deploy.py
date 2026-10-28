"""Deploy from the admin page.

The hub runs without root and never deploys itself. It shows what runs and what is new
on ``main`` (GitHub's public API, cached), and when CI is green it leaves a request in
``<data>/deploy/request`` with the sha it showed. The root helper
(deploy/admin/allskyhub-deploy.sh, started by a systemd path unit) pulls exactly that
sha if it still is ``origin/main``, runs the migrations, restarts the hub and answers
in ``state`` (running, ok, failed) and ``log``.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

SHA = re.compile(r"^[0-9a-f]{40}$")
CACHE_S = 60
API = "https://api.github.com"
GOOD_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
LOG_TAIL = 80
STATES = ("requested", "running", "ok", "failed")


class DeployError(Exception):
    """German message for the page."""


@dataclass(frozen=True)
class Commit:
    sha: str
    title: str
    author: str
    at: dt.datetime | None


@dataclass(frozen=True)
class Upstream:
    """``main`` on GitHub, compared with what runs here."""

    sha: str
    commits: list[Commit] = field(default_factory=lambda: list[Commit]())  # newest first
    ahead: int = 0
    ci: str = "unknown"  # "success", "failure", "pending" or "unknown"
    ci_url: str = ""


@dataclass(frozen=True)
class Job:
    """The root helper's answer to the last request."""

    state: str  # "idle" or one of STATES
    target: str | None
    log: str
    changed_at: dt.datetime | None

    @property
    def busy(self) -> bool:
        return self.state in ("requested", "running")


def _commit(item: dict[str, Any]) -> Commit:
    info: dict[str, Any] = item.get("commit") or {}
    author: dict[str, Any] = info.get("author") or {}
    when = author.get("date")
    at = dt.datetime.fromisoformat(when.replace("Z", "+00:00")) if isinstance(when, str) else None
    message = str(info.get("message") or "")
    return Commit(
        sha=str(item.get("sha", "")),
        title=message.splitlines()[0] if message else "",
        author=str(author.get("name") or ""),
        at=at,
    )


def ci_state(check_runs: list[dict[str, Any]], only: tuple[str, ...] = ()) -> str:
    """The commit's check runs together: green only when every one finished well. With
    ``only``, just the runs whose name starts with one of them (the hub's tests, not the
    Raspberry Pi image that takes far longer to build)."""
    if only:
        check_runs = [r for r in check_runs if str(r.get("name", "")).startswith(only)]
    if not check_runs:
        return "unknown"
    if any(run.get("status") != "completed" for run in check_runs):
        return "pending"
    if all(run.get("conclusion") in GOOD_CONCLUSIONS for run in check_runs):
        return "success"
    return "failure"


class GitHub:
    """Anonymous GitHub API (60 requests an hour), so answers are kept for a minute."""

    def __init__(
        self, repo: str, client: httpx.AsyncClient | None = None, checks: tuple[str, ...] = ()
    ) -> None:
        self.repo = repo
        self.checks = checks
        self._client = client
        self._cached: tuple[float, str | None, Upstream] | None = None

    async def upstream(self, running: str | None) -> Upstream:
        cached = self._cached
        if cached and cached[1] == running and time.monotonic() - cached[0] < CACHE_S:
            return cached[2]
        client = self._client or httpx.AsyncClient(timeout=10)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "allskyhub-hub"}
        try:
            r = await client.get(f"{API}/repos/{self.repo}/commits/main", headers=headers)
            r.raise_for_status()
            head = _commit(r.json())
            if not SHA.fullmatch(head.sha):
                raise ValueError("no sha for main")
            commits = [head] if head.sha != running else []
            ahead = len(commits)
            if running and commits:
                r = await client.get(
                    f"{API}/repos/{self.repo}/compare/{running}...{head.sha}", headers=headers
                )
                if r.status_code == 200:
                    body: dict[str, Any] = r.json()
                    ahead = int(body.get("ahead_by") or 0) or ahead
                    commits = [_commit(c) for c in reversed(body.get("commits") or [])] or commits
            r = await client.get(
                f"{API}/repos/{self.repo}/commits/{head.sha}/check-runs", headers=headers
            )
            r.raise_for_status()
            runs: list[dict[str, Any]] = r.json().get("check_runs") or []
        except httpx.HTTPStatusError as exc:
            log.warning("admin: GitHub not reachable: %s", exc)
            raise DeployError(_refused(exc.response)) from exc
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("admin: GitHub not reachable: %s", exc)
            raise DeployError("GitHub ist gerade nicht erreichbar.") from exc
        finally:
            if self._client is None:
                await client.aclose()
        result = Upstream(
            sha=head.sha,
            commits=commits,
            ahead=ahead,
            ci=ci_state(runs, self.checks),
            ci_url=f"https://github.com/{self.repo}/commit/{head.sha}",
        )
        self._cached = (time.monotonic(), running, result)
        return result

    def forget(self) -> None:
        self._cached = None


def _refused(r: httpx.Response) -> str:
    """GitHub's anonymous limit (60 an hour) is per address, shared with everything else
    on the server: say so, with the time it resets."""
    if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
        try:
            reset = dt.datetime.fromtimestamp(int(r.headers["x-ratelimit-reset"]), dt.UTC)
        except (KeyError, ValueError):
            return "GitHub-Abrufgrenze dieses Servers erreicht (60 pro Stunde)."
        return (
            "GitHub-Abrufgrenze dieses Servers erreicht (60 pro Stunde); wieder ab "
            f"{reset:%H:%M} UTC."
        )
    return "GitHub ist gerade nicht erreichbar."


def spool(data_dir: Path) -> Path:
    return data_dir / "deploy"


def job(data_dir: Path) -> Job:
    """What the root helper last reported (or that a request is still waiting)."""
    folder = spool(data_dir)

    def read(name: str) -> str:
        try:
            return (folder / name).read_text(errors="replace")
        except OSError:
            return ""

    changed: dt.datetime | None = None
    for name in ("state", "request"):
        try:
            mtime = dt.datetime.fromtimestamp((folder / name).stat().st_mtime, dt.UTC)
        except OSError:
            continue
        changed = mtime if changed is None else max(changed, mtime)
    state = "requested" if (folder / "request").is_file() else read("state").strip()
    target = (read("request") or read("target")).split("\n", 1)[0].strip()
    return Job(
        state=state if state in STATES else "idle",
        target=target if SHA.fullmatch(target) else None,
        log="\n".join(read("log").splitlines()[-LOG_TAIL:]),
        changed_at=changed,
    )


def request(data_dir: Path, sha: str, by: str, running: str | None, upstream: Upstream) -> None:
    """Leave the request for the root helper; refuses what the helper would refuse too."""
    if not SHA.fullmatch(sha) or sha != upstream.sha:
        raise DeployError("Auf main ist inzwischen etwas Neues. Bitte die Seite neu laden.")
    if sha == running:
        raise DeployError("Diese Version läuft schon.")
    if upstream.ci != "success":
        raise DeployError("Die CI für diese Version ist nicht grün.")
    if job(data_dir).busy:
        raise DeployError("Es läuft schon ein Deploy.")
    folder = spool(data_dir)
    folder.mkdir(mode=0o750, exist_ok=True)
    tmp = folder / "request.tmp"
    tmp.write_text(f"{sha}\n{by}\n")
    tmp.replace(folder / "request")

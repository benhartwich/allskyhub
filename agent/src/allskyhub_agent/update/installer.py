"""Install agent updates atomically and roll back unhealthy ones (SPEC §8).

Ported from myboxi's updater. Layout on the camera::

    /opt/allskyhub-agent/releases/<version>/.venv   one directory per version
    /opt/allskyhub-agent/current -> releases/<version>

Every step survives a power cut: a release directory appears only complete (extracted
next to it, then renamed), `current` changes with one `rename`, and the state file
remembers an unfinished switch, so the next run checks the health or rolls back.

The updater runs as root from a systemd timer (the agent can't write /opt or restart
itself). It switches only during the day, so a night's capture is never interrupted, and
asks the running agent over its local web UI whether the new version is healthy.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import shutil
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

from allskyhub_agent.update.manifest import ManifestError, fetch_manifest
from allskyhub_protocol.updates import UpdateManifest, is_newer

log = logging.getLogger(__name__)

HEALTH_TIMEOUT_S = 300.0  # a night frame can take a minute, the camera a while to open
CHUNK = 256 * 1024

Status = Callable[[], dict[str, Any] | None]
Runner = Callable[[list[str]], int]
Sleep = Callable[[float], None]
Monotonic = Callable[[], float]


def read_state(path: Path) -> dict[str, Any] | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


class UpdateFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Paths:
    install_dir: Path  # /opt/allskyhub-agent
    work_dir: Path  # downloads
    state_file: Path  # readable by the agent, for status
    keys_dir: Path

    @property
    def releases(self) -> Path:
        return self.install_dir / "releases"

    @property
    def current(self) -> Path:
        return self.install_dir / "current"


@dataclass
class Updater:
    paths: Paths
    manifest_url: str
    http: httpx.Client
    status: Status  # the running agent's /api/status, None when unreachable
    run: Runner
    sleep: Sleep
    monotonic: Monotonic
    fallback_version: str
    service: str = "allskyhub-agent.service"
    health_timeout_s: float = HEALTH_TIMEOUT_S
    _state: dict[str, Any] = field(default_factory=dict[str, Any])

    # --- state file ------------------------------------------------------------------------

    def load_state(self) -> dict[str, Any]:
        return read_state(self.paths.state_file) or {}

    def save_state(self, state: str, version: str | None = None, **extra: Any) -> None:
        keep = {k: self._state[k] for k in ("failed_version",) if k in self._state}
        self._state = keep | {
            "state": state,
            "version": version,
            "at": datetime.now(UTC).isoformat(),
        } | extra  # fmt: skip
        tmp = self.paths.state_file.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self._state), encoding="utf-8")
        tmp.chmod(0o644)
        tmp.replace(self.paths.state_file)
        log.info("update state %s %s %s", state, version or "", extra or "")

    def current_version(self) -> str:
        try:
            return self.paths.current.readlink().name
        except OSError:
            return self.fallback_version

    # --- the run ---------------------------------------------------------------------------

    def run_once(self) -> None:
        self._state = self.load_state()
        if self._state.get("state") == "installing":
            self._finish_interrupted()
        self._update()

    def _update(self) -> None:
        current = self.current_version()
        try:
            manifest = fetch_manifest(self.http, self.manifest_url, self.paths.keys_dir)
        except httpx.HTTPError as exc:
            log.info("no update manifest (offline or nothing published): %s", exc)
            return
        except ManifestError as exc:
            if exc.code == "no_keys":  # an image without signing keys: updates are off
                log.info("no trusted update keys in %s; updates are off", self.paths.keys_dir)
                return
            self.save_state("failed", None, code=exc.code)
            return
        version = manifest.version
        if not is_newer(version, current):
            if self._state.get("state") not in ("installed", "rolled_back"):
                self.save_state("up_to_date", current)
            return
        if self._state.get("failed_version") == version:
            return  # rolled back before: wait for a newer release
        try:
            release = self._prepare(manifest)
        except UpdateFailed as exc:
            self.save_state("failed", version, code=exc.code)
            return
        before = self.status()
        if before is not None and _mode(before) == "night":
            self.save_state("waiting", version)  # never interrupt a night: next run
            return
        self._switch(release, version, current, was_capturing=_frames(before) > 0)

    def _prepare(self, manifest: UpdateManifest) -> Path:
        release = self.paths.releases / manifest.version
        if release.is_dir():
            return release
        self.save_state("downloading", manifest.version)
        free = shutil.disk_usage(self.paths.install_dir).free
        if free < manifest.bundle.size * 5 + 50 * 1024 * 1024:
            raise UpdateFailed("no_space")
        bundle = self.download(manifest)
        try:
            return self.unpack(bundle, manifest.version)
        finally:
            bundle.unlink(missing_ok=True)

    def _switch(self, release: Path, version: str, previous: str, was_capturing: bool) -> None:
        self.save_state("installing", version, previous=previous, was_capturing=was_capturing)
        self.point_current(release)
        self.restart_agent()
        if self.healthy(version, was_capturing):
            self.save_state("installed", version)
            self.cleanup(keep={version, previous})
            return
        self._roll_back(version, previous)

    def _roll_back(self, version: str, previous: str) -> None:
        log.error("version %s not healthy, rolling back to %s", version, previous)
        old = self.paths.releases / previous
        if old.is_dir():
            self.point_current(old)
            self.restart_agent()
        self._state["failed_version"] = version
        self.save_state("rolled_back", version, code="unhealthy")

    def _finish_interrupted(self) -> None:
        """Power cut between switching and the health check: check now."""
        version, previous = self._state.get("version"), self._state.get("previous")
        if not isinstance(version, str) or not isinstance(previous, str):
            return
        if self.current_version() != version:
            self.save_state("waiting", version)  # cut before the switch: try again
            return
        if self.healthy(version, bool(self._state.get("was_capturing"))):
            self.save_state("installed", version)
        else:
            self._roll_back(version, previous)

    # --- steps -----------------------------------------------------------------------------

    def download(self, manifest: UpdateManifest) -> Path:
        work = self.paths.work_dir
        work.mkdir(parents=True, exist_ok=True)
        part = work / f"allskyhub-agent-{manifest.version}.tar.xz.part"
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if 0 < have < manifest.bundle.size else {}
        try:
            with self.http.stream(
                "GET", manifest.bundle.url, headers=headers, follow_redirects=True
            ) as response:
                if response.status_code == 200:
                    have = 0
                elif response.status_code != 206:
                    raise UpdateFailed("download")
                with part.open("r+b" if have else "wb") as fh:
                    fh.seek(have)
                    fh.truncate()
                    for chunk in response.iter_bytes(CHUNK):
                        have += len(chunk)
                        if have > manifest.bundle.size:
                            raise UpdateFailed("checksum")
                        fh.write(chunk)
        except httpx.HTTPError:
            raise UpdateFailed("download") from None
        digest = hashlib.sha256()
        with part.open("rb") as fh:
            while block := fh.read(CHUNK):
                digest.update(block)
        if have != manifest.bundle.size or digest.hexdigest() != manifest.bundle.sha256:
            part.unlink(missing_ok=True)
            raise UpdateFailed("checksum")
        done = work / f"allskyhub-agent-{manifest.version}.tar.xz"
        part.replace(done)
        return done

    def unpack(self, bundle: Path, version: str) -> Path:
        releases = self.paths.releases
        releases.mkdir(parents=True, exist_ok=True)
        staging = releases / f".partial-{version}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            with tarfile.open(bundle, "r:xz") as tar:
                for member in tar.getmembers():
                    name = member.name.removeprefix("./")
                    parts = Path(name).parts
                    if (name != version and not name.startswith(f"{version}/")) or ".." in parts:
                        raise UpdateFailed("bad_bundle")
                    if member.islnk() and ".." in Path(member.linkname).parts:
                        raise UpdateFailed("bad_bundle")
                if hasattr(tarfile, "tar_filter"):  # Python >= 3.11.4 (Trixie: 3.13)
                    tar.extractall(staging, filter="tar", numeric_owner=True)
                else:  # the names were checked above
                    tar.extractall(staging, numeric_owner=True)  # noqa: S202
        except (tarfile.TarError, OSError, EOFError, LookupError):
            shutil.rmtree(staging, ignore_errors=True)
            raise UpdateFailed("bad_bundle") from None
        except UpdateFailed:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        target = releases / version
        (staging / version).replace(target)
        shutil.rmtree(staging, ignore_errors=True)
        return target

    def point_current(self, release: Path) -> None:
        tmp = self.paths.install_dir / ".current.new"
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        tmp.symlink_to(Path("releases") / release.name)
        tmp.replace(self.paths.current)

    def restart_agent(self) -> None:
        code = self.run(["systemctl", "restart", self.service])
        if code != 0:
            log.error("could not restart the agent (exit %d)", code)

    def healthy(self, version: str, was_capturing: bool) -> bool:
        """The new version answers with its version, and captures if the old one did."""
        deadline = self.monotonic() + self.health_timeout_s
        while self.monotonic() < deadline:
            st = self.status()
            ok = st is not None and st.get("version") == version
            if ok and (not was_capturing or _frames(st) > 0):
                return True
            self.sleep(5.0)
        return False

    def cleanup(self, keep: set[str]) -> None:
        for entry in self.paths.releases.iterdir():
            if entry.name not in keep and not entry.name.startswith("."):
                shutil.rmtree(entry, ignore_errors=True)


def _frames(status: dict[str, Any] | None) -> int:
    if status is None:
        return 0
    n = status.get("frames")
    return n if isinstance(n, int) else 0


def _mode(status: dict[str, Any]) -> str | None:
    frame = status.get("frame")
    if isinstance(frame, dict):
        mode = cast(dict[str, Any], frame).get("mode")
        return mode if isinstance(mode, str) else None
    return None

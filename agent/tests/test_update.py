"""Signed agent updates (SPEC §8): manifest signature, atomic switch, health, rollback."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from allskyhub_agent.update.installer import Paths, Updater
from allskyhub_agent.update.manifest import verify_signature
from allskyhub_protocol.updates import is_newer

URL = "https://updates.test/channel-stable/manifest.json"
BUNDLE_URL = "https://updates.test/allskyhub-agent-{v}-arm64.tar.xz"


@pytest.fixture
def key(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    private = Ed25519PrivateKey.generate()
    trusted = tmp_path / "keys"
    trusted.mkdir()
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (trusted / "main.pem").write_bytes(pem)
    return private, trusted


def bundle_bytes(version: str, extra: str | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for name in (f"{version}/VERSION", f"{version}/.venv/bin/allskyhub-agent"):
            data = version.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra:
            tar.addfile(tarfile.TarInfo(extra), io.BytesIO(b""))
    return buf.getvalue()


@dataclass
class Channel:
    files: dict[str, bytes] = field(default_factory=dict[str, bytes])

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = self.files.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        if rng := request.headers.get("range"):
            start = int(rng.removeprefix("bytes=").split("-")[0])
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)

    def publish(self, private: Ed25519PrivateKey, version: str, bundle: bytes | None = None,
                sha: str | None = None) -> None:  # fmt: skip
        data = bundle if bundle is not None else bundle_bytes(version)
        url = BUNDLE_URL.format(v=version)
        manifest = json.dumps({
            "channel": "stable", "version": version, "released_at": "2026-10-08T12:00:00Z",
            "bundle": {"url": url, "sha256": sha or hashlib.sha256(data).hexdigest(),
                       "size": len(data)},
        }).encode()  # fmt: skip
        self.files[URL] = manifest
        self.files[URL + ".sig"] = private.sign(manifest)
        self.files[url] = data


@dataclass
class Camera:
    """The camera around the updater: releases, the running agent, its mode and the clock."""

    root: Path
    healthy_versions: set[str] = field(default_factory=lambda: {"0.1.0", "0.2.0"})
    mode: str = "day"
    capturing: bool = True
    running: str | None = "0.1.0"
    commands: list[list[str]] = field(default_factory=list[list[str]])
    now: float = 0.0

    def install(self, version: str) -> None:
        (self.root / "opt" / "releases" / version / ".venv").mkdir(parents=True, exist_ok=True)
        (self.root / "opt" / "current").unlink(missing_ok=True)
        (self.root / "opt" / "current").symlink_to(Path("releases") / version)

    def current(self) -> str:
        return (self.root / "opt" / "current").readlink().name

    def status(self) -> dict[str, Any] | None:
        if self.running is None:
            return None
        return {"version": self.running, "frames": 3 if self.capturing else 0,
                "frame": {"mode": self.mode}}  # fmt: skip

    def run(self, cmd: list[str]) -> int:
        self.commands.append(cmd)
        if cmd[:2] == ["systemctl", "restart"]:
            v = self.current()
            self.running = v if v in self.healthy_versions else None
        return 0

    def sleep(self, s: float) -> None:
        self.now += s


def make(cam: Camera, channel: Channel, keys: Path) -> Updater:
    root = cam.root
    return Updater(
        paths=Paths(root / "opt", root / "work", root / "state.json", keys),
        manifest_url=URL,
        http=httpx.Client(transport=httpx.MockTransport(channel.handler)),
        status=cam.status,
        run=cam.run,
        sleep=cam.sleep,
        monotonic=lambda: cam.now,
        fallback_version="0.1.0",
    )


def state(cam: Camera) -> dict[str, Any]:
    return json.loads((cam.root / "state.json").read_text())


def test_versions_and_signature(key: tuple[Ed25519PrivateKey, Path]) -> None:
    private, keys = key
    assert is_newer("0.10.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("1.0", "0.1.0")
    assert verify_signature(b"x", private.sign(b"x"), keys)
    assert not verify_signature(b"y", private.sign(b"x"), keys)
    assert not verify_signature(b"x", Ed25519PrivateKey.generate().sign(b"x"), keys)


def test_installs_a_newer_release(tmp_path: Path, key: tuple[Ed25519PrivateKey, Path]) -> None:
    private, keys = key
    cam = Camera(tmp_path)
    cam.install("0.1.0")
    ch = Channel()
    ch.publish(private, "0.2.0")
    make(cam, ch, keys).run_once()
    assert cam.current() == "0.2.0"
    assert cam.running == "0.2.0"
    assert state(cam)["state"] == "installed"
    assert (tmp_path / "opt" / "releases" / "0.2.0" / "VERSION").read_text() == "0.2.0"
    assert ["systemctl", "restart", "allskyhub-agent.service"] in cam.commands
    make(cam, ch, keys).run_once()  # nothing newer: stays
    assert cam.commands.count(["systemctl", "restart", "allskyhub-agent.service"]) == 1


def test_rolls_back_an_unhealthy_release(
    tmp_path: Path, key: tuple[Ed25519PrivateKey, Path]
) -> None:
    private, keys = key
    cam = Camera(tmp_path, healthy_versions={"0.1.0"})
    cam.install("0.1.0")
    ch = Channel()
    ch.publish(private, "0.2.0")
    make(cam, ch, keys).run_once()
    assert cam.current() == "0.1.0"
    assert cam.running == "0.1.0"
    st = state(cam)
    assert (st["state"], st["failed_version"]) == ("rolled_back", "0.2.0")
    restarts = len(cam.commands)
    make(cam, ch, keys).run_once()  # the same release again: not retried
    assert len(cam.commands) == restarts


def test_never_during_the_night(tmp_path: Path, key: tuple[Ed25519PrivateKey, Path]) -> None:
    private, keys = key
    cam = Camera(tmp_path, mode="night")
    cam.install("0.1.0")
    ch = Channel()
    ch.publish(private, "0.2.0")
    make(cam, ch, keys).run_once()
    assert cam.current() == "0.1.0"
    assert state(cam)["state"] == "waiting"
    assert (tmp_path / "opt" / "releases" / "0.2.0").is_dir()  # downloaded already
    cam.mode = "day"
    make(cam, ch, keys).run_once()
    assert cam.current() == "0.2.0"


@pytest.mark.parametrize(
    ("kind", "code"),
    [("bad_sig", "bad_signature"), ("bad_sha", "checksum"), ("escape", "bad_bundle")],
)
def test_rejects_bad_updates(tmp_path: Path, key: tuple[Ed25519PrivateKey, Path], kind: str,
                             code: str) -> None:  # fmt: skip
    private, keys = key
    cam = Camera(tmp_path)
    cam.install("0.1.0")
    ch = Channel()
    if kind == "bad_sig":
        ch.publish(Ed25519PrivateKey.generate(), "0.2.0")
    elif kind == "bad_sha":
        ch.publish(private, "0.2.0", sha="0" * 64)
    else:
        ch.publish(private, "0.2.0", bundle=bundle_bytes("0.2.0", extra="../evil"))
    make(cam, ch, keys).run_once()
    assert cam.current() == "0.1.0"
    assert state(cam)["code"] == code
    assert not (tmp_path / "evil").exists()


def test_offline_or_no_keys_does_nothing(
    tmp_path: Path, key: tuple[Ed25519PrivateKey, Path]
) -> None:
    _, keys = key
    cam = Camera(tmp_path)
    cam.install("0.1.0")
    make(cam, Channel(), keys).run_once()  # 404: nothing published yet
    assert not (tmp_path / "state.json").exists()
    empty = tmp_path / "nokeys"
    empty.mkdir()
    make(cam, Channel(), empty).run_once()  # no keys in the image: updates are off, quietly
    assert not (tmp_path / "state.json").exists()
    assert cam.current() == "0.1.0"


def test_power_cut_after_the_switch_is_finished(
    tmp_path: Path, key: tuple[Ed25519PrivateKey, Path]
) -> None:
    _, keys = key
    cam = Camera(tmp_path, healthy_versions={"0.1.0"})
    cam.install("0.1.0")
    (tmp_path / "opt" / "releases" / "0.2.0").mkdir(parents=True)
    cam.install("0.2.0")  # switched, then the power went
    cam.running = None
    (tmp_path / "state.json").write_text(json.dumps(
        {"state": "installing", "version": "0.2.0", "previous": "0.1.0", "was_capturing": True}
    ))  # fmt: skip
    make(cam, Channel(), keys).run_once()
    assert cam.current() == "0.1.0"
    assert state(cam)["state"] == "rolled_back"


def test_status_reports_the_updater_state(
    tmp_path: Path, key: tuple[Ed25519PrivateKey, Path]
) -> None:
    from allskyhub_agent.update.status import update_status
    from allskyhub_protocol import UpdateState

    assert update_status(tmp_path / "state.json") is None
    private, keys = key
    cam = Camera(tmp_path, healthy_versions={"0.1.0"})
    cam.install("0.1.0")
    ch = Channel()
    ch.publish(private, "0.2.0")
    make(cam, ch, keys).run_once()
    st = update_status(tmp_path / "state.json")
    assert st is not None
    assert (st.state, st.version, st.code) == (UpdateState.ROLLED_BACK, "0.2.0", "unhealthy")
    (tmp_path / "state.json").write_text('{"state": "weird"}')
    assert update_status(tmp_path / "state.json") is None

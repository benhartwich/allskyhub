"""The commit shown in /healthz."""

from __future__ import annotations

from pathlib import Path

from allskyhub_server.version import commit

SHA = "28e898d0123456789abcdef0123456789abcdef0"


def test_loose_ref(tmp_path: Path) -> None:
    (tmp_path / ".git/refs/heads").mkdir(parents=True)
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git/refs/heads/main").write_text(SHA + "\n")
    (tmp_path / "server/src").mkdir(parents=True)
    assert commit(tmp_path / "server/src") == SHA


def test_packed_ref(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git/packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{SHA} refs/heads/main\n"
    )
    assert commit(tmp_path) == SHA


def test_detached_head(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text(SHA + "\n")
    assert commit(tmp_path) == SHA


def test_no_checkout(tmp_path: Path) -> None:
    assert commit(tmp_path) is None

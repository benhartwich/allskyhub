"""Which commit this hub runs, for /healthz (so cameras and peers can check compatibility).

Read straight from the checkout's ``.git`` (HEAD, loose ref or packed-refs) instead of
running git: the service user does not own the deploy checkout, and git refuses that.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _git_dir(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / ".git").is_dir():
            return folder / ".git"
    return None


def commit(start: Path) -> str | None:
    """The full sha of HEAD in the checkout containing ``start``, or None."""
    git = _git_dir(start)
    if git is None:
        return None
    try:
        head = (git / "HEAD").read_text().strip()
        if _SHA.fullmatch(head):
            return head
        if not head.startswith("ref: "):
            return None
        ref = head.removeprefix("ref: ")
        loose = git / ref
        if loose.is_file():
            sha = loose.read_text().strip()
            return sha if _SHA.fullmatch(sha) else None
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                sha, _, name = line.partition(" ")
                if name == ref and _SHA.fullmatch(sha):
                    return sha
    except OSError:
        return None
    return None


@cache
def full() -> str | None:
    """Full sha of the running code, None outside a git checkout."""
    return commit(Path(__file__).resolve().parent)


def short() -> str:
    """Short sha of the running code, ``"unknown"`` outside a git checkout."""
    sha = full()
    return sha[:7] if sha else "unknown"

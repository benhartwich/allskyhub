"""The import's rights when it is started as root (security review 2026-11-04).

Root is only needed for two things: reading the other program's folders (a home directory
may be 0700) and hard links to its files (fs.protected_hardlinks). Everything else, above
all every write into the agent's data folder, must not run as root: the folder is the
agent's, and a link planted there would make root write anywhere. So the import becomes
the agent and keeps only CAP_DAC_READ_SEARCH (read anything) and, raised just around the
hard link, CAP_FOWNER. Writes are then checked as the agent's.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Generator
from contextlib import contextmanager

CAP_DAC_READ_SEARCH = 2
CAP_FOWNER = 3
_PR_SET_KEEPCAPS = 8
_VERSION_3 = 0x20080522


class _Header(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _Data(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


_dropped = False


def _capset(effective: set[int], permitted: set[int]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    header = _Header(_VERSION_3, 0)
    data = (_Data * 2)()  # all caps used here are below 32: only the first word
    data[0].effective = sum(1 << c for c in effective)
    data[0].permitted = sum(1 << c for c in permitted)
    if libc.capset(ctypes.byref(header), data) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def drop_to(uid: int, gid: int) -> bool:
    """Run as root: from here on the agent's user and group, with CAP_DAC_READ_SEARCH and
    (not raised) CAP_FOWNER only, for good. False (nothing changed) if not root or the
    agent is root itself."""
    global _dropped
    if os.geteuid() != 0 or uid == 0:
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_KEEPCAPS, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    os.setgroups([gid])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    _capset({CAP_DAC_READ_SEARCH}, {CAP_DAC_READ_SEARCH, CAP_FOWNER})
    libc.prctl(_PR_SET_KEEPCAPS, 0, 0, 0, 0)
    _dropped = True
    return True


@contextmanager
def capability(cap: int) -> Generator[None]:
    """`cap` raised for the block, if the import dropped root (else a no-op)."""
    if not _dropped:
        yield
        return
    keep = {CAP_DAC_READ_SEARCH, CAP_FOWNER}
    _capset({CAP_DAC_READ_SEARCH, cap}, keep)
    try:
        yield
    finally:
        _capset({CAP_DAC_READ_SEARCH}, keep)

"""The root helpers never touch the hub's folder as root (security review 2026-11-04,
allsky's review of #409): a spool the hub swapped for a link leaves the files behind it
alone. Needs root and runuser, so it runs on the server, not in CI."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPERS = {
    "storage": (ROOT / "deploy/storage/allskyhub-storage.sh", "ALLSKYHUB_STORAGE"),
    "deploy": (ROOT / "deploy/admin/allskyhub-deploy.sh", "ALLSKYHUB_DEPLOY"),
}

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("runuser") is None, reason="needs root and runuser"
)


@pytest.mark.parametrize("name", sorted(HELPERS))
def test_a_linked_spool_is_left_alone(name: str) -> None:
    script, prefix = HELPERS[name]
    # Not the test's tmp_path, which "nobody" may not reach; opened to everyone below.
    base = Path(tempfile.mkdtemp(prefix="ash-helper-test."))
    try:
        base.chmod(0o755)
        victim, hub = base / "victim", base / "hub"
        victim.mkdir()
        hub.mkdir()
        for f in ("request", "state", "target", "log"):
            (victim / f).write_text("keep\n")
        shutil.chown(hub, "nobody")
        (hub / "spool").symlink_to(victim)
        env = {
            **os.environ,
            f"{prefix}_SPOOL": str(hub / "spool"),
            f"{prefix}_LOCK": str(base / "lock"),
            "ALLSKYHUB_HUB_USER": "nobody",
            "ALLSKYHUB_STORAGE_MOUNT": str(base / "none"),
            "ALLSKYHUB_DEPLOY_REPO": str(base / "none"),
        }
        r = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, check=False
        )
        assert r.returncode == 0
        assert sorted(p.name for p in victim.iterdir()) == ["log", "request", "state", "target"]
        assert all(p.read_text() == "keep\n" for p in victim.iterdir())
    finally:
        shutil.rmtree(base)

"""The images' own disk on the admin page, and growing it (admin/storage.py)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from allskyhub_server.admin import storage
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, csrf_from, make_account

MOUNTINFO = (
    "22 1 252:4 / / rw,relatime shared:1 - ext4 /dev/vda4 rw\n"
    "97 22 252:16 / /srv/allskyhub-img rw,noatime shared:50 - ext4 /dev/vdb rw\n"
    "98 22 252:16 /images /var/lib/allskyhub-server/images rw,relatime shared:50 - ext4"
    " /dev/vdb rw\n"
)


def test_the_disk_under_the_images(tmp_path: Path) -> None:
    info = tmp_path / "mountinfo"
    info.write_text(MOUNTINFO)
    images = Path("/var/lib/allskyhub-server/images/cam/20261105")
    assert storage._source(images, MOUNTINFO) == "/dev/vdb"  # pyright: ignore[reportPrivateUsage]
    assert storage._source(Path("/var/www"), MOUNTINFO) == "/dev/vda4"  # pyright: ignore[reportPrivateUsage]
    blocks = tmp_path / "block"
    (blocks / "vdb").mkdir(parents=True)
    (blocks / "vdb" / "size").write_text(str(300 * 10**9 // 512))
    found = storage.disk(tmp_path, mountinfo=_info(tmp_path), sys_block=blocks)
    assert found is not None
    assert found.device == "vdb"
    assert found.disk_bytes == 300 * 10**9 // 512 * 512


def _info(tmp_path: Path) -> Path:
    """mountinfo with tmp_path mounted from /dev/vdb."""
    path = tmp_path / "mountinfo2"
    path.write_text(f"98 22 252:16 / {tmp_path} rw - ext4 /dev/vdb rw\n")
    return path


def test_growable_only_with_room_beyond_ext4s_own_share() -> None:
    fs = 210_241_560_576  # 200 GiB with ext4's tables and journal taken off
    assert not storage.Disk("vdb", fs, 0, 214_748_364_800).growable
    assert storage.Disk("vdb", fs, 0, 268_435_456_000).growable  # grown to 250 GiB
    assert not storage.Disk("vdb", fs, 0, None).growable


def test_request_and_job(tmp_path: Path) -> None:
    small = storage.Disk("vdb", 200 * 10**9, 0, 200 * 10**9)
    with pytest.raises(storage.StorageError):
        storage.request(tmp_path, small, "ben@example.org")
    big = storage.Disk("vdb", 200 * 10**9, 0, 300 * 10**9)
    storage.request(tmp_path, big, "ben@example.org")
    assert storage.job(tmp_path).state == "requested"
    with pytest.raises(storage.StorageError):  # once at a time
        storage.request(tmp_path, big, "ben@example.org")
    (tmp_path / "storage" / "request").unlink()
    (tmp_path / "storage" / "state").write_text("ok\n")
    (tmp_path / "storage" / "log").write_text("16:13 Fertig\n")
    done = storage.job(tmp_path)
    assert (done.state, done.busy, done.log) == ("ok", False, "16:13 Fertig")


async def test_the_admin_grows_it(
    client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", admin)
    grown = storage.Disk("vdb", 200 * 10**9, 50 * 10**9, 300 * 10**9)

    def disk(images: Path) -> storage.Disk:
        return grown

    monkeypatch.setattr(storage, "disk", disk)
    page = await client.get("/admin")
    assert "Bildspeicher" in page.text
    assert "Auf 300 GB vergrößern" in page.text
    token = csrf_from(page.text)
    r = await client.post(
        "/admin/storage/grow", data={"csrf_token": token, "password": "falsch"},
        follow_redirects=True,
    )  # fmt: skip
    assert "Das Passwort stimmt nicht" in r.text
    r = await client.post(
        "/admin/storage/grow", data={"csrf_token": token, "password": PASSWORD},
        follow_redirects=True,
    )  # fmt: skip
    assert "Vergrößern angefordert" in r.text
    assert (settings.data_dir / "storage" / "request").read_text().startswith(admin)
    job = await client.get("/admin/storage/job")
    assert "wartet auf den Server" in job.text

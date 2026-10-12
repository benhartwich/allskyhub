"""Log, support bundle, backup and restore in the local UI (roadmap L15)."""

from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from allskyhub_agent.live import LiveState
from allskyhub_agent.web.server import WebServer
from allskyhub_agent.web.support import BACKUP_KIND, RecentLog, Support


def test_recent_log_keeps_the_last_lines() -> None:
    log = RecentLog(size=3)
    logger = logging.getLogger("test.support")
    logger.addHandler(log)
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("line %d", i)
        logger.debug("not kept")
    finally:
        logger.removeHandler(log)
    lines = log.lines()
    assert [ln.rsplit(" ", 1)[1] for ln in lines] == ["2", "3", "4"]
    assert "INFO test.support: line 4" in lines[-1]
    assert log.lines(1) == lines[-1:]


@pytest.fixture
def support(tmp_path: Path) -> tuple[Support, list[Any]]:
    log = RecentLog()
    log.emit(logging.LogRecord("agent", logging.INFO, "", 0, "capture started", None, None))
    (tmp_path / "orientation.json").write_text('{"rot": 30}')
    restored: list[Any] = []

    def restore(values: dict[str, Any], edits: dict[str, Any] | None) -> list[str]:
        if values.get("keep_days") == 0:
            return ["keep_days"]
        restored.append((values, edits))
        return []

    def broken() -> object:
        raise OSError("no network")

    sup = Support(
        log=log,
        facts={"version": lambda: "0.1.0", "network": broken},
        files={"orientation.json": tmp_path / "orientation.json", "gone.json": tmp_path / "x"},
        settings=lambda: {"keep_days": 61, "timezone": "Europe/Vienna"},
        mask_edits=lambda: {"exclude": [], "include": []},
        restore=restore,
    )
    return sup, restored


def test_bundle(support: tuple[Support, list[Any]]) -> None:
    sup, _ = support
    z = zipfile.ZipFile(io.BytesIO(sup.bundle()))
    assert sorted(z.namelist()) == ["agent.log", "facts.json", "orientation.json"]
    assert "capture started" in z.read("agent.log").decode()
    facts = json.loads(z.read("facts.json"))
    assert facts["version"] == "0.1.0"
    assert facts["network"] == "error: no network"  # a broken part does not stop the bundle
    assert facts["settings"]["keep_days"] == 61


def test_backup_round_trip(support: tuple[Support, list[Any]]) -> None:
    sup, restored = support
    backup = sup.backup()
    assert backup["kind"] == BACKUP_KIND
    assert sup.apply(json.loads(json.dumps(backup))) == []
    assert restored == [({"keep_days": 61, "timezone": "Europe/Vienna"},
                         {"exclude": [], "include": []})]  # fmt: skip
    assert sup.apply({"settings": {}}) == ["backup"]  # not one of ours
    assert sup.apply([1, 2]) == ["backup"]
    assert sup.apply(backup | {"settings": {"keep_days": 0}}) == ["keep_days"]


@pytest.fixture
def web(support: tuple[Support, list[Any]]) -> Iterator[int]:
    server = WebServer(LiveState(), "127.0.0.1", 0, support=support[0])
    server.start()
    yield server.port
    server.stop()


def _get(port: int, path: str, body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_routes(web: int) -> None:
    status, _, body = _get(web, "/api/logs?lines=5")
    assert status == 200
    assert "capture started" in json.loads(body)["lines"][-1]
    status, headers, body = _get(web, "/api/support.zip")
    assert (status, headers["Content-Type"]) == (200, "application/zip")
    assert "allskyhub-support-" in headers["Content-Disposition"]
    assert zipfile.is_zipfile(io.BytesIO(body))
    status, headers, body = _get(web, "/api/backup.json")
    assert status == 200
    assert "attachment" in headers["Content-Disposition"]
    assert _get(web, "/api/restore", body)[0] == 202
    status, _, err = _get(web, "/api/restore", b'{"kind": "something else"}')
    assert (status, json.loads(err)["fields"]) == (400, ["backup"])

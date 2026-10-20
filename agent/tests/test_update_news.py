"""What's new in each release (SPEC §8 What's new)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from allskyhub_agent import __version__
from allskyhub_agent.update import news
from allskyhub_protocol.updates import version_key

ROOT = Path(__file__).resolve().parents[2]


def _entry(v: str) -> dict[str, object]:
    return {"version": v, "de": [f"Neu in {v}"], "en": [f"New in {v}"]}


def test_the_shipped_file_is_valid_and_newest_first() -> None:
    raw = json.loads((ROOT / "agent/src/allskyhub_agent/update/news.json").read_text())
    entries = news.load_news()
    assert len(entries) == len(raw) > 0
    versions = [e["version"] for e in raw]
    assert versions == sorted(versions, key=version_key, reverse=True)
    assert len(set(versions)) == len(versions)
    for e in entries:
        assert len(e.de) == len(e.en)


def test_the_release_being_built_has_an_entry() -> None:
    # The release workflow takes the release notes from it, and it refuses a tag without one.
    version = re.search(r'^version = "(.*)"$', (ROOT / "agent/pyproject.toml").read_text(), re.M)
    assert version is not None
    newest = news.load_news()[0].version
    assert version_key(newest) >= version_key(version.group(1))


def test_only_the_running_release_and_older_ones() -> None:
    entries = news.load_news(json.dumps([_entry("0.3.0"), _entry("0.2.0"), _entry("0.1.0")]))
    assert [n["version"] for n in news.news_for("0.2.0", entries)] == ["0.2.0", "0.1.0"]
    assert [n["version"] for n in news.news_for("0.2.5", entries, limit=1)] == ["0.2.0"]
    assert news.news_for("0.0.9", entries) == []


def test_a_broken_file_shows_nothing() -> None:
    assert news.load_news("not json") == []
    assert news.load_news(json.dumps([{"version": "0.1.0", "de": []}])) == []


def test_update_facts_carry_the_news(tmp_path: Path) -> None:
    from allskyhub_agent import cli

    facts = cli.update_facts(tmp_path / "opt", tmp_path / "unit")
    assert [n["version"] for n in facts["news"]] == [
        n["version"] for n in news.news_for(__version__)
    ]

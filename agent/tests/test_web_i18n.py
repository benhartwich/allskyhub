"""The local UI's texts in German and English (SPEC §7 Language and units)."""

from __future__ import annotations

import re
from importlib.resources import files

_ENTRY = re.compile(r"""^\s*"([\w.]+)": (?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'),$""", re.M)


def _app_js() -> str:
    return (files("allskyhub_agent.web") / "app.js").read_text(encoding="utf-8")


def _dictionary(src: str) -> tuple[dict[str, str], dict[str, str]]:
    block = src[src.index("const T = {") : src.index("\nconst $ = ")]
    de = block[block.index("  de: {") : block.index("  en: {")]
    en = block[block.index("  en: {") :]

    def entries(part: str) -> dict[str, str]:
        return {m.group(1): m.group(0) for m in _ENTRY.finditer(part)}

    return entries(de), entries(en)


def test_both_languages_have_the_same_keys() -> None:
    # SPEC §7: every text exists in de and en, so nothing shows as a bare key.
    de, en = _dictionary(_app_js())
    assert len(de) > 300
    assert de.keys() == en.keys()


def test_placeholders_match() -> None:
    de, en = _dictionary(_app_js())
    for key in de:
        assert set(re.findall(r"\{\w+\}", de[key].split(":", 1)[1])) == set(
            re.findall(r"\{\w+\}", en[key].split(":", 1)[1])
        ), key


def test_every_used_key_is_defined() -> None:
    src = _app_js()
    de, _ = _dictionary(src)
    code = src[src.index("\nconst $ = ") :]
    used = set(re.findall(r'\btn?\("([\w.]+)"', code))
    index = (files("allskyhub_agent.web") / "index.html").read_text(encoding="utf-8")
    used |= set(re.findall(r'data-i18n(?:-title|-label)?="([\w.]+)"', index))
    used = {k for k in used if not k.endswith(".")}  # prefixes such as "kind."
    assert used - de.keys() == set()

"""The shared look is complete and refers only to its own files."""

from __future__ import annotations

import re

import allskyhub_design
import pytest


def test_files_exist() -> None:
    for name in allskyhub_design.FILES:
        assert allskyhub_design.path(name).is_file(), name
    with pytest.raises(KeyError):
        allskyhub_design.path("../secret")


def test_css_urls_point_into_the_package() -> None:
    for css in ("tokens.css", "base.css"):
        text = allskyhub_design.path(css).read_text()
        for url in re.findall(r'url\("([^"]+)"\)', text):
            assert url in allskyhub_design.FILES, (css, url)


def test_red_theme_defines_every_colour_token() -> None:
    tokens = allskyhub_design.path("tokens.css").read_text()
    root, red = tokens.split('[data-theme="red"]', 1)
    colours = {"--bg", "--text", "--text-2", "--accent", "--ok", "--warn", "--danger", "--line"}
    for name in colours:
        assert f"{name}:" in root, name
        assert f"{name}:" in red, name

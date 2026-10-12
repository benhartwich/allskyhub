"""The shared look (packages/design) is served by the hub under /static/design/."""

from __future__ import annotations

import re

import httpx


async def test_design_files_are_served(client: httpx.AsyncClient) -> None:
    page = (await client.get("/")).text
    for name in ("design/tokens.css", "design/base.css", "app.css", "theme.js"):
        match = re.search(rf'"/static/{re.escape(name)}\?v=(\w+)"', page)
        assert match, name
        r = await client.get(f"/static/{name}?v={match[1]}")
        assert r.status_code == 200, name
        assert "immutable" in r.headers["cache-control"]
    font = await client.get("/static/design/fonts/space-grotesk.woff2")
    assert font.status_code == 200
    assert (await client.get("/static/design/stars.svg")).status_code == 200
    assert "data-theme-toggle" in page

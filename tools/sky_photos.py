"""Builds the photo list for the app's sky objects (planets, moon, Messier objects):
``packages/sky/src/allskyhub_sky/data/photos.json``.

Only files on Wikimedia Commons whose licence allows use with attribution and without
share-alike or other conditions: public domain, CC0, CC BY. Each entry keeps the file
page, author, licence and its URL, which the app shows under the photo. The hub fetches
the picture itself (``api/app_sky.py``), so Wikimedia learns nothing about app users.

Run by hand when objects or photos should change::

    uv run python tools/sky_photos.py
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

OUT = Path(__file__).resolve().parents[1] / "packages/sky/src/allskyhub_sky/data/photos.json"
UA = "allskyhub/1.0 (https://allskyhub.org; support@webinx.eu) sky-photos tool"
WIKI = "https://en.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIDTH = 800

PAGES = {
    "moon": "Moon",
    "mercury": "Mercury (planet)",
    "venus": "Venus",
    "mars": "Mars",
    "jupiter": "Jupiter",
    "saturn": "Saturn",
    "uranus": "Uranus",
    "neptune": "Neptune",
    **{f"M{i}": f"Messier {i}" for i in range(1, 111)},
}
# Files to try first, where the page's own pictures do not show the object as a whole.
PREFERRED = {"moon": ["Moon_nearside_LRO_5000_(2).png"]}
# Licences that need only attribution (or nothing). Share-alike, non-commercial and
# no-derivatives licences are left out on purpose.
OK = re.compile(r"^(public domain|pd\b.*|cc0.*|cc by \d(\.\d)?( \w+)?)$", re.IGNORECASE)


def _text(value: str) -> str:
    """Plain text from Commons' HTML metadata."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _get(client: httpx.Client, url: str, params: dict[str, str]) -> dict[str, Any]:
    for attempt in range(4):
        r = client.get(url, params={**params, "format": "json", "formatversion": "2"})
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate limited: {url}")


# Pictures on an object's page that are not the object: portraits, charts, maps.
NOT_THE_OBJECT = re.compile(
    r"charles[ _]messier|atlas|chart|finder|location|map|diagram|iau|constellation|logo|icon|"
    r"stamp|portrait|telescope|mission|spacecraft|surveyor|apollo|rover|lander",
    re.IGNORECASE,
)


def names_object(name: str, key: str, title: str) -> bool:
    """Whether a file name names the object (only the lead image needs no name)."""
    if key.startswith("M"):
        n = key[1:]
        return bool(re.search(rf"(messier|\bm)[ _-]?0*{n}(?!\d)", name, re.IGNORECASE))
    return title.split(" (")[0].lower() in name.lower()


def candidates(client: httpx.Client, key: str, title: str) -> list[str]:
    """The page's lead image first, then other images that name the object."""
    data = _get(client, WIKI, {"action": "query", "titles": title, "redirects": "1",
                               "prop": "pageimages|images", "piprop": "name",
                               "imlimit": "50"})  # fmt: skip
    pages = data["query"]["pages"]
    if not pages or "missing" in pages[0]:
        return []
    page = pages[0]
    names = [page["pageimage"]] if "pageimage" in page else []
    for image in page.get("images", []):
        name = str(image["title"]).removeprefix("File:")
        if (
            name.lower().endswith((".jpg", ".jpeg", ".png"))
            and name not in names
            and names_object(name, key, title)
        ):
            names.append(name)
    return [n for n in names if not NOT_THE_OBJECT.search(n)]


def check(client: httpx.Client, name: str) -> dict[str, str] | None:
    data = _get(client, COMMONS, {"action": "query", "titles": f"File:{name}",
                                  "prop": "imageinfo", "iiprop": "url|extmetadata|mime",
                                  "iiurlwidth": str(WIDTH)})  # fmt: skip
    pages = data["query"]["pages"]
    info = (pages[0].get("imageinfo") or [None])[0] if pages else None
    if not info or info.get("mime") not in ("image/jpeg", "image/png"):
        return None
    meta = {k: _text(str(v.get("value", ""))) for k, v in info.get("extmetadata", {}).items()}
    licence = meta.get("LicenseShortName", "")
    if not OK.match(licence) or "sa" in licence.lower().split():
        return None
    if "nc" in licence.lower() or "nd" in licence.lower().split():
        return None
    artist = meta.get("Artist") or meta.get("Credit") or "unbekannt"
    return {
        "file": name,
        "page": str(info["descriptionurl"]),
        # Without Commons' tracking parameters.
        "url": str(info.get("thumburl") or info["url"]).split("?")[0],
        "author": artist[:200],
        "licence": licence,
        "licence_url": meta.get("LicenseUrl", ""),
    }


def main() -> int:
    out: dict[str, dict[str, str]] = {}
    with httpx.Client(headers={"User-Agent": UA}, timeout=30) as client:
        for key, title in PAGES.items():
            for name in [*PREFERRED.get(key, []), *candidates(client, key, title)][:12]:
                found = check(client, name)
                if found:
                    out[key] = found
                    break
            print(key, out.get(key, {}).get("licence", "–"), out.get(key, {}).get("file", ""))
            time.sleep(0.3)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(out)} of {len(PAGES)} objects with a photo → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

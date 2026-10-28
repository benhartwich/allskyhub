"""Builds the star lists for the sky-brightness report charts:
``packages/sky/src/allskyhub_sky/data/report_stars.json``.

Stars to magnitude 7 around a few constellations that are easy to find (the ones Globe at
Night uses and a few for the southern sky), from d3-celestial's ``stars.8.json``
(Hipparcos/Tycho, BSD-3-Clause like the constellations already in allskyhub_sky).

Run by hand with the downloaded catalogue::

    uv run python tools/report_stars.py path/to/stars.8.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "packages/sky/src/allskyhub_sky/data"
# Constellation → radius around its label point (degrees).
CHARTS = {
    "Ori": 14.0, "Leo": 15.0, "Boo": 15.0, "Cyg": 14.0, "Peg": 14.0, "Per": 14.0,
    "UMa": 16.0, "Cas": 12.0, "Sco": 15.0, "Cru": 10.0,
}  # fmt: skip
MAX_MAG = 7.0


def separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r = math.radians
    c = math.sin(r(dec1)) * math.sin(r(dec2)) + math.cos(r(dec1)) * math.cos(r(dec2)) * math.cos(
        r(ra1 - ra2)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def main(source: str) -> int:
    stars = json.loads(Path(source).read_text())["features"]
    names = json.loads((DATA / "constellations.json").read_text())["names"]
    out: dict[str, list[list[float]]] = {}
    for key, radius in CHARTS.items():
        _, ra0, dec0, _ = names[key]
        picked: list[list[float]] = []
        for star in stars:
            mag = star["properties"]["mag"]
            ra, dec = star["geometry"]["coordinates"]
            ra %= 360
            if mag <= MAX_MAG and separation(ra, dec, ra0, dec0) <= radius:
                picked.append([round(ra, 3), round(dec, 3), round(mag, 2)])
        out[key] = sorted(picked, key=lambda s: s[2])
        print(key, len(picked))
    (DATA / "report_stars.json").write_text(json.dumps(out, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

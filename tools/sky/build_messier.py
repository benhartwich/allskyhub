"""Build packages/sky/src/allskyhub_sky/data/messier.json from d3-celestial (BSD-3-Clause).

Input: `messier.json` from the npm package d3-celestial (data/). Output: {id: [name,
ra, dec, mag, type]}, J2000, degrees, ra 0..360, rounded to 0.01°. ``name`` is the
German common name where there is one (below), else empty.

    uv run python tools/sky/build_messier.py messier.json \
        > packages/sky/src/allskyhub_sky/data/messier.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

GERMAN = {
    "M1": "Krebsnebel",
    "M6": "Schmetterlingshaufen",
    "M7": "Ptolemäus-Haufen",
    "M8": "Lagunennebel",
    "M11": "Wildentenhaufen",
    "M13": "Herkuleshaufen",
    "M16": "Adlernebel",
    "M17": "Omeganebel",
    "M20": "Trifidnebel",
    "M24": "Kleine Sagittarius-Sternwolke",
    "M27": "Hantelnebel",
    "M31": "Andromedagalaxie",
    "M33": "Dreiecksgalaxie",
    "M42": "Orionnebel",
    "M44": "Praesepe (Krippe)",
    "M45": "Plejaden",
    "M51": "Whirlpoolgalaxie",
    "M57": "Ringnebel",
    "M63": "Sonnenblumengalaxie",
    "M64": "Black-Eye-Galaxie",
    "M76": "Kleiner Hantelnebel",
    "M83": "Südliche Feuerradgalaxie",
    "M87": "Virgo A",
    "M81": "Bodes Galaxie",
    "M82": "Zigarrengalaxie",
    "M97": "Eulennebel",
    "M101": "Feuerradgalaxie",
    "M104": "Sombrerogalaxie",
}


def main() -> None:
    doc: dict[str, Any] = json.loads(Path(sys.argv[1]).read_text())
    out: dict[str, list[Any]] = {}
    for feature in doc["features"]:
        props: dict[str, Any] = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        name = GERMAN.get(feature["id"], "")
        out[feature["id"]] = [name, round(lon % 360.0, 2), round(lat, 2),
                              props.get("mag"), props.get("type") or ""]  # fmt: skip
    json.dump(out, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

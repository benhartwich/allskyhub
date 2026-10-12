"""Build server/.../devices/data/constellations.json from d3-celestial (BSD-3-Clause).

Inputs: `constellations.lines.json` and `constellations.json` from the npm package
d3-celestial (data/). Output: {"lines": {abbr: [[[ra, dec], ...], ...]}, "names":
{abbr: [german name, ra, dec, rank]}}, J2000, degrees, ra 0..360, rounded to 0.01°.

    uv run python tools/sky/build_constellations.py lines.json names.json > .../constellations.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _ra(lon: float) -> float:
    return round(lon % 360.0, 2)


def main() -> None:
    lines_doc: dict[str, Any] = json.loads(Path(sys.argv[1]).read_text())
    names_doc: dict[str, Any] = json.loads(Path(sys.argv[2]).read_text())
    lines: dict[str, list[list[list[float]]]] = {}
    for feature in lines_doc["features"]:
        strings: list[list[list[float]]] = feature["geometry"]["coordinates"]
        lines[feature["id"]] = [[[_ra(lon), round(lat, 2)] for lon, lat in s] for s in strings]
    names: dict[str, list[Any]] = {}
    for feature in names_doc["features"]:
        props: dict[str, Any] = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"]
        name = " ".join(str(props.get("de") or props["name"]).split())  # odd spaces in "de"
        names[feature["id"]] = [name, _ra(lon), round(lat, 2),
                                int(props.get("rank") or 3)]  # fmt: skip
    json.dump({"lines": lines, "names": names}, sys.stdout, ensure_ascii=False,
              separators=(",", ":"), sort_keys=True)  # fmt: skip
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

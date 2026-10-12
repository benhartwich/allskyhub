"""Build server/.../static/world-land.svg from Natural Earth land (110m, public domain).

Input: world-atlas `land-110m.json` (TopoJSON). Output: one path in an equirectangular
projection, viewBox 0 0 3600 1800 (longitude -180..180 → x, latitude 90..-90 → y), so the
hub can put camera dots on top with the same simple formula (devices/camera_map.py).

    uv run python tools/map/build_world_svg.py land-110m.json > .../static/world-land.svg
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    topo: dict[str, Any] = json.loads(Path(sys.argv[1]).read_text())
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs: list[list[tuple[float, float]]] = []
    for arc in topo["arcs"]:
        x = y = 0
        points: list[tuple[float, float]] = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)

    def ring(indices: list[int]) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for i in indices:
            pts = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
            out.extend(pts if not out else pts[1:])
        return out

    parts: list[str] = []
    for geom in topo["objects"]["land"]["geometries"]:
        polygons = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]
        for polygon in polygons:
            for r in polygon:
                # Rings crossing the antimeridian are unwrapped to stay continuous and
                # drawn a second time shifted by 360°, so they neither smear across the map
                # nor get cut (the viewBox clips what lies outside).
                pts = ring(r)
                unwrapped = [pts[0]]
                for lon, lat in pts[1:]:
                    prev = unwrapped[-1][0]
                    while lon - prev > 180:
                        lon -= 360
                    while lon - prev < -180:
                        lon += 360
                    unwrapped.append((lon, lat))
                lons = [p[0] for p in unwrapped]
                shifts = [0.0]
                if min(lons) < -180:
                    shifts.append(360.0)
                if max(lons) > 180:
                    shifts.append(-360.0)
                for shift in shifts:
                    coords = " ".join(
                        f"{(lon + shift + 180) * 10:.0f},{(90 - lat) * 10:.0f}"
                        for lon, lat in unwrapped
                    )
                    parts.append(f"M{coords}Z")
    print(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3600 1800">'
        '<path fill="#1c2a55" stroke="#3a4f8f" stroke-width="2" d="' + "".join(parts) + '"/></svg>'
    )


if __name__ == "__main__":
    main()

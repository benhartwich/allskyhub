"""Sky mask and orientation in the local UI (roadmap L8).

The owner sees on the live image what the camera counts as sky (everything else shaded),
draws areas to leave out or add, and checks the orientation: the horizon and the
compass points from the plate solve (SPEC §4.8) drawn where the camera thinks they are.
If north is not where the tree line says it is, the solve is wrong.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from allskyhub_agent.calib.orientation import read_orientation
from allskyhub_agent.process.skymask import SkyMask, parse_edits

MAX_SIDE = 2000  # the shade image is drawn at most this large
_POINTS = (("N", 0.0), ("O", 90.0), ("S", 180.0), ("W", 270.0))
_LABEL_ALT = 8.0  # the letters sit a little above the horizon, inside the image circle


class LocalCalibration:
    def __init__(self, sky_mask: SkyMask, orientation_path: Path) -> None:
        self._mask = sky_mask
        self._orientation = orientation_path

    def info(self) -> dict[str, Any]:
        """`GET /api/calibration`: the mask's state, the drawn areas and the orientation
        with the overlay in fractions of the frame (null without a solve)."""
        learned_at = self._mask.learned_at if self._mask.learned else None
        return {
            "mask_learned_at": _iso(learned_at),
            "edits": self._mask.edits.to_json(),
            "orientation": self._orientation_info(),
        }

    def _orientation_info(self) -> dict[str, Any] | None:
        kept = read_orientation(self._orientation)
        if kept is None:
            return None
        sol, at = kept
        out: dict[str, Any] = {
            "north_deg": round(sol.rot % 360.0, 1),
            "mirrored": sol.flip < 0,
            "solved_at": _iso(at),
            "stars": sol.stars,
            "rms_deg": sol.rms_deg,
            "overlay": None,
        }
        if not sol.width or not sol.height:
            return out  # an older file without the frame size: nothing to draw on
        w, h = float(sol.width), float(sol.height)

        def ring(alt: float) -> list[list[float]]:
            az = np.arange(0.0, 361.0, 5.0)
            x, y = sol.project(np.full_like(az, alt), az)
            return [
                [round(float(a) / w, 4), round(float(b) / h, 4)] for a, b in zip(x, y, strict=True)
            ]

        alt = np.full(len(_POINTS), _LABEL_ALT)
        x, y = sol.project(alt, np.array([az for _, az in _POINTS]))
        zx, zy = sol.project(np.array([90.0]), np.array([0.0]))
        out["overlay"] = {
            "horizon": ring(0.0),
            "rings": [ring(30.0), ring(60.0)],
            "zenith": [round(float(zx[0]) / w, 4), round(float(zy[0]) / h, 4)],
            "points": [
                {"label": label, "x": round(float(px) / w, 4), "y": round(float(py) / h, 4)}
                for (label, _), px, py in zip(_POINTS, x, y, strict=True)
            ],
        }
        return out

    def shade_png(self, width: int, height: int) -> bytes | None:
        """A transparent PNG of this size that darkens everything that is not sky; None
        while there is neither a learned mask nor a drawn area."""
        width, height = max(1, min(width, MAX_SIDE)), max(1, min(height, MAX_SIDE))
        mask = self._mask.get(height, width)
        if mask is None:
            return None
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[..., 2] = 200  # red in OpenCV's BGRA, the colour that says "not used"
        rgba[..., 3] = 150
        rgba[mask, 3] = 0
        ok, buf = cv2.imencode(".png", rgba)
        return buf.tobytes() if ok else None

    def set_edits(self, raw: object) -> bool:
        edits = parse_edits(raw)
        if edits is None:
            return False
        self._mask.set_edits(edits)
        return True

    def forget_mask(self) -> None:
        self._mask.forget()


def _iso(at: datetime | None) -> str | None:
    return at.isoformat().replace("+00:00", "Z") if at is not None else None

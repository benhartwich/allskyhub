"""Flat field without covering the lens (SPEC §4.10).

A fisheye darkens toward the edge of its image circle (vignetting). The flat here is a
smooth radial curve v(rho) around the lens centre, rho the distance as a share of the image
circle's radius, v(0) = 1; every frame is divided by it (multiplied by the gain 1/v).

**Twilight flat.** In twilight (sun 0° to -8°) the sky is bright, smooth and free of
stars. 25 frames at the lowest gain with a mean of 15–35 % are normalised and their
median fitted as radial curve times a tilted plane, in log space so the fit is linear:
log M = c + bx·x + by·y + a2·rho^2 + a4·rho^4 + a6·rho^6. The plane is the twilight sky being
brighter toward the sun and is thrown away. 24 zones (3 rings x 8 directions) check that
the sky held still and nothing was clipped.

**Night flat.** Until there is a twilight flat, the curve is estimated from the night's
median frame of the sky mask (§4.7), above about 20° and without stars. The night sky
itself brightens toward the horizon, so this one is only for the picture, never for the
sky values (`undo_for_measuring`), and only once two nights agree.

The division is done on linear values: libcamera frames are sRGB-encoded and linearised
first. A gain map is built once per frame size, the gain never above 2.0.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from allskyhub_agent.adapters.camera import Image

log = logging.getLogger(__name__)

Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]
Floats = npt.NDArray[np.float64]

FIT_WIDTH = 480  # the flat is smooth: fitted on small frames
MAX_GAIN = 2.0
NO_EFFECT = 1.03  # a flat that brightens the edge by less is not applied


@dataclass(frozen=True)
class FlatConfig:
    twilight_sun: tuple[float, float] = (-8.0, 0.0)
    twilight_frames: int = 25
    twilight_mean: tuple[float, float] = (0.15, 0.35)
    twilight_gap: timedelta = timedelta(minutes=30)  # a longer gap starts a new collection
    twilight_every: timedelta = timedelta(days=30)
    zone_outer: float = 0.85
    zones_needed: int = 20
    zone_drift: float = 0.03
    max_residual: float = 0.03
    night_reach: float = 0.78  # about 20° above the horizon
    night_agree: float = 0.05
    monotonic: float = 0.05
    centre_moved: float = 0.02
    clipped: int = 250


@dataclass(frozen=True)
class Curve:
    """v(rho) = exp(a2·rho^2 + a4·rho^4 + a6·rho^6) around (cx, cy), shares of the frame size."""

    a: tuple[float, float, float]
    cx: float = 0.5
    cy: float = 0.5

    def v(self, rho: Floats) -> Floats:
        r2 = rho * rho
        a2, a4, a6 = self.a
        return np.exp(a2 * r2 + a4 * r2 * r2 + a6 * r2 * r2 * r2)

    @property
    def max_gain(self) -> float:
        return float(1.0 / self.v(np.array([1.0]))[0])

    def problem(self, cfg: FlatConfig) -> str | None:
        """Why this curve is not used, None when it is fine (German, for status)."""
        rho = np.linspace(0.0, 1.0, 21)
        v = self.v(rho)
        if float(np.max(v[1:] / v[:-1])) > 1.0 + cfg.monotonic:
            return "Flat ungleichmäßig (wird zum Rand heller)"
        if self.max_gain > MAX_GAIN:
            return f"Flat zu stark (Rand x{self.max_gain:.1f})".replace(".", ",")
        if self.max_gain < NO_EFFECT:
            return "kaum Randabdunklung, keine Korrektur nötig"
        return None

    def to_json(self) -> dict[str, object]:
        return {"a": list(self.a), "cx": self.cx, "cy": self.cy}

    @classmethod
    def from_json(cls, d: object) -> Curve | None:
        if not isinstance(d, dict):
            return None
        try:
            a = [float(x) for x in d["a"]]  # type: ignore[index]
            return cls((a[0], a[1], a[2]), float(d["cx"]), float(d["cy"]))  # type: ignore[index]
        except (KeyError, TypeError, ValueError, IndexError):
            return None


@dataclass
class _Twilight:
    frames: list[npt.NDArray[np.float32]] = field(default_factory=list)
    clipped: list[Mask] = field(default_factory=list)
    last: datetime | None = None


def to_linear(image: Image, srgb: bool) -> npt.NDArray[np.float32]:
    """0..1 linear values."""
    x = image.astype(np.float32) / 255.0
    if not srgb:
        return x
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _srgb_luts() -> tuple[npt.NDArray[np.uint16], npt.NDArray[np.uint8]]:
    """8 bit sRGB → 12 bit linear, and back."""
    x = np.arange(256, dtype=np.float64) / 255.0
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    up = np.round(lin * 4095).astype(np.uint16)
    y = np.arange(4096, dtype=np.float64) / 4095.0
    enc = np.where(y <= 0.0031308, y * 12.92, 1.055 * y ** (1 / 2.4) - 0.055)
    down = np.round(np.minimum(np.maximum(enc, 0.0), 1.0) * 255).astype(np.uint8)
    return up, down


def radius_px(h: int, w: int, circle_frac: float) -> float:
    return min(h, w) * circle_frac


def rho_grid(h: int, w: int, cx: float, cy: float, circle_frac: float) -> Floats:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    r = radius_px(h, w, circle_frac)
    return np.sqrt((xx - cx * (w - 1)) ** 2 + (yy - cy * (h - 1)) ** 2) / r


def _small(image: Image) -> tuple[Image, float]:
    h, w = image.shape[:2]
    if w <= FIT_WIDTH:
        return image, 1.0
    size = (FIT_WIDTH, max(1, round(h * FIT_WIDTH / w)))
    return np.asarray(
        cv2.resize(image, size, interpolation=cv2.INTER_AREA), dtype=np.uint8
    ), w / FIT_WIDTH


def zones(rho: Floats, h: int, w: int, cx: float, cy: float, outer: float) -> list[Mask]:
    """3 rings x 8 directions (E, SE, ... clockwise from east in the image)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    ang = (np.degrees(np.arctan2(yy - cy * (h - 1), xx - cx * (w - 1))) + 360.0 + 22.5) % 360.0
    sector = (ang // 45.0).astype(np.int64)
    edges = (0.0, outer / 3, 2 * outer / 3, outer)
    out: list[Mask] = []
    for i in range(3):
        ring = (rho > edges[i]) & (rho <= edges[i + 1])
        for s in range(8):
            out.append(ring & (sector == s))
    return out


def fit_twilight(
    frames: list[npt.NDArray[np.float32]],
    clipped: list[Mask],
    sky: Mask | None,
    cx: float,
    cy: float,
    circle_frac: float,
    cfg: FlatConfig,
) -> tuple[Curve | None, str | None, int]:
    """The radial curve from the twilight frames (normalised, linear, gray), the reason
    when it is not accepted, and the number of valid zones."""
    h, w = frames[0].shape
    stack = np.empty((len(frames), h, w), dtype=np.float32)
    for i, f in enumerate(frames):
        stack[i] = f
    med = np.median(stack, axis=0)
    rho = rho_grid(h, w, cx, cy, circle_frac)
    any_clipped = np.zeros((h, w), dtype=np.bool_)
    for c in clipped:
        any_clipped |= c
    use = (rho <= 0.95) & ~any_clipped & (med > 0)
    if sky is not None and sky.shape == (h, w):
        use &= sky
    half = len(frames) // 2
    first = np.median(stack[:half], axis=0)
    last = np.median(stack[-half:], axis=0)
    valid = 0
    for z in zones(rho, h, w, cx, cy, cfg.zone_outer):
        inside = z if sky is None or sky.shape != (h, w) else z & sky
        n = int(inside.sum())
        if n == 0 or int((inside & any_clipped).sum()) > n // 1000:
            continue
        a, b = float(np.median(first[inside])), float(np.median(last[inside]))
        if a > 0 and abs(b - a) / a <= cfg.zone_drift:
            valid += 1
    if valid < cfg.zones_needed:
        return None, f"Dämmerung zu unruhig ({valid} von 24 Zonen)", valid
    if int(use.sum()) < 1000:
        return None, "zu wenig Himmel für den Flat", valid
    r = radius_px(h, w, circle_frac)
    yy, xx = np.nonzero(use)
    x = (xx - cx * (w - 1)) / r
    y = (yy - cy * (h - 1)) / r
    p2 = rho[use] ** 2
    basis = np.empty((p2.size, 6), dtype=np.float64)
    basis[:, 0] = 1.0
    basis[:, 1] = x
    basis[:, 2] = y
    basis[:, 3] = p2
    basis[:, 4] = p2 * p2
    basis[:, 5] = p2 * p2 * p2
    target = np.log(med[use].astype(np.float64))
    coef = np.linalg.lstsq(basis, target, rcond=None)[0]
    residual = float(np.sqrt(np.mean((basis @ coef - target) ** 2)))
    if residual > cfg.max_residual:
        return (
            None,
            f"Flat passt nicht ({100 * residual:.1f} % Abweichung)".replace(".", ","),
            valid,
        )
    return Curve((float(coef[3]), float(coef[4]), float(coef[5])), cx, cy), None, valid


def fit_night(
    median: Gray, sky: Mask, srgb: bool, cx: float, cy: float, circle_frac: float, cfg: FlatConfig
) -> Curve | None:
    """The radial curve from a night's median frame: inside the mask, above about 20°,
    stars removed by a median filter; v = exp(a2·rho^2 + a4·rho^4)."""
    small, _ = _small(median)
    h, w = small.shape[:2]
    if sky.shape != (h, w):
        sky = (
            np.asarray(
                cv2.resize(sky.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST),
                dtype=np.uint8,
            )
            > 0
        )
    smooth = np.asarray(cv2.medianBlur(small, 5), dtype=np.uint8)
    lin = to_linear(smooth, srgb).astype(np.float64)
    rho = rho_grid(h, w, cx, cy, circle_frac)
    use = sky & (rho <= cfg.night_reach) & (smooth > 2) & (smooth < cfg.clipped)
    if int(use.sum()) < 1000:
        return None
    p2 = rho[use] ** 2
    basis = np.empty((p2.size, 3), dtype=np.float64)
    basis[:, 0] = 1.0
    basis[:, 1] = p2
    basis[:, 2] = p2 * p2
    coef = np.linalg.lstsq(basis, np.log(lin[use]), rcond=None)[0]
    return Curve((float(coef[1]), float(coef[2]), 0.0), cx, cy)


def agree(a: Curve, b: Curve, tolerance: float) -> bool:
    rho = np.linspace(0.0, 1.0, 21)
    va, vb = a.v(rho), b.v(rho)
    return float(np.max(np.abs(va - vb) / va)) <= tolerance


class FlatField:
    def __init__(
        self,
        path: Path,
        srgb: bool,
        circle_frac: float,
        centre: Callable[[], tuple[float, float] | None] | None = None,
        cfg: FlatConfig | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = path
        self._srgb = srgb
        self._frac = circle_frac
        self._centre = centre or (lambda: None)
        self._cfg = cfg or FlatConfig()
        self._now = now
        self._lock = threading.Lock()
        self._twilight: Curve | None = None
        self._twilight_at: datetime | None = None
        self._nights: list[tuple[str, Curve]] = []
        self._reason: str | None = None
        self._collect = _Twilight()
        self._maps: dict[tuple[int, int], npt.NDArray[np.uint16]] = {}
        self._up, self._down = _srgb_luts()
        self._load()

    # --- which flat ------------------------------------------------------------------
    def _active(self) -> tuple[str, Curve, datetime | None] | None:
        if self._twilight is not None:
            return "twilight", self._twilight, self._twilight_at
        if len(self._nights) >= 2 and agree(self._nights[-1][1], self._nights[-2][1],
                                            self._cfg.night_agree):  # fmt: skip
            night = self._nights[-1][1]
            if night.problem(self._cfg) is None:
                return "night", night, None
        return None

    def status(self) -> dict[str, object]:
        """SPEC §6.3 `flat`."""
        with self._lock:
            active = self._active()
            if active is None:
                reason = self._reason or "noch kein Flat (Dämmerung oder zwei klare Nächte)"
                return {"source": None, "at": None, "max_gain": None, "reason": reason}
            source, curve, at = active
            return {
                "source": source,
                "at": at.isoformat() if at is not None else None,
                "max_gain": round(curve.max_gain, 2),
                "reason": None,
            }

    def _centre_now(self) -> tuple[float, float]:
        c = self._centre()
        return c if c is not None else (0.5, 0.5)

    # --- learning --------------------------------------------------------------------
    def wants_twilight(self, at: datetime, h: int, w: int) -> bool:
        """No flat yet, 30 days old, or the lens centre moved by more than 2 % of the
        image circle's radius."""
        with self._lock:
            if self._twilight is None or self._twilight_at is None:
                return True
            if at - self._twilight_at >= self._cfg.twilight_every:
                return True
            cx, cy = self._centre_now()
            moved = math.hypot((cx - self._twilight.cx) * w, (cy - self._twilight.cy) * h)
            return moved > self._cfg.centre_moved * radius_px(h, w, self._frac)

    def observe_twilight(
        self, at: datetime, sun: float, image: Image, mean: float, low_gain: bool, sky: Mask | None
    ) -> None:
        """A raw frame (before the flat); collects the twilight frames and fits them."""
        cfg = self._cfg
        lo, hi = cfg.twilight_sun
        m_lo, m_hi = cfg.twilight_mean
        if not (lo <= sun <= hi and m_lo <= mean <= m_hi and low_gain):
            return
        if not self.wants_twilight(at, image.shape[0], image.shape[1]):
            return
        c = self._collect
        if c.last is not None and at - c.last > cfg.twilight_gap:
            self._collect = c = _Twilight()
        small, _ = _small(image)
        gray = np.asarray(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
        lin = to_linear(gray, self._srgb)
        level = float(lin.mean())
        if level <= 0:
            return
        c.frames.append((lin / level).astype(np.float32))
        c.clipped.append(np.asarray(small.max(axis=2) >= cfg.clipped, dtype=np.bool_))
        c.last = at
        if len(c.frames) < cfg.twilight_frames:
            return
        self._collect = _Twilight()
        h, w = gray.shape
        small_sky = None
        if sky is not None:
            small_sky = (
                np.asarray(
                    cv2.resize(sky.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST),
                    dtype=np.uint8,
                )
                > 0
            )
        cx, cy = self._centre_now()
        curve, reason, valid = fit_twilight(c.frames, c.clipped, small_sky, cx, cy, self._frac, cfg)
        if curve is not None:
            reason = curve.problem(cfg)
        with self._lock:
            if curve is not None and reason is None:
                self._twilight, self._twilight_at, self._reason = curve, at, None
                self._maps = {}
                log.info("twilight flat: edge x%.2f, %d of 24 zones", curve.max_gain, valid)
            else:
                self._reason = reason
                log.info("twilight flat not used: %s", reason)
            self._save()

    def observe_night(self, night_id: str, median: Gray, sky: Mask) -> None:
        """The sky mask's median frame of a night (SPEC §4.7)."""
        cx, cy = self._centre_now()
        curve = fit_night(median, sky, self._srgb, cx, cy, self._frac, self._cfg)
        if curve is None:
            return
        with self._lock:
            self._nights = [*(n for n in self._nights if n[0] != night_id), (night_id, curve)][-2:]
            if self._twilight is None:
                problem = curve.problem(self._cfg)
                if problem is not None:
                    self._reason = problem
                elif len(self._nights) < 2:
                    self._reason = "Flat geschätzt, wartet auf die zweite Nacht"
                elif not agree(self._nights[0][1], curve, self._cfg.night_agree):
                    self._reason = "Nächte uneinig, noch kein Flat"
                else:
                    self._reason = None
                self._maps = {}
            self._save()
        log.info("night flat %s: edge x%.2f", night_id, curve.max_gain)

    def forget(self) -> None:
        with self._lock:
            self._twilight = self._twilight_at = None
            self._nights, self._reason, self._maps = [], None, {}
            self._collect = _Twilight()
        self._path.unlink(missing_ok=True)

    # --- applying --------------------------------------------------------------------
    def _gain_map(self, h: int, w: int, curve: Curve) -> npt.NDArray[np.uint16]:
        key = (h, w)
        m = self._maps.get(key)
        if m is None:
            rho = np.minimum(rho_grid(h, w, curve.cx, curve.cy, self._frac), 1.0)
            gain = np.minimum(1.0 / curve.v(rho), MAX_GAIN)
            m = np.round(gain * 256).astype(np.uint16)
            self._maps[key] = m
        return m

    def apply(self, image: Image) -> Image:
        """The frame divided by the flat in use; unchanged without one."""
        with self._lock:
            active = self._active()
            if active is None:
                return image
            g = self._gain_map(image.shape[0], image.shape[1], active[1])
        return self._scale(image, g, divide=False)

    def undo_for_measuring(self, image: Image) -> Image:
        """SPEC §4.10: the sky values are measured without the night flat."""
        with self._lock:
            active = self._active()
            if active is None or active[0] != "night":
                return image
            g = self._gain_map(image.shape[0], image.shape[1], active[1])
        return self._scale(image, g, divide=True)

    def _scale(self, image: Image, g: npt.NDArray[np.uint16], divide: bool) -> Image:
        gain = g[:, :, None] if image.ndim == 3 else g
        if self._srgb:
            lin = self._up[image].astype(np.uint32)
            lin = (lin * 256) // gain if divide else (lin * gain) // 256
            return self._down[np.minimum(lin, 4095)]
        x = image.astype(np.uint32)
        x = (x * 256) // gain if divide else (x * gain) // 256
        return np.minimum(x, 255).astype(np.uint8)

    # --- disk --------------------------------------------------------------------------
    def _save(self) -> None:
        meta = {
            "twilight": self._twilight.to_json() if self._twilight is not None else None,
            "twilight_at": self._twilight_at.isoformat() if self._twilight_at else None,
            "nights": [[n, c.to_json()] for n, c in self._nights],
            "reason": self._reason,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.stem + ".tmp.npz")
            np.savez(tmp, meta=np.array(json.dumps(meta)))
            tmp.replace(self._path)
        except OSError as exc:
            log.warning("flat not saved: %s", exc)

    def _load(self) -> None:
        try:
            with np.load(self._path) as data:
                meta = json.loads(str(data["meta"]))
        except (OSError, ValueError, KeyError):
            return
        if not isinstance(meta, dict):
            return
        self._twilight = Curve.from_json(meta.get("twilight"))
        try:
            at = meta.get("twilight_at")
            self._twilight_at = datetime.fromisoformat(at) if isinstance(at, str) else None
        except ValueError:
            self._twilight_at = None
        if self._twilight_at is None:
            self._twilight = None
        nights = meta.get("nights")
        if isinstance(nights, list):
            for item in nights:
                if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str):
                    c = Curve.from_json(item[1])
                    if c is not None:
                        self._nights.append((item[0], c))
        reason = meta.get("reason")
        self._reason = reason if isinstance(reason, str) else None

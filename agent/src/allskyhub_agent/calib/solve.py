"""Image orientation from the stars (SPEC §4.8), ported from Benjamin's constellation
overlay helper for Allsky (scripts/utilities/constellation_overlay.py).

1. Point sources in a clear night frame, brightest first.
2. Two star <-> point correspondences fix the centre, scale and rotation of an
   equidistant fisheye exactly. Every pair of bright points is tried against every pair
   of bright catalogue stars (in both mirror senses); each guess is scored by how many
   other bright stars then land on a point.
3. The best guesses are refined: every catalogue star is looked for near its predicted
   place and kept only if it clearly outshines its neighbourhood; the lens
   r = a1*t + a3*t^3 (t = zenith angle / 90°), the centre, the rotation and, with
   enough stars, a small tilt of the camera are fitted while the search window shrinks.
4. Only a clear winner is accepted: many stars, a small error and no rival close to it.

The original used SciPy for the fit and for neighbour searches; here a small
Levenberg-Marquardt with soft-L1 weights and grid lookups do that with numpy only.
"""

# OpenCV's type stubs leave many return types unknown; the numbers are checked here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

F = npt.NDArray[np.float64]
Gray = npt.NDArray[np.uint8]

CATALOGUE = Path(__file__).with_name("bright_stars.json")
MAX_TILT = 20.0  # degrees the camera's axis may lean


@dataclass(frozen=True)
class Catalogue:
    alt: F
    az: F
    mag: F
    names: list[str]


@dataclass(frozen=True)
class Solution:
    """The fitted lens; `project`/`unproject` map between (alt, az) and pixels."""

    cx: float
    cy: float
    a1: float
    a3: float
    rot: float  # image direction of north at the horizon, 0 = up, clockwise (no tilt)
    flip: float  # +1: east is clockwise from north in the image, -1: mirrored
    tilt_east: float
    tilt_north: float
    stars: int
    rms_px: float
    rms_deg: float
    width: int = 0  # the size of the frame it was solved on; 0: unknown (older file)
    height: int = 0

    @property
    def params(self) -> list[float]:
        return [self.cx, self.cy, self.a1, self.a3, self.rot, self.tilt_east, self.tilt_north]

    def project(self, alt: F, az: F) -> tuple[F, F]:
        return project(alt, az, self.params, self.flip)

    def unproject(self, x: F, y: F) -> tuple[F, F]:
        return unproject(x, y, self.params, self.flip)

    def position(self, x: float, y: float, width: int, height: int) -> tuple[float, float] | None:
        """(azimuth, altitude) in degrees of a pixel in a frame of this size, rounded;
        None if the frame has another size than the solved one."""
        if self.width and (width, height) != (self.width, self.height):
            return None
        alt, az = self.unproject(np.array([x]), np.array([y]))
        return round(float(az[0]) % 360.0, 1), round(float(alt[0]), 1)


# --- sky ------------------------------------------------------------------------------


def sidereal_deg(utc: datetime, lon: float) -> float:
    """Local sidereal time in degrees."""
    jd = utc.astimezone(UTC).timestamp() / 86400.0 + 2440587.5
    d = jd - 2451545.0
    t = d / 36525.0
    return (280.46061837 + 360.98564736629 * d + 0.000387933 * t * t - t**3 / 38710000.0
            + lon) % 360.0  # fmt: skip


def alt_az(ra: F, dec: F, lst: float, lat: float) -> tuple[F, F]:
    rr, dd, la = np.radians(ra), np.radians(dec), math.radians(lat)
    ha = math.radians(lst) - rr
    alt = np.arcsin(np.sin(dd) * math.sin(la) + np.cos(dd) * math.cos(la) * np.cos(ha))
    az = np.arctan2(np.sin(ha), np.cos(ha) * math.sin(la) - np.tan(dd) * math.cos(la))
    return np.degrees(alt), (np.degrees(az) + 180.0) % 360.0


@cache
def _stars() -> tuple[F, F, F, tuple[str, ...]]:
    rows: list[list[float | str]] = json.loads(CATALOGUE.read_text(encoding="utf-8"))["stars"]
    ra = np.array([float(r[0]) for r in rows])
    dec = np.array([float(r[1]) for r in rows])
    mag = np.array([float(r[2]) for r in rows])
    names = tuple(str(r[3]) if len(r) > 3 else "" for r in rows)
    return ra, dec, mag, names


def catalogue(utc: datetime, lat: float, lon: float, min_alt: float = 5.0) -> Catalogue:
    """The bright stars (Hipparcos, V < 3) above `min_alt` at this time and place."""
    ra, dec, mag, names = _stars()
    alt, az = alt_az(ra, dec, sidereal_deg(utc, lon), lat)
    keep = alt >= min_alt
    idx = np.nonzero(keep)[0].tolist()
    return Catalogue(alt[keep], az[keep], mag[keep], [names[i] for i in idx])


# --- lens model -------------------------------------------------------------------------


def _clip(v: F, lo: float, hi: float) -> F:
    return np.minimum(np.maximum(v, lo), hi)


def tilt(alt: F, az: F, tx: float, ty: float, inverse: bool = False) -> tuple[F, F]:
    """Horizon (alt, az) -> (alt, az) in the frame of a camera whose axis leans tx
    degrees toward the east and ty toward the north; inverse: back again."""
    tau = math.radians(math.hypot(tx, ty))
    if tau < 1e-14:
        return alt, az
    phi = math.atan2(tx, ty)
    a, z = np.radians(alt).ravel(), np.radians(az).ravel()
    v = np.empty((3, a.size))
    v[0], v[1], v[2] = np.cos(a) * np.sin(z), np.cos(a) * np.cos(z), np.sin(a)
    axis = np.array([math.sin(tau) * math.sin(phi), math.sin(tau) * math.cos(phi), math.cos(tau)])
    k = np.cross(axis, np.array([0.0, 0.0, 1.0]))
    k = k / float(np.linalg.norm(k))
    ang = -tau if inverse else tau
    kv = np.cross(k[:, None], v, axis=0)
    w = v * math.cos(ang) + kv * math.sin(ang) + k[:, None] * (k @ v) * (1.0 - math.cos(ang))
    out_alt = np.degrees(np.arcsin(_clip(w[2], -1.0, 1.0))).reshape(alt.shape)
    out_az = (np.degrees(np.arctan2(w[0], w[1])) % 360.0).reshape(alt.shape)
    return out_alt, out_az


def project(alt: F, az: F, p: list[float], flip: float) -> tuple[F, F]:
    """(alt, az) -> pixel. p = (cx, cy, a1, a3, rot, tilt east, tilt north)."""
    alt, az = tilt(alt, az, p[5], p[6])
    cx, cy, a1, a3, rot = p[:5]
    t = (90.0 - alt) / 90.0
    r = a1 * t + a3 * t**3
    ang = np.radians(rot + flip * az)
    return cx + r * np.sin(ang), cy - r * np.cos(ang)


def unproject(x: F, y: F, p: list[float], flip: float) -> tuple[F, F]:
    """Pixel -> (alt, az); Newton on r = a1*t + a3*t^3."""
    cx, cy, a1, a3, rot = p[:5]
    dx, dy = x - cx, cy - y
    r = np.hypot(dx, dy)
    az = ((np.degrees(np.arctan2(dx, dy)) - rot) * flip) % 360.0
    t = _clip(r / a1, 0.0, 1.5)
    for _ in range(30):
        f = a1 * t + a3 * t**3 - r
        fp = np.maximum(a1 + 3 * a3 * t * t, 1e-6)
        t = _clip(t - f / fp, 0.0, 1.5)
    return tilt(90.0 - 90.0 * t, az, p[5], p[6], inverse=True)


# --- image ------------------------------------------------------------------------------


def _wide_blur(g: npt.NDArray[np.float32], sigma: float) -> npt.NDArray[np.float32]:
    """A large Gaussian blur on a reduced copy: the same for a smooth background."""
    f = max(1, int(sigma // 4))
    if f == 1:
        return np.asarray(cv2.GaussianBlur(g, (0, 0), sigma), dtype=np.float32)
    h, w = g.shape
    small = cv2.resize(g, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f)
    return np.asarray(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR), dtype=np.float32)


def sky_region(gray: Gray) -> npt.NDArray[np.bool_]:
    """The large bright disc, without the dark corners and any text on them."""
    h, w = gray.shape
    smooth = _wide_blur(gray.astype(np.float32), max(8.0, w / 150))
    level = 0.35 * float(np.median(smooth[h // 3 : 2 * h // 3, w // 3 : 2 * w // 3]))
    sky = (smooth > max(6.0, level)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(sky, connectivity=8)
    if int(n) > 1:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        sky = (np.asarray(lab, dtype=np.int32) == big).astype(np.uint8)
    margin = max(3, int(w * 0.006))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1))
    return np.asarray(cv2.erode(sky, kernel), dtype=np.uint8) > 0


def star_map(gray: Gray) -> npt.NDArray[np.float32]:
    """Band-pass to star-sized spots; saturated stars keep their halo."""
    g = gray.astype(np.float32)
    w = g.shape[1]
    fine = np.asarray(cv2.GaussianBlur(g, (0, 0), max(2.0, w / 960)), dtype=np.float32)
    return fine - _wide_blur(g, max(8.0, w / 240))


def _near_counts(pts: F, values: F, radius: float, rel: float) -> npt.NDArray[np.int64]:
    """For each point: neighbours within `radius` at least `rel` times as strong."""
    n = len(pts)
    out = np.zeros(n, dtype=np.int64)
    if n == 0:
        return out
    cell = max(radius, 1.0)
    keys = np.floor(pts / cell).astype(np.int64)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (kx, ky) in enumerate(keys.tolist()):
        grid.setdefault((kx, ky), []).append(i)
    r2 = radius * radius
    for i, (kx, ky) in enumerate(keys.tolist()):
        cnt = 0
        for gx in (kx - 1, kx, kx + 1):
            for gy in (ky - 1, ky, ky + 1):
                for j in grid.get((gx, gy), ()):
                    if j == i:
                        continue
                    d = pts[j] - pts[i]
                    if float(d[0] * d[0] + d[1] * d[1]) < r2 and values[j] >= rel * values[i]:
                        cnt += 1
        out[i] = cnt
    return out


def detect(gray: Gray, max_n: int, static: F | None = None) -> F:
    """Point sources as local maxima, brightest first, (n, 2) of (x, y). Maxima at the
    same place in `static` (another frame of the night) are text or hot pixels."""
    w = gray.shape[1]
    g = gray.astype(np.float32)
    diff = np.asarray(cv2.GaussianBlur(g, (0, 0), 1.2), dtype=np.float32) - np.asarray(
        cv2.GaussianBlur(g, (0, 0), max(3.0, w / 700)), dtype=np.float32
    )
    wide = _wide_blur(g, max(10.0, w / 150))
    sky = sky_region(gray)
    sky_level = float(np.median(wide[sky])) if sky.any() else float(np.median(wide))
    v = diff[sky][::7]
    noise = 1.4826 * float(np.median(np.abs(v - np.median(v)))) if v.size else 2.0
    k = max(5, int(w / 550) | 1)
    dil = np.asarray(cv2.dilate(diff, np.ones((k, k), np.uint8)), dtype=np.float32)
    peaks = (diff == dil) & (diff > max(4.0, min(12.0, 6 * noise))) & sky & (wide > 0.5 * sky_level)
    m = max(1, int(0.012 * w))  # the frame's edge: cut-off stars and frame lines
    peaks[:m, :] = False
    peaks[-m:, :] = False
    peaks[:, :m] = False
    peaks[:, -m:] = False
    ys, xs = np.nonzero(peaks)
    pts = np.empty((len(xs), 2))
    pts[:, 0], pts[:, 1] = xs, ys
    strength = star_map(gray)[ys, xs].astype(np.float64)
    if len(pts):
        # A star is an isolated point; letters have several similar maxima close by.
        c = diff[ys, xs].astype(np.float64)
        crowded = _near_counts(pts, c, max(12.0, w / 80), 0.4) >= 2
        pts, strength = pts[~crowded], strength[~crowded]
    if static is not None and len(static) and len(pts):
        tol = max(2.0, w / 1300)
        dist = _near_counts(np.concatenate([pts, static]),
                            np.ones(len(pts) + len(static)), tol, 0.0)  # fmt: skip
        # Points with a static point within tol (the count includes other new points,
        # which are farther apart than tol: maxima are at least k px apart).
        hit = dist[: len(pts)] > 0
        pts, strength = pts[~hit], strength[~hit]
    order = np.argsort(-strength)[:max_n]
    return pts[order]


# --- identification -----------------------------------------------------------------------


def _hypotheses(
    cat: Catalogue, dets: F, w: int, h: int, n_blobs: int = 22, n_stars: int = 22, keep: int = 12
) -> list[tuple[int, float, float, float, float, float]]:
    """(score, cx, cy, R, rot, flip) of the best distinct guesses from pairs."""
    alt, az, mag = cat.alt, cat.az, cat.mag
    score_sel = (mag <= 3.0) & (alt >= 12)
    pick = np.nonzero((mag <= 3.0) & (alt >= 15))[0]
    pick = pick[np.argsort(mag[pick])][:n_stars]
    blobs = dets[:n_blobs]
    thr = 0.008 * w
    hitmap = np.zeros((h, w), np.uint8)
    for x, y in dets[:150].tolist():
        cv2.circle(hitmap, (round(x), round(y)), int(thr), 1, -1)
    hits_ok = hitmap.astype(np.bool_)
    p = blobs[:, 0] + 1j * blobs[:, 1]
    ii, jj = np.nonzero(~np.eye(len(p), dtype=np.bool_))
    dp = p[ii] - p[jj]
    found: list[tuple[int, float, float, float, float, float]] = []
    for flip in (1.0, -1.0):
        s = (90.0 - alt[pick]) / 90.0 * np.exp(1j * np.radians(flip * az[pick]))
        s_all = (90.0 - alt[score_sel]) / 90.0 * np.exp(1j * np.radians(flip * az[score_sel]))
        for a in range(len(s)):
            for b in range(a + 1, len(s)):
                ds = s[a] - s[b]
                if abs(ds) < 0.05:
                    continue
                aa = dp / ds
                rr = np.abs(aa)
                cc = p[ii] - aa * s[a]
                ok = (
                    (rr > 0.15 * min(w, h)) & (rr < 4.0 * max(w, h))
                    & (cc.real > -0.2 * w) & (cc.real < 1.2 * w)
                    & (cc.imag > -0.2 * h) & (cc.imag < 1.2 * h)
                )  # fmt: skip
                idx = np.nonzero(ok)[0]
                if len(idx) == 0:
                    continue
                q = cc[idx, None] + aa[idx, None] * s_all[None, :]
                inside = (q.real >= 0) & (q.real < w - 0.5) & (q.imag >= 0) & (q.imag < h - 0.5)
                hits = np.zeros(q.shape, dtype=np.bool_)
                hits[inside] = hits_ok[
                    np.rint(q.imag[inside]).astype(np.int64),
                    np.rint(q.real[inside]).astype(np.int64),
                ]
                score = hits.sum(axis=1)
                for m in np.nonzero((score >= 6) & (inside.sum(axis=1) >= 6))[0].tolist():
                    n = int(idx[m])
                    rot = (math.degrees(float(np.angle(aa[n]))) + 90.0) % 360.0
                    found.append(
                        (
                            int(score[m]),
                            float(cc[n].real),
                            float(cc[n].imag),
                            float(rr[n]),
                            rot,
                            flip,
                        )
                    )
    found.sort(reverse=True)
    distinct: list[tuple[int, float, float, float, float, float]] = []
    for g in found:
        if all(
            abs(g[1] - d[1]) > 0.02 * w
            or abs(g[2] - d[2]) > 0.02 * w
            or abs(g[3] / d[3] - 1) > 0.05
            or abs((g[4] - d[4] + 180) % 360 - 180) > 3
            or g[5] != d[5]
            for d in distinct
        ):
            distinct.append(g)
        if len(distinct) >= keep:
            break
    return distinct


def _noise_threshold(stars: npt.NDArray[np.float32], sky: npt.NDArray[np.bool_]) -> float:
    v = stars[sky][::7]
    return max(3.0, 12.0 * 1.4826 * float(np.median(np.abs(v - np.median(v)))))


def _bright_pairs(
    cat: Catalogue, stars: npt.NDArray[np.float32], sky: npt.NDArray[np.bool_], thr: float,
    p: list[float], flip: float, radius: float, dominance: float = 1.35,
) -> F:  # fmt: skip
    """(alt, az, x, y) for catalogue stars whose search window holds one clear winner."""
    h, w = stars.shape
    x, y = project(cat.alt, cat.az, p, flip)
    r = int(radius)
    out: list[tuple[float, float, float, float]] = []
    for a, z, px, py in zip(cat.alt.tolist(), cat.az.tolist(), x.tolist(), y.tolist(),
                            strict=True):  # fmt: skip
        if not (math.isfinite(px) and math.isfinite(py)):
            continue
        x0, y0, x1, y1 = int(px) - r, int(py) - r, int(px) + r + 1, int(py) + r + 1
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
            continue
        win = stars[y0:y1, x0:x1].astype(np.float64)
        win[~sky[y0:y1, x0:x1]] = -1e9
        jy, jx = divmod(int(np.argmax(win)), win.shape[1])
        best = float(win[jy, jx])
        if best < thr:
            continue
        yy, xx = np.ogrid[: win.shape[0], : win.shape[1]]
        win[(yy - jy) ** 2 + (xx - jx) ** 2 < 36] = -1e9
        if best < dominance * max(float(win.max()), 1.0):
            continue
        out.append((a, z, float(x0 + jx), float(y0 + jy)))
    # One point can't be two stars: a lens shrunk to nothing would "find" every star on
    # the same bright point.
    seen: dict[tuple[float, float], int] = {}
    for _, _, px, py in out:
        seen[(px, py)] = seen.get((px, py), 0) + 1
    out = [o for o in out if seen[(o[2], o[3])] == 1]
    return np.array(out, dtype=np.float64).reshape(-1, 4)


def _fit(pairs: F, p0: list[float], flip: float, free_a3: bool, free_tilt: bool) -> list[float]:
    """Levenberg-Marquardt with soft-L1 weights (f_scale 6 px), like SciPy's
    least_squares(loss="soft_l1"); the tilt is bounded to MAX_TILT."""
    free = [0, 1, 2, 4] + ([3] if free_a3 else []) + ([5, 6] if free_tilt else [])
    p = list(p0)

    def resid(q: list[float]) -> F:
        x, y = project(pairs[:, 0], pairs[:, 1], q, flip)
        return np.concatenate([x - pairs[:, 2], y - pairs[:, 3]])

    lam = 1e-3
    for _ in range(60):
        r = resid(p)
        wgt = 1.0 / np.sqrt(1.0 + (r / 6.0) ** 2)  # soft-L1: IRLS weights
        cost = float(np.sum(6.0**2 * 2 * (np.sqrt(1 + (r / 6.0) ** 2) - 1)))
        jac = np.empty((r.size, len(free)))
        for j, i in enumerate(free):
            step = 1e-4 * max(1.0, abs(p[i]))
            q = list(p)
            q[i] += step
            jac[:, j] = (resid(q) - r) / step
        jw = jac * wgt[:, None]
        a = jw.T @ jw
        g = jw.T @ (r * wgt)
        improved = False
        for _ in range(10):
            try:
                delta = np.linalg.solve(a + lam * np.diag(np.diag(a) + 1e-9), -g)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            q = list(p)
            for j, i in enumerate(free):
                q[i] += float(delta[j])
            for i in (5, 6):
                q[i] = min(MAX_TILT, max(-MAX_TILT, q[i]))
            rq = resid(q)
            cq = float(np.sum(6.0**2 * 2 * (np.sqrt(1 + (rq / 6.0) ** 2) - 1)))
            if math.isfinite(cq) and cq < cost:
                p, lam, improved = q, max(lam / 10, 1e-9), True
                if cost - cq < 1e-9 * max(cost, 1.0):
                    return p
                break
            lam *= 10
        if not improved:
            break
    return p


def _refine(
    cat: Catalogue, stars: npt.NDArray[np.float32], sky: npt.NDArray[np.bool_], thr: float,
    p: list[float], flip: float, w: int,
) -> tuple[list[float] | None, F]:  # fmt: skip
    s = w / 3840.0
    pairs = np.empty((0, 4))
    for it, rad in enumerate((100, 80, 62, 48, 38, 30, 24, 20)):
        pairs = _bright_pairs(cat, stars, sky, thr, p, flip, rad * s)
        if len(pairs) < 8:
            return None, pairs
        p = _fit(pairs, p, flip, free_a3=it >= 2, free_tilt=it >= 4 and len(pairs) >= 15)
    return p, pairs


def residuals(pairs: F, p: list[float], flip: float) -> tuple[float, float]:
    """RMS residual in px and in degrees (each star at its own plate scale)."""
    x, y = project(pairs[:, 0], pairs[:, 1], p, flip)
    e = np.hypot(x - pairs[:, 2], y - pairs[:, 3])
    t = (90.0 - pairs[:, 0]) / 90.0
    deg = e / np.maximum((p[2] + 3 * p[3] * t**2) / 90.0, 1e-6)
    return float(np.sqrt(np.mean(e**2))), float(np.sqrt(np.mean(deg**2)))


def solve(
    gray: Gray, utc: datetime, lat: float, lon: float, static: Gray | None = None
) -> Solution | None:
    """The lens and orientation of a clear night frame, or None without a clear winner.

    `static`: another frame of the same night, about half an hour away; points at the
    same place in both are text or hot pixels, not stars.
    """
    h, w = gray.shape
    stat = detect(static, 3000) if static is not None and static.shape == gray.shape else None
    dets = detect(gray, 400, stat)
    if len(dets) < 12:
        return None
    cat = catalogue(utc, lat, lon)
    stars = star_map(gray)
    sky = sky_region(gray)
    thr = _noise_threshold(stars, sky)
    results: list[tuple[int, list[float], F, float, float, float]] = []
    for _, cx, cy, rr, rot, flip in _hypotheses(cat, dets, w, h):
        p, pairs = _refine(cat, stars, sky, thr, [cx, cy, rr, 0.0, rot, 0.0, 0.0], flip, w)
        if p is None or p[2] < 0.15 * min(w, h):
            continue
        rms_px, rms_deg = residuals(pairs, p, flip)
        if rms_deg < 0.75:
            results.append((len(pairs), p, pairs, flip, rms_px, rms_deg))
    if not results:
        return None
    results.sort(key=lambda r: -r[0])
    n, p, _pairs, flip, rms_px, rms_deg = results[0]
    rivals = [
        r for r in results[1:]
        if math.hypot(r[1][0] - p[0], r[1][1] - p[1]) > 0.02 * w
        or abs((r[1][4] - p[4] + 180) % 360 - 180) > 3
        or r[3] != flip
    ]  # fmt: skip
    if n < 12 or rms_deg > 0.6 or (rivals and rivals[0][0] >= 0.7 * n):
        return None
    return Solution(
        cx=round(p[0], 2), cy=round(p[1], 2), a1=round(p[2], 3), a3=round(p[3], 3),
        rot=round(p[4] % 360.0, 3), flip=flip, tilt_east=round(p[5], 3),
        tilt_north=round(p[6], 3), stars=n, rms_px=round(rms_px, 2), rms_deg=round(rms_deg, 3),
        width=w, height=h,
    )  # fmt: skip

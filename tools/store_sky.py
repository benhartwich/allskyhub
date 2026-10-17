"""Synthetic all-sky pictures for the store screenshots (Benjamin, 2026-10-16: "virtuelle
Screenshots"). Not a camera's picture: the real stars to magnitude 5 (Hipparcos, from the
agent's calibration catalogue) plus faint random ones, a Milky Way along the galactic
equator, sky glow towards the horizon and a tree line, through an equidistant fisheye at
the neutral example place. Writes into app/test/store/sky/:

    live.jpg          the sky at 23:30, 1080 x 1080
    live_thumb.jpg    the same, 480 x 360
    meteor.jpg        a frame with a bright meteor
    meteor_thumb.jpg  the same, small
    keogram.jpg       one north-south column per frame from 19:00 to 07:00
    startrails.jpg    the stars' paths over the night
    ../sky_view.json  what the hub's sky view (api/sky_view.py) answers at 23:30

    uv run python tools/store_sky.py
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFilter

from allskyhub_sky import alt_az, precess, sidereal_deg, sky_lines
from allskyhub_sky.astro import moon_radec, sun_altitude
from allskyhub_sky.objects import PLANETS, planet_radec

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "test" / "store" / "sky"
CATALOGUE = ROOT / "agent" / "src" / "allskyhub_agent" / "calib" / "stars_mag5.json"
LAT, LON = 50.04, 10.39
ZONE = dt.timezone(dt.timedelta(hours=2))
W, H = 1080, 1080
RADIUS = 520.0
TREE_STEPS = 1440
Array = NDArray[np.float64]


def _stars(rng: np.random.Generator) -> Array:
    """(ra, dec, vmag) rows: the catalogue plus faint stars to 7.5, denser along the
    Milky Way."""
    data: dict[str, Any] = json.loads(CATALOGUE.read_text())
    bright = np.array(data["stars"], dtype=np.float64)
    n = 9000
    ra = rng.uniform(0, 360, n * 3)
    dec = np.degrees(np.arcsin(rng.uniform(-1, 1, n * 3)))
    b = _galactic_b(np.radians(ra), np.radians(dec))
    keep = rng.uniform(0, 1, n * 3) < 0.25 + 0.75 * np.exp(-((b / 12) ** 2))
    ra, dec = ra[keep][:n], dec[keep][:n]
    mag = 5.0 + 2.5 * rng.power(2.2, ra.size)
    out = np.empty((bright.shape[0] + ra.size, 3))
    out[: bright.shape[0]] = bright
    out[bright.shape[0] :, 0] = ra
    out[bright.shape[0] :, 1] = dec
    out[bright.shape[0] :, 2] = mag
    return out


def _galactic_b(ra: Array, dec: Array) -> Array:
    """Galactic latitude (degrees) of ICRS directions in radians."""
    pole_ra, pole_dec = math.radians(192.8595), math.radians(27.1283)
    s = np.sin(dec) * math.sin(pole_dec) + np.cos(dec) * math.cos(pole_dec) * np.cos(ra - pole_ra)
    return np.degrees(np.arcsin(np.minimum(1.0, np.maximum(-1.0, s))))


def _galactic_l(ra: Array, dec: Array) -> Array:
    pole_ra, pole_dec, l_ncp = math.radians(192.8595), math.radians(27.1283), 122.932
    y = np.cos(dec) * np.sin(ra - pole_ra)
    x = np.sin(dec) * math.cos(pole_dec) - np.cos(dec) * math.sin(pole_dec) * np.cos(ra - pole_ra)
    return (l_ncp - np.degrees(np.arctan2(y, x))) % 360.0


def _grid(width: int, height: int, radius: float) -> tuple[Array, Array, NDArray[np.bool_]]:
    """Altitude and azimuth (radians) of each pixel; north up, east left."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    dx, dy = xx - width / 2, yy - height / 2
    r = np.hypot(dx, dy) / radius
    inside = r <= 1.0
    alt = np.radians(90.0 * (1.0 - np.minimum(r, 1.0)))
    az = np.arctan2(-dx, -dy) % (2 * math.pi)
    return alt, az, inside


def _radec(alt: Array, az: Array, lst: float) -> tuple[Array, Array]:
    lat = math.radians(LAT)
    sin_dec = np.sin(alt) * math.sin(lat) + np.cos(alt) * math.cos(lat) * np.cos(az)
    dec = np.arcsin(np.minimum(1.0, np.maximum(-1.0, sin_dec)))
    ha = np.arctan2(
        -np.sin(az) * np.cos(alt),
        np.sin(alt) * math.cos(lat) - np.cos(alt) * math.sin(lat) * np.cos(az),
    )
    return (math.radians(lst) - ha) % (2 * math.pi), dec


def _altaz(ra: Array, dec: Array, lst: float) -> tuple[Array, Array]:
    lat = math.radians(LAT)
    ha = math.radians(lst) - ra
    sin_alt = np.sin(dec) * math.sin(lat) + np.cos(dec) * math.cos(lat) * np.cos(ha)
    alt = np.arcsin(np.minimum(1.0, np.maximum(-1.0, sin_alt)))
    az = np.arctan2(
        -np.sin(ha) * np.cos(dec),
        np.sin(dec) * math.cos(lat) - np.cos(dec) * math.sin(lat) * np.cos(ha),
    )
    return alt, az % (2 * math.pi)


def _xy(alt: Array, az: Array, width: int, height: int, radius: float) -> tuple[Array, Array]:
    r = radius * (1.0 - np.degrees(alt) / 90.0)
    return width / 2 - r * np.sin(az), height / 2 - r * np.cos(az)


class Sky:
    def __init__(self, seed: int = 7) -> None:
        self.rng = np.random.default_rng(seed)
        self.stars = _stars(self.rng)
        self.colour = self.rng.normal(0, 1, self.stars.shape[0])  # blue-white to orange
        self.trees = self._trees()

    def _trees(self) -> Array:
        """Height of the tree line (degrees) for each quarter degree of azimuth: a few
        tree crowns, a roof in the south-west, fine twigs."""
        az = np.arange(TREE_STEPS) / (TREE_STEPS / 360)
        base = np.full(TREE_STEPS, 1.0)
        for _ in range(70):
            centre, width, height = (
                self.rng.uniform(0, 360),
                self.rng.uniform(1.5, 6),
                self.rng.uniform(1.5, 6.5),
            )
            d = (az - centre + 180) % 360 - 180
            crown = height * np.sqrt(np.maximum(0.0, 1 - (d / width) ** 2))
            base = np.maximum(base, crown)
        roof = (az > 205) & (az < 228)
        base[roof] = np.maximum(base[roof], 6.0 + 2.5 * (1 - np.abs(az[roof] - 216.5) / 11.5))
        twigs = self.rng.normal(0, 0.35, TREE_STEPS)
        return np.maximum(0.6, base + np.convolve(twigs, np.ones(3) / 3, mode="same"))

    def background(self, at: dt.datetime, width: int, height: int, radius: float) -> Array:
        alt, az, inside = _grid(width, height, radius)
        lst = sidereal_deg(at, LON)
        ra, dec = _radec(alt, az, lst)
        b = _galactic_b(ra, dec)
        gl = _galactic_l(ra, dec)
        inward = np.maximum(0.0, np.cos(np.radians(gl)))
        towards_centre = 0.35 + 0.65 * inward**2
        lane = 1.0 - 0.55 * np.exp(-(((b - 1.2) / 2.0) ** 2)) * inward
        clouds = self._texture(ra, dec)
        milky = 0.016 * np.exp(-((b / 8.0) ** 2)) * towards_centre * lane * clouds
        zenith = np.degrees(alt)
        glow = 0.0024 + 0.010 * np.exp(-zenith / 11.0)
        rgb = np.empty((height, width, 3))
        rgb[..., 0] = glow * 1.35 + milky * 0.95
        rgb[..., 1] = glow * 0.95 + milky * 0.97
        rgb[..., 2] = glow * 0.75 + milky * 1.1 + 0.0012
        trees = self.trees[(np.degrees(az) * (TREE_STEPS / 360)).astype(int) % TREE_STEPS]
        dark = (zenith < trees) | ~inside
        rgb[dark] = 0.0004
        return rgb

    def _texture(self, ra: Array, dec: Array) -> Array:
        """Patchy star clouds: a few sine waves on the celestial sphere, fixed per seed."""
        if not hasattr(self, "_waves"):
            self._waves = [
                # whole waves around in right ascension, or the sky gets a seam at 0h
                (int(self.rng.integers(3, 19)), self.rng.uniform(4, 24), self.rng.uniform(0, 6.3))
                for _ in range(12)
            ]
        out = np.ones_like(ra)
        for kr, kd, phase in self._waves:
            out += 0.07 * np.sin(kr * ra + kd * dec + phase)
        return np.maximum(0.3, out)

    def frame(
        self, at: dt.datetime, width: int = W, height: int = H, radius: float = RADIUS
    ) -> Image.Image:
        rgb = self.background(at, width, height, radius)
        lst = sidereal_deg(at, LON)
        ra, dec, mag = (
            np.radians(self.stars[:, 0]),
            np.radians(self.stars[:, 1]),
            self.stars[:, 2],
        )
        alt, az = _altaz(ra, dec, lst)
        up = (
            np.degrees(alt)
            > self.trees[(np.degrees(az) * (TREE_STEPS / 360)).astype(int) % TREE_STEPS] + 0.3
        )
        x, y = _xy(alt[up], az[up], width, height, radius)
        flux = 10 ** (-0.4 * (mag[up] - 1.0))
        extinction = np.exp(-0.25 / np.maximum(np.sin(alt[up]), 0.08))
        flux *= extinction
        scale = radius / RADIUS
        stars = np.zeros_like(rgb)
        tint = self.colour[up]
        for xi, yi, f, c in zip(x, y, flux, tint, strict=True):
            sigma = (0.75 + 0.45 * min(3.5, max(0.0, math.log10(f + 1e-9) + 2.0))) * scale
            reach = max(2, int(sigma * 3.5))
            cx, cy = round(xi), round(yi)
            x0, x1 = max(0, cx - reach), min(width, cx + reach + 1)
            y0, y1 = max(0, cy - reach), min(height, cy + reach + 1)
            if x0 >= x1 or y0 >= y1:
                continue
            gy, gx = np.mgrid[y0:y1, x0:x1]
            blob = np.exp(-((gx - xi) ** 2 + (gy - yi) ** 2) / (2 * sigma**2))
            amp = min(8.0, 0.6 * f / sigma**2)
            colour = (1.0 + 0.12 * c, 1.0, 1.0 - 0.15 * c)
            for ch in range(3):
                stars[y0:y1, x0:x1, ch] += amp * blob * colour[ch]
        rgb += stars
        rgb += np.abs(self.rng.normal(0, 0.0004, rgb.shape))
        return _image(rgb)

    def meteor(self, at: dt.datetime) -> Image.Image:
        """A frame with a bright meteor from the north-east towards the south."""
        image = self.frame(at)
        streak = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(streak)
        x0, y0, x1, y1 = 380.0, 300.0, 650.0, 640.0
        steps = 60
        for i in range(steps):
            t0, t1 = i / steps, (i + 1) / steps
            brightness = int(255 * math.sin(math.pi * min(1.0, t1 * 1.15)) ** 0.6)
            draw.line(
                [
                    (x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0),
                    (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1),
                ],
                fill=brightness,
                width=3,
            )
        core = np.asarray(streak.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float64) / 255
        halo = np.asarray(streak.filter(ImageFilter.GaussianBlur(6)), dtype=np.float64) / 255
        rgb = np.asarray(image, dtype=np.float64) / 255
        rgb[..., 0] += core * 0.95 + halo * 0.15
        rgb[..., 1] += core * 1.0 + halo * 0.35
        rgb[..., 2] += core * 0.9 + halo * 0.25
        return Image.fromarray((np.minimum(rgb, 1.0) * 255).astype(np.uint8))

    def keogram(self, start: dt.datetime, end: dt.datetime, columns: int = 660) -> Image.Image:
        """One north-south column per frame, small frames for speed."""
        height, radius = 540, 260.0
        out = np.empty((height, columns, 3))
        for i in range(columns):
            at = start + (end - start) * (i / (columns - 1))
            rgb = self._small(at, height, radius)
            out[:, i, :] = rgb[:, height // 2, :]
        twilight = np.array(
            [_twilight(start + (end - start) * (i / (columns - 1))) for i in range(columns)]
        )
        out += twilight[None, :, None] * np.array([0.35, 0.55, 1.0])[None, None, :]
        return _image(out)

    def _small(self, at: dt.datetime, size: int, radius: float) -> Array:
        rgb = self.background(at, size, size, radius)
        lst = sidereal_deg(at, LON)
        ra, dec, mag = (
            np.radians(self.stars[:, 0]),
            np.radians(self.stars[:, 1]),
            self.stars[:, 2],
        )
        alt, az = _altaz(ra, dec, lst)
        bright = (mag < 4.0) & (np.degrees(alt) > 5)
        x, y = _xy(alt[bright], az[bright], size, size, radius)
        for xi, yi, m in zip(x, y, mag[bright], strict=True):
            cx, cy = round(xi), round(yi)
            if 0 <= cx < size and 0 <= cy < size:
                rgb[cy, cx, :] += 10 ** (-0.4 * (m - 1.0)) * 0.6
        return rgb

    def startrails(self, start: dt.datetime, end: dt.datetime) -> Image.Image:
        """The brighter stars' arcs over the night on the background of the middle."""
        base = self.background(start + (end - start) / 2, W, H, RADIUS) * 0.8
        trails = Image.new("RGB", (W, H))
        draw = ImageDraw.Draw(trails)
        ra, dec, mag = (
            np.radians(self.stars[:, 0]),
            np.radians(self.stars[:, 1]),
            self.stars[:, 2],
        )
        pick = mag < 3.9
        steps = 90
        points: list[tuple[Array, Array, NDArray[np.bool_]]] = []
        for i in range(steps + 1):
            at = start + (end - start) * (i / steps)
            alt, az = _altaz(ra[pick], dec[pick], sidereal_deg(at, LON))
            x, y = _xy(alt, az, W, H, RADIUS)
            up = (
                np.degrees(alt)
                > self.trees[(np.degrees(az) * (TREE_STEPS / 360)).astype(int) % TREE_STEPS]
            )
            points.append((x, y, up))
        level = np.minimum(255, 25 + 230 * 10 ** (-0.4 * (mag[pick] - 0.8))).astype(int)
        tint = self.colour[pick]
        for (xa, ya, ua), (xb, yb, ub) in itertools.pairwise(points):
            for j in range(xa.size):
                if ua[j] and ub[j]:
                    v = int(level[j])
                    colour = (
                        min(255, int(v * (1 + 0.1 * tint[j]))),
                        v,
                        min(255, int(v * (1 - 0.12 * tint[j]))),
                    )
                    width = 2 if v > 150 else 1
                    draw.line([(xa[j], ya[j]), (xb[j], yb[j])], fill=colour, width=width)
        lines = np.asarray(trails.filter(ImageFilter.GaussianBlur(0.6)), dtype=np.float64) / 255
        return _image(np.maximum(base, lines))


def _twilight(at: dt.datetime) -> float:
    """Blue sky light while the sun is less than 18° below the horizon."""
    sun = sun_altitude(at, LAT, LON)
    return float(min(1.0, math.exp((sun + 18.0) / 2.5) * 0.004))


def _image(rgb: Array) -> Image.Image:
    """Linear light stretched like a camera's night picture (asinh keeps star colours)."""
    k = 120.0
    v = np.arcsinh(np.maximum(0.0, rgb) * k) / math.asinh(k)
    v = np.minimum(1.0, v)
    return Image.fromarray((v * 255).astype(np.uint8))


def sky_view(at: dt.datetime) -> dict[str, Any]:
    """The hub's sky view for the example place: constellations, moon and planets, with a
    meteor and the ISS of that night."""
    lines = sky_lines(LAT, LON, at)
    lst = sidereal_deg(at, LON)
    objects: list[dict[str, Any]] = [
        {"label": name, "kind": "constellation", "alt": alt, "az": az}
        for name, alt, az in lines.names
    ]
    for key, label in PLANETS.items():
        alt, az = alt_az(*precess(*planet_radec(key, at), at), lst, LAT)
        if alt >= -5:
            objects.append({"label": label, "kind": "planet", "alt": alt, "az": az})
    alt, az = alt_az(*moon_radec(at), lst, LAT)
    if alt >= -5:
        objects.append({"label": "Mond", "kind": "moon", "alt": alt, "az": az})
    utc = at.astimezone(dt.UTC)
    return {
        "at": utc.isoformat().replace("+00:00", "Z"),
        "lines": [[[round(a, 2), round(z, 2)] for a, z in line] for line in lines.lines],
        "objects": objects,
        "meteors": [
            {
                "at": (utc - dt.timedelta(minutes=40)).isoformat().replace("+00:00", "Z"),
                "start": [62.0, 70.0],
                "end": [38.0, 30.0],
            }
        ],
        "overflights": [
            {
                "at": (utc - dt.timedelta(minutes=95)).isoformat().replace("+00:00", "Z"),
                "start": [12.0, 250.0],
                "end": [35.0, 40.0],
                "kind": "satellite",
                "name": "ISS (ZARYA)",
            }
        ],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sky = Sky()
    night = dt.datetime(2026, 10, 14, 23, 30, tzinfo=ZONE)
    live = sky.frame(night)
    live.save(OUT / "live.jpg", quality=90)
    live.resize((360, 360), Image.Resampling.LANCZOS).save(OUT / "live_thumb.jpg", quality=88)
    meteor = sky.meteor(dt.datetime(2026, 10, 15, 2, 14, tzinfo=ZONE))
    meteor.save(OUT / "meteor.jpg", quality=90)
    meteor.resize((360, 360), Image.Resampling.LANCZOS).save(OUT / "meteor_thumb.jpg", quality=88)
    start = dt.datetime(2026, 10, 14, 19, 0, tzinfo=ZONE)
    end = dt.datetime(2026, 10, 15, 7, 0, tzinfo=ZONE)
    sky.keogram(start, end).save(OUT / "keogram.jpg", quality=90)
    trails_start = dt.datetime(2026, 10, 14, 21, 0, tzinfo=ZONE)
    trails_end = dt.datetime(2026, 10, 15, 3, 0, tzinfo=ZONE)
    sky.startrails(trails_start, trails_end).save(OUT / "startrails.jpg", quality=90)
    (OUT.parent / "sky_view.json").write_text(json.dumps(sky_view(night), indent=1))
    for p in sorted(OUT.iterdir()):
        print(p)


if __name__ == "__main__":
    main()

"""Star size (HFR) for the focus helper and the sky values (SPEC §6.3, §7)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt

from allskyhub_agent.detect.hfr import FocusHfr, Hfr, focus_hfr, star_hfr
from allskyhub_agent.detect.sky import SkyMeter
from allskyhub_agent.live import LiveState
from allskyhub_protocol import FrameInfo, Mode


def _field(
    sigma: float, n: int = 40, size: int = 300, peak: float = 120.0, seed: int = 1
) -> npt.NDArray[np.uint8]:
    """Gaussian stars of width `sigma` on a noisy background."""
    rng = np.random.default_rng(seed)
    img = np.full((size, size), 20.0)
    for x, y in rng.uniform(20, size - 20, (n, 2)):
        x0, y0 = int(x) - 15, int(y) - 15  # each star only in its own window: fast
        yy, xx = np.mgrid[y0 : y0 + 31, x0 : x0 + 31]
        img[y0 : y0 + 31, x0 : x0 + 31] += peak * np.exp(
            -((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2)
        )
    img += rng.normal(0, 2.0, img.shape)
    return np.minimum(np.maximum(img, 0), 255).astype(np.uint8)


def test_blurred_stars_are_larger() -> None:
    sizes = [star_hfr(_field(s)) for s in (0.8, 1.5, 2.5, 4.0)]
    values = [v.hfr for v in sizes if v is not None]
    assert len(values) == 4
    assert values == sorted(values)
    # Near the mean radius of a Gaussian (1.25 sigma): noise does not inflate sharp stars.
    assert 0.8 < values[0] < 1.3
    assert 2.7 < values[2] < 3.6


def test_too_few_stars_give_nothing() -> None:
    assert star_hfr(_field(1.0, n=0)) is None
    assert star_hfr(_field(1.0, n=2)) is None


def test_saturated_stars_and_hot_pixels_are_skipped() -> None:
    img = _field(1.0, n=0)
    img[50, 50] = img[120, 200] = img[220, 80] = img[250, 250] = 255  # hot pixels
    assert star_hfr(img) is None
    burnt = _field(2.0, peak=600.0)  # flat tops
    assert star_hfr(burnt) is None


def test_focus_hfr_centre_and_edge() -> None:
    img = _field(1.2, n=400, size=800)
    rgb = np.empty((800, 800, 3), dtype=np.uint8)
    rgb[:, :, :] = img[:, :, None]
    out = focus_hfr(rgb)
    assert out.centre is not None
    assert out.edge is not None
    assert abs(out.centre.hfr - out.edge.hfr) < 0.5


def _frame(sun: float) -> FrameInfo:
    return FrameInfo(
        captured_at=datetime(2026, 11, 15, 22, 0, tzinfo=UTC),
        night_id="20261115",
        name="image-x.jpg",
        mode=Mode.NIGHT,
        exposure_us=30_000_000,
        gain=200,
        mean=0.1,
        sun_elevation=sun,
        profile="sim",
    )


def test_sky_reports_hfr_only_at_night() -> None:
    img = _field(1.5, n=300, size=600)
    rgb = np.empty((600, 600, 3), dtype=np.uint8)
    rgb[:, :, :] = img[:, :, None]
    meter = SkyMeter()
    night = meter.measure(_frame(-20.0), rgb)
    assert night.hfr is not None
    assert 1.4 < night.hfr < 2.4
    assert meter.measure(_frame(-5.0), rgb).hfr is None


def test_focus_mode_smooths_and_keeps_the_best() -> None:
    live = LiveState()
    live.set_focus_mode(True)
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    for v in (3.0, 2.0, 9.0, 2.0, 2.0, 2.0):  # 9.0: one odd frame
        live.publish(_frame(-20.0), img, 1.0, FocusHfr(Hfr(v, 10), Hfr(v + 1, 30)))
    s = live.status()
    assert s["hfr"] == 2.0  # the last 5 without the smallest and largest
    assert s["hfr_best"] == 2.0
    assert s["hfr_edge"] == 3.0
    assert s["hfr_stars"] == 10
    assert s["hfr_history"] == [3.0, 2.5, 4.67, 4.0, 2.33, 2.0]

    live.publish(_frame(-20.0), img, 1.0, FocusHfr(None, None))  # clouds
    assert live.status()["hfr"] is None
    live.reset_max()
    s = live.status()
    assert s["hfr_best"] is None
    assert s["hfr_history"] == []


def test_no_hfr_outside_focus_mode() -> None:
    live = LiveState()
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    live.publish(_frame(-20.0), img, 1.0, FocusHfr(Hfr(2.0, 10), None))
    assert live.status()["hfr"] is None

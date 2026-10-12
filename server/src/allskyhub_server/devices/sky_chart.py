"""Small SVG line charts for the web UI (no JavaScript): a night's sky measurements and
the camera's own state over time (exposure, gain, temperatures, disk).

Only numbers go into the SVG; missing values (``None``) become gaps, never zeros.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from markupsafe import Markup

from allskyhub_server.models import StatusSample

WIDTH, HEIGHT, PAD = 600, 120, 4


@dataclass(frozen=True)
class Series:
    title: str
    unit: str
    value: Callable[[Any], float | None]
    low: float
    high: float
    # Higher is better (darker sky) for SQM; the chart shows it upward either way.
    css: str
    fmt: str = "{:.0f}"


SERIES = (
    Series("Bewölkung", "%", lambda s: None if s.cloud_cover is None else s.cloud_cover * 100,
           0, 100, "chart-clouds"),
    Series("Himmelshelligkeit", "mag/″²", lambda s: s.sqm_mag, 16, 22, "chart-sqm", "{:.2f}"),
    Series("Sterne", "", lambda s: None if s.stars is None else float(s.stars), 0, 0,
           "chart-stars"),
)  # fmt: skip


def _exposure(s: StatusSample) -> float | None:
    return None if s.exposure_us is None else s.exposure_us / 1e6


STATUS_SERIES = (
    Series("Belichtung", "s", _exposure, 0, 0, "chart-exposure", "{:.3g}"),
    Series("Gain", "", lambda s: s.gain, 0, 0, "chart-gain"),
    Series("Sensor", "°C", lambda s: s.sensor_temp_c, 0, 40, "chart-temp", "{:.1f}"),
    Series("Prozessor", "°C", lambda s: s.cpu_temp_c, 20, 80, "chart-cpu", "{:.0f}"),
    Series("Speicher frei", "%", lambda s: s.disk_free_pct, 0, 100, "chart-disk"),
)


@dataclass(frozen=True)
class Chart:
    title: str
    unit: str
    svg: Markup
    latest: str | None
    low: str
    high: str


def _segments(
    points: Sequence[tuple[dt.datetime, float | None]],
) -> list[list[tuple[dt.datetime, float]]]:
    segments: list[list[tuple[dt.datetime, float]]] = [[]]
    for at, value in points:
        if value is None:
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append((at, value))
    return [seg for seg in segments if seg]


def charts(samples: Sequence[Any], series_list: Sequence[Series] = SERIES) -> list[Chart]:
    """One chart per series that has at least one value; ``samples`` have an ``at``."""
    if not samples:
        return []
    start, end = samples[0].at, samples[-1].at
    span = max((end - start).total_seconds(), 1.0)
    result: list[Chart] = []
    for series in series_list:
        points = [(s.at, series.value(s)) for s in samples]
        values = [v for _, v in points if v is not None]
        if not values:
            continue
        low, high = series.low, series.high
        if high <= low:  # open scale (stars): from 0 to the night's maximum
            low, high = 0.0, max(max(values), 1.0)
        low, high = min(low, min(values)), max(high, max(values))

        def x(at: dt.datetime) -> float:
            return PAD + (WIDTH - 2 * PAD) * (at - start).total_seconds() / span

        def y(value: float, lo: float = low, hi: float = high) -> float:
            return HEIGHT - PAD - (HEIGHT - 2 * PAD) * (value - lo) / (hi - lo)

        lines = "".join(
            f'<polyline points="{" ".join(f"{x(at):.1f},{y(v):.1f}" for at, v in seg)}"/>'
            if len(seg) > 1
            else f'<circle cx="{x(seg[0][0]):.1f}" cy="{y(seg[0][1]):.1f}" r="2"/>'
            for seg in _segments(points)
        )
        # Only constant titles and computed numbers go in: no user input, safe to mark up.
        svg = Markup(  # noqa: S704
            f'<svg class="sky-chart {series.css}" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'preserveAspectRatio="none" role="img" aria-label="{series.title}">{lines}</svg>'
        )
        result.append(
            Chart(
                title=series.title,
                unit=series.unit,
                svg=svg,
                latest=series.fmt.format(values[-1]),
                low=series.fmt.format(low),
                high=series.fmt.format(high),
            )
        )
    return result

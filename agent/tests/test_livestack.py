"""Live stacking (SPEC §4.12)."""

from datetime import UTC, datetime, timedelta

import numpy as np

from allskyhub_agent.adapters.camera import Image
from allskyhub_agent.process.livestack import LiveStack

T0 = datetime(2026, 11, 15, 16, 30, tzinfo=UTC)


def _noisy(seed: int, shape: tuple[int, int, int] = (40, 60, 3)) -> Image:
    rng = np.random.default_rng(seed)
    noise = np.minimum(np.maximum(rng.normal(100, 20, shape), 0), 255)
    return noise.astype(np.uint8)


def test_short_exposures_are_averaged_within_15_s() -> None:
    stack = LiveStack(width=1280)
    first = stack.add(_noisy(0), T0, 1_000_000)
    assert stack.size == 1
    assert bool((first == _noisy(0)).all())
    shown = first
    for i in range(1, 5):  # five 1 s frames back to back, like 3W at dusk
        shown = stack.add(_noisy(i), T0 + timedelta(seconds=i), 1_000_000)
    assert stack.size == 5
    assert float(np.std(shown.astype(float))) < 0.6 * float(np.std(first.astype(float)))


def test_at_most_eight_frames() -> None:
    stack = LiveStack(width=1280)
    for i in range(12):
        stack.add(_noisy(i), T0 + timedelta(seconds=i), 100_000)
    assert stack.size == 8


def test_long_exposures_and_frames_far_apart_stay_single() -> None:
    long = LiveStack(width=1280)
    long.add(_noisy(0), T0, 20_000_000)
    shown = long.add(_noisy(1), T0 + timedelta(seconds=20), 20_000_000)
    assert long.size == 1
    assert bool((shown == _noisy(1)).all())
    apart = LiveStack(width=1280)  # the day delay: 30 s between short frames
    apart.add(_noisy(0), T0, 1_000)
    apart.add(_noisy(1), T0 + timedelta(seconds=30), 1_000)
    assert apart.size == 1


def test_frames_are_kept_at_the_live_width_and_a_new_size_starts_anew() -> None:
    stack = LiveStack(width=30)
    shown = stack.add(_noisy(0), T0, 1_000)
    assert shown.shape == (20, 30, 3)
    stack.add(_noisy(1), T0 + timedelta(seconds=1), 1_000)
    assert stack.size == 2
    stack.add(_noisy(2, (40, 40, 3)), T0 + timedelta(seconds=2), 1_000)
    assert stack.size == 1

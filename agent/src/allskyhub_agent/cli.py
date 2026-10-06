"""Command line: `allskyhub-agent --sim run --frames 20 --data /tmp/ash`."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from allskyhub_agent import __version__
from allskyhub_agent.adapters.camera import Camera
from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.core.clock import Clock, SimClock, SystemClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, LoopConfig, Runner
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import FrameInfo


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="allskyhub-agent")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--sim", action="store_true", help="simulated camera and clock")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--tz", default="Europe/Vienna", help="local time zone (night folders)")
    p.add_argument("--profile", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="capture frames")
    run.add_argument("--frames", type=int, default=None, help="stop after N frames")
    run.add_argument("--data", type=Path, required=True, help="data directory")
    run.add_argument("--start", default=None, help="simulation start time, ISO 8601 with zone")
    run.add_argument("--day-delay", type=float, default=30.0)
    run.add_argument("--night-delay", type=float, default=0.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.sim:
        print("Real cameras arrive in M1; use --sim for now.", file=sys.stderr)
        return 2

    tz = ZoneInfo(args.tz)
    loc = Location(args.lat, args.lon)
    profile = get_profile(args.profile or "sim")

    clock: Clock
    if args.start:
        clock = SimClock(datetime.fromisoformat(args.start))
    else:
        clock = SimClock(SystemClock().now())

    def radiance() -> float:
        return sky_radiance(sun_elevation(clock.now(), loc.lat, loc.lon))

    camera: Camera = SimCamera(radiance, clock=clock)
    runner = Runner(
        camera=camera,
        auto_exposure=AutoExposure(profile.exposure),
        store=ImageStore(args.data, tz),
        clock=clock,
        location=loc,
        profile=profile.id,
        loop=LoopConfig(args.day_delay, args.night_delay),
        mask_radius_frac=profile.image_circle_frac,
    )

    def show(f: FrameInfo) -> None:
        local = f.captured_at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{local}  {f.mode.value:5}  sun {f.sun_elevation:6.1f}°  "
            f"exp {f.exposure_us / 1000:10.3f} ms  gain {f.gain:5.1f}  mean {f.mean:.3f}  {f.name}"
        )

    try:
        runner.run(args.frames, on_frame=show)
    except KeyboardInterrupt:
        pass
    finally:
        camera.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

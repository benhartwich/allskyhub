"""Command line.

allskyhub-agent --sim --lat 48.14 --lon 14.39 run --frames 20 --data /tmp/ash
allskyhub-agent --lat 48.14 --lon 14.39 --asi-sdk /path/libASICamera2.so run --data DIR
allskyhub-agent --lat 0 --lon 0 --asi-sdk /path/libASICamera2.so probe
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from allskyhub_agent import __version__
from allskyhub_agent.adapters.asi_sdk import AsiError, AsiSdk
from allskyhub_agent.adapters.camera import Camera
from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.adapters.zwo import CameraError, ZwoCamera
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
    p.add_argument(
        "--profile", default=None, help="hardware profile (default: sim or zwo-asi678mc)"
    )
    p.add_argument(
        "--asi-sdk",
        type=Path,
        default=Path("/usr/local/lib/libASICamera2.so"),
        help="path to libASICamera2.so for ZWO cameras",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="list connected ZWO cameras without opening them")
    run = sub.add_parser("run", help="capture frames")
    run.add_argument("--frames", type=int, default=None, help="stop after N frames")
    run.add_argument("--data", type=Path, required=True, help="data directory")
    run.add_argument("--start", default=None, help="simulation start time, ISO 8601 with zone")
    run.add_argument("--day-delay", type=float, default=30.0)
    run.add_argument("--night-delay", type=float, default=0.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "probe":
        sdk = AsiSdk(args.asi_sdk)
        for i in range(sdk.num_cameras()):
            info = sdk.camera_info(i)
            print(f"{i}: {info.name}  {info.width}x{info.height}  {info.pixel_size_um} µm")
        return 0

    tz = ZoneInfo(args.tz)
    loc = Location(args.lat, args.lon)
    profile = get_profile(args.profile or ("sim" if args.sim else "zwo-asi678mc"))

    clock: Clock
    camera: Camera
    if args.sim:
        clock = SimClock(datetime.fromisoformat(args.start) if args.start else SystemClock().now())

        def radiance() -> float:
            return sky_radiance(sun_elevation(clock.now(), loc.lat, loc.lon))

        camera = SimCamera(radiance, clock=clock)
    else:
        if args.start:
            print("--start only works with --sim", file=sys.stderr)
            return 2
        if profile.camera != "zwo":
            print(f"camera type {profile.camera!r} is not supported yet", file=sys.stderr)
            return 2
        clock = SystemClock()
        try:
            camera = ZwoCamera(AsiSdk(args.asi_sdk))
        except (OSError, AsiError, CameraError) as exc:
            print(f"cannot open camera: {exc}", file=sys.stderr)
            return 1
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

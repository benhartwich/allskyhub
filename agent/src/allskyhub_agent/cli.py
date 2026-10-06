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

import httpx

from allskyhub_agent import __version__, system
from allskyhub_agent.adapters.asi_sdk import AsiError, AsiSdk
from allskyhub_agent.adapters.camera import Camera
from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.adapters.zwo import CameraError, ZwoCamera
from allskyhub_agent.core.clock import Clock, SimClock, SystemClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.hub.client import HubClient, HubConfig, HubSession
from allskyhub_agent.hub.identity import DEFAULT_KEY_PATH, DeviceIdentity
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, LoopConfig, Runner
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.web.server import WebServer
from allskyhub_protocol import FrameInfo, Status


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
    run.add_argument("--hub", default=None, help="hub URL, e.g. https://allskyhub.org (SPEC §6)")
    run.add_argument("--key", type=Path, default=DEFAULT_KEY_PATH, help="device key file")
    run.add_argument(
        "--http",
        default=None,
        metavar="HOST:PORT",
        help="serve the local web UI, e.g. 0.0.0.0:8080 (SPEC §7)",
    )
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
    live = LiveState()
    store = ImageStore(args.data, tz)
    pairing: PairingState | None = None
    hub: HubClient | None = None
    if args.hub:
        identity = DeviceIdentity.load_or_create(args.key)
        cfg = HubConfig(hub_url=args.hub)
        pairing = PairingState(identity.device_id, cfg.hub_url, profile.id, __version__)
        pairing_state = pairing

        def status() -> Status | None:
            f = live.last_frame()
            if f is None:
                return None
            return Status(
                mode=f.mode,
                exposure_us=f.exposure_us,
                gain=f.gain,
                mean=f.mean,
                sensor_temp_c=f.sensor_temp_c,
                cpu_temp_c=system.cpu_temp_c(),
                disk_free_pct=system.disk_free_pct(args.data),
                uptime_s=system.uptime_s(),
                time_trusted=system.time_trusted(),
            )

        def make_session(http: httpx.AsyncClient) -> HubSession:
            return HubSession(
                cfg, identity, pairing_state, store, live, profile.id, __version__, status, http
            )

        hub = HubClient(make_session, cfg)
        hub.start()
        print(f"device {identity.device_id}, hub {cfg.hub_url}")

    web: WebServer | None = None
    if args.http:
        host, _, port = str(args.http).rpartition(":")
        host = host or "0.0.0.0"  # noqa: S104 - the local UI is meant for the LAN (SPEC §7)
        web = WebServer(live, host, int(port), pairing)
        web.start()
        print(f"web UI on http://{host}:{web.port}/")

    runner = Runner(
        camera=camera,
        auto_exposure=AutoExposure(profile.exposure),
        store=store,
        clock=clock,
        location=loc,
        profile=profile.id,
        loop=LoopConfig(args.day_delay, args.night_delay),
        mask_radius_frac=profile.image_circle_frac,
        live=live,
        local_tz=tz,
    )

    def show(f: FrameInfo) -> None:
        if hub is not None:
            hub.notify_frame(f)
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
        if web is not None:
            web.stop()
        if hub is not None:
            hub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

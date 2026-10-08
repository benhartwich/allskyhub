"""Command line.

allskyhub-agent --sim --lat 48.14 --lon 14.39 run --frames 20 --data /tmp/ash
allskyhub-agent --lat 48.14 --lon 14.39 --asi-sdk /path/libASICamera2.so run --data DIR
allskyhub-agent --lat 0 --lon 0 --asi-sdk /path/libASICamera2.so probe
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from allskyhub_agent import __version__, system
from allskyhub_agent.adapters.asi_sdk import AsiError, AsiSdk
from allskyhub_agent.adapters.camera import Camera, CameraError
from allskyhub_agent.adapters.libcamera import LibcameraCamera, list_cameras
from allskyhub_agent.adapters.network import Network, NmcliNetwork, SimNetwork
from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.adapters.zwo import ZwoCamera
from allskyhub_agent.core.clock import Clock, SimClock, SystemClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.detect.events import DetectionWorker, EventStore
from allskyhub_agent.discovery import Announcer
from allskyhub_agent.hub.identity import DEFAULT_KEY_PATH, DeviceIdentity
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.live import LiveState
from allskyhub_agent.products.build import NightProducts, build_night, night_products
from allskyhub_agent.products.worker import ProductWorker
from allskyhub_agent.profiles import Profile, get_profile
from allskyhub_agent.runner import Location, LoopConfig, Runner
from allskyhub_agent.services import HubManager, run_announcer
from allskyhub_agent.settings import DEFAULT_PATH as SETTINGS_PATH
from allskyhub_agent.settings import AgentSettings
from allskyhub_agent.setup.controller import NetworkRequest, SetupController
from allskyhub_agent.setup.setup_file import DEFAULT_PATH as SETUP_FILE_PATH
from allskyhub_agent.setup.setup_file import apply_once as apply_setup_file
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.web.server import WebServer
from allskyhub_protocol import DeviceSettings, FrameInfo, SetSettingsArgs, Status

log = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="allskyhub-agent")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--sim", action="store_true", help="simulated camera and clock")
    p.add_argument("--lat", type=float, default=None, help="latitude (else from setup)")
    p.add_argument("--lon", type=float, default=None, help="longitude (else from setup)")
    p.add_argument("--tz", default=None, help="local time zone (night folders; else from setup)")
    p.add_argument(
        "--profile",
        default=None,
        help="zwo-asi678mc, rpi-hq, sim or auto (default: from settings, else auto)",
    )
    p.add_argument(
        "--asi-sdk",
        type=Path,
        default=Path("/usr/local/lib/libASICamera2.so"),
        help="path to libASICamera2.so for ZWO cameras",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="list connected cameras (ZWO, libcamera) without opening them")
    prod = sub.add_parser("products", help="build a night's keogram, startrails, timelapse")
    prod.add_argument("--data", type=Path, required=True, help="data directory")
    prod.add_argument("--night", required=True, help="night id, YYYYMMDD")
    run = sub.add_parser("run", help="capture frames")
    run.add_argument("--frames", type=int, default=None, help="stop after N frames")
    run.add_argument("--data", type=Path, required=True, help="data directory")
    run.add_argument("--start", default=None, help="simulation start time, ISO 8601 with zone")
    run.add_argument("--day-delay", type=float, default=None, help="seconds (else settings, 30)")
    run.add_argument("--night-delay", type=float, default=None, help="seconds (else settings, 0)")
    run.add_argument("--hub", default=None, help="hub URL, e.g. https://allskyhub.org (SPEC §6)")
    run.add_argument("--key", type=Path, default=DEFAULT_KEY_PATH, help="device key file")
    run.add_argument(
        "--network",
        choices=("none", "nmcli", "sim"),
        default="none",
        help="manage the network: setup mode, setup file, saved hub URL (SPEC §7.1-7.3)",
    )
    run.add_argument("--settings", type=Path, default=SETTINGS_PATH, help="agent settings file")
    run.add_argument("--setup-file", type=Path, default=SETUP_FILE_PATH)
    run.add_argument(
        "--http",
        default=None,
        metavar="HOST:PORT",
        help="serve the local web UI, e.g. 0.0.0.0:8080 (SPEC §7)",
    )
    return p


def open_camera(hint: str, asi_sdk: Path) -> tuple[Camera | None, Profile]:
    """Open the camera for a profile id, or find one with "auto" (ZWO first, SPEC §3).

    Returns (None, profile) if no such camera is connected.
    """
    if hint in ("auto", "zwo-asi678mc") and asi_sdk.exists():
        sdk = AsiSdk(asi_sdk)
        if sdk.num_cameras() > 0:
            return ZwoCamera(sdk), get_profile("zwo-asi678mc")
    if hint in ("auto", "rpi-hq") and list_cameras():
        return LibcameraCamera(), get_profile("rpi-hq")
    return None, get_profile("zwo-asi678mc" if hint == "auto" else hint)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "probe":
        found = 0
        if args.asi_sdk.exists():
            sdk = AsiSdk(args.asi_sdk)
            for i in range(sdk.num_cameras()):
                info = sdk.camera_info(i)
                print(f"zwo {i}: {info.name}  {info.width}x{info.height}  {info.pixel_size_um} µm")
                found += 1
        for cam in list_cameras():
            print(f"libcamera {cam.index}: {cam.sensor}  {cam.width}x{cam.height}")
            found += 1
        if not found:
            print("no camera found")
        return 0 if found else 1

    tz = ZoneInfo(args.tz or "UTC")
    if args.cmd == "products":
        result = build_night(ImageStore(args.data, tz), args.night)
        print(f"night {result.night_id}: {result.frames} night frames, built {result.built}")
        for name, why in result.skipped.items():
            print(f"  skipped {name}: {why}")
        return 0

    live = LiveState()
    store = ImageStore(args.data, tz)
    pairing: PairingState | None = None
    hub: HubManager | None = None
    setup: SetupController | None = None
    stop_bg = threading.Event()
    restart_requested = threading.Event()

    # Product mode (SPEC §7.1-7.3): the agent manages the network and keeps hub URL,
    # location, time zone and camera choice in its settings file.
    network: Network | None = None
    if args.network == "nmcli":
        network = NmcliNetwork()
    elif args.network == "sim":
        network = SimNetwork()
    settings_path: Path | None = args.settings if network is not None else None
    settings_lock = threading.Lock()

    def load_settings() -> AgentSettings:
        with settings_lock:
            return AgentSettings.load(args.settings) if settings_path else AgentSettings()

    def update_settings(**changes: Any) -> AgentSettings:
        with settings_lock:
            new = AgentSettings.load(args.settings).with_updates(**changes)
            new.save(args.settings)
            return new

    hub_url: str | None = args.hub
    if network is not None:
        applied = apply_setup_file(args.setup_file, network) if args.network == "nmcli" else None
        if applied is not None:
            update_settings(
                hub_url=applied.hub_url,
                latitude=applied.latitude,
                longitude=applied.longitude,
                timezone=applied.timezone,
                camera=applied.camera,
            )
        hub_url = hub_url or load_settings().hub_url

    profile_hint = "sim" if args.sim else (args.profile or load_settings().camera)
    # Detections (SPEC §6.4); the night folders do not depend on the time zone.
    event_store = EventStore(store)
    identity: DeviceIdentity | None = None
    profile_id = {"auto": "zwo-asi678mc"}.get(profile_hint, profile_hint)
    if hub_url:
        identity = DeviceIdentity.load_or_create(args.key)
        pairing = PairingState(identity.device_id, hub_url, profile_id, __version__)

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
                settings=device_settings() if settings_path else None,
            )

        def device_settings() -> DeviceSettings:
            cur = load_settings()
            return DeviceSettings(
                latitude=cur.latitude,
                longitude=cur.longitude,
                timezone=cur.timezone,
                camera=cur.camera,
                day_delay_s=cur.day_delay_s,
                night_delay_s=cur.night_delay_s,
            )

        def set_settings(a: SetSettingsArgs) -> None:
            """SPEC §6.5: store, then restart the capture with the new settings."""
            update_settings(
                latitude=a.latitude,
                longitude=a.longitude,
                timezone=a.timezone,
                camera=a.camera,
                day_delay_s=a.day_delay_s,
                night_delay_s=a.night_delay_s,
            )
            log.info("new settings from the hub; restarting the capture")
            # Give the ack a moment to leave, then end run(): the service manager starts
            # the agent again, which reads the new settings.
            threading.Timer(2.0, restart_requested.set).start()

        hub = HubManager(
            identity,
            pairing,
            store,
            live,
            profile_id,
            status,
            settings_path,
            events=event_store,
            on_set_settings=set_settings if settings_path else None,
        )
        hub.start(hub_url)
        print(f"device {identity.device_id}, hub {hub_url}")
        if network is not None:
            hub_manager = hub

            def joined(req: NetworkRequest) -> None:
                before = load_settings().hub_url
                after = update_settings(
                    hub_url=req.hub_url,
                    latitude=req.latitude,
                    longitude=req.longitude,
                    timezone=req.timezone,
                )
                if after.hub_url != before:
                    hub_manager.restart(after.hub_url)

            setup = SetupController(network, identity.device_id, on_joined=joined)
            threading.Thread(target=setup.run, args=(stop_bg,), name="setup", daemon=True).start()

    web: WebServer | None = None
    if args.http:
        host, _, port = str(args.http).rpartition(":")
        host = host or "0.0.0.0"  # noqa: S104 - the local UI is meant for the LAN (SPEC §7)
        web = WebServer(
            live, host, int(port), pairing, setup, settings=load_settings if settings_path else None
        )
        web.start()
        print(f"web UI on http://{host}:{web.port}/")
        if identity is not None:
            announcer = Announcer(identity.device_id, web.port)
            threading.Thread(
                target=run_announcer, args=(announcer, stop_bg), name="mdns", daemon=True
            ).start()

    def shutdown() -> None:
        stop_bg.set()
        if web is not None:
            web.stop()
        if hub is not None:
            hub.stop()

    # Location and time zone: the command line wins; in product mode they come from setup
    # (SPEC §7.1, §7.3) and capture waits until they are known (SPEC §4.2).
    loc: Location | None = None
    if args.lat is not None and args.lon is not None:
        loc = Location(args.lat, args.lon)
    try:
        while loc is None:
            cur = load_settings()
            if cur.latitude is not None and cur.longitude is not None:
                loc = Location(cur.latitude, cur.longitude)
                if args.tz is None:
                    tz = ZoneInfo(cur.timezone)
                    store = ImageStore(args.data, tz)
                break
            if settings_path is None:
                print("--lat and --lon are needed without --network", file=sys.stderr)
                shutdown()
                return 2
            log.info("waiting for the camera's location from setup")
            time.sleep(5)
    except KeyboardInterrupt:
        shutdown()
        return 0

    clock: Clock
    camera: Camera | None = None
    profile: Profile = get_profile("sim")
    location = loc
    if args.sim or profile_hint == "sim":
        # --sim on a laptop runs on simulated time (fast); the "sim" camera of a device
        # (settings, e.g. for testing an image without a camera) runs in real time.
        if args.start:
            clock = SimClock(datetime.fromisoformat(args.start))
        elif args.sim:
            clock = SimClock(SystemClock().now())
        else:
            clock = SystemClock()
        sim_clock = clock

        def radiance() -> float:
            return sky_radiance(sun_elevation(sim_clock.now(), location.lat, location.lon))

        camera = SimCamera(radiance, clock=clock)
        profile = get_profile("sim")
    else:
        if args.start:
            print("--start only works with --sim", file=sys.stderr)
            shutdown()
            return 2
        clock = SystemClock()
        try:
            while camera is None:
                camera, profile = open_camera(profile_hint, args.asi_sdk)
                if camera is None:
                    if profile_hint != "auto":
                        print(f"camera {profile_hint!r} not found", file=sys.stderr)
                        shutdown()
                        return 1
                    log.warning("no camera found; looking again in 30 s")
                    time.sleep(30)
        except KeyboardInterrupt:
            shutdown()
            return 0
        except (OSError, AsiError, CameraError) as exc:
            print(f"cannot open camera: {exc}", file=sys.stderr)
            shutdown()
            return 1

    detect = DetectionWorker(
        store,
        on_event=hub.notify_event if hub is not None else None,
        mask_radius_frac=profile.image_circle_frac,
        events=event_store,
    )
    detect.start()

    runner = Runner(
        camera=camera,
        auto_exposure=AutoExposure(profile.exposure),
        store=store,
        clock=clock,
        location=loc,
        profile=profile.id,
        loop=LoopConfig(
            args.day_delay if args.day_delay is not None else load_settings().day_delay_s,
            args.night_delay if args.night_delay is not None else load_settings().night_delay_s,
        ),
        mask_radius_frac=profile.image_circle_frac,
        live=live,
        local_tz=tz,
        analyzers=[detect.on_frame],
    )

    def products_done(result: NightProducts) -> None:
        if hub is not None:
            announced = night_products(store, result.night_id)
            if announced is not None:
                hub.notify_products(announced)

    products = ProductWorker(store, on_done=products_done)
    products.start()

    def show(f: FrameInfo) -> None:
        products.on_frame(f)
        if hub is not None:
            hub.notify_frame(f)
        local = f.captured_at.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{local}  {f.mode.value:5}  sun {f.sun_elevation:6.1f}°  "
            f"exp {f.exposure_us / 1000:10.3f} ms  gain {f.gain:5.1f}  mean {f.mean:.3f}  {f.name}"
        )

    def watch_restart() -> None:
        restart_requested.wait()
        runner.stop()

    threading.Thread(target=watch_restart, name="restart", daemon=True).start()
    try:
        runner.run(args.frames, on_frame=show)
    except KeyboardInterrupt:
        pass
    finally:
        camera.close()
        products.stop()
        detect.stop()
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

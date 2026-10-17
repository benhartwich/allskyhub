"""Command line.

allskyhub-agent --sim --lat 50.04 --lon 10.39 run --frames 20 --data /tmp/ash
allskyhub-agent --lat 50.04 --lon 10.39 --asi-sdk /path/libASICamera2.so run --data DIR
allskyhub-agent --lat 0 --lon 0 --asi-sdk /path/libASICamera2.so probe
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx

from allskyhub_agent import __version__, settings_schema, system
from allskyhub_agent.adapters import netinfo
from allskyhub_agent.adapters.asi_sdk import AsiError, AsiSdk
from allskyhub_agent.adapters.camera import Camera, CameraError
from allskyhub_agent.adapters.libcamera import LibcameraCamera, list_cameras
from allskyhub_agent.adapters.network import Network, NmcliNetwork, SimNetwork
from allskyhub_agent.adapters.sim import SimCamera, sky_radiance
from allskyhub_agent.adapters.zwo import ZwoCamera, ZwoSettings
from allskyhub_agent.adsb import AdsbReader
from allskyhub_agent.alpaca.nina import NinaRelay
from allskyhub_agent.alpaca.safety import SafetyConfig, SafetyMonitor
from allskyhub_agent.alpaca.server import Alpaca, AlpacaServer, PortInUse
from allskyhub_agent.calib.orientation import Orienter
from allskyhub_agent.core.clock import Clock, SimClock, SystemClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.core.quality import FrameQuality
from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.detect import tuning
from allskyhub_agent.detect.cloudmap import cloud_map
from allskyhub_agent.detect.events import DetectionWorker, EventStore
from allskyhub_agent.detect.labels import Labels
from allskyhub_agent.detect.nlc import NlcDetector
from allskyhub_agent.detect.sky import Mask, Points, SkyConfig, SkyMeter
from allskyhub_agent.detect.trail import label_for as trail_label
from allskyhub_agent.discovery import Announcer, host_name, local_ipv4
from allskyhub_agent.hub.identity import DEFAULT_KEY_PATH, DeviceIdentity
from allskyhub_agent.hub.pairing import PairingState
from allskyhub_agent.hub.public import PublicPage
from allskyhub_agent.importer.allsky import AllskyImporter, AllskySettings
from allskyhub_agent.importer.backlog import BACKLOG_NAME, Backlog
from allskyhub_agent.importer.common import ImportReport
from allskyhub_agent.importer.indi_allsky import DEFAULT_DB as INDI_DB
from allskyhub_agent.importer.indi_allsky import IndiAllskyImporter, IndiAllskySettings
from allskyhub_agent.importer.indi_allsky import cameras as indi_cameras
from allskyhub_agent.lightning_sensor import (
    LightningSensor,
    LightningSensorConfig,
    StormCheck,
    Strike,
)
from allskyhub_agent.live import LiveState
from allskyhub_agent.mqtt import MqttConfig, MqttPublisher
from allskyhub_agent.process.hotpixels import HotPixels
from allskyhub_agent.process.skymask import SkyMask, parse_edits
from allskyhub_agent.products.build import (
    NightProducts,
    ProductConfig,
    build_night,
    night_products,
)
from allskyhub_agent.products.day import MINI, DayConfig, MiniTimelapse
from allskyhub_agent.products.worker import ProductWorker
from allskyhub_agent.profiles import Profile, get_profile, with_night_limits
from allskyhub_agent.runner import Location, LoopConfig, Runner
from allskyhub_agent.services import HubManager, run_announcer
from allskyhub_agent.settings import DEFAULT_PATH as SETTINGS_PATH
from allskyhub_agent.settings import TIMELAPSE_CRF, AgentSettings
from allskyhub_agent.settings_schema import build_schema
from allskyhub_agent.setup.controller import (
    SETUP_KEY_NAME,
    NetworkRequest,
    SetupController,
    read_setup_key,
)
from allskyhub_agent.setup.setup_file import DEFAULT_PATH as SETUP_FILE_PATH
from allskyhub_agent.setup.setup_file import apply_once as apply_setup_file
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.update.installer import Paths, Updater, read_request
from allskyhub_agent.update.reboot import STATE_NAME as SECURITY_REBOOT_NAME
from allskyhub_agent.update.reboot import SecurityReboot
from allskyhub_agent.update.status import STATE_FILE, update_status
from allskyhub_agent.watchdog import (
    Heartbeat,
    WatchState,
    decide,
    usb_camera_devices,
    usb_reset,
)
from allskyhub_agent.weather import WeatherConfig, WeatherStation
from allskyhub_agent.web.archive import LocalArchive
from allskyhub_agent.web.auth import LocalAuth
from allskyhub_agent.web.calibration import LocalCalibration
from allskyhub_agent.web.check import CheckFacts, run_checks
from allskyhub_agent.web.overlay import OverlayLayout
from allskyhub_agent.web.server import SettingsForm, SystemPage, TlsSetup, UpdatePage, WebServer
from allskyhub_agent.web.support import RecentLog, Support
from allskyhub_agent.web.tls import Certificates
from allskyhub_protocol import (
    AdsbStatus,
    CheckItem,
    DeviceSettings,
    Event,
    EventKind,
    EventLabel,
    FrameInfo,
    Mode,
    Observatory,
    SetSettingsArgs,
    SettingSpec,
    Status,
    UpdateState,
)
from allskyhub_sky.astro import moon_altitude, moon_illumination

log = logging.getLogger(__name__)

# Written by the agent, read by the root updater (roadmap L14).
UPDATE_REQUEST = Path("/var/lib/allskyhub-agent/update-request")


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
    imp = sub.add_parser(
        "import-allsky", help="take over the pictures of an existing Allsky (roadmap L18)"
    )
    imp.add_argument("--from", dest="source", type=Path, required=True, help="Allsky's folder")
    imp.add_argument("--data", type=Path, required=True, help="the agent's data directory")
    imp.add_argument("--settings", type=Path, default=None, help="the agent's settings file")
    imp.add_argument("--nights", default=None, help="only these Allsky folders, YYYYMMDD,...")
    imp.add_argument("--dry-run", action="store_true", help="only show what would come over")
    imp.add_argument("--copy", action="store_true", help="copy instead of hard links")
    ind = sub.add_parser(
        "import-indi-allsky", help="take over the pictures of an existing indi-allsky (L18)"
    )
    ind.add_argument("--db", type=Path, default=INDI_DB, help=f"its database (default {INDI_DB})")
    ind.add_argument("--images", type=Path, default=None,
                     help="its image folder (default: from its configuration)")  # fmt: skip
    ind.add_argument("--camera", type=int, default=None, help="its camera id, if it has several")
    ind.add_argument("--data", type=Path, required=True, help="the agent's data directory")
    ind.add_argument("--settings", type=Path, default=None, help="the agent's settings file")
    ind.add_argument("--nights", default=None, help="only these nights, YYYYMMDD,...")
    ind.add_argument("--dry-run", action="store_true", help="only show what would come over")
    ind.add_argument("--copy", action="store_true", help="copy instead of hard links")
    upd = sub.add_parser("update", help="install a newer signed agent release (root, SPEC §8)")
    upd.add_argument("--manifest-url", default=os.environ.get("ALLSKYHUB_UPDATE_MANIFEST_URL",
                                                              DEFAULT_MANIFEST_URL))  # fmt: skip
    upd.add_argument("--install-dir", type=Path, default=Path("/opt/allskyhub-agent"))
    upd.add_argument("--keys", type=Path, default=Path("/etc/allskyhub-agent/update-keys"))
    upd.add_argument("--state", type=Path, default=Path("/var/lib/allskyhub-updater/state.json"))
    upd.add_argument("--work", type=Path, default=Path("/var/cache/allskyhub-updater"))
    upd.add_argument("--agent-status", default="http://127.0.0.1:8080/api/health")
    upd.add_argument("--request", type=Path, default=UPDATE_REQUEST,
                     help="request from the local UI: now or rollback (roadmap L14)")  # fmt: skip
    wd = sub.add_parser("watchdog", help="one watchdog check (root, SPEC §8.1)")
    wd.add_argument("--state", type=Path, default=Path("/var/lib/allskyhub-watchdog/state.json"))
    wd.add_argument("--agent-health", default="http://127.0.0.1:8080/api/health")
    wd.add_argument("--updater-state", type=Path, default=STATE_FILE)
    wd.add_argument("--dry-run", action="store_true", help="only print what it would do")
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
    run.add_argument(
        "--settings",
        type=Path,
        default=None,
        help=f"agent settings file (always used in product mode, default {SETTINGS_PATH})",
    )
    run.add_argument("--setup-file", type=Path, default=SETUP_FILE_PATH)
    run.add_argument(
        "--http",
        default=None,
        metavar="HOST:PORT",
        help="serve the local web UI, e.g. 0.0.0.0:8080 (SPEC §7)",
    )
    return p


DEFAULT_MANIFEST_URL = (
    "https://github.com/benhartwich/allskyhub/releases/download/channel-stable/manifest.json"
)


def run_import(args: argparse.Namespace) -> int:
    """Roadmap L18: Allsky's nights, products and meteors into the agent's storage."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    current = AgentSettings.load(args.settings) if args.settings else AgentSettings()
    tz = ZoneInfo(args.tz or current.timezone)
    data: Path = args.data
    st = data.stat()
    owner = (st.st_uid, st.st_gid)
    store = ImageStore(data, tz)
    allsky = AllskySettings.load(args.source)
    camera = current.camera if current.camera != "auto" else None
    profile = args.profile or camera or allsky.camera or "zwo-asi678mc"
    importer = AllskyImporter(
        args.source, store, EventStore(store), tz, profile, copy=args.copy, owner=owner
    )
    print(f"Allsky in {args.source}: {allsky.location or 'no place'}, {allsky.camera_name}, "
          f"{allsky.latitude}, {allsky.longitude}")  # fmt: skip
    only = set(args.nights.split(",")) if args.nights else None
    if not importer.folders(only):
        print("no Allsky night folders found")
        return 1
    r = importer.run(only, dry=args.dry_run)
    _report_import(r, args.dry_run)
    if args.dry_run:
        return 0
    _finish_import(args, current, store, r, (allsky.latitude, allsky.longitude),
                   allsky.camera, allsky.days_to_keep, owner)  # fmt: skip
    return 0


def run_import_indi(args: argparse.Namespace) -> int:
    """Roadmap L18: indi-allsky's nights and products into the agent's storage."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    current = AgentSettings.load(args.settings) if args.settings else AgentSettings()
    tz = ZoneInfo(args.tz or current.timezone)
    data: Path = args.data
    st = data.stat()
    owner = (st.st_uid, st.st_gid)
    store = ImageStore(data, tz)
    db: Path = args.db
    if not db.is_file():
        print(f"no indi-allsky database at {db}")
        return 1
    found = indi_cameras(db)
    for cam in found:
        print(f"camera {cam.id}: {cam.name or '?'}, {cam.images} images")
    cam_id: int | None = args.camera
    if cam_id is None and len([c for c in found if c.images]) > 1:
        print("several cameras have images: choose one with --camera")
        return 1
    if cam_id is not None and cam_id not in {c.id for c in found}:
        print(f"no camera {cam_id} in {db}")
        return 1
    indi = IndiAllskySettings.load(db, cam_id if cam_id is not None else
                                   (found[0].id if found else None))  # fmt: skip
    camera = current.camera if current.camera != "auto" else None
    profile = args.profile or camera or indi.camera or "zwo-asi678mc"
    importer = IndiAllskyImporter(db, store, tz, profile, camera_id=cam_id,
                                  image_folder=args.images, copy=args.copy,
                                  owner=owner)  # fmt: skip
    print(
        f"indi-allsky in {db}: {indi.location or 'no place'}, {indi.camera_name}, "
        f"{indi.latitude}, {indi.longitude}, images in {args.images or indi.image_folder}"
    )
    only = set(args.nights.split(",")) if args.nights else None
    if not importer.nights(only):
        print("no indi-allsky nights found")
        return 1
    r = importer.run(only, dry=args.dry_run)
    _report_import(r, args.dry_run)
    if args.dry_run:
        return 0
    _finish_import(args, current, store, r, (indi.latitude, indi.longitude), indi.camera,
                   indi.days_to_keep, owner)  # fmt: skip
    return 0


def _report_import(r: ImportReport, dry_run: bool) -> None:
    verb = "would take over" if dry_run else "took over"
    print(
        f"{verb}: {r.images} images ({r.images_skipped} already there), {r.products} products, "
        f"{r.meteors} meteors, {r.lightning} lightning, {r.episodes} aurora/NLC, "
        f"{r.sky} sky samples, {len(r.nights)} nights, {r.bytes / 1e9:.1f} GB"
        + ("" if dry_run else f"; {r.linked} hard links, {r.copied} copies")
    )
    for reason, n in sorted(r.unsupported.items()):
        print(f"left out: {n} x {reason}")


def _finish_import(
    args: argparse.Namespace,
    current: AgentSettings,
    store: ImageStore,
    r: ImportReport,
    location: tuple[float | None, float | None],
    camera: str | None,
    days_to_keep: int | None,
    owner: tuple[int, int],
) -> None:
    """The location if the agent has none, retention, and the nights for the hub."""
    lat, lon = location
    tz = store.local_tz
    if args.settings and not current.has_location and lat is not None and lon is not None:
        current = current.with_updates(latitude=lat, longitude=lon, camera=camera)
        current.save(args.settings)
        print(f"location {lat}, {lon} saved in {args.settings}")
    # Retention (SPEC §4.5) must not delete what was just taken over: at least as long as
    # the other program kept its nights, and long enough for the oldest imported one.
    nights = store.nights()
    if args.settings and nights:
        oldest = datetime.strptime(nights[-1], "%Y%m%d").replace(tzinfo=tz)
        age = (datetime.now(tz) - oldest).days + 1
        keep = min(3650, max(current.keep_days, days_to_keep or 0, age))
        if keep > current.keep_days:
            current.with_values({"keep_days": keep}).save(args.settings)
            print(f"keeping nights for {keep} days now (was {current.keep_days}); "
                  "restart the agent to apply it")  # fmt: skip
    # SPEC §6.7: the running agent announces these nights to the hub, one per second.
    Backlog(args.data / BACKLOG_NAME).add(sorted(r.nights), owner)


INSTALL_DIR = Path("/opt/allskyhub-agent")
UPDATER_PATH_UNIT = Path("/etc/systemd/system/allskyhub-updater.path")


def update_facts(
    install_dir: Path = INSTALL_DIR, path_unit: Path = UPDATER_PATH_UNIT
) -> dict[str, Any]:
    """Roadmap L14: the running version, the installed releases and the updater's state."""
    releases = install_dir / "releases"
    try:
        current: str | None = (install_dir / "current").readlink().name
    except OSError:
        current = None
    try:
        names = sorted(d.name for d in releases.iterdir() if d.is_dir())
    except OSError:
        names = []
    state = update_status()
    return {
        "version": __version__,
        "release": current,
        "releases": names,
        "state": state.model_dump(mode="json") if state is not None else None,
        "updater": _exists(path_unit),
        "pending": _exists(UPDATE_REQUEST),
    }


def forget_pixel_maps(calibration: Path) -> None:
    """SPEC §4.1: after a change of flip or rotation the hot pixels, the sky mask (with the
    drawn areas, which are fractions of the old picture) and the orientation are of the old
    picture; they are learned again."""
    for name in ("hotpixels.npz", "skymask.png", "skymask_edits.json", "orientation.json"):
        try:
            (calibration / name).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("cannot remove %s: %s", name, exc)
    log.info("flip or rotation changed: hot pixels, sky mask and orientation are learned again")


def _exists(path: Path) -> bool:
    """Path.exists() raises on a directory this user may not read (other installations)."""
    try:
        return path.exists()
    except OSError:
        return False


def request_update(word: str, path_unit: Path = UPDATER_PATH_UNIT) -> bool:
    """Leave the request for allskyhub-updater.path; False without an updater."""
    if not _exists(path_unit):
        return False
    try:
        tmp = UPDATE_REQUEST.with_suffix(".tmp")
        tmp.write_text(word + "\n", encoding="utf-8")
        tmp.replace(UPDATE_REQUEST)
    except OSError:
        log.exception("cannot leave the update request")
        return False
    log.info("update %s requested in the local UI", word)
    return True


def wifi_country(settings_file: Path) -> str | None:
    """The radio country the image's root helper set (SPEC §7.1), for the Wi-Fi form."""
    try:
        code = (settings_file.parent / "wifi-country").read_text().strip().upper()
    except OSError:
        return None
    return code if len(code) == 2 and code.isalpha() else None


def system_facts(data: Path) -> dict[str, Any]:
    """The local UI's system page (roadmap L12)."""
    return {
        "model": system.model(),
        "os": system.os_name(),
        "uptime_s": system.uptime_s(),
        "load": system.load_avg(),
        "cpus": os.cpu_count(),
        "cpu_temp_c": system.cpu_temp_c(),
        "memory": system.memory(),
        "disk": system.disk(data),
        "power": system.power_flags(),
        "time_trusted": system.time_trusted(),
    }


def system_action(action: str, restart: threading.Event) -> str | None:
    """Roadmap L12. Reboot and shutdown go through logind; the image allows them to the
    agent's user by polkit (image/files/etc/polkit-1), elsewhere they are refused."""
    if action == "restart":
        log.info("restart requested in the local UI")
        threading.Timer(1.0, restart.set).start()  # let the answer leave first
        return None
    verb = {"reboot": "reboot", "shutdown": "poweroff"}[action]
    try:
        res = subprocess.run(  # noqa: S603 - fixed arguments
            ["systemctl", "--no-ask-password", verb],  # noqa: S607 - systemctl from PATH
            capture_output=True, text=True, timeout=20, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return "Das System hat den Befehl nicht angenommen."
    if res.returncode != 0:
        log.warning("%s refused: %s", verb, res.stderr.strip()[-200:])
        return (
            "Auf dieser Installation darf die Kamera das System nicht neu starten oder ausschalten."
        )
    log.info("%s requested in the local UI", verb)
    return None


def network_ok() -> bool:
    """The local network works: the default gateway answers a ping, or DNS resolves.
    Deliberately not the hub (SPEC §8.1)."""
    gw = netinfo.default_gateway()
    if gw is not None:
        res = subprocess.run(  # noqa: S603 - fixed arguments
            ["ping", "-c", "1", "-W", "3", gw],  # noqa: S607 - ping from PATH
            capture_output=True, check=False, timeout=10,
        )  # fmt: skip
        if res.returncode == 0:
            return True
    try:
        socket.getaddrinfo("pool.ntp.org", 123)
    except OSError:
        return False
    return True


def restart_agent() -> None:
    subprocess.run(
        ["systemctl", "restart", "allskyhub-agent.service"],  # noqa: S607 - systemctl from PATH
        check=False, timeout=120,
    )  # fmt: skip


def run_watchdog(args: argparse.Namespace) -> int:
    """One check from allskyhub-watchdog.timer, as root (SPEC §8.1, roadmap L17)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        r = httpx.get(args.agent_health, timeout=10)
        health: object = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        health = None
    frames = cast(dict[str, Any], health).get("frames") if isinstance(health, dict) else None
    upd = update_status(args.updater_state)
    busy = upd is not None and upd.state.value in ("installing", "downloading")
    st = WatchState.load(args.state)
    paused = isinstance(health, dict) and cast(dict[str, Any], health).get("paused") is True
    action = decide(st, datetime.now(UTC), frames if isinstance(frames, int) else None,
                    network_ok(), busy, paused)  # fmt: skip
    if action is not None:
        log.warning("watchdog: %s (frames %s since %s, network down since %s)", action,
                    st.frames, st.frames_changed, st.net_down_since)  # fmt: skip
    if args.dry_run:
        print(action or "nothing to do")
        return 0
    st.save(args.state)
    if action == "restart":
        restart_agent()
    elif action == "usb_reset":
        n = usb_reset(usb_camera_devices())
        log.warning("watchdog: reset %d USB camera(s)", n)
        restart_agent()
    elif action == "reboot":
        subprocess.run(["systemctl", "reboot"], check=False, timeout=60)  # noqa: S607 - systemctl from PATH
    return 0


def run_update(args: argparse.Namespace) -> int:
    """One updater run (SPEC §8), started by allskyhub-updater.timer as root."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    def status() -> dict[str, Any] | None:
        try:
            r = httpx.get(args.agent_status, timeout=5)
            data: object = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return cast(dict[str, Any], data) if isinstance(data, dict) else None

    def run_cmd(cmd: list[str]) -> int:
        return subprocess.run(cmd, check=False, timeout=120).returncode  # noqa: S603

    with httpx.Client(timeout=30) as http:
        Updater(
            paths=Paths(args.install_dir, args.work, args.state, args.keys),
            manifest_url=args.manifest_url,
            http=http,
            status=status,
            run=run_cmd,
            sleep=time.sleep,
            monotonic=time.monotonic,
            fallback_version=__version__,
        ).run_once(read_request(args.request))
    return 0


def open_camera(
    hint: str, asi_sdk: Path, zwo: ZwoSettings | None = None
) -> tuple[Camera | None, Profile]:
    """Open the camera for a profile id, or find one with "auto" (ZWO first, SPEC §3).

    Returns (None, profile) if no such camera is connected.
    """
    if hint in ("auto", "zwo-asi678mc") and asi_sdk.exists():
        sdk = AsiSdk(asi_sdk)
        if sdk.num_cameras() > 0:
            return ZwoCamera(sdk, settings=zwo), get_profile("zwo-asi678mc")
    if hint in ("auto", "rpi-hq") and list_cameras():
        return LibcameraCamera(), get_profile("rpi-hq")
    return None, get_profile("zwo-asi678mc" if hint == "auto" else hint)


def overlay_values(
    sky_meter: SkyMeter | None, loc: Location | None, weather: WeatherStation | None = None
) -> dict[str, Any]:
    """What the overlay shows besides the frame (SPEC §7 Overlay): the newest sky values,
    the moon now and the weather sensor and heater (SPEC §7.11)."""
    sky = sky_meter.latest if sky_meter is not None else None
    out: dict[str, Any] = {"sky": sky.model_dump(mode="json") if sky is not None else None}
    if weather is not None:
        env, heater = weather.env_status, weather.heater_status
        out["env"] = env.model_dump(mode="json") if env is not None else None
        out["heater"] = heater.model_dump(mode="json") if heater is not None else None
    if loc is not None:
        now = datetime.now(UTC)
        out["moon"] = {
            "altitude_deg": round(moon_altitude(now, loc.lat, loc.lon), 1),
            "illumination": round(moon_illumination(now), 2),
        }
    return out


def weather_state(
    weather: WeatherStation, lightning: LightningSensor | None = None
) -> dict[str, Any]:
    """The weather sensor's, the heater's and the lightning sensor's values for MQTT (SPEC
    §7.7, §7.11, §7.12)."""
    out: dict[str, Any] = {}
    ls = lightning.status if lightning is not None else None
    if ls is not None:
        out |= {"strikes_30min": ls.strikes_30min, "nearest_km": ls.nearest_km}
    env = weather.env_status
    if env is not None:
        out |= {
            "env_temp_c": env.temperature_c,
            "humidity_pct": env.humidity_pct,
            "dewpoint_c": env.dewpoint_c,
            "pressure_hpa": env.pressure_hpa,
        }
    heater = weather.heater_status
    if heater is not None:
        out["heater_on"] = heater.on
    return out


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

    if args.cmd == "import-allsky":
        return run_import(args)
    if args.cmd == "import-indi-allsky":
        return run_import_indi(args)
    tz = ZoneInfo(args.tz or "UTC")
    if args.cmd == "update":
        return run_update(args)
    if args.cmd == "watchdog":
        return run_watchdog(args)
    if args.cmd == "products":
        result = build_night(ImageStore(args.data, tz), args.night)
        print(f"night {result.night_id}: {result.frames} night frames, built {result.built}")
        for name, why in result.skipped.items():
            print(f"  skipped {name}: {why}")
        return 0

    # The running agent logs to the journal too; httpx would add a line per request.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    recent_log = RecentLog()  # for the local UI (roadmap L15)
    logging.getLogger().addHandler(recent_log)

    live = LiveState(Path(args.data) / "capture-paused")
    store = ImageStore(args.data, tz)
    pairing: PairingState | None = None
    local_auth: LocalAuth | None = None
    hub: HubManager | None = None
    setup: SetupController | None = None
    stop_bg = threading.Event()
    restart_requested = threading.Event()

    # Product mode (SPEC §7.1-7.3): the agent manages the network and keeps hub URL,
    # location, time zone and camera choice in its settings file.
    settings_file: Path = args.settings or SETTINGS_PATH
    network: Network | None = None
    if args.network == "nmcli":
        network = NmcliNetwork(country_file=settings_file.parent / "wifi-country")
    elif args.network == "sim":
        network = SimNetwork()
    # Product mode always keeps settings; a test install next to Allsky (--network none)
    # keeps them when --settings is given (roadmap L7).
    settings_path: Path | None = (
        settings_file if network is not None or args.settings is not None else None
    )
    settings_lock = threading.Lock()
    alpaca_error: list[str | None] = [None]  # SPEC §7.5, for status and the local UI
    observatory: list[Callable[[], Observatory | None]] = [lambda: None]  # N.I.N.A., §6.3
    adsb_status: list[Callable[[], AdsbStatus | None]] = [lambda: None]  # receiver, §7.8
    weather_box: list[WeatherStation | None] = [None]  # sensor and heater, §7.11
    lightning_box: list[LightningSensor | None] = [None]  # AS3935, §7.12
    check_box: list[CheckFacts | None] = [None]  # camera check, §7; set once all parts exist
    strike_hooks: list[Callable[[Strike], None]] = []  # safety monitor, once it exists
    reboot_required: list[Callable[[], bool]] = [lambda: False]  # system updates, §8
    # Learned at night, edited in the local UI (roadmap L8) before the camera is open.
    calibration_dir = Path(args.data) / "calibration"
    sky_mask = SkyMask(calibration_dir / "skymask.png")

    def load_settings() -> AgentSettings:
        with settings_lock:
            return AgentSettings.load(settings_file) if settings_path else AgentSettings()

    def update_settings(**changes: Any) -> AgentSettings:
        with settings_lock:
            new = AgentSettings.load(settings_file).with_updates(**changes)
            new.save(settings_file)
            return new

    store = ImageStore(args.data, tz, quality=load_settings().jpeg_quality)

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
    # SPEC §6.9: the settings form; its limits follow the profile once the camera is open.
    schema_box = [
        build_schema(get_profile({"auto": "zwo-asi678mc"}.get(profile_hint, profile_hint)))
    ]

    def current_schema() -> list[SettingSpec]:
        return schema_box[0]

    def settings_values() -> dict[str, Any]:
        return settings_schema.values(load_settings(), current_schema())

    def change_settings(changes: Mapping[str, Any], source: str) -> list[str]:
        """SPEC §6.5: check, store, then restart the capture with the new settings; the
        invalid keys if any (nothing changes then)."""
        if settings_path is None:
            return ["args"]
        bad = settings_schema.check(current_schema(), changes)
        if bad:
            return bad
        with settings_lock:
            before = AgentSettings.load(settings_file)
            after = before.with_values(changes)
            after.save(settings_file)
        if (before.image_flip, before.image_rotate) != (after.image_flip, after.image_rotate):
            forget_pixel_maps(Path(args.data) / "calibration")
        log.info("new settings from %s; restarting the capture", source)
        # Give the answer a moment to leave, then end run(): the service manager starts
        # the agent again, which reads the new settings.
        threading.Timer(2.0, restart_requested.set).start()
        return []

    # Detections (SPEC §6.4); the night folders do not depend on the time zone.
    event_store = EventStore(
        store,
        orientation=lambda: orienter.solution if orienter is not None else None,
        location=lambda: (loc.lat, loc.lon) if loc is not None else None,
        time_trusted=system.time_trusted if settings_path else lambda: True,
    )
    # SPEC §6.4 Labels: the owner's "confirmed" / "false positive", in sync with the hub.
    labels = Labels(store)

    def label_from_hub(label: EventLabel) -> bool:
        if not any(e.id == label.event_id for e in event_store.night_events(label.night_id)):
            return False
        labels.set(label)
        return True

    # Sky condition for status (SPEC §6.3); configured for the profile once it is known.
    sky_meter: SkyMeter | None = None
    # Plate-solved image orientation (SPEC §4.8), created with the detection worker.
    orienter: Orienter | None = None
    identity: DeviceIdentity | None = None
    profile_id = {"auto": "zwo-asi678mc"}.get(profile_hint, profile_hint)
    if hub_url:
        identity = DeviceIdentity.load_or_create(args.key)
        pairing = PairingState(identity.device_id, hub_url, profile_id, __version__)
        # SPEC §7.4: a paired camera's local UI needs a login.
        local_auth = LocalAuth(args.data / "local_auth.json", identity.device_id)
        pairing.watch(local_auth.mark_paired, local_auth.clear)

        def status() -> Status | None:
            f = live.last_frame()
            if f is None and not live.paused:
                return None
            # Paused since before a restart (SPEC §4.1): no frame yet, the hub must still
            # learn about the pause.
            mode = f.mode if f is not None else (
                Mode.NIGHT if loc is not None and sun_elevation(datetime.now(UTC), loc.lat,
                                                                loc.lon) < -6 else Mode.DAY
            )  # fmt: skip
            return Status(
                mode=mode,
                exposure_us=f.exposure_us if f is not None else 0,
                gain=f.gain if f is not None else 0.0,
                mean=f.mean if f is not None else 0.0,
                sensor_temp_c=f.sensor_temp_c if f is not None else None,
                cpu_temp_c=system.cpu_temp_c(),
                disk_free_pct=system.disk_free_pct(args.data),
                uptime_s=system.uptime_s(),
                time_trusted=system.time_trusted(),
                settings=device_settings() if settings_path else None,
                sky=sky_meter.latest if sky_meter is not None else None,
                orientation=orienter.latest if orienter is not None else None,
                update=update_status() if settings_path else None,
                alpaca_error=alpaca_error[0],
                capture_paused=live.paused,
                observatory=observatory[0](),
                adsb=adsb_status[0](),
                reboot_required=reboot_required[0](),
                env=weather_box[0].env_status if weather_box[0] is not None else None,
                heater=weather_box[0].heater_status if weather_box[0] is not None else None,
                lightning_sensor=lightning_box[0].status if lightning_box[0] is not None else None,
                checks=status_checks(),
            )

        def status_checks() -> list[CheckItem] | None:
            """SPEC §6.3: the camera check for the hub's setup card, without the hub's
            own light (it knows whether it is connected)."""
            facts = check_box[0]
            if facts is None:
                return None
            items = run_checks(facts, datetime.now(UTC))
            return [CheckItem.model_validate(i) for i in items if i["id"] != "hub"]

        def device_settings() -> DeviceSettings:
            return DeviceSettings.model_validate(settings_values())

        def set_settings(a: SetSettingsArgs) -> list[str]:
            return change_settings(a.model_dump(exclude_unset=True), "the hub")

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
            local_access=local_auth,
            settings_schema=current_schema if settings_path else None,
            backlog=Backlog(args.data / BACKLOG_NAME),
            night_events=event_store.night_events,
            resend=lambda: [*labels.all(), *LocalArchive(store).deleted_nights(datetime.now(UTC))],
            on_label=label_from_hub,
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

            setup = SetupController(
                network,
                identity.device_id,
                on_joined=joined,
                key=lambda: read_setup_key(Path(args.data) / SETUP_KEY_NAME),
            )
            threading.Thread(target=setup.run, args=(stop_bg,), name="setup", daemon=True).start()

    def where() -> tuple[float, float] | None:
        cur = load_settings()
        if cur.latitude is not None and cur.longitude is not None:
            return cur.latitude, cur.longitude
        return (args.lat, args.lon) if args.lat is not None and args.lon is not None else None

    calibration = LocalCalibration(sky_mask, calibration_dir / "orientation.json", where)

    # SPEC §7.11: weather sensor and dew heater (roadmap L30); off and pinless by default.
    wcfg = load_settings()
    weather = WeatherStation(
        WeatherConfig(
            sensor=wcfg.env_sensor,
            address=wcfg.env_sensor_address,
            heater=wcfg.heater,
            heater_gpio=wcfg.heater_gpio,
            heater_active_low=wcfg.heater_active_low,
            dew_margin_c=wcfg.heater_dew_margin_c,
        ),
        night=lambda: (lf := live.last_frame()) is not None and lf.mode is Mode.NIGHT,
    )
    weather.start()
    weather_box[0] = weather

    # SPEC §7.12: the lightning sensor (roadmap L31); never on the heater's pin.
    irq = wcfg.lightning_sensor_irq_gpio

    def strike_heard(strike: Strike) -> None:
        for hook in strike_hooks:
            hook(strike)

    lightning_sensor = LightningSensor(
        LightningSensorConfig(
            sensor=wcfg.lightning_sensor,
            address=wcfg.lightning_sensor_address,
            irq_gpio=None if irq is not None and irq == wcfg.heater_gpio else irq,
            indoor=wcfg.lightning_sensor_indoor,
        ),
        on_strike=strike_heard,
    )
    lightning_sensor.start()
    lightning_box[0] = lightning_sensor
    # SPEC §7 Camera check: the local UI's traffic light, also sent in status (§6.3).
    check_facts = CheckFacts(
        power=system.power_flags,
        time_trusted=system.time_trusted,
        disk_free_pct=lambda: system.disk_free_pct(Path(args.data)),
        last_frame=live.last_frame,
        paused=lambda: live.paused,
        calibration=calibration.info,
        hub=(lambda: (pairing.info().paired, pairing.info().connected))
        if pairing is not None
        else None,
        env=lambda: weather.env_status,
        heater=lambda: (wcfg.heater, wcfg.heater_gpio, weather.heater_status),
        lightning=lambda: lightning_sensor.status,
    )
    check_box[0] = check_facts

    def observatory_facts() -> dict[str, Any]:
        """SPEC §7.5 for the system page: Alpaca on/off, port or error, N.I.N.A."""
        cur = load_settings()
        obs = observatory[0]()
        return {
            "alpaca": {
                "enabled": cur.alpaca_enabled,
                "port": cur.alpaca_port,
                "error": alpaca_error[0],
            },
            "observatory": obs.model_dump(mode="json") if obs is not None else None,
        }

    def restore_backup(values: dict[str, Any], edits: dict[str, Any] | None) -> list[str]:
        """Roadmap L15: a backup's settings (only the keys this camera has) and drawn areas."""
        known = settings_schema.keys(current_schema())
        bad = [] if edits is None or parse_edits(edits) is not None else ["skymask_edits"]
        if bad:
            return bad
        if edits is not None:
            calibration.set_edits(edits)
        changes = {k: v for k, v in values.items() if k in known}
        return change_settings(changes, "a backup") if changes else []

    web: WebServer | None = None
    if args.http:
        host, _, port = str(args.http).rpartition(":")
        host = host or "0.0.0.0"  # noqa: S104 - the local UI is meant for the LAN (SPEC §7)
        # SPEC §7.6: HTTPS for the local UI (roadmap L24).
        certs = Certificates(Path(args.data) / "tls")
        https_on = settings_path is not None and load_settings().https
        tls_context: ssl.SSLContext | None = None
        if https_on:
            names = [socket.gethostname(), f"{socket.gethostname()}.local"]
            if identity is not None:
                names.append(f"{host_name(identity.device_id)}.local")
            addresses = [ipaddress.ip_address(a) for a in local_ipv4()]
            try:
                tls_context = certs.context(sorted(set(names)), addresses, datetime.now(UTC))
            except (OSError, ssl.SSLError, ValueError) as exc:
                log.warning("HTTPS not started: %s", exc)
        web = WebServer(
            live,
            host,
            int(port),
            pairing,
            setup,
            settings=load_settings if settings_path else None,
            auth=local_auth,
            time_trusted=system.time_trusted,
            settings_form=SettingsForm(current_schema, settings_values, change_settings)
            if settings_path
            else None,
            archive=LocalArchive(
                store,
                event_store,
                labels,
                on_label=lambda lab: hub.notify_message(lab) if hub is not None else None,
                on_deleted=lambda gone: hub.notify_message(gone) if hub is not None else None,
            ),
            calibration=calibration,
            public=PublicPage(hub_url, identity) if hub_url and identity is not None else None,
            system_page=SystemPage(
                lambda: (
                    system_facts(Path(args.data))
                    | observatory_facts()
                    | {"reboot_required": reboot_required[0]()}
                ),
                lambda action: system_action(action, restart_requested),
            ),
            network_info=lambda: netinfo.summary() | {"country": wifi_country(settings_file)},
            update_page=UpdatePage(update_facts, request_update),
            support=Support(
                log=recent_log,
                facts={
                    "version": lambda: __version__,
                    "update": update_facts,
                    "system": lambda: system_facts(Path(args.data)),
                    "network": netinfo.summary,
                    "status": live.status,
                    "calibration": lambda: calibration.info(),
                },
                files={
                    "orientation.json": calibration_dir / "orientation.json",
                    "skymask.png": calibration_dir / "skymask.png",
                    "skymask_edits.json": calibration_dir / "skymask_edits.json",
                    "updater-state.json": STATE_FILE,
                },
                settings=settings_values,
                mask_edits=lambda: sky_mask.edits.to_json(),
                restore=restore_backup,
            ),
            tls=TlsSetup(
                certs,
                https_on and tls_context is not None,
                on_change=lambda: threading.Timer(2.0, restart_requested.set).start(),
            ),
            tls_context=tls_context,
            overlay=OverlayLayout(Path(args.data) / "overlay.json"),
            status_extra=lambda: overlay_values(sky_meter, loc, weather),
            check=check_facts,
        )
        web.start()
        if web.tls_port is not None:
            print(f"web UI on https://{host}:{web.tls_port}/")
        print(f"web UI on http://{host}:{web.port}/")
        if identity is not None:
            announcer = Announcer(identity.device_id, web.port)
            threading.Thread(
                target=run_announcer, args=(announcer, stop_bg), name="mdns", daemon=True
            ).start()

    def shutdown() -> None:
        stop_bg.set()
        weather.stop()  # the heater off and its pin released
        lightning_sensor.stop()
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
                    store = ImageStore(args.data, tz, quality=cur.jpeg_quality)
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
                cur = load_settings()
                zwo = ZwoSettings(wb_r=cur.wb_r, wb_b=cur.wb_b)
                camera, profile = open_camera(profile_hint, args.asi_sdk, zwo)
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

    schema_box[0] = build_schema(profile)
    profile = with_night_limits(profile, load_settings())
    sky_meter = SkyMeter(SkyConfig(sqm_offset=profile.sqm_offset), profile.image_circle_frac)
    sky_mask.fallback_frac = profile.image_circle_frac
    here = loc
    orienter = Orienter(
        Path(args.data) / "calibration" / "orientation.json",
        location=lambda: (here.lat, here.lon),
        time_trusted=system.time_trusted if settings_path else lambda: True,
    )
    trusted = system.time_trusted if settings_path else lambda: True

    def map_clouds(
        frame: FrameInfo, shape: tuple[int, int], sky: Mask, xs: Points, ys: Points
    ) -> list[float | None] | None:
        """SPEC §6.3 `cloud_map`: only with an orientation and a trusted clock."""
        sol = orienter.solution
        if sol is None or not trusted():
            return None
        return cloud_map(sol, shape, sky, xs, ys, frame.captured_at, frame.exposure_us,
                         here.lat, here.lon)  # fmt: skip

    sky_meter.set_mapper(map_clouds)
    tuned = load_settings()
    # SPEC §7.5: the camera as safety monitor for observatory software (roadmap L16).
    safety = SafetyMonitor(
        SafetyConfig(tuned.safe_cloud_max / 100.0, timedelta(minutes=tuned.safe_hold_min))
    )
    # SPEC §7.12: a strike the sensor hears makes it unsafe for 30 minutes, as a flash does.
    strike_hooks.append(lambda s: safety.lightning(s.at))

    # SPEC §7.7: MQTT for home automation (roadmap L23), only publishing.
    mqtt: MqttPublisher | None = None
    if tuned.mqtt_host:
        mqtt_id = identity.device_id if identity is not None else socket.gethostname()
        mqtt = MqttPublisher(
            MqttConfig(tuned.mqtt_host, tuned.mqtt_port, tuned.mqtt_tls, tuned.mqtt_user,
                       tuned.mqtt_password, tuned.mqtt_prefix),
            mqtt_id,
            f"allskyhub {mqtt_id[:8]}",
            profile.id,
            __version__,
            weather=weather.sensor_on,
            heater=weather.heater_on,
            lightning=lightning_sensor.on,
        )  # fmt: skip
        mqtt.start()

    def mqtt_state(f: FrameInfo) -> None:
        if mqtt is None:
            return
        sky = sky_meter.latest
        cover = sky.cloud_cover if sky is not None else None
        mqtt.state(
            {
                "at": f.captured_at.isoformat(),
                "mode": f.mode.value,
                "exposure_us": f.exposure_us,
                "gain": f.gain,
                "mean": f.mean,
                "sensor_temp_c": f.sensor_temp_c,
                "cpu_temp_c": system.cpu_temp_c(),
                "cloud_cover": None if cover is None else round(cover * 100, 1),
                "sqm_mag": sky.sqm_mag if sky is not None else None,
                "stars": sky.stars if sky is not None else None,
                "safe": safety.is_safe(datetime.now(UTC)) if tuned.alpaca_enabled else None,
                **weather_state(weather, lightning_sensor),
            }
        )

    # SPEC §7.8: the owner's ADS-B receiver marks aircraft among the meteors (roadmap H3).
    adsb: AdsbReader | None = None

    def aircraft_found(event: Event, detail: str) -> None:
        label = EventLabel(
            night_id=event.night_id,
            event_id=event.id,
            label="false_positive",
            reason="aircraft",
            auto=True,
            detail=detail,
            at=datetime.now(UTC),
        )
        amended = event_store.amend(event, {"aircraft": detail.split(" ")[0]})
        stored = labels.set(label)
        if hub is not None:
            hub.notify_event(amended)
            if stored:
                hub.notify_message(label)

    if tuned.adsb_url:
        here = loc
        adsb = reader = AdsbReader(
            tuned.adsb_url,
            location=lambda: (here.lat, here.lon),
            relay=lambda a: hub.notify_message(a) if hub is not None else None,
            on_match=aircraft_found,
        )
        adsb.start()
        adsb_status[0] = lambda: reader.status

    # SPEC §7.12: the sensor confirms the camera's flashes; a storm it never heard is labelled.
    def flash_checked(event: Event, data: dict[str, Any]) -> Event:
        amended = event_store.amend(event, data)
        if hub is not None:
            hub.notify_event(amended)
        return amended

    def flash_false_alarm(event: Event) -> None:
        label = EventLabel(
            night_id=event.night_id,
            event_id=event.id,
            label="false_positive",
            reason="other",
            auto=True,
            detail="kein Blitz am Sensor",
            at=datetime.now(UTC),
        )
        if labels.set(label) and hub is not None:
            hub.notify_message(label)

    storm_check: StormCheck | None = None
    if lightning_sensor.on:
        storm_check = StormCheck(lightning_sensor.heard, flash_checked, flash_false_alarm)
        storm_check.start()

    def event_out(event: Event) -> None:
        if event.kind is EventKind.LIGHTNING:
            safety.lightning(event.start)
            if storm_check is not None:
                storm_check.add(event)
        # SPEC §6.4 Trail verdicts: a streak that is no meteor is kept with an automatic label.
        label = trail_label(event)
        if label is not None and event.id in labels.night(event.night_id):
            label = None  # a pass that grows: labelled with its first piece, or by the owner
        if label is not None:
            labels.set(label)
        if adsb is not None:
            adsb.check_later(event)
        if mqtt is not None and label is None:  # false positives count nowhere
            mqtt.event(event)
        if hub is not None:
            hub.notify_event(event)
            if label is not None:
                hub.notify_message(label)

    if tuned.alpaca_enabled:
        alpaca_id = identity.device_id if identity is not None else "local"
        try:
            alpaca_server = AlpacaServer(
                Alpaca(
                    alpaca_id,
                    __version__,
                    safety,
                    weather=(lambda: weather.env_status) if weather.sensor_on else None,
                ),
                tuned.alpaca_port,
                blocked=lambda: setup is not None and setup.active,
            )
        except PortInUse as exc:
            log.warning("ASCOM Alpaca not started: %s", exc)
            alpaca_error[0] = "port_in_use"
        else:
            alpaca_server.start()
            alpaca_error[0] = None
    if tuned.nina_url:
        nina = NinaRelay(
            tuned.nina_url,
            tz,
            safe=lambda: safety.is_safe(datetime.now(UTC)) if tuned.alpaca_enabled else None,
        )
        nina.start()
        observatory[0] = lambda: nina.latest
    detect = DetectionWorker(
        store,
        on_event=event_out,
        cfg=tuning.meteor_config(tuned.detect_meteor),
        mask_radius_frac=profile.image_circle_frac,
        events=event_store,
        lightning_cfg=tuning.lightning_config(tuned.detect_lightning),
        sky=sky_meter,
        aurora_cfg=tuning.aurora_config(tuned.detect_aurora),
        sky_mask=sky_mask,
        orienter=orienter,
        nlc=NlcDetector(
            orientation=lambda: orienter.solution,
            location=lambda: (here.lat, here.lon),
            sky_mask=sky_mask.get,
            cfg=tuning.nlc_config(tuned.detect_nlc),
        )
        if tuned.detect_nlc != "off"
        else None,
        latitude=lambda: here.lat,
        enabled=tuning.enabled(tuned),
        on_sky=safety.observe,
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
        hot_pixels=HotPixels(Path(args.data) / "calibration" / "hotpixels.npz"),
        sky_mask=sky_mask,
        quality=FrameQuality(
            tuned.bad_frames, tuned.bad_dark_pct / 100.0, tuned.bad_bright_pct / 100.0
        ),
        moon_mode=tuned.moon_mode,
        day_capture=tuned.day_capture,
        flip=tuned.image_flip,
        rotate=int(tuned.image_rotate),
    )

    def announce_products(night: str) -> None:
        if hub is not None:
            announced = night_products(store, night)
            if announced is not None:
                hub.notify_products(announced)

    def products_done(result: NightProducts) -> None:
        announce_products(result.night_id)

    night_cfg = load_settings()
    # SPEC §8.1: systemd restarts the agent if frames stop (WatchdogSec in the unit).
    Heartbeat(lambda: int(cast(int, live.status()["frames"])), paused=lambda: live.paused).start()
    products = ProductWorker(
        store,
        ProductConfig(
            timelapse_fps=night_cfg.timelapse_fps,
            timelapse_crf=TIMELAPSE_CRF[night_cfg.timelapse_quality],
            keogram=night_cfg.product_keogram,
            startrails=night_cfg.product_startrails,
            timelapse=night_cfg.product_timelapse,
        ),
        on_done=products_done,
        keep_days=night_cfg.keep_days,
        day=DayConfig(night_cfg.product_day_timelapse, night_cfg.product_day_keogram)
        if night_cfg.product_day_timelapse or night_cfg.product_day_keogram
        else None,
        local_tz=tz,
        on_day_done=announce_products,
    )
    products.start()
    # SPEC §5.2: the last hour as a small video for the live page, never archived.
    mini_path = Path(args.data) / "live" / MINI
    mini = MiniTimelapse(store, mini_path, ProductConfig(timelapse_crf=28))
    if night_cfg.product_mini_timelapse:
        mini.start()
    else:
        mini_path.unlink(missing_ok=True)

    # SPEC §8: reboot by day when system security updates need it.
    def updating() -> bool:
        st = update_status() if settings_path else None
        return st is not None and st.state in (UpdateState.DOWNLOADING, UpdateState.INSTALLING)

    here = loc
    security_reboot = SecurityReboot(
        Path(args.data) / SECURITY_REBOOT_NAME,
        tz=lambda: tz,
        sun_deg=lambda t: sun_elevation(t, here.lat, here.lon),
        busy=lambda: products.busy,
        updating=updating,
        reboot=lambda: system_action("reboot", restart_requested),
    )
    if settings_path:
        security_reboot.start()
    reboot_required[0] = lambda: security_reboot.required

    def show(f: FrameInfo) -> None:
        products.on_frame(f)
        mqtt_state(f)
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
        mini.stop()
        if mqtt is not None:
            mqtt.stop()
        detect.stop()
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Import an existing Allsky installation (roadmap L18).

Takes over what Allsky (github.com/AllskyTeam/allsky) stored, so switching to allskyhub
loses no pictures: every image of ``images/<date>/`` with its metadata, keogram, star
trails and timelapse as night products (SPEC §5.2), and the meteors of Benjamin's meteor
module as events (SPEC §6.4). Images are hard-linked when Allsky's folder is on the same
disk (no extra space), else copied. Running it again only adds what is new.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, cast

from PIL import Image as PILImage

from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.detect.events import EventStore
from allskyhub_agent.importer.common import (
    FilePlacer,
    ImportReport,
    system_timezone,
    thumbnail,
    video_duration,
)
from allskyhub_agent.products.build import KEOGRAM, MANIFEST, STARTRAILS, TIMELAPSE
from allskyhub_agent.store.images import ImageStore, night_id
from allskyhub_protocol import EventKind, FrameInfo, Mode, SkyMetrics

log = logging.getLogger(__name__)

_IMAGE = re.compile(r"^image-(\d{14})\.jpg$")
_FOLDER = re.compile(r"^\d{8}$")
_CAMERAS = {("ZWO", "ASI678MC"): "zwo-asi678mc"}


def coordinate(value: object) -> float | None:
    """Allsky writes "48.305840N" / "14.286400E" (or a plain number)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*([NSEWnsew]?)\s*", value)
    if m is None:
        return None
    v = float(m[1])
    return -v if m[2].upper() in ("S", "W") else v


@dataclass(frozen=True)
class AllskySettings:
    """What allskyhub can use from Allsky's settings.json."""

    latitude: float | None
    longitude: float | None
    night_angle: float
    camera: str | None  # an allskyhub profile id, if the camera is a supported one
    location: str
    owner: str
    camera_name: str
    lens: str
    computer: str
    days_to_keep: int | None = None  # None: Allsky keeps every night ("0")

    @classmethod
    def load(cls, allsky_home: Path) -> AllskySettings:
        try:
            raw: object = json.loads((allsky_home / "config" / "settings.json").read_text())
        except (OSError, ValueError):
            raw = {}
        d = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        angle = d.get("angle")
        keep = d.get("daystokeep")
        cam = _CAMERAS.get((str(d.get("cameratype", "")), str(d.get("cameramodel", ""))))
        return cls(
            latitude=coordinate(d.get("latitude")),
            longitude=coordinate(d.get("longitude")),
            night_angle=float(angle) if isinstance(angle, int | float) else -6.0,
            camera=cam,
            location=str(d.get("location") or ""),
            owner=str(d.get("owner") or ""),
            camera_name=str(d.get("camera") or ""),
            lens=str(d.get("lens") or ""),
            computer=str(d.get("computer") or ""),
            days_to_keep=int(keep) if isinstance(keep, int | float) and keep > 0 else None,
        )


def _image_rows(allsky_home: Path) -> dict[str, dict[str, Any]]:
    """Frame metadata Allsky kept in its database, by image name (newer installations)."""
    db = allsky_home / "config" / "myFiles" / "allsky.db"
    if not db.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cur = con.execute(
                "SELECT AS_CAMERAIMAGE, AS_DAY_OR_NIGHT, AS_EXPOSURE_US, AS_GAIN,"
                " AS_TEMPERATURE_C, AS_MEAN FROM allsky_image"
            )
            rows = cur.fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning("cannot read %s: %s", db, exc)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, dn, exp, gain, temp, mean in rows:
        if isinstance(name, str):
            out[name] = {"dn": dn, "exp": exp, "gain": gain, "temp": temp, "mean": mean}
    return out


def _sky_rows(allsky_home: Path) -> list[tuple[int, float | None, float | None, float | None]]:
    """(unix time, sqm, cloud %, stars) from the sky quality module's table, oldest first."""
    db = allsky_home / "config" / "myFiles" / "allsky.db"
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT timestamp, AS_SKYQUALITY_SQM, AS_SKYQUALITY_CLOUD, AS_SKYQUALITY_STARS"
                " FROM allsky_skyquality ORDER BY timestamp"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []  # no sky quality module

    def num(v: object) -> float | None:
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    return [(int(t), num(q), num(c), num(n)) for t, q, c, n in rows if isinstance(t, int | float)]


class AllskyImporter(FilePlacer):
    def __init__(
        self,
        allsky_home: Path,
        store: ImageStore,
        events: EventStore,
        local_tz: tzinfo,
        profile: str,
        source_tz: tzinfo | None = None,
        copy: bool = False,
        owner: tuple[int, int] | None = None,
        progress: Callable[[str], None] = print,
    ) -> None:
        super().__init__(copy, owner)
        self._home = allsky_home
        self._images = allsky_home / "images"
        self._store = store
        self._events = events
        self._tz = local_tz
        self._src_tz = source_tz or system_timezone()
        self._profile = profile
        self._progress = progress
        self.settings = AllskySettings.load(allsky_home)

    def folders(self, only: set[str] | None = None) -> list[str]:
        if not self._images.is_dir():
            return []
        names = sorted(
            p.name for p in self._images.iterdir() if p.is_dir() and _FOLDER.match(p.name)
        )
        return [n for n in names if only is None or n in only]

    # --- one Allsky folder ---------------------------------------------------------------
    def _frame(self, name: str, at: datetime, row: dict[str, Any] | None) -> FrameInfo:
        s = self.settings
        lat, lon = s.latitude or 0.0, s.longitude or 0.0
        sun = sun_elevation(at, lat, lon)
        if row is not None and row.get("dn") in ("DAY", "NIGHT"):
            mode = Mode.NIGHT if row["dn"] == "NIGHT" else Mode.DAY
        else:
            mode = Mode.NIGHT if sun < s.night_angle else Mode.DAY

        def num(key: str, lo: float, hi: float) -> float | None:
            v = row.get(key) if row is not None else None
            return float(v) if isinstance(v, int | float) and lo <= v <= hi else None

        return FrameInfo(
            captured_at=at,
            night_id=night_id(at, self._tz),
            name=name,
            mode=mode,
            exposure_us=round(num("exp", 0, 1e10) or 0),
            gain=num("gain", 0, 1e5) or 0.0,
            mean=num("mean", 0, 1) or 0.0,
            sun_elevation=round(max(-90.0, min(90.0, sun)), 2),
            sensor_temp_c=num("temp", -100, 150),
            profile=self._profile,
        )

    def _import_images(
        self, folder: Path, rows: dict[str, dict[str, Any]], report: ImportReport, dry: bool
    ) -> None:
        new: dict[str, list[FrameInfo]] = defaultdict(list)
        known: dict[str, set[str]] = {}
        for src in sorted(folder.iterdir()):
            m = _IMAGE.match(src.name)
            if m is None or not src.is_file():
                continue
            local = datetime.strptime(m[1], "%Y%m%d%H%M%S").replace(tzinfo=self._src_tz)
            at = local.astimezone(UTC)
            nid = night_id(at, self._tz)
            if nid not in known:
                known[nid] = {f.name for f in self._store.read_index(nid)}
            if src.name in known[nid]:
                report.images_skipped += 1
                continue
            dest = self._store.night_dir(nid) / src.name
            self._place(src, dest, report, dry)
            if not dry:
                thumb = self._store.night_dir(nid) / "thumbnails" / src.name
                if not thumb.exists():
                    try:
                        thumbnail(src, thumb)
                        self._chown(thumb)
                    except OSError:
                        log.warning("%s: unreadable, no thumbnail", src)
            new[nid].append(self._frame(src.name, at, rows.get(src.name)))
            report.images += 1
            report.nights.add(nid)
        if dry:
            return
        for nid, frames in new.items():
            for f in sorted(frames, key=lambda x: x.captured_at):
                self._store.append_index(f)
            self._chown(self._store.night_dir(nid) / "frames.jsonl")

    def _import_products(self, date: str, report: ImportReport, dry: bool) -> None:
        """Allsky's products of a date belong to the night that starts on that evening."""
        src_dir = self._images / date
        night = self._store.night_dir(date)
        candidates = [
            (src_dir / "keogram" / f"keogram-{date}.jpg", KEOGRAM),
            (src_dir / "startrails" / f"startrails-{date}.jpg", STARTRAILS),
            (src_dir / f"allsky-{date}.mp4", TIMELAPSE),
        ]
        durations: dict[str, float] = {}
        for src, name in candidates:
            if not src.is_file() or not self._place(src, night / name, report, dry):
                continue
            report.products += 1
            report.nights.add(date)
            if dry:
                continue
            thumb = night / "thumbnails" / (Path(name).stem + ".jpg")
            try:
                if name == TIMELAPSE:
                    poster = next((src_dir / "videothumbnail").glob("*.jpg"), None)
                    if poster is not None:
                        thumbnail(poster, thumb)
                    duration = video_duration(src)
                    if duration is not None:
                        durations[name] = duration
                else:
                    thumbnail(src, thumb)
                if thumb.exists():
                    self._chown(thumb)
            except OSError:
                log.warning("%s: no thumbnail", src)
        if durations and not dry:
            manifest = night / MANIFEST
            if not manifest.exists():
                manifest.write_text(json.dumps({"built": [], "duration_s": durations}))
                self._chown(manifest)

    def _import_meteors(
        self, date: str, rows: dict[str, dict[str, Any]], report: ImportReport, dry: bool
    ) -> None:
        """Benjamin's meteor module: meteors/meteors-<time>.json with a list of streaks."""
        src_dir = self._images / date / "meteors"
        if not src_dir.is_dir():
            return
        for jf in sorted(src_dir.glob("meteors-*.json")):
            try:
                data: object = json.loads(jf.read_text())
            except (OSError, ValueError):
                continue
            items: list[Any] = cast(list[Any], data) if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                m = cast(dict[str, Any], item)
                stamp = str(m.get("time", ""))
                try:
                    local = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=self._src_tz)
                except ValueError:
                    continue
                start = local.astimezone(UTC)
                row = rows.get(f"image-{stamp}.jpg")
                exp = row.get("exp") if row is not None else None
                end = (
                    start + timedelta(microseconds=float(exp))
                    if isinstance(exp, int | float)
                    else start
                )
                length = float(m.get("length") or 0)
                angle = m.get("angle")
                raw = m.get("showers")
                showers = [str(x) for x in cast(list[Any], raw)] if isinstance(raw, list) else []
                report.meteors += 1
                if dry:
                    report.nights.add(night_id(start, self._tz))
                    continue
                picture = src_dir / str(m.get("file") or "")
                event = self._events.import_event(
                    EventKind.METEOR,
                    night_id(start, self._tz),
                    start,
                    end,
                    round(min(1.0, 0.5 + 0.5 * min(1.0, (length - 40.0) / 200.0)), 2),
                    {
                        "length_px": round(length),
                        "peak": round(float(m.get("peak") or 0) / 255.0, 3),
                        "frames": 1,
                        # Same axis convention as the agent's own meteors (detect/meteor.py).
                        "direction_deg": _axis_direction(float(angle))
                        if isinstance(angle, int | float)
                        else None,
                        "shower": str(showers[0]) if showers else None,
                        "shower_match": "date" if showers else None,
                        "azimuth_deg": None,
                        "altitude_deg": None,
                    },
                    picture if picture.is_file() else None,
                )
                if event is None:
                    report.meteors -= 1
                else:  # only nights with something new go to the hub again
                    report.nights.add(night_id(start, self._tz))

    def _web(self) -> Path:
        """The local website, where Benjamin's lightning, aurora and NLC modules write."""
        return self._home / "html" / "allsky"

    def _local(self, stamp: str, fmt: str) -> datetime | None:
        try:
            return datetime.strptime(stamp, fmt).replace(tzinfo=self._src_tz).astimezone(UTC)
        except ValueError:
            return None

    def _import_lightning(self, nights: set[str], report: ImportReport, dry: bool) -> None:
        """Benjamin's lightning module: lightning/lightning.json, one row per bolt with its
        whole frame; storms as the agent groups them (gaps over 30 min, SPEC §6.4)."""
        folder = self._web() / "lightning"
        try:
            raw: object = json.loads((folder / "lightning.json").read_text())
        except (OSError, ValueError):
            return
        rows = (
            [cast(dict[str, Any], r) for r in cast(list[Any], raw) if isinstance(r, dict)]
            if isinstance(raw, list)
            else []
        )
        bolts: list[tuple[datetime, dict[str, Any]]] = []
        for r in rows:
            at = self._local(str(r.get("time", "")), "%Y%m%d%H%M%S")
            if at is not None:
                bolts.append((at, r))
        bolts.sort(key=lambda b: b[0])
        storm_start = bolts[0][0] if bolts else datetime.now(UTC)
        recent: list[datetime] = []
        last: datetime | None = None
        for at, r in bolts:
            if last is None or at - last > STORM_GAP:
                storm_start = at
            last = at
            recent = [t for t in recent if at - t <= STORM_GAP] + [at]
            nid = night_id(at, self._tz)
            if nid not in nights:
                continue
            report.lightning += 1
            if dry:
                report.nights.add(nid)
                continue
            picture = folder / str(r.get("file") or "")
            area = r.get("area")
            pixels = _pixels(picture)
            frac = (
                round(min(1.0, float(area) / pixels), 4)
                if isinstance(area, int | float) and pixels
                else None
            )
            peak = r.get("peak")
            exp = r.get("exposure_ms")
            end = at + timedelta(milliseconds=float(exp)) if isinstance(exp, int | float) else at
            event = self._events.import_event(
                EventKind.LIGHTNING,
                nid,
                at,
                end,
                round(min(1.0, 0.5 + 25.0 * (frac or 0.0)), 2),
                {
                    "area_frac": frac,
                    "peak": round(float(peak) / 255.0, 3)
                    if isinstance(peak, int | float)
                    else None,
                    "storm_flashes": len(recent),
                    "storm": "storm-" + storm_start.strftime("%Y%m%dT%H%M%SZ"),
                    "azimuth_deg": None,
                    "altitude_deg": None,
                },
                picture if picture.is_file() else None,
            )
            if event is None:
                report.lightning -= 1
            else:
                report.nights.add(nid)

    def _import_episodes(
        self, kind: EventKind, nights: set[str], report: ImportReport, dry: bool
    ) -> None:
        """Benjamin's aurora and NLC modules: a picture per candidate frame (`<kind>/`, and
        for aurora also `images/<day>/aurora/`) and a rolling `<kind>.json` with the index
        of the last days. Candidates less than 30 min apart are one episode (SPEC §6.4)."""
        name = "aurora" if kind is EventKind.AURORA else "nlc"
        rows: dict[str, dict[str, Any]] = {}
        try:
            raw: object = json.loads((self._web() / f"{name}.json").read_text())
        except (OSError, ValueError):
            raw = []
        for item in cast(list[Any], raw) if isinstance(raw, list) else []:
            if isinstance(item, dict):
                r = cast(dict[str, Any], item)
                if r.get("thumb"):
                    rows[str(r["thumb"])] = r
        pictures: list[tuple[datetime, Path]] = []
        dirs = [self._web() / name] + ([self._images / d / "aurora" for d in sorted(nights)]
                                        if kind is EventKind.AURORA else [])  # fmt: skip
        seen: set[str] = set()
        for d in dirs:
            for f in sorted(d.glob(f"{name}-*.jpg")) if d.is_dir() else []:
                if f.name in seen:
                    continue
                at = self._local(f.stem.split("-", 1)[1][:15], "%Y%m%d_%H%M%S")
                if at is not None:
                    seen.add(f.name)
                    pictures.append((at, f))
        pictures.sort()
        episodes: list[list[tuple[datetime, Path]]] = []
        for at, f in pictures:
            if episodes and at - episodes[-1][-1][0] <= STORM_GAP:
                episodes[-1].append((at, f))
            else:
                episodes.append([(at, f)])
        for ep in episodes:
            start, end = ep[0][0], ep[-1][0]
            nid = night_id(start, self._tz)
            if nid not in nights:
                continue
            report.episodes += 1
            if dry:
                report.nights.add(nid)
                continue
            known = [(rows[f.name], f) for _, f in ep if f.name in rows]
            best_row, best_pic = (
                max(known, key=lambda k: float(k[0].get("index") or 0))
                if known
                else ({}, ep[len(ep) // 2][1])
            )
            index = best_row.get("index")
            colour = best_row.get("green" if kind is EventKind.AURORA else "blue")
            event = self._events.import_event(
                kind,
                nid,
                start,
                end,
                round(min(1.0, 0.5 + float(index) / 10.0), 2)
                if isinstance(index, int | float)
                else 0.6,
                {
                    "peak_index": float(index) if isinstance(index, int | float) else None,
                    ("green" if kind is EventKind.AURORA else "blue"): float(colour)
                    if isinstance(colour, int | float)
                    else None,
                    "frames": len(ep),
                    "direction_deg": None,
                    "ongoing": False,
                    "image_rev": 1,
                    "azimuth_deg": _COMPASS.get(str(best_row.get("dir", ""))),
                    "altitude_deg": None,
                },
                best_pic,
            )
            if event is None:
                report.episodes -= 1
            else:
                report.nights.add(nid)

    def _import_sky(self, folders: list[str], report: ImportReport, dry: bool) -> None:
        """The sky quality module's history as the sky log (SPEC §5.1) for the charts, with
        the agent's own rules: cloud cover only at night, sky brightness only at
        astronomical night (SPEC §6.3)."""
        lat, lon = self.settings.latitude, self.settings.longitude
        if lat is None or lon is None:
            return
        days = set(folders)
        known: dict[str, set[datetime]] = {}
        new: dict[str, list[SkyMetrics]] = defaultdict(list)
        for t, sqm_mag, cloud, stars in _sky_rows(self._home):
            at = datetime.fromtimestamp(t, UTC)
            # Only samples of the folders being imported (by the agent's night or the date).
            nid = night_id(at, self._tz)
            local_day = at.astimezone(self._src_tz).strftime("%Y%m%d")
            if nid not in days and local_day not in days:
                continue
            sun = sun_elevation(at, lat, lon)
            m = SkyMetrics(
                at=at,
                night_id=nid,
                cloud_cover=round(min(1.0, max(0.0, cloud / 100)), 3)
                if cloud is not None and sun <= -12
                else None,
                sqm_mag=round(sqm_mag, 2) if sqm_mag is not None and sun <= -18 else None,
                stars=round(stars) if stars is not None and sun <= -12 else None,
            )
            if (m.cloud_cover, m.sqm_mag) == (None, None):
                continue
            if nid not in known:
                known[nid] = {x.at for x in self._store.read_sky(nid)}
            if at in known[nid]:
                continue
            new[nid].append(m)
            report.sky += 1
        if dry:
            return
        for nid, samples in new.items():
            for m in samples:
                self._store.append_sky(m)
            self._chown(self._store.night_dir(nid) / "sky.jsonl")

    def run(self, only: set[str] | None = None, dry: bool = False) -> ImportReport:
        report = ImportReport()
        rows = _image_rows(self._home)
        folders = self.folders(only)
        for i, date in enumerate(folders, 1):
            self._progress(f"[{i}/{len(folders)}] {date}")
            self._import_images(self._images / date, rows, report, dry)
            self._import_products(date, report, dry)
            self._import_meteors(date, rows, report, dry)
            report.folders += 1
        self._import_sky(folders, report, dry)
        nights = set(folders)
        self._import_lightning(nights, report, dry)
        self._import_episodes(EventKind.AURORA, nights, report, dry)
        self._import_episodes(EventKind.NLC, nights, report, dry)
        return report


STORM_GAP = timedelta(minutes=30)
_COMPASS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0,
            "NW": 315.0}  # fmt: skip


def _pixels(picture: Path) -> int | None:
    try:
        with PILImage.open(picture) as im:
            return im.width * im.height
    except OSError:
        return None


def _axis_direction(angle_deg: float) -> float:
    """Allsky's streak angle (from the image x axis) as 0 = up, clockwise, 0..180."""
    dx, dy = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return round(math.degrees(math.atan2(dx, -dy)) % 180.0, 1)

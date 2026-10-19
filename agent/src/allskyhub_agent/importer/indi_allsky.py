"""Import an existing indi-allsky installation (roadmap L18).

Takes over what indi-allsky (github.com/aaronwmorris/indi-allsky) stored, so switching to
allskyhub loses no pictures. indi-allsky keeps everything in one SQLite database
(default ``/var/lib/indi-allsky/indi-allsky.sqlite``, tables of
``indi_allsky/flask/models.py``) and the files under its image folder (config
``IMAGE_FOLDER``, default ``/var/www/html/allsky/images``); ``filename`` columns are
relative to that folder (older versions stored absolute paths).

What comes over, read-only (the database is opened ``mode=ro``):

- settings from the newest row of table ``config`` (JSON): ``LOCATION_LATITUDE``,
  ``LOCATION_LONGITUDE``, ``LOCATION_NAME``, ``NIGHT_SUN_ALT_DEG``, ``OWNER``,
  ``LENS_NAME``, ``IMAGE_EXPIRE_DAYS`` / ``TIMELAPSE_EXPIRE_DAYS``; the camera model from
  table ``camera`` (``name``);
- every JPEG of table ``image`` with its frame metadata (SPEC §4.4): ``createDate``
  (naive local time), ``exposure`` (s), ``gain``, ``temp``, ``night``, ``adu`` (8 bit);
  grouped by the agent's own night id (SPEC §4.5), not by indi-allsky's ``dayDate``;
- products (SPEC §5.2) by ``dayDate`` and ``night``: table ``keogram`` as keogram /
  day keogram, ``startrail`` (night only) as star trails, ``video`` as timelapse / day
  timelapse (H.264 MP4 only).

Left out on purpose:

- ``image.detections``: indi-allsky counts Hough lines per frame, with no distinction
  between meteors, planes and satellites and no position or time of the streak, so they
  do not map honestly onto meteor events (SPEC §6.4);
- ``image.sqm`` is indi-allsky's uncalibrated "janky SQM" (``indi_allsky/sqm.py``), not
  mag/arcsec², and ``image.stars`` alone is no sky log sample (SPEC §5.1), so there is
  no sky log;
- ``image.exclude`` (indi-allsky's own skip flag) is not a dark/bright verdict
  (``FrameInfo.bad``, SPEC §4.9); star trail timelapses, mini timelapses, panoramas,
  RAW/FITS files and day star trails have no allskyhub product;
- PNG/TIFF images and WebM or non-H.264 timelapses (counted as unsupported).

Images are hard-linked when the image folder is on the same disk (no extra space), else
copied. Running it again only adds what is new.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, cast

from allskyhub_agent.core.sun import sun_elevation
from allskyhub_agent.importer.common import (
    FilePlacer,
    ImportReport,
    system_timezone,
    thumbnail,
    video_codec,
    video_duration,
)
from allskyhub_agent.products.build import KEOGRAM, MANIFEST, STARTRAILS, TIMELAPSE
from allskyhub_agent.products.day import DAY_KEOGRAM, DAY_MANIFEST, DAY_TIMELAPSE
from allskyhub_agent.store.images import ImageStore, night_id
from allskyhub_protocol import FrameInfo, Mode

log = logging.getLogger(__name__)

DEFAULT_DB = Path("/var/lib/indi-allsky/indi-allsky.sqlite")
DEFAULT_IMAGES = Path("/var/www/html/allsky/images")
_JPEG = (".jpg", ".jpeg")
# indi-allsky's camera names (INDI device names, or libcamera_<sensor>) to profiles.
_CAMERAS = {"ASI678MC": "zwo-asi678mc", "IMX477": "rpi-hq"}


def connect(db: Path) -> sqlite3.Connection:
    """indi-allsky's database, read-only: the import never changes it."""
    con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _local(value: object) -> datetime | None:
    """SQLAlchemy keeps a DateTime in SQLite as "YYYY-MM-DD HH:MM:SS[.ffffff]"."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _day(value: object) -> str | None:
    """A Date column ("YYYY-MM-DD") as a night id."""
    if isinstance(value, str) and len(value) == 10:
        compact = value.replace("-", "")
        if len(compact) == 8 and compact.isdigit():
            return compact
    return None


@dataclass(frozen=True)
class IndiCamera:
    id: int
    name: str
    images: int


def cameras(db: Path) -> list[IndiCamera]:
    """The cameras of this installation with their image count, most images first."""
    try:
        con = connect(db)
        try:
            rows = con.execute(
                "SELECT camera.id, camera.name, COUNT(image.id) AS n FROM camera"
                " LEFT JOIN image ON image.camera_id = camera.id GROUP BY camera.id"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning("cannot read %s: %s", db, exc)
        return []
    out = [IndiCamera(int(r["id"]), str(r["name"] or ""), int(r["n"] or 0)) for r in rows]
    return sorted(out, key=lambda c: (-c.images, c.id))


@dataclass(frozen=True)
class IndiAllskySettings:
    """What allskyhub can use from indi-allsky's configuration."""

    latitude: float | None
    longitude: float | None
    night_angle: float
    camera: str | None  # an allskyhub profile id, if the camera is a supported one
    camera_name: str
    location: str
    owner: str
    lens: str
    image_folder: Path
    days_to_keep: int | None = None  # its images
    product_days_to_keep: int | None = None  # its timelapses and keograms

    @classmethod
    def load(cls, db: Path, camera_id: int | None = None) -> IndiAllskySettings:
        config: dict[str, Any] = {}
        cam: dict[str, Any] = {}
        try:
            con = connect(db)
            try:
                row = con.execute(
                    "SELECT data FROM config ORDER BY createDate DESC, id DESC LIMIT 1"
                ).fetchone()
                if row is not None and isinstance(row["data"], str):
                    raw: object = json.loads(row["data"])
                    config = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
                if camera_id is not None:
                    c = con.execute("SELECT * FROM camera WHERE id = ?", (camera_id,)).fetchone()
                    cam = {k: c[k] for k in c.keys()} if c else {}  # noqa: SIM118 - a Row
            finally:
                con.close()
        except (sqlite3.Error, ValueError) as exc:
            log.warning("cannot read the configuration in %s: %s", db, exc)
        lat = _num(config.get("LOCATION_LATITUDE"))
        lon = _num(config.get("LOCATION_LONGITUDE"))
        if lat is None or lon is None:  # older installations: the camera row has it
            lat, lon = _num(cam.get("latitude")), _num(cam.get("longitude"))
        angle = _num(config.get("NIGHT_SUN_ALT_DEG"))
        name = str(cam.get("name") or "")
        profile = next((p for k, p in _CAMERAS.items() if k in name.upper()), None)

        # Images and products have their own retention, like ours (SPEC §4.5).
        def days(v: object) -> int | None:
            ok = isinstance(v, int | float) and not isinstance(v, bool) and v > 0
            return int(cast(float, v)) if ok else None

        folder = config.get("IMAGE_FOLDER")
        return cls(
            latitude=lat,
            longitude=lon,
            night_angle=angle if angle is not None else -6.0,
            camera=profile,
            camera_name=name,
            location=str(config.get("LOCATION_NAME") or cam.get("location") or ""),
            owner=str(config.get("OWNER") or cam.get("owner") or ""),
            lens=str(config.get("LENS_NAME") or cam.get("lensName") or ""),
            image_folder=Path(folder) if isinstance(folder, str) and folder else DEFAULT_IMAGES,
            days_to_keep=days(config.get("IMAGE_EXPIRE_DAYS")),
            product_days_to_keep=days(config.get("TIMELAPSE_EXPIRE_DAYS")),
        )


# (indi-allsky table, its `night` value) -> the agent's product and its manifest.
_PRODUCTS: list[tuple[str, bool, str, str]] = [
    ("keogram", True, KEOGRAM, MANIFEST),
    ("startrail", True, STARTRAILS, MANIFEST),
    ("video", True, TIMELAPSE, MANIFEST),
    ("keogram", False, DAY_KEOGRAM, DAY_MANIFEST),
    ("video", False, DAY_TIMELAPSE, DAY_MANIFEST),
]


class IndiAllskyImporter(FilePlacer):
    def __init__(
        self,
        db: Path,
        store: ImageStore,
        local_tz: tzinfo,
        profile: str,
        camera_id: int | None = None,
        image_folder: Path | None = None,
        source_tz: tzinfo | None = None,
        copy: bool = False,
        owner: tuple[int, int] | None = None,
        progress: Callable[[str], None] = print,
    ) -> None:
        super().__init__(copy, owner)
        self._db = db
        self._store = store
        self._tz = local_tz
        self._src_tz = source_tz or system_timezone()
        self._profile = profile
        self._progress = progress
        found = cameras(db)
        self.camera_id = camera_id if camera_id is not None else (found[0].id if found else 0)
        self.settings = IndiAllskySettings.load(db, self.camera_id)
        self._root = (image_folder or self.settings.image_folder).resolve()

    # --- the database --------------------------------------------------------------------
    def _rows(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        try:
            con = connect(self._db)
            try:
                return con.execute(sql, params).fetchall()
            finally:
                con.close()
        except sqlite3.Error as exc:
            log.warning("cannot read %s: %s", self._db, exc)
            return []

    def _file(self, filename: object) -> Path | None:
        """A `filename` column as a file inside the image folder; None if it is missing or
        points elsewhere (the import runs as root and must not take over other files)."""
        if not isinstance(filename, str) or not filename:
            return None
        path = Path(filename)
        try:
            path = (path if path.is_absolute() else self._root / path).resolve()
        except (OSError, RuntimeError):
            return None
        if not path.is_relative_to(self._root) or not path.is_file():
            return None
        return path

    def _at(self, value: object) -> datetime | None:
        local = _local(value)
        return local.replace(tzinfo=self._src_tz).astimezone(UTC) if local else None

    def nights(self, only: set[str] | None = None) -> list[str]:
        """Night ids with images or products of the chosen camera, oldest first."""
        found: set[str] = set()
        for r in self._rows(
            "SELECT DISTINCT substr(createDate, 1, 13) AS h FROM image WHERE camera_id = ?",
            (self.camera_id,),
        ):
            at = self._at(str(r["h"]) + ":30:00")
            if at is not None:
                found.add(night_id(at, self._tz))
        for table in ("keogram", "startrail", "video"):
            for r in self._rows(
                f"SELECT DISTINCT dayDate FROM {table} WHERE camera_id = ?",  # noqa: S608
                (self.camera_id,),
            ):
                day = _day(r["dayDate"])
                if day is not None:
                    found.add(day)
        return sorted(n for n in found if only is None or n in only)

    # --- one night -----------------------------------------------------------------------
    def _frame(self, name: str, at: datetime, row: sqlite3.Row) -> FrameInfo:
        s = self.settings
        sun = sun_elevation(at, s.latitude or 0.0, s.longitude or 0.0)
        night = row["night"]
        if isinstance(night, int):
            mode = Mode.NIGHT if night else Mode.DAY
        else:
            mode = Mode.NIGHT if sun < s.night_angle else Mode.DAY

        def num(key: str, lo: float, hi: float) -> float | None:
            v = _num(row[key])
            return v if v is not None and lo <= v <= hi else None

        adu = num("adu", 0, 255)
        return FrameInfo(
            captured_at=at,
            night_id=night_id(at, self._tz),
            name=name,
            mode=mode,
            exposure_us=round((num("exposure", 0, 1e4) or 0) * 1e6),
            gain=num("gain", 0, 1e5) or 0.0,
            mean=round(adu / 255.0, 4) if adu is not None else 0.0,
            sun_elevation=round(max(-90.0, min(90.0, sun)), 2),
            sensor_temp_c=num("temp", -100, 150),
            profile=self._profile,
        )

    def _import_images(self, night: str, report: ImportReport, dry: bool) -> None:
        # Frames of a night lie between its noon and the next noon (SPEC §4.5): a day
        # either side covers any time zone, then the night id decides.
        start = datetime.strptime(night, "%Y%m%d")  # noqa: DTZ007 - indi-allsky's local time
        lo = (start - timedelta(days=1)).strftime("%Y-%m-%d")
        hi = (start + timedelta(days=3)).strftime("%Y-%m-%d")
        rows = self._rows(
            "SELECT filename, createDate, exposure, gain, temp, night, adu FROM image"
            " WHERE camera_id = ? AND createDate >= ? AND createDate < ? ORDER BY createDate",
            (self.camera_id, lo, hi),
        )
        known = {f.name for f in self._store.read_index(night)}
        frames: list[FrameInfo] = []
        for row in rows:
            at = self._at(row["createDate"])
            if at is None or night_id(at, self._tz) != night:
                continue
            src = self._file(row["filename"])
            if src is None:
                report.skip("image file missing")
                continue
            if src.suffix.lower() not in _JPEG:
                report.skip(f"{src.suffix.lstrip('.').upper()} image, not JPEG")
                continue
            if src.name in known:
                report.images_skipped += 1
                continue
            known.add(src.name)
            dest = self._store.night_dir(night) / src.name
            self._place(src, dest, report, dry)
            if not dry:
                thumb = self._store.night_dir(night) / "thumbnails" / src.name
                if not thumb.exists():
                    try:
                        thumbnail(src, thumb)
                        self._chown(thumb)
                    except OSError:
                        log.warning("%s: unreadable, no thumbnail", src)
            frames.append(self._frame(src.name, at, row))
            report.images += 1
            report.nights.add(night)
        if dry or not frames:
            return
        for f in frames:
            self._store.append_index(f)
        self._chown(self._store.night_dir(night) / "frames.jsonl")

    def _poster(self, day: str, is_night: bool, video: sqlite3.Row) -> Path | None:
        """A timelapse's thumbnail: indi-allsky's middle frame of it, else its own
        (150 px) thumbnail."""
        rows = self._rows(
            "SELECT filename FROM image WHERE camera_id = ? AND dayDate = ? AND night = ?"
            " ORDER BY createDate",
            (self.camera_id, f"{day[:4]}-{day[4:6]}-{day[6:]}", int(is_night)),
        )
        files = [p for p in (self._file(r["filename"]) for r in rows) if p is not None]
        files = [p for p in files if p.suffix.lower() in _JPEG]
        if files:
            return files[len(files) // 2]
        uuid = video["thumbnail_uuid"]
        if not isinstance(uuid, str):
            return None
        thumbs = self._rows("SELECT filename FROM thumbnail WHERE uuid = ?", (uuid,))
        return self._file(thumbs[0]["filename"]) if thumbs else None

    def _import_products(self, night: str, report: ImportReport, dry: bool) -> None:
        """indi-allsky's products of a `dayDate` belong to the night that starts on that
        evening: night products to that night, day products to its folder too (SPEC §5.2)."""
        folder = self._store.night_dir(night)
        day = f"{night[:4]}-{night[4:6]}-{night[6:]}"
        durations: dict[str, dict[str, float]] = {}
        for table, is_night, name, manifest in _PRODUCTS:
            extra = ", framerate, frames, thumbnail_uuid" if table == "video" else ""
            rows = self._rows(
                f"SELECT filename, success, createDate{extra} FROM {table}"  # noqa: S608
                " WHERE camera_id = ? AND dayDate = ? AND night = ?",
                (self.camera_id, day, int(is_night)),
            )
            # Several rows for one night: the newest successful one with its file.
            rows.sort(key=lambda r: (bool(r["success"]), str(r["createDate"])), reverse=True)
            found = next(((r, p) for r in rows if (p := self._file(r["filename"]))), None)
            if found is None:
                continue
            row, src = found
            want = Path(name).suffix
            if src.suffix.lower() not in (_JPEG if want == ".jpg" else (want,)):
                report.skip(f"{src.suffix.lstrip('.').upper()} {table}, not {want[1:].upper()}")
                continue
            if table == "video" and video_codec(src) not in (None, "h264"):
                report.skip("timelapse not H.264")
                continue
            if not self._place(src, folder / name, report, dry):
                continue
            report.products += 1
            report.nights.add(night)
            if dry:
                continue
            thumb = folder / "thumbnails" / (Path(name).stem + ".jpg")
            try:
                if table == "video":
                    poster = self._poster(night, is_night, row)
                    if poster is not None:
                        thumbnail(poster, thumb)
                    length = video_duration(src)
                    fps, count = _num(row["framerate"]), _num(row["frames"])
                    if length is None and fps and count:
                        length = round(count / fps, 1)
                    if length is not None:
                        durations.setdefault(manifest, {})[name] = length
                else:
                    thumbnail(src, thumb)
                if thumb.exists():
                    self._chown(thumb)
            except OSError:
                log.warning("%s: no thumbnail", src)
        for manifest, lengths in durations.items():
            path = folder / manifest
            if not path.exists():
                path.write_text(json.dumps({"built": [], "duration_s": lengths}))
                self._chown(path)

    def run(self, only: set[str] | None = None, dry: bool = False) -> ImportReport:
        report = ImportReport()
        nights = self.nights(only)
        for i, night in enumerate(nights, 1):
            self._progress(f"[{i}/{len(nights)}] {night}")
            self._import_images(night, report, dry)
            self._import_products(night, report, dry)
            report.folders += 1
        return report

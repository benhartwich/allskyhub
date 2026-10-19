"""Taking over an existing indi-allsky installation (roadmap L18)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image as PILImage

from allskyhub_agent.cli import run_import_indi
from allskyhub_agent.importer.backlog import BACKLOG_NAME, Backlog
from allskyhub_agent.importer.indi_allsky import (
    IndiAllskyImporter,
    IndiAllskySettings,
    cameras,
)
from allskyhub_agent.products.build import night_products
from allskyhub_agent.settings import AgentSettings
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import Mode, ProductKind

BERLIN = ZoneInfo("Europe/Berlin")
VIENNA = ZoneInfo("Europe/Vienna")
UUID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"

# The tables and columns as indi-allsky's SQLAlchemy models create them
# (indi_allsky/flask/models.py: IndiAllSkyDbCameraTable `camera`, IndiAllSkyDbImageTable
# `image`, IndiAllSkyDbThumbnailTable `thumbnail`, IndiAllSkyDbKeogramTable `keogram`,
# IndiAllSkyDbStarTrailsTable `startrail`, IndiAllSkyDbVideoTable `video`,
# IndiAllSkyDbConfigTable `config`), reduced to the columns the import reads plus a few
# NOT NULL neighbours. DateTime is SQLAlchemy's SQLite text "YYYY-MM-DD HH:MM:SS.ffffff",
# Date "YYYY-MM-DD", Boolean 0/1, JSON text.
SCHEMA = """
CREATE TABLE camera (id INTEGER PRIMARY KEY, uuid VARCHAR(36), name VARCHAR(100) NOT NULL,
  createDate DATETIME, location VARCHAR(100), latitude FLOAT, longitude FLOAT,
  owner VARCHAR(100), lensName VARCHAR(100), local BOOLEAN NOT NULL DEFAULT 1);
CREATE TABLE thumbnail (id INTEGER PRIMARY KEY, uuid VARCHAR(36), filename VARCHAR(255) NOT NULL,
  createDate DATETIME NOT NULL, camera_id INTEGER NOT NULL);
CREATE TABLE image (id INTEGER PRIMARY KEY, filename VARCHAR(255) NOT NULL,
  thumbnail_uuid VARCHAR(36), createDate DATETIME NOT NULL, dayDate DATE NOT NULL,
  exposure FLOAT NOT NULL, gain FLOAT NOT NULL, binmode INTEGER NOT NULL DEFAULT 1,
  temp FLOAT, night BOOLEAN NOT NULL DEFAULT 1, adu FLOAT NOT NULL, sqm FLOAT,
  stars INTEGER, detections INTEGER NOT NULL DEFAULT 0,
  exclude BOOLEAN NOT NULL DEFAULT 0, camera_id INTEGER NOT NULL);
CREATE TABLE keogram (id INTEGER PRIMARY KEY, filename VARCHAR(255) NOT NULL,
  thumbnail_uuid VARCHAR(36), createDate DATETIME NOT NULL, dayDate DATE NOT NULL,
  night BOOLEAN NOT NULL, success BOOLEAN NOT NULL DEFAULT 0, frames INTEGER NOT NULL DEFAULT 0,
  camera_id INTEGER NOT NULL);
CREATE TABLE startrail (id INTEGER PRIMARY KEY, filename VARCHAR(255) NOT NULL,
  thumbnail_uuid VARCHAR(36), createDate DATETIME NOT NULL, dayDate DATE NOT NULL,
  night BOOLEAN NOT NULL, success BOOLEAN NOT NULL DEFAULT 0, frames INTEGER NOT NULL DEFAULT 0,
  camera_id INTEGER NOT NULL);
CREATE TABLE video (id INTEGER PRIMARY KEY, filename VARCHAR(255) NOT NULL,
  thumbnail_uuid VARCHAR(36), createDate DATETIME NOT NULL, dayDate DATE NOT NULL,
  night BOOLEAN NOT NULL, success BOOLEAN NOT NULL DEFAULT 0,
  framerate FLOAT NOT NULL DEFAULT 0, frames INTEGER NOT NULL DEFAULT 0,
  camera_id INTEGER NOT NULL);
CREATE TABLE config (id INTEGER PRIMARY KEY, createDate DATETIME NOT NULL,
  level VARCHAR(12) NOT NULL, encrypted BOOLEAN NOT NULL DEFAULT 0, note VARCHAR(255) NOT NULL,
  data JSON, user_id INTEGER);
"""


def _jpeg(path: Path, value: int = 40) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.fromarray(np.full((90, 160, 3), value, dtype=np.uint8)).save(path, "JPEG")


def _exposure(stamp: str, night: bool) -> str:
    """indi_allsky/image.py _getImageFolder + filename_t 'ccd{id}_{%Y%m%d_%H%M%S}.{ext}'."""
    at = datetime.strptime(stamp, "%Y%m%d_%H%M%S")  # noqa: DTZ007
    day = stamp[:8] if night is False or at.hour >= 12 else f"{int(stamp[:8]) - 1}"
    return f"ccd_{UUID}/exposures/{day}/{'night' if night else 'day'}/{at:%d_%H}/ccd1_{stamp}.jpg"


def make_indi(root: Path) -> tuple[Path, Path]:
    images = root / "www" / "images"
    db = root / "indi-allsky.sqlite"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute(
        "INSERT INTO camera (id, uuid, name, latitude, longitude) VALUES"
        " (1, ?, 'ZWO CCD ASI678MC', 1.0, 2.0)",
        (UUID,),
    )
    config = {"LOCATION_NAME": "Musterdorf", "LOCATION_LATITUDE": 50.03601,
              "LOCATION_LONGITUDE": 10.38951, "NIGHT_SUN_ALT_DEG": -8.0, "OWNER": "Ben",
              "LENS_NAME": "2.5 mm", "IMAGE_FOLDER": str(images), "IMAGE_EXPIRE_DAYS": 30,
              "TIMELAPSE_EXPIRE_DAYS": 365}  # fmt: skip
    con.execute(
        "INSERT INTO config (createDate, level, note, data) VALUES (?, '1', 'old', ?)",
        ("2026-01-01 10:00:00.000000", json.dumps({"LOCATION_LATITUDE": 0.0})),
    )
    con.execute(
        "INSERT INTO config (createDate, level, note, data) VALUES (?, '1', 'new', ?)",
        ("2026-10-01 10:00:00.000000", json.dumps(config)),
    )
    frames = [  # (local stamp, night, exposure s, gain, temp, adu)
        ("20261011_070000", False, 0.001, 0.0, 20.0, 110.0),  # morning: the night before
        ("20261011_213000", True, 15.0, 200.0, 13.0, 30.0),
        ("20261012_031500", True, 30.0, 150.0, 12.5, 53.55),
    ]
    for stamp, night, exp, gain, temp, adu in frames:
        rel = _exposure(stamp, night)
        _jpeg(images / rel)
        at = datetime.strptime(stamp, "%Y%m%d_%H%M%S")  # noqa: DTZ007
        day = rel.split("/")[2]
        con.execute(
            "INSERT INTO image (filename, createDate, dayDate, exposure, gain, temp, night, adu,"
            " sqm, stars, detections, camera_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1234.5, 80, 3, 1)",
            (rel, f"{at:%Y-%m-%d %H:%M:%S}.000000", f"{day[:4]}-{day[4:6]}-{day[6:]}",
             exp, gain, temp, int(night), adu),
        )  # fmt: skip
    # A PNG frame, and a row pointing outside the image folder: both left out.
    png = images / f"ccd_{UUID}/exposures/20261011/night/11_22/ccd1_20261011_220000.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"png")
    con.execute(
        "INSERT INTO image (filename, createDate, dayDate, exposure, gain, night, adu, camera_id)"
        " VALUES (?, '2026-10-11 22:00:00.000000', '2026-10-11', 15, 200, 1, 30, 1)",
        (f"ccd_{UUID}/exposures/20261011/night/11_22/ccd1_20261011_220000.png",),
    )
    _jpeg(root / "secret.jpg")
    con.execute(
        "INSERT INTO image (filename, createDate, dayDate, exposure, gain, night, adu, camera_id)"
        " VALUES ('../../secret.jpg', '2026-10-11 22:30:00.000000', '2026-10-11', 15, 200, 1,"
        " 30, 1)"
    )
    # Products: indi_allsky/video.py names them allsky-<kind>_ccd<id>_<day>_<night|day>_<ts>.
    tl = images / f"ccd_{UUID}" / "timelapse" / "20261011"
    _jpeg(tl / "allsky-keogram_ccd1_20261011_night_1760241600.jpg")
    _jpeg(tl / "allsky-startrail_ccd1_20261011_night_1760241600.jpg")
    _jpeg(tl / "allsky-keogram_ccd1_20261011_day_1760205600.jpg")
    (tl / "allsky-timelapse_ccd1_20261011_night_1760241600.mp4").write_bytes(b"\x00" * 100)
    (tl / "allsky-timelapse_ccd1_20261011_day_1760205600.webm").write_bytes(b"\x00" * 50)
    rows = [
        ("keogram", "allsky-keogram_ccd1_20261011_night_1760241600.jpg", 1, ""),
        ("keogram", "allsky-keogram_ccd1_20261011_night_1.jpg", 1, ""),  # gone: older entry
        ("startrail", "allsky-startrail_ccd1_20261011_night_1760241600.jpg", 1, ""),
        ("keogram", "allsky-keogram_ccd1_20261011_day_1760205600.jpg", 0, ""),
        ("video", "allsky-timelapse_ccd1_20261011_night_1760241600.mp4", 1, "v"),
        ("video", "allsky-timelapse_ccd1_20261011_day_1760205600.webm", 0, "v"),
    ]
    for table, name, night, video in rows:
        extra = ", framerate, frames" if video else ""
        values = ", 25, 250" if video else ""
        con.execute(
            f"INSERT INTO {table} (filename, createDate, dayDate, night, success{extra},"  # noqa: S608
            f" camera_id) VALUES (?, '2026-10-12 06:00:00.000000', '2026-10-11', ?, 1{values}, 1)",
            (f"ccd_{UUID}/timelapse/20261011/{name}", night),
        )
    con.commit()
    con.close()
    return db, images


def make_importer(
    tmp_path: Path, db: Path, copy: bool = False, camera_id: int | None = None
) -> tuple[IndiAllskyImporter, ImageStore]:
    store = ImageStore(tmp_path / "data", VIENNA)
    imp = IndiAllskyImporter(db, store, VIENNA, "zwo-asi678mc", camera_id=camera_id,
                             source_tz=BERLIN, copy=copy, progress=lambda _: None)  # fmt: skip
    return imp, store


def test_settings_from_the_newest_config(tmp_path: Path) -> None:
    db, images = make_indi(tmp_path)
    s = IndiAllskySettings.load(db, 1)
    assert (s.latitude, s.longitude, s.night_angle) == (50.03601, 10.38951, -8.0)
    assert (s.camera, s.camera_name, s.location, s.owner, s.lens) == (
        "zwo-asi678mc",
        "ZWO CCD ASI678MC",
        "Musterdorf",
        "Ben",
        "2.5 mm",
    )
    assert s.image_folder == images
    assert (s.days_to_keep, s.product_days_to_keep) == (30, 365)  # images, timelapses
    assert [(c.id, c.images) for c in cameras(db)] == [(1, 5)]


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    db, _ = make_indi(tmp_path)
    imp, store = make_importer(tmp_path, db)
    r = imp.run(dry=True)
    assert (r.images, r.products) == (3, 4)
    assert r.nights == {"20261010", "20261011"}
    assert r.unsupported == {
        "PNG image, not JPEG": 1,
        "image file missing": 1,  # the row pointing outside the image folder
        "WEBM video, not MP4": 1,
    }
    assert store.nights() == []


def test_import_places_everything_and_is_repeatable(tmp_path: Path) -> None:
    db, images = make_indi(tmp_path)
    imp, store = make_importer(tmp_path, db)
    r = imp.run()
    assert (r.images, r.products, r.linked) == (3, 4, 7)
    # 07:00 belongs to the night before; 21:30 and 03:15 to the night of the 11th (SPEC §4.5).
    assert [f.name for f in store.read_index("20261010")] == ["ccd1_20261011_070000.jpg"]
    night = store.read_index("20261011")
    assert [f.name for f in night] == ["ccd1_20261011_213000.jpg", "ccd1_20261012_031500.jpg"]
    late = night[1]
    assert late.captured_at == datetime(2026, 10, 12, 1, 15, tzinfo=UTC)
    # SPEC §4.4: exposure in µs from seconds, mean from the 8-bit ADU.
    assert (late.mode, late.exposure_us, late.gain, late.sensor_temp_c, late.mean) == (
        Mode.NIGHT,
        30_000_000,
        150.0,
        12.5,
        0.21,
    )
    assert store.read_index("20261010")[0].mode is Mode.DAY  # indi-allsky's `night` column
    src = images / _exposure("20261012_031500", True)
    dest = store.night_dir("20261011") / "ccd1_20261012_031500.jpg"
    assert src.stat().st_ino == dest.stat().st_ino  # a hard link, no copy
    assert store.path_for("20261011", dest.name, thumbnail=True) is not None
    assert not (store.night_dir("20261011") / "secret.jpg").exists()

    products = night_products(store, "20261011")
    assert products is not None
    got = {p.kind: p for p in products.products}
    assert set(got) == {ProductKind.KEOGRAM, ProductKind.STARTRAILS, ProductKind.TIMELAPSE,
                        ProductKind.DAY_KEOGRAM}  # fmt: skip
    assert got[ProductKind.TIMELAPSE].duration_s == 10.0  # 250 frames at 25 fps
    assert all(p.thumbnail for p in products.products)
    assert store.read_sky("20261011") == []  # indi-allsky's jSQM is not mag/arcsec²

    again = imp.run()
    assert (again.images, again.products) == (0, 0)
    assert again.images_skipped == 3
    assert again.nights == set()  # nothing new: nothing to announce to the hub again
    assert len(store.read_index("20261011")) == 2


def test_absolute_paths_and_copy(tmp_path: Path) -> None:
    """Older indi-allsky versions stored absolute file names."""
    db, images = make_indi(tmp_path)
    con = sqlite3.connect(db)
    con.execute("UPDATE image SET filename = ? || '/' || filename", (str(images),))
    con.commit()
    con.close()
    imp, store = make_importer(tmp_path, db, copy=True)
    r = imp.run(only={"20261011"})
    assert (r.images, r.linked, r.copied) == (2, 0, 6)
    assert (store.night_dir("20261011") / "keogram.jpg").stat().st_nlink == 1


def test_only_the_chosen_camera(tmp_path: Path) -> None:
    db, images = make_indi(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO camera (id, uuid, name) VALUES (2, 'other', 'libcamera_imx477')")
    _jpeg(images / "ccd_other/exposures/20261011/night/11_23/ccd2_20261011_230000.jpg")
    con.execute(
        "INSERT INTO image (filename, createDate, dayDate, exposure, gain, night, adu, camera_id)"
        " VALUES ('ccd_other/exposures/20261011/night/11_23/ccd2_20261011_230000.jpg',"
        " '2026-10-11 23:00:00.000000', '2026-10-11', 10, 100, 1, 20, 2)"
    )
    con.commit()
    con.close()
    assert IndiAllskySettings.load(db, 2).camera == "rpi-hq"
    imp, store = make_importer(tmp_path, db, camera_id=2)
    r = imp.run()
    assert (r.images, r.products) == (1, 0)
    assert [f.name for f in store.read_index("20261011")] == ["ccd2_20261011_230000.jpg"]


def _args(tmp_path: Path, db: Path, **kw: object) -> argparse.Namespace:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    path = tmp_path / "settings.json"
    if not path.exists():
        AgentSettings(timezone="Europe/Vienna").save(path)
    base: dict[str, object] = {"db": db, "images": None, "camera": None, "data": data,
                               "settings": path, "tz": None, "profile": None, "copy": False,
                               "nights": None, "dry_run": False}  # fmt: skip
    return argparse.Namespace(**(base | kw))


def test_cli_saves_location_retention_and_backlog(tmp_path: Path) -> None:
    db, _ = make_indi(tmp_path)
    args = _args(tmp_path, db)
    assert run_import_indi(args) == 0
    s = AgentSettings.load(args.settings)
    assert (s.latitude, s.longitude) == (50.03601, 10.38951)
    # SPEC §4.5: at least indi-allsky's retention, its images and products apart.
    assert s.keep_days >= 30
    assert s.keep_product_days >= 365
    backlog = Backlog(args.data / BACKLOG_NAME)
    assert backlog.next() == "20261010"  # SPEC §6.7: announced to the hub


def test_cli_wants_a_camera_when_several_have_images(tmp_path: Path) -> None:
    db, _ = make_indi(tmp_path)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO camera (id, uuid, name) VALUES (2, 'other', 'libcamera_imx477')")
    con.execute(
        "INSERT INTO image (filename, createDate, dayDate, exposure, gain, night, adu, camera_id)"
        " VALUES ('x.jpg', '2026-10-11 23:00:00.000000', '2026-10-11', 10, 100, 1, 20, 2)"
    )
    con.commit()
    con.close()
    assert run_import_indi(_args(tmp_path, db)) == 1
    assert run_import_indi(_args(tmp_path, db, camera=1, dry_run=True)) == 0
    assert run_import_indi(_args(tmp_path, db, camera=7)) == 1
    assert run_import_indi(_args(tmp_path, tmp_path / "missing.sqlite")) == 1

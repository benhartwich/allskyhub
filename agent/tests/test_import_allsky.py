"""Taking over an existing Allsky installation (roadmap L18)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image as PILImage

from allskyhub_agent.cli import run_import
from allskyhub_agent.detect.events import EventStore
from allskyhub_agent.importer.allsky import AllskyImporter, AllskySettings, coordinate
from allskyhub_agent.importer.backlog import Backlog
from allskyhub_agent.products.build import night_products
from allskyhub_agent.settings import AgentSettings
from allskyhub_agent.store.images import ImageStore
from allskyhub_protocol import EventKind, Mode

BERLIN = ZoneInfo("Europe/Berlin")
VIENNA = ZoneInfo("Europe/Vienna")


def _jpeg(path: Path, value: int = 40) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.fromarray(np.full((90, 160, 3), value, dtype=np.uint8)).save(path, "JPEG")


def make_allsky(root: Path) -> Path:
    home = root / "allsky"
    (home / "config" / "myFiles").mkdir(parents=True)
    (home / "config" / "settings.json").write_text(json.dumps({
        "latitude": "48.305840N", "longitude": "14.286400E", "angle": -6,
        "location": "Linz", "owner": "Ben", "camera": "ZWO ASI678MC",
        "cameratype": "ZWO", "cameramodel": "ASI678MC", "lens": "2.5 mm",
    }))  # fmt: skip
    day = home / "images" / "20261011"
    for stamp in ("20261011070000", "20261011213000", "20261012031500"):
        _jpeg(day / f"image-{stamp}.jpg")
    _jpeg(day / "thumbnails" / "image-20261011213000.jpg")  # Allsky's small ones are ignored
    _jpeg(day / "keogram" / "keogram-20261011.jpg")
    _jpeg(day / "startrails" / "startrails-20261011.jpg")
    (day / "allsky-20261011.mp4").write_bytes(b"\x00" * 100)
    _jpeg(day / "meteors" / "meteors-20261012031500.jpg", 200)
    (day / "meteors" / "meteors-20261012031500.json").write_text(json.dumps([{
        "time": "20261012031500", "file": "meteors-20261012031500.jpg", "length": 106.6,
        "angle": 146.1, "peak": 38, "showers": ["Orionids"],
    }]))  # fmt: skip
    (home / "images" / "image.jpg").write_bytes(b"x")  # not a night folder
    con = sqlite3.connect(home / "config" / "myFiles" / "allsky.db")
    con.execute(
        "CREATE TABLE allsky_image (id INTEGER, timestamp INTEGER, AS_CAMERAIMAGE TEXT,"
        " AS_DAY_OR_NIGHT TEXT, AS_EXPOSURE_US REAL, AS_GAIN REAL, AS_TEMPERATURE_C REAL,"
        " AS_MEAN REAL)"
    )
    con.execute(
        "INSERT INTO allsky_image VALUES (1, 0, 'image-20261012031500.jpg', 'NIGHT',"
        " 30000000, 150, 12.5, 0.21)"
    )
    con.execute(
        "CREATE TABLE allsky_skyquality (id INTEGER, timestamp INTEGER, AS_SKYQUALITY_SQM REAL,"
        " AS_SKYQUALITY_CLOUD REAL, AS_SKYQUALITY_STARS REAL)"
    )
    night = int(datetime(2026, 10, 11, 21, 0, tzinfo=UTC).timestamp())  # 23:00 local
    noon = int(datetime(2026, 10, 11, 10, 0, tzinfo=UTC).timestamp())
    con.execute("INSERT INTO allsky_skyquality VALUES (1, ?, 20.5, 20.0, 140)", (night,))
    con.execute("INSERT INTO allsky_skyquality VALUES (2, ?, 13.8, 90.0, 3)", (noon,))
    con.commit()
    con.close()
    return home


def make_importer(
    tmp_path: Path, home: Path, copy: bool = False
) -> tuple[AllskyImporter, ImageStore, EventStore]:
    store = ImageStore(tmp_path / "data", VIENNA)
    events = EventStore(store)
    imp = AllskyImporter(home, store, events, VIENNA, "zwo-asi678mc", source_tz=BERLIN,
                         copy=copy, progress=lambda _: None)  # fmt: skip
    return imp, store, events


def test_settings_and_coordinates(tmp_path: Path) -> None:
    s = AllskySettings.load(make_allsky(tmp_path))
    assert (s.latitude, s.longitude, s.camera, s.location) == (
        48.30584,
        14.28640,
        "zwo-asi678mc",
        "Linz",
    )
    assert coordinate("33.9S") == -33.9
    assert coordinate("70.5W") == -70.5
    assert coordinate("nonsense") is None


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    imp, store, _ = make_importer(tmp_path, make_allsky(tmp_path))
    r = imp.run(dry=True)
    assert (r.images, r.products, r.meteors) == (3, 3, 1)
    assert r.nights == {"20261010", "20261011"}
    assert store.nights() == []


def test_import_places_everything_and_is_repeatable(tmp_path: Path) -> None:
    home = make_allsky(tmp_path)
    imp, store, events = make_importer(tmp_path, home)
    r = imp.run()
    assert (r.images, r.products, r.meteors, r.linked) == (3, 3, 1, 6)
    # 07:00 belongs to the night before; 21:30 and 03:15 to the night of the 11th (SPEC §4.5).
    assert [f.name for f in store.read_index("20261010")] == ["image-20261011070000.jpg"]
    night = store.read_index("20261011")
    assert [f.name for f in night] == ["image-20261011213000.jpg", "image-20261012031500.jpg"]
    late = night[1]
    assert late.captured_at == datetime(2026, 10, 12, 1, 15, tzinfo=UTC)
    assert (late.mode, late.exposure_us, late.gain, late.sensor_temp_c) == (
        Mode.NIGHT,
        30_000_000,
        150.0,
        12.5,
    )
    assert night[0].mode is Mode.NIGHT  # from the sun, no database row
    src = home / "images" / "20261011" / "image-20261012031500.jpg"
    dest = store.night_dir("20261011") / "image-20261012031500.jpg"
    assert src.stat().st_ino == dest.stat().st_ino  # a hard link, no copy
    assert store.path_for("20261011", dest.name, thumbnail=True) is not None
    products = night_products(store, "20261011")
    assert products is not None
    assert {p.name for p in products.products} == {"keogram.jpg", "startrails.jpg", "timelapse.mp4"}
    (meteor,) = events.night_events("20261011")
    assert meteor.kind is EventKind.METEOR
    assert meteor.imported
    assert meteor.has_image
    assert meteor.data["shower"] == "Orionids"
    assert meteor.data["direction_deg"] == 56.1  # as Streak.direction_deg would give
    assert meteor.end > meteor.start  # the exposure from Allsky's database

    (sky,) = store.read_sky("20261011")  # the noon sample is day: left out (SPEC §6.3)
    assert (sky.cloud_cover, sky.sqm_mag, sky.stars) == (0.2, 20.5, 140)
    assert r.sky == 1

    again = imp.run()
    assert again.sky == 0
    assert (again.images, again.products, again.meteors) == (0, 0, 0)
    assert again.images_skipped == 3
    assert again.nights == set()  # nothing new: nothing to announce to the hub again
    assert len(store.read_index("20261011")) == 2
    assert len(events.night_events("20261011")) == 1


def test_copy_when_asked(tmp_path: Path) -> None:
    imp, store, _ = make_importer(tmp_path, make_allsky(tmp_path), copy=True)
    r = imp.run(only={"20261011"})
    assert (r.linked, r.copied) == (0, 6)
    dest = store.night_dir("20261011") / "keogram.jpg"
    assert dest.stat().st_nlink == 1


def test_backlog_keeps_nights_until_done(tmp_path: Path) -> None:
    b = Backlog(tmp_path / "hub_backlog.json")
    b.add(["20261011", "20261010"])
    b.add(["20261011"])
    assert b.next() == "20261010"
    b.done("20261010")
    assert Backlog(tmp_path / "hub_backlog.json").next() == "20261011"
    b.done("20261011")
    assert b.next() is None
    assert not (tmp_path / "hub_backlog.json").exists()


def test_import_keeps_what_it_took_over(tmp_path: Path) -> None:
    # SPEC §4.5: retention must not delete the imported nights the next morning.
    home = make_allsky(tmp_path)
    settings = json.loads((home / "config" / "settings.json").read_text())
    (home / "config" / "settings.json").write_text(json.dumps(settings | {"daystokeep": 60}))
    data = tmp_path / "data"
    data.mkdir()
    path = tmp_path / "settings.json"
    AgentSettings(timezone="Europe/Vienna").save(path)
    args = argparse.Namespace(source=home, data=data, settings=path, tz=None, profile=None,
                              copy=False, nights=None, dry_run=False)  # fmt: skip
    assert run_import(args) == 0
    s = AgentSettings.load(path)
    assert s.keep_days >= 60  # Allsky's 60, or more if the oldest night is older
    assert s.latitude == 48.30584


def test_lightning_and_episodes(tmp_path: Path) -> None:
    """Benjamin's lightning and NLC modules (roadmap L18): bolts with storms, candidate
    pictures grouped into episodes; only for the nights that are taken over."""
    home = make_allsky(tmp_path)
    web = home / "html" / "allsky"
    _jpeg(web / "lightning" / "lightning-20261011223000.jpg", 220)
    _jpeg(web / "lightning" / "lightning-20261011224500.jpg", 220)
    (web / "lightning" / "lightning.json").write_text(json.dumps([
        {"time": "20261011223000", "file": "lightning-20261011223000.jpg", "area": 1440,
         "peak": 153, "period": "night", "exposure_ms": 2000},
        {"time": "20261011224500", "file": "lightning-20261011224500.jpg", "area": 2880,
         "peak": 204, "period": "night", "exposure_ms": 2000},
        {"time": "20260801224500", "file": "gone.jpg", "area": 10, "peak": 1},  # no folder
    ]))  # fmt: skip
    for stamp in ("20261012_052000", "20261012_052500", "20261012_061000"):
        _jpeg(web / "nlc" / f"nlc-{stamp}.jpg", 120)
    (web / "nlc.json").write_text(json.dumps([
        {"t": 0, "index": 4.5, "blue": 47.9, "dir": "E", "nlc": True,
         "thumb": "nlc-20261012_052500.jpg"},
    ]))  # fmt: skip
    imp, _, events = make_importer(tmp_path, home)
    dry = imp.run(dry=True)
    assert (dry.lightning, dry.episodes) == (2, 2)
    report = imp.run()
    assert (report.lightning, report.episodes) == (2, 2)
    got = sorted(events.night_events("20261011"), key=lambda e: (e.kind.value, e.start))
    bolts = [e for e in got if e.kind is EventKind.LIGHTNING]
    assert [e.data["storm_flashes"] for e in bolts] == [1, 2]
    assert {e.data["storm"] for e in bolts} == {"storm-20261011T203000Z"}
    assert bolts[1].data["area_frac"] == 0.2  # 2880 of 160 x 90 pixels
    assert bolts[0].imported
    assert bolts[0].has_image
    nlc = [e for e in got if e.kind is EventKind.NLC]
    assert len(nlc) == 2  # 05:20 + 05:25 one episode, 06:10 another
    assert nlc[0].data["frames"] == 2
    assert nlc[0].data["peak_index"] == 4.5
    assert nlc[0].data["azimuth_deg"] == 90.0
    assert nlc[1].data["peak_index"] is None
    again = imp.run()
    assert (again.lightning, again.episodes) == (0, 0)

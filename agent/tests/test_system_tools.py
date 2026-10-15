"""Pause, delete a night, camera check, following the log (SPEC §4.1, §7)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from allskyhub_agent.live import LiveState
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.watchdog import Heartbeat, WatchState, decide
from allskyhub_agent.web.archive import LocalArchive
from allskyhub_agent.web.check import CheckFacts, run_checks, weather_checks
from allskyhub_agent.web.support import RecentLog
from allskyhub_protocol import EnvStatus, FrameInfo, HeaterStatus, Mode, NightDeleted

TZ = ZoneInfo("Europe/Vienna")
NOW = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)


def test_pause_survives_a_restart(tmp_path: Path) -> None:
    flag = tmp_path / "capture-paused"
    live = LiveState(flag)
    assert not live.paused
    live.set_paused(True)
    assert flag.exists()
    assert LiveState(flag).paused  # after a restart
    assert live.health()["paused"] is True
    live.set_paused(False)
    assert not flag.exists()
    assert "paused" not in live.health()


def test_a_pause_is_no_stall() -> None:
    st = WatchState()
    t0 = NOW
    for m in range(0, 120, 5):
        assert decide(st, t0 + timedelta(minutes=m), 5, True, False, paused=True) is None
    # Resumed: the ladder counts from the end of the pause.
    assert decide(st, t0 + timedelta(minutes=130), 5, True, False) is None
    assert decide(st, t0 + timedelta(minutes=140), 5, True, False) == "restart"
    clock = [0.0]
    paused = [True]
    hb = Heartbeat(lambda: 0, max_age_s=600, grace_s=60, notify=lambda _m: True,
                   monotonic=lambda: clock[0], paused=lambda: paused[0])  # fmt: skip
    clock[0] = 5000
    assert hb.beat()  # no frames for a long time, but paused
    paused[0] = False
    clock[0] = 5700
    assert not hb.beat()


def _night(store: ImageStore, at: datetime) -> str:
    saved = store.save(np.zeros((9, 16, 3), dtype=np.uint8), at)
    store.append_index(
        FrameInfo(
            captured_at=at,
            night_id=saved.night_id,
            name=saved.name,
            mode=Mode.NIGHT,
            exposure_us=1,
            gain=0,
            mean=0.2,
            sun_elevation=-20,
            profile="sim",
        )
    )
    return saved.night_id


def test_delete_a_night(tmp_path: Path) -> None:
    store = ImageStore(tmp_path, TZ)
    old = _night(store, NOW - timedelta(days=3))
    current = _night(store, NOW)
    sent: list[NightDeleted] = []
    archive = LocalArchive(store, on_deleted=sent.append)
    assert archive.delete_night(current, NOW) == "current"
    assert archive.delete_night("20200101", NOW) == "not_found"
    assert archive.delete_night(old, NOW) is None
    assert not store.night_dir(old).exists()
    assert sent == [NightDeleted(night_id=old)]
    assert [n["night_id"] for n in archive.nights()] == [current]
    # Sent again after every hello for 60 days.
    assert LocalArchive(store).deleted_nights(NOW + timedelta(days=59)) == sent
    assert LocalArchive(store).deleted_nights(NOW + timedelta(days=61)) == []


def test_camera_check() -> None:
    frame = FrameInfo(captured_at=NOW - timedelta(minutes=2), night_id="20261013", name="x.jpg",
                      mode=Mode.NIGHT, exposure_us=60_000_000, gain=0, mean=0.2,
                      sun_elevation=-20, profile="sim")  # fmt: skip
    facts = CheckFacts(
        power=lambda: ["under_voltage_seen"],
        time_trusted=lambda: False,
        disk_free_pct=lambda: 55.0,
        last_frame=lambda: frame,
        paused=lambda: False,
        calibration=lambda: {"mask_learned_at": "2026-10-12T22:00:00Z", "orientation": None},
        hub=lambda: (True, True),
    )
    got = {c["id"]: c["state"] for c in run_checks(facts, NOW)}
    assert got == {"power": "warn", "clock": "warn", "disk": "ok", "frames": "ok", "mask": "ok",
                   "orientation": "warn", "hub": "ok"}  # fmt: skip
    stalled = run_checks(facts, NOW + timedelta(minutes=20))
    assert next(c for c in stalled if c["id"] == "frames")["state"] == "fail"


def test_weather_and_heater_check() -> None:
    """SPEC §7.11: the sensor's values or why it fails; a heater without a pin warns."""

    def facts(
        env: EnvStatus | None, heater: tuple[str, int | None, HeaterStatus | None]
    ) -> CheckFacts:
        return CheckFacts(power=lambda: [], time_trusted=lambda: True,
                          disk_free_pct=lambda: 50.0, last_frame=lambda: None,
                          paused=lambda: False, env=lambda: env, heater=lambda: heater)  # fmt: skip

    good = EnvStatus(at=NOW, temperature_c=8.4, humidity_pct=91.0, pressure_hpa=1012.3,
                     dewpoint_c=7.1)  # fmt: skip
    on = HeaterStatus(on=True, reason="dew")
    got = weather_checks(facts(good, ("auto", 17, on)))
    assert [(c["id"], c["state"]) for c in got] == [("weather", "ok"), ("heater", "ok")]
    assert "Taupunkt 7,1 °C" in got[0]["detail"]
    assert "nahe am Taupunkt" in got[1]["detail"]
    got = weather_checks(facts(EnvStatus(error="not_found"), ("auto", None, None)))
    assert [(c["id"], c["state"]) for c in got] == [("weather", "fail"), ("heater", "warn")]
    assert weather_checks(facts(None, ("off", None, None))) == []


def test_follow_the_log() -> None:
    log = RecentLog(size=3)
    rec = logging.LogRecord("agent", logging.INFO, "", 0, "%s", ("a",), None)
    log.emit(rec)
    seq, lines = log.since(0)
    assert (seq, len(lines)) == (1, 1)
    for x in "bcde":
        log.emit(logging.LogRecord("agent", logging.INFO, "", 0, "%s", (x,), None))
    seq2, lines = log.since(seq)
    assert seq2 == 5
    assert [ln[-1] for ln in lines] == ["c", "d", "e"]  # only what is still kept
    assert log.since(seq2) == (5, [])

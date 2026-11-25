"""SPEC §6.3 `waiting`: the place from setup, or a camera failing 3 times in a row."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from allskyhub_agent.adapters.camera import CameraError, CaptureRequest, Frame
from allskyhub_agent.adapters.sim import SimCamera
from allskyhub_agent.core.clock import SimClock
from allskyhub_agent.core.exposure import AutoExposure
from allskyhub_agent.profiles import get_profile
from allskyhub_agent.runner import Location, Runner
from allskyhub_agent.store.images import ImageStore
from allskyhub_agent.waiting import WaitState
from allskyhub_protocol import Envelope, Mode, Status, Waiting, parse_envelope

T0 = datetime(2026, 11, 25, 18, 0, tzinfo=UTC)


def test_location_then_camera_from_the_third_failure() -> None:
    now = [T0]
    wait = WaitState(now=lambda: now[0])
    assert wait.current is None
    wait.waiting_for_location(True)
    now[0] += timedelta(seconds=5)
    wait.waiting_for_location(True)  # still the first moment
    assert wait.current == Waiting(reason="location", since=T0)
    wait.waiting_for_location(False)
    assert wait.current is None
    for i in range(2):
        now[0] = T0 + timedelta(minutes=i)
        wait.camera_failed("rpicam-still failed (255): no cameras available")
        assert wait.current is None  # one or two failures are not reported
    now[0] = T0 + timedelta(minutes=5)
    wait.camera_failed("x" * 300)
    current = wait.current
    assert current is not None
    assert current.reason == "camera"
    assert current.since == T0  # the first failure of the row
    assert current.message == "x" * 200
    wait.camera_ok()
    assert wait.current is None


def test_status_carries_it() -> None:
    status = Status(
        mode=Mode.DAY, exposure_us=0, gain=0.0, mean=0.0, uptime_s=60, time_trusted=True,
        waiting=Waiting(reason="location", since=T0),
    )  # fmt: skip
    back = parse_envelope(Envelope.wrap(status, ts=T0).model_dump_json()).body
    assert isinstance(back, Status)
    assert back.waiting == Waiting(reason="location", since=T0)


class _Failing(SimCamera):
    def __init__(self, fails: int, clock: SimClock) -> None:
        super().__init__(lambda: 0.01, width=64, height=36, clock=clock)
        self.fails = fails

    def capture(self, req: CaptureRequest) -> Frame:
        if self.fails > 0:
            self.fails -= 1
            raise CameraError("rpicam-still failed (255): no cameras available")
        return super().capture(req)


def test_the_capture_loop_reports_failures_in_a_row(tmp_path: Path) -> None:
    clock = SimClock(datetime(2026, 10, 6, 22, 0, tzinfo=UTC))
    profile = get_profile("sim")
    wait = WaitState(now=clock.now)
    seen: list[Waiting | None] = []
    camera = _Failing(3, clock)
    capture = camera.capture

    def watched(req: CaptureRequest) -> Frame:
        seen.append(wait.current)
        return capture(req)

    camera.capture = watched  # type: ignore[method-assign]
    runner = Runner(
        camera=camera,
        auto_exposure=AutoExposure(profile.exposure),
        store=ImageStore(tmp_path, ZoneInfo("Europe/Vienna")),
        clock=clock,
        location=Location(50.04, 10.39),
        profile=profile.id,
        waiting=wait,
    )
    assert runner.run(frames=1) == 1
    assert [w.reason if w else None for w in seen] == [None, None, None, "camera"]
    assert wait.current is None  # the good frame ends it

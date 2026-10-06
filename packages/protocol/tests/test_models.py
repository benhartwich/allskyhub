"""Protocol model tests (SPEC §6)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from allskyhub_protocol import (
    Command,
    CommandName,
    Envelope,
    Event,
    EventKind,
    FrameInfo,
    Hello,
    Mode,
    parse_envelope,
)

TS = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)


def test_roundtrip_frame() -> None:
    body = FrameInfo(
        captured_at=TS,
        night_id="20261006",
        name="image-20261006233000.jpg",
        mode=Mode.NIGHT,
        exposure_us=30_000_000,
        gain=120,
        mean=0.21,
        sun_elevation=-35.2,
        profile="zwo-asi678mc",
    )
    env = Envelope.wrap(body, ts=TS, msg_id="m1")
    back = parse_envelope(env.model_dump_json())
    assert back == env
    assert isinstance(back.body, FrameInfo)
    assert back.type == "frame"


def test_unknown_type_rejected() -> None:
    raw = '{"v":1,"type":"bogus","id":"x","ts":"2026-10-06T21:30:00Z","body":{}}'
    with pytest.raises(ValidationError):
        parse_envelope(raw)


def test_type_body_mismatch_rejected() -> None:
    hello = Hello(device_id="abcdef0123", profile="rpi-hq", agent_version="0.1.0")
    with pytest.raises(ValidationError):
        Envelope(type="status", id="x", ts=TS, body=hello)


def test_wrong_version_rejected() -> None:
    raw = Envelope.wrap(Command(name=CommandName.RESTART), ts=TS).model_dump_json()
    with pytest.raises(ValidationError):
        parse_envelope(raw.replace('"v":1', '"v":2'))


def test_naive_timestamp_rejected() -> None:
    raw = '{"v":1,"type":"command","id":"x","ts":"2026-10-06T21:30:00","body":{"name":"restart"}}'
    with pytest.raises(ValidationError):
        parse_envelope(raw)


def test_event_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError):
        Event(kind=EventKind.METEOR, start=TS, end=TS - timedelta(seconds=1), confidence=0.9)


def test_extra_fields_rejected() -> None:
    raw = (
        '{"v":1,"type":"command","id":"x","ts":"2026-10-06T21:30:00Z",'
        '"body":{"name":"restart","oops":1}}'
    )
    with pytest.raises(ValidationError):
        parse_envelope(raw)

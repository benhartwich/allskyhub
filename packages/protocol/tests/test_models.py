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
    hello = Hello(device_id="a" * 26, profile="rpi-hq", agent_version="0.1.0")
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
        Event(
            id="meteor-20261006T213000Z",
            night_id="20261006",
            kind=EventKind.METEOR,
            start=TS,
            end=TS - timedelta(seconds=1),
            confidence=0.9,
        )


def test_extra_fields_rejected() -> None:
    raw = (
        '{"v":1,"type":"command","id":"x","ts":"2026-10-06T21:30:00Z",'
        '"body":{"name":"restart","oops":1}}'
    )
    with pytest.raises(ValidationError):
        parse_envelope(raw)


def test_products_roundtrip_and_name_kind_check() -> None:
    from allskyhub_protocol import ProductFile, ProductKind, Products

    body = Products(
        night_id="20261006",
        products=[
            ProductFile(
                kind=ProductKind.KEOGRAM,
                name="keogram.jpg",
                content_type="image/jpeg",
                size=1234,
                thumbnail=True,
            ),
            ProductFile(
                kind=ProductKind.TIMELAPSE,
                name="timelapse.mp4",
                content_type="video/mp4",
                size=99_000_000,
                thumbnail=True,
                duration_s=21.4,
            ),
        ],
    )
    back = parse_envelope(Envelope.wrap(body, ts=TS).model_dump_json())
    assert back.body == body
    with pytest.raises(ValidationError):
        ProductFile(
            kind=ProductKind.KEOGRAM,
            name="timelapse.mp4",
            content_type="video/mp4",
            size=1,
            thumbnail=False,
        )
    with pytest.raises(ValidationError):
        Products(night_id="20261006", products=[])


def test_upload_product_args() -> None:
    from allskyhub_protocol import FrameVariant, UploadProductArgs

    a = UploadProductArgs.model_validate({"night_id": "20261006", "name": "timelapse.mp4"})
    assert a.variant is FrameVariant.FULL
    with pytest.raises(ValidationError):
        UploadProductArgs.model_validate({"night_id": "20261006", "name": "../etc/passwd"})


def test_event_ids_and_upload_event_args() -> None:
    from allskyhub_protocol import UploadEventArgs, event_id

    assert event_id(EventKind.METEOR, TS) == "meteor-20261006T213000Z"
    assert event_id(EventKind.SKY_QUALITY, TS, seq=2) == "skyquality-20261006T213000Z-2"
    ev = Event(
        id=event_id(EventKind.METEOR, TS),
        night_id="20261006",
        kind=EventKind.METEOR,
        start=TS,
        end=TS + timedelta(seconds=2),
        confidence=0.8,
        has_image=True,
        data={"length_px": 120, "peak": 0.9, "frames": 2, "direction_deg": 45.0, "shower": None},
    )
    assert parse_envelope(Envelope.wrap(ev, ts=TS).model_dump_json()).body == ev
    with pytest.raises(ValidationError):  # id of another kind
        Event(id="lightning-20261006T213000Z", night_id="20261006", kind=EventKind.METEOR,
              start=TS, end=TS, confidence=0.5)  # fmt: skip
    a = UploadEventArgs.model_validate({"night_id": "20261006", "event_id": ev.id})
    assert a.variant.value == "full"
    with pytest.raises(ValidationError):
        UploadEventArgs.model_validate({"night_id": "20261006", "event_id": "../../etc"})

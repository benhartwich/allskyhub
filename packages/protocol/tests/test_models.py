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
    Lens,
    Mode,
    Orientation,
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


def test_unknown_fields_are_ignored() -> None:
    """SPEC §6.1: a newer peer may add fields; an older one must still understand it."""
    from allskyhub_protocol import PublicUpdate, TokenResponse

    raw = (
        '{"v":1,"type":"command","id":"x","ts":"2026-10-06T21:30:00Z",'
        '"body":{"name":"restart","oops":1}}'
    )
    env = parse_envelope(raw)
    assert isinstance(env.body, Command)
    assert env.body.name == "restart"
    token = TokenResponse.model_validate(
        {"access_token": "t", "expires_in": 60, "hub_keys": [], "something_new": True}
    )
    assert token.expires_in == 60
    # What an owner typed is checked strictly (SPEC §6.8).
    with pytest.raises(ValidationError):
        PublicUpdate.model_validate({"enabled": True, "titel": "typo"})


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
    # SPEC §6.1: a kind this side does not know yet is skipped, not the whole message.
    newer = Products.model_validate(
        {
            "night_id": "20261006",
            "products": [
                {"kind": "day_timelapse", "name": "day-timelapse.mp4",
                 "content_type": "video/mp4", "size": 5, "thumbnail": True},
                {"kind": "keogram", "name": "keogram.jpg", "content_type": "image/jpeg",
                 "size": 5, "thumbnail": True},
            ],
        }
    )  # fmt: skip
    assert [p.kind for p in newer.products] == [ProductKind.KEOGRAM]
    only_new = Products.model_validate(
        {"night_id": "20261006", "products": [{"kind": "keolapse", "name": "x.jpg",
                                               "content_type": "image/jpeg", "size": 1,
                                               "thumbnail": False}]}
    )  # fmt: skip
    assert only_new.products == []


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


def test_set_settings_args() -> None:
    from allskyhub_protocol import SetSettingsArgs

    a = SetSettingsArgs.model_validate({"latitude": 48.1, "longitude": 14.4, "night_delay_s": 5})
    assert (a.latitude, a.night_delay_s, a.camera) == (48.1, 5, None)
    for bad in (
        {"latitude": 48.1},
        {"timezone": "Nowhere/Town"},
        {"camera": "nikon"},
        {"day_delay_s": -1},
        {"unknown": {"not": "a scalar"}},
    ):
        with pytest.raises(ValidationError):
            SetSettingsArgs.model_validate(bad)


SCHEMA_JSON = [
    {"key": "location", "type": "location", "label": "Standort", "group": "Standort"},
    {"key": "timezone", "type": "string", "label": "Zeitzone", "group": "Standort"},
    {
        "key": "night_delay_s",
        "type": "number",
        "label": "Pause nachts",
        "group": "Aufnahme",
        "unit": "s",
        "min": 0,
        "max": 3600,
    },
    {
        "key": "max_gain",
        "type": "int",
        "label": "Höchster Gain",
        "group": "Aufnahme",
        "min": 0,
        "max": 600,
        "step": 1,
    },
    {
        "key": "flip",
        "type": "choice",
        "label": "Spiegeln",
        "group": "Bild",
        "choices": [{"value": "none", "label": "Nein"}, {"value": "h", "label": "Horizontal"}],
    },
    {
        "key": "overlay",
        "type": "bool",
        "label": {"de": "Einblendung", "en": "Overlay"},
        "group": {"de": "Bild", "en": "Image"},
        "help": {"de": "Text im Bild"},
    },
    {
        "key": "title",
        "type": "string",
        "label": "Titel",
        "group": "Bild",
        "max_length": 5,
        "nullable": True,
    },
]


def test_settings_schema_in_hello_and_checks() -> None:
    """SPEC §6.9: one schema, the same checks for hub and device."""
    from allskyhub_protocol import Hello, SetSettingsArgs, SettingSpec, invalid_settings

    hello = Hello.model_validate(
        {
            "device_id": "a" * 26,
            "profile": "sim",
            "agent_version": "1",
            "settings_schema": SCHEMA_JSON,
        }
    )
    schema = hello.settings_schema
    assert [s.key for s in schema][:2] == ["location", "timezone"]
    good = {
        "latitude": 48.1,
        "longitude": 14.4,
        "max_gain": 300,
        "flip": "h",
        "overlay": True,
        "title": None,
    }
    assert invalid_settings(schema, good) == []
    assert SetSettingsArgs.model_validate(good).model_extra == {
        "max_gain": 300,
        "flip": "h",
        "overlay": True,
        "title": None,
    }
    bad = {
        "latitude": 48.1,
        "max_gain": 2.5,
        "flip": "v",
        "overlay": 1,
        "title": "zu lang",
        "night_delay_s": 4000,
        "nope": 1,
    }
    assert invalid_settings(schema, bad) == [
        "flip",
        "latitude",
        "max_gain",
        "night_delay_s",
        "nope",
        "overlay",
        "title",
    ]
    assert (
        Hello.model_validate(
            {"device_id": "a" * 26, "profile": "p", "agent_version": "1"}
        ).settings_schema
        == []
    )
    for broken in (
        {"key": "x", "type": "choice", "label": "X", "group": "G"},
        {"key": "x", "type": "bool", "label": "X", "group": "G", "min": 1},
        {"key": "x", "type": "location", "label": "X", "group": "G"},
        {"key": "X", "type": "bool", "label": "X", "group": "G"},
    ):
        with pytest.raises(ValidationError):
            SettingSpec.model_validate(broken)


def test_settings_schema_texts_problems_and_numbers() -> None:
    """SPEC §6.9: German or German + English texts, unusable schemas, NaN and infinity."""
    from allskyhub_protocol import Hello, SettingSpec, invalid_settings, localized, schema_problems

    hello = Hello.model_validate(
        {
            "device_id": "a" * 26,
            "profile": "sim",
            "agent_version": "1",
            "settings_schema": SCHEMA_JSON,
        }
    )
    overlay = next(s for s in hello.settings_schema if s.key == "overlay")
    assert localized(overlay.label, "en") == "Overlay"
    assert localized(overlay.label) == "Einblendung"
    assert overlay.help is not None
    assert localized(overlay.help, "en") == "Text im Bild"  # falls back to German
    assert schema_problems(hello.settings_schema) == []
    dup = [*hello.settings_schema, hello.settings_schema[1]]
    lat = SettingSpec.model_validate(
        {"key": "latitude", "type": "number", "label": "Breite", "group": "Standort"}
    )
    assert schema_problems([*dup, lat]) == [
        "duplicate key timezone",
        "latitude is part of location",
    ]
    for v in (float("nan"), float("inf")):
        assert invalid_settings(hello.settings_schema, {"night_delay_s": v}) == ["night_delay_s"]
        assert invalid_settings(hello.settings_schema, {"latitude": v, "longitude": 1.0}) == [
            "latitude"
        ]


def test_write_only_setting() -> None:
    """SPEC §6.9: a secret is a string field that is never reported."""
    from allskyhub_protocol import SettingSpec

    key = SettingSpec.model_validate(
        {
            "key": "mqtt_password",
            "type": "string",
            "label": "Passwort",
            "group": "MQTT",
            "write_only": True,
            "nullable": True,
        }
    )
    assert key.write_only
    with pytest.raises(ValidationError):
        SettingSpec.model_validate(
            {"key": "x", "type": "int", "label": "X", "group": "G", "write_only": True}
        )


def test_orientation_lens_is_optional() -> None:
    """SPEC §6.3: older agents send no lens; unknown fields in it are ignored."""
    base = {"north_deg": 12.5, "mirrored": True, "solved_at": "2026-10-15T20:00:00Z",
            "stars": 40, "rms_deg": 0.3}  # fmt: skip
    assert Orientation.model_validate(base).lens is None
    lens = {"cx": 960.0, "cy": 540.0, "a1": 600.0, "a3": -20.0, "tilt_east_deg": 0.4,
            "tilt_north_deg": -0.2, "width": 1920, "height": 1080, "k5": 1}  # fmt: skip
    parsed = Orientation.model_validate({**base, "lens": lens})
    assert parsed.lens == Lens(cx=960.0, cy=540.0, a1=600.0, a3=-20.0, tilt_east_deg=0.4,
                               tilt_north_deg=-0.2, width=1920, height=1080)  # fmt: skip

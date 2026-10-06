"""Device identity and device API bodies (SPEC §6.1, §6.2, §6.5, §6.6)."""

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from allskyhub_protocol import (
    PAIRING_CODE_ALPHABET,
    Command,
    CommandName,
    Envelope,
    FrameVariant,
    Purpose,
    RegisterResponse,
    UploadFrameArgs,
    b64url,
    b64url_decode,
    device_id_from_public_key,
    normalize_pairing_code,
    parse_envelope,
    signing_payload,
)
from allskyhub_protocol.device_api import DEVICE_ID_PATTERN

TS = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)

# RFC 8032 §7.1, test 1: public key.
RFC8032_PUB = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")


def test_device_id_is_stable_base32_of_key_hash() -> None:
    # SPEC §6.2: base32(sha256(key)[:16]), lowercase, no padding.
    device_id = device_id_from_public_key(RFC8032_PUB)
    assert len(device_id) == 26
    assert device_id == device_id.lower()
    assert device_id == device_id_from_public_key(RFC8032_PUB)
    assert re.fullmatch(DEVICE_ID_PATTERN, device_id)


def test_device_id_rejects_wrong_key_length() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        device_id_from_public_key(b"\x00" * 31)


def test_signing_payload_is_exact() -> None:
    payload = signing_payload(Purpose.TOKEN, "a" * 26, "nonce-0123456789")
    assert payload == b"allskyhub-v1\ntoken\n" + b"a" * 26 + b"\nnonce-0123456789"


def test_b64url_roundtrip_without_padding() -> None:
    for n in range(1, 6):
        data = bytes(range(n))
        text = b64url(data)
        assert "=" not in text
        assert b64url_decode(text) == data
    with pytest.raises(ValueError, match="base64url"):
        b64url_decode("not/base64+url=")


def test_pairing_code_alphabet_and_normalization() -> None:
    assert not set("01ILO") & set(PAIRING_CODE_ALPHABET)
    assert normalize_pairing_code(" abc-def ") == "ABCDEF"
    RegisterResponse(device_id="a" * 26, paired=False, pairing_code="ABCDEF", expires_in=900)
    with pytest.raises(ValidationError):
        RegisterResponse(device_id="a" * 26, paired=False, pairing_code="ABC0EF")


def test_upload_frame_command_roundtrip() -> None:
    args = UploadFrameArgs(night_id="20261006", name="image-20261006233000.jpg")
    assert args.variant is FrameVariant.FULL
    cmd = Command(name=CommandName.UPLOAD_FRAME, args=args.model_dump(mode="json"))
    back = parse_envelope(Envelope.wrap(cmd, ts=TS).model_dump_json())
    assert isinstance(back.body, Command)
    assert UploadFrameArgs.model_validate(back.body.args) == args


def test_upload_frame_rejects_path_tricks() -> None:
    with pytest.raises(ValidationError):
        UploadFrameArgs(night_id="20261006", name="../etc/passwd")

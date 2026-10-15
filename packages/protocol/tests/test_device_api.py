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


def test_token_response_hub_keys_default_empty() -> None:
    """SPEC §6.2 step 6: older hubs send no hub_keys."""
    from allskyhub_protocol import HubKey, TokenResponse

    token = "t"  # noqa: S105 - a test value
    assert TokenResponse(access_token=token, expires_in=3600).hub_keys == []
    key = HubKey(kid="k1", public_key="A" * 43)
    assert TokenResponse(access_token=token, expires_in=1, hub_keys=[key]).hub_keys == [key]
    with pytest.raises(ValidationError):
        HubKey(kid="k1", public_key="short")


def test_local_access_verifier() -> None:
    """SPEC §6.5 local_access: Argon2id at least m=19456, t=2, or null."""
    from allskyhub_protocol import LocalAccessArgs

    good = "$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"
    assert LocalAccessArgs(verifier=good).verifier == good
    assert LocalAccessArgs(verifier=None).verifier is None
    for bad in (
        "$argon2id$v=19$m=4096,t=2,p=1$c2FsdA$aGFzaA",
        "$argon2id$v=19$m=19456,t=1,p=1$c2FsdA$aGFzaA",
        "$argon2i$v=19$m=19456,t=2,p=1$c2FsdA$aGFzaA",
        "plain",
    ):
        with pytest.raises(ValidationError):
            LocalAccessArgs(verifier=bad)


def test_local_login_assertion_roundtrip() -> None:
    """SPEC §7.4 A step 4: payload.signature, Ed25519 over the fixed prefix."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from allskyhub_protocol import (
        LocalLoginClaims,
        b64url,
        encode_local_login_claims,
        local_login_signing_payload,
        split_local_login_assertion,
    )

    key = Ed25519PrivateKey.generate()
    claims = LocalLoginClaims(
        kid="k1", aud="a" * 26, sub="user-1", nonce="n" * 43, iat=1000, exp=1060
    )
    payload = encode_local_login_claims(claims)
    assert local_login_signing_payload(payload).startswith(b"allskyhub-v1\nlocal-login\n")
    assertion = f"{payload}.{b64url(key.sign(local_login_signing_payload(payload)))}"
    part, parsed, signature = split_local_login_assertion(assertion)
    assert parsed == claims
    key.public_key().verify(signature, local_login_signing_payload(part))
    for bad in ("x", "a.b.c", f"{payload}.!", f"{b64url(b'{}')}.{b64url(b'x')}"):
        with pytest.raises(ValueError, match=r"not|invalid"):
            split_local_login_assertion(bad)
    with pytest.raises(ValidationError):
        LocalLoginClaims(kid="k1", aud="a" * 26, sub="u", nonce="n" * 43, iat=0, exp=61)


def test_public_profile_limits() -> None:
    """SPEC §6.8: defaults, length limits, unknown keys."""
    from allskyhub_protocol import PublicProfile, PublicUpdate

    p = PublicProfile()
    assert p.sections.live
    assert not p.show_coordinates
    assert PublicProfile(title="  Pöstlingberg ").title == "Pöstlingberg"
    with pytest.raises(ValidationError):
        PublicProfile(title="x" * 101)
    with pytest.raises(ValidationError):
        PublicProfile.model_validate({"colour": "red"})
    assert PublicUpdate().enabled is None


def test_public_state_from_hub_ignores_what_it_does_not_know() -> None:
    """SPEC §6.1: a newer hub's answer with new sections or profile keys still parses."""
    import json

    from allskyhub_protocol import PublicState

    raw = json.dumps({
        "enabled": True, "url": "https://allskyhub.org/sky/x", "extra": 1,
        "profile": {"title": "Sternwarte", "banner": "new",
                    "sections": {"live": False, "future_section": True}},
    })  # fmt: skip
    state = PublicState.from_hub(raw)
    assert state.profile.title == "Sternwarte"
    assert state.profile.sections.live is False

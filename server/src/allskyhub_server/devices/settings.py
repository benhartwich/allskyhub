"""Changing a camera's settings from the hub (SPEC §6.5 `set_settings`, roadmap #2).

The hub sends the command and waits for the device's answer: the device acks before it
restarts its capture, so the result is known within seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from allskyhub_protocol import Command, CommandName, ErrorReply, SetSettingsArgs
from allskyhub_server.devices.connections import ConnectionRegistry, DeviceOfflineError
from allskyhub_server.i18n import english, tr

REPLY_TIMEOUT_S = 10.0

FIELD_NAMES = {
    "latitude": "Breite",
    "longitude": "Länge",
    "timezone": "Zeitzone",
    "camera": "Kamera",
    "day_delay_s": "Pause am Tag",
    "night_delay_s": "Pause in der Nacht",
}
FIELD_NAMES_EN = {
    "latitude": "Latitude",
    "longitude": "Longitude",
    "timezone": "Time zone",
    "camera": "Camera",
    "day_delay_s": "Pause by day",
    "night_delay_s": "Pause at night",
}
CAMERA_CHOICES = {
    "auto": "Automatisch erkennen",
    "zwo-asi678mc": "ZWO ASI678MC",
    "rpi-hq": "Raspberry Pi HQ Camera",
    "sim": "Simulation (Test)",
}


@dataclass(frozen=True)
class Outcome:
    ok: bool
    # Message for web (German) and app (its language, i18n.py); None on success.
    message: str | None = None
    # HTTP status for the app API.
    status: int = 200


def describe_fields(message: str, labels: dict[str, str] | None = None) -> str:
    """The device names invalid fields comma-separated (SPEC §6.5); make them readable,
    with the labels of the device's schema (§6.9) when known."""
    names = labels or (FIELD_NAMES_EN if english() else FIELD_NAMES)
    found = [names.get(f.strip(), f.strip()) for f in message.split(",") if f.strip()]
    return ", ".join(dict.fromkeys(found)) or tr("unbekannt", "unknown")


# Settings that make the camera talk to other machines in the owner's network (or send
# credentials there): only the owner changes them, never a shared admin (roadmap C10).
OWNER_ONLY = frozenset({
    "nina_url", "adsb_url", "mqtt_host", "mqtt_port", "mqtt_tls", "mqtt_user",
    "mqtt_password", "mqtt_prefix",
})  # fmt: skip


async def apply(
    registry: ConnectionRegistry,
    device_id: str,
    args: SetSettingsArgs | dict[str, Any],
    labels: dict[str, str] | None = None,
    *,
    owner: bool = True,
) -> Outcome:
    """Send only the given keys (a null resets a nullable setting, SPEC §6.5); the device
    changes all or nothing."""
    payload = (
        args.model_dump(mode="json", exclude_unset=True)
        if isinstance(args, SetSettingsArgs)
        else args
    )
    if not payload:
        return Outcome(False, tr("Keine Änderung angegeben.", "No change given."), 400)
    if not owner and (kept := sorted(OWNER_ONLY & payload.keys())):
        names = ", ".join(dict.fromkeys((labels or {}).get(k, k) for k in kept))
        return Outcome(
            False,
            tr(
                f"Nur der Besitzer kann das ändern: {names}.",
                f"Only the owner can change this: {names}.",
            ),
            403,
        )
    cmd = Command(name=CommandName.SET_SETTINGS, args=payload)
    try:
        reply = await registry.command(device_id, cmd, REPLY_TIMEOUT_S)
    except DeviceOfflineError:
        return Outcome(
            False,
            tr(
                "Die Kamera ist offline. Einstellungen gehen nur, wenn sie verbunden ist.",
                "The camera is offline. Settings can only be changed while it is connected.",
            ),
            409,
        )
    except TimeoutError:
        return Outcome(
            False,
            tr(
                "Die Kamera hat nicht rechtzeitig geantwortet.",
                "The camera did not answer in time.",
            ),
            504,
        )
    if isinstance(reply, ErrorReply):
        if reply.code == "invalid_args":
            fields = describe_fields(reply.message, labels)
            return Outcome(
                False,
                tr(f"Die Kamera hat abgelehnt: {fields}.", f"The camera refused: {fields}."),
                400,
            )
        return Outcome(
            False,
            tr(
                f"Die Kamera meldet einen Fehler ({reply.code}).",
                f"The camera reports an error ({reply.code}).",
            ),
            502,
        )
    return Outcome(True)

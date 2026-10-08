"""Changing a camera's settings from the hub (SPEC §6.5 `set_settings`, roadmap #2).

The hub sends the command and waits for the device's answer: the device acks before it
restarts its capture, so the result is known within seconds.
"""

from __future__ import annotations

from dataclasses import dataclass

from allskyhub_protocol import Command, CommandName, ErrorReply, SetSettingsArgs
from allskyhub_server.devices.connections import ConnectionRegistry, DeviceOfflineError

REPLY_TIMEOUT_S = 10.0

FIELD_NAMES = {
    "latitude": "Breite",
    "longitude": "Länge",
    "timezone": "Zeitzone",
    "camera": "Kamera",
    "day_delay_s": "Pause am Tag",
    "night_delay_s": "Pause in der Nacht",
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
    # German message for web and app; None on success.
    message: str | None = None
    # HTTP status for the app API.
    status: int = 200


def describe_fields(message: str) -> str:
    """The device names invalid fields comma-separated (SPEC §6.5); make them German."""
    names = [FIELD_NAMES.get(f.strip(), f.strip()) for f in message.split(",") if f.strip()]
    return ", ".join(names) or "unbekannt"


async def apply(registry: ConnectionRegistry, device_id: str, args: SetSettingsArgs) -> Outcome:
    """Send only the given keys; the device changes all or nothing."""
    payload = args.model_dump(mode="json", exclude_none=True)
    if not payload:
        return Outcome(False, "Keine Änderung angegeben.", 400)
    cmd = Command(name=CommandName.SET_SETTINGS, args=payload)
    try:
        reply = await registry.command(device_id, cmd, REPLY_TIMEOUT_S)
    except DeviceOfflineError:
        return Outcome(
            False, "Die Kamera ist offline. Einstellungen gehen nur, wenn sie verbunden ist.", 409
        )
    except TimeoutError:
        return Outcome(False, "Die Kamera hat nicht rechtzeitig geantwortet.", 504)
    if isinstance(reply, ErrorReply):
        if reply.code == "invalid_args":
            return Outcome(
                False, f"Die Kamera hat abgelehnt: {describe_fields(reply.message)}.", 400
            )
        return Outcome(False, f"Die Kamera meldet einen Fehler ({reply.code}).", 502)
    return Outcome(True)

"""The device's settings schema (SPEC §6.9) and the values it reports and accepts.

One list for the hub's web UI, the app and the local UI. Limits come from the hardware
profile, so a camera never offers a value it cannot use.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from allskyhub_agent.profiles import Profile
from allskyhub_agent.settings import CAMERA_CHOICES, AgentSettings
from allskyhub_protocol import SetSettingsArgs, SettingSpec, invalid_settings

_CAMERA_LABELS = {
    "auto": "Automatisch",
    "zwo-asi678mc": "ZWO ASI678MC",
    "rpi-hq": "Raspberry Pi HQ",
    "sim": "Simuliert (Test)",
}


def build_schema(profile: Profile) -> list[SettingSpec]:
    night = profile.exposure.night
    max_exposure_s = night.max_exposure_us / 1e6
    entries: list[dict[str, Any]] = [
        {
            "key": "location",
            "type": "location",
            "label": {"de": "Standort", "en": "Location"},
            "group": {"de": "Standort", "en": "Location"},
            "help": {
                "de": "Für Tag und Nacht und die Ausrichtung am Himmel.",
                "en": "For day and night and the orientation on the sky.",
            },
        },
        {
            "key": "timezone",
            "type": "string",
            "label": {"de": "Zeitzone", "en": "Time zone"},
            "group": {"de": "Standort", "en": "Location"},
            "max_length": 64,
            "help": {
                "de": "IANA-Name, z. B. Europe/Vienna. Bestimmt die Nachtordner.",
                "en": "IANA name, e.g. Europe/Vienna. Decides the night folders.",
            },
        },
        {
            "key": "camera",
            "type": "choice",
            "label": {"de": "Kamera", "en": "Camera"},
            "group": {"de": "Kamera", "en": "Camera"},
            "choices": [{"value": c, "label": _CAMERA_LABELS[c]} for c in CAMERA_CHOICES],
        },
        {
            "key": "day_delay_s",
            "type": "number",
            "label": {"de": "Pause am Tag", "en": "Pause by day"},
            "group": {"de": "Aufnahme", "en": "Capture"},
            "unit": "s",
            "min": 0,
            "max": 3600,
            "step": 1,
        },
        {
            "key": "night_delay_s",
            "type": "number",
            "label": {"de": "Pause in der Nacht", "en": "Pause at night"},
            "group": {"de": "Aufnahme", "en": "Capture"},
            "unit": "s",
            "min": 0,
            "max": 3600,
            "step": 1,
        },
        {
            "key": "night_max_exposure_s",
            "type": "number",
            "nullable": True,
            "label": {"de": "Längste Belichtung nachts", "en": "Longest exposure at night"},
            "group": {"de": "Aufnahme", "en": "Capture"},
            "unit": "s",
            "min": 1,
            "max": max_exposure_s,
            "step": 1,
            "help": {
                "de": f"Leer: {max_exposure_s:g} s. Kürzer gibt rundere Sterne.",
                "en": f"Empty: {max_exposure_s:g} s. Shorter gives rounder stars.",
            },
        },
        {
            "key": "night_max_gain",
            "type": "int",
            "nullable": True,
            "label": {"de": "Höchster Gain nachts", "en": "Highest gain at night"},
            "group": {"de": "Aufnahme", "en": "Capture"},
            "min": 0,
            "max": int(night.max_gain),
            "step": 1,
            "help": {
                "de": f"Leer: {night.max_gain:g}. Weniger rauscht weniger, wird aber dunkler.",
                "en": f"Empty: {night.max_gain:g}. Less is less noisy but darker.",
            },
        },
    ]
    if profile.camera == "zwo":
        for key, de, en in (
            ("wb_r", "Weißabgleich Rot", "White balance red"),
            ("wb_b", "Weißabgleich Blau", "White balance blue"),
        ):
            entries.append(
                {
                    "key": key,
                    "type": "int",
                    "nullable": True,
                    "label": {"de": de, "en": en},
                    "group": {"de": "Farbe", "en": "Colour"},
                    "min": 1,
                    "max": 99,
                    "step": 1,
                    "help": {"de": "Leer: Wert der Kamera.", "en": "Empty: the camera's value."},
                }
            )
    entries.append(
        {
            "key": "jpeg_quality",
            "type": "int",
            "label": {"de": "JPEG-Qualität", "en": "JPEG quality"},
            "group": {"de": "Bild", "en": "Image"},
            "unit": "%",
            "min": 50,
            "max": 100,
            "step": 1,
            "help": {
                "de": "Höher braucht mehr Speicher und Upload.",
                "en": "Higher needs more storage and upload.",
            },
        }
    )
    return [SettingSpec.model_validate(e) for e in entries]


def values(s: AgentSettings, schema: list[SettingSpec]) -> dict[str, Any]:
    """`status.settings` (SPEC §6.3): every schema key, location as latitude/longitude."""
    out: dict[str, Any] = {"latitude": s.latitude, "longitude": s.longitude}
    for spec in schema:
        if spec.key != "location":
            out[spec.key] = getattr(s, spec.key)
    return out


def check(schema: list[SettingSpec], changes: Mapping[str, Any]) -> list[str]:
    """Invalid keys of a partial update (SPEC §6.5): the schema, then the fixed checks
    (time zone name, location pair) of `SetSettingsArgs`."""
    bad = set(invalid_settings(schema, changes))
    if not bad:
        try:
            SetSettingsArgs.model_validate(dict(changes))
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]} or {"args"}
    return sorted(bad)

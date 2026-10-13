"""The device's settings schema (SPEC §6.9) and the values it reports and accepts.

One list for the hub's web UI, the app and the local UI. Limits come from the hardware
profile, so a camera never offers a value it cannot use.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from allskyhub_agent.profiles import Profile
from allskyhub_agent.settings import (
    CAMERA_CHOICES,
    DETECT_CHOICES,
    DETECTORS,
    AgentSettings,
    valid_nina_url,
)
from allskyhub_protocol import SetSettingsArgs, SettingSpec, invalid_settings

_CAMERA_LABELS = {
    "auto": "Automatisch",
    "zwo-asi678mc": "ZWO ASI678MC",
    "rpi-hq": "Raspberry Pi HQ",
    "sim": "Simuliert (Test)",
}

_LEVEL_LABELS = {
    "off": {"de": "Aus", "en": "Off"},
    "low": {"de": "Nur deutliche", "en": "Only clear ones"},
    "normal": {"de": "Normal", "en": "Normal"},
    "high": {"de": "Auch schwache", "en": "Faint ones too"},
}
_DETECTOR_TEXT = {
    "meteor": (
        "Meteore",
        "Meteors",
        "Flugzeuge und Satelliten werden aussortiert.",
        "Aircraft and satellites are left out.",
    ),
    "aurora": (
        "Polarlicht",
        "Aurora",
        "Grünes Leuchten tief am Himmel Richtung Pol.",
        "Green glow low in the sky toward the pole.",
    ),
    "lightning": (
        "Blitze",
        "Lightning",
        "Ein großer Teil des Himmels hellt kurz auf.",
        "A large part of the sky lights up briefly.",
    ),
    "nlc": (
        "Leuchtende Nachtwolken",
        "Noctilucent clouds",
        "Im Sommer in der Dämmerung; braucht die Ausrichtung.",
        "In summer twilight; needs the orientation.",
    ),
}


def _bad_frame_entries() -> list[dict[str, Any]]:
    """Bad frames (SPEC §4.9, roadmap L19)."""
    group = {"de": "Bild", "en": "Image"}
    return [
        {
            "key": "bad_frames",
            "type": "bool",
            "label": {"de": "Schlechte Bilder aussortieren", "en": "Sort out bad frames"},
            "group": group,
            "help": {
                "de": "Zu dunkle oder zu helle Bilder bleiben gespeichert, fehlen aber in "
                "Zeitraffer, Keogramm, Startrails und den Bildlisten.",
                "en": "Frames that are too dark or too bright stay stored but are left out of "
                "the timelapse, keogram, star trails and the image lists.",
            },
        },
        {
            "key": "bad_dark_pct",
            "type": "int",
            "label": {"de": "Zu dunkel unter", "en": "Too dark below"},
            "group": group,
            "unit": "%",
            "min": 0,
            "max": 20,
            "step": 1,
            "help": {
                "de": "Mittlere Helligkeit im Himmel; 0 schaltet diese Grenze ab.",
                "en": "Mean brightness of the sky; 0 switches this limit off.",
            },
        },
        {
            "key": "bad_bright_pct",
            "type": "int",
            "label": {"de": "Zu hell über", "en": "Too bright above"},
            "group": group,
            "unit": "%",
            "min": 50,
            "max": 100,
            "step": 1,
            "help": {
                "de": "Mittlere Helligkeit im Himmel; 100 schaltet diese Grenze ab.",
                "en": "Mean brightness of the sky; 100 switches this limit off.",
            },
        },
    ]


def _detector_entries() -> list[dict[str, Any]]:
    return [
        {
            "key": f"detect_{k}",
            "type": "choice",
            "label": {"de": de, "en": en},
            "group": {"de": "Erkennung", "en": "Detection"},
            "choices": [{"value": c, "label": _LEVEL_LABELS[c]} for c in DETECT_CHOICES],
            "help": {"de": help_de, "en": help_en},
        }
        for k in DETECTORS
        for de, en, help_de, help_en in [_DETECTOR_TEXT[k]]
    ]


def _product_entries() -> list[dict[str, Any]]:
    group = {"de": "Produkte", "en": "Products"}
    switches = [
        {"key": key, "type": "bool", "label": {"de": de, "en": en}, "group": group}
        for key, de, en in (
            ("product_keogram", "Keogramm", "Keogram"),
            ("product_startrails", "Sternspuren", "Star trails"),
            ("product_timelapse", "Zeitraffer", "Timelapse"),
            ("product_day_timelapse", "Zeitraffer vom Tag", "Day timelapse"),
            ("product_day_keogram", "Keogramm vom Tag", "Day keogram"),
        )
    ]
    switches.append(
        {
            "key": "product_mini_timelapse",
            "type": "bool",
            "label": {
                "de": "Mini-Zeitraffer der letzten Stunde",
                "en": "Mini timelapse of the last hour",
            },
            "group": group,
            "help": {
                "de": "Alle 10 Minuten neu für die Live-Seite; kostet auf dem Pi Rechenzeit.",
                "en": "Rebuilt every 10 minutes for the live page; costs CPU time on the Pi.",
            },
        }
    )
    return [
        *switches,
        {
            "key": "timelapse_fps",
            "type": "int",
            "label": {"de": "Zeitraffer: Bilder pro Sekunde", "en": "Timelapse frames per second"},
            "group": group,
            "min": 1,
            "max": 60,
            "step": 1,
            "help": {
                "de": "Mehr ergibt einen kürzeren, flüssigeren Film.",
                "en": "More gives a shorter, smoother film.",
            },
        },
        {
            "key": "timelapse_quality",
            "type": "choice",
            "label": {"de": "Zeitraffer: Qualität", "en": "Timelapse quality"},
            "group": group,
            "choices": [
                {"value": "small", "label": {"de": "Klein", "en": "Small"}},
                {"value": "normal", "label": {"de": "Normal", "en": "Normal"}},
                {"value": "high", "label": {"de": "Hoch", "en": "High"}},
            ],
            "help": {
                "de": "Höher braucht mehr Speicher und Upload. Gilt ab der nächsten Nacht.",
                "en": "Higher needs more storage and upload. Applies from the next night on.",
            },
        },
        {
            "key": "keep_days",
            "type": "int",
            "label": {"de": "Nächte aufbewahren", "en": "Keep nights"},
            "group": {"de": "Speicher", "en": "Storage"},
            "unit": "d",
            "min": 1,
            "max": 3650,
            "step": 1,
            "help": {
                "de": "Ältere Nächte werden nach den Nachtprodukten gelöscht. Wird die Karte "
                "voll, gehen die ältesten trotzdem früher.",
                "en": "Older nights are deleted after the night products. If the card fills "
                "up, the oldest go earlier anyway.",
            },
        },
    ]


def _observatory_entries() -> list[dict[str, Any]]:
    """SPEC §7.5: the camera as safety monitor for N.I.N.A., SGP, Voyager (roadmap L16)."""
    group = {"de": "Sternwarte", "en": "Observatory"}
    return [
        {
            "key": "alpaca_enabled",
            "type": "bool",
            "label": {"de": "ASCOM Alpaca", "en": "ASCOM Alpaca"},
            "group": group,
            "help": {
                "de": "N.I.N.A., SGP oder Voyager finden die Kamera im Netz als Safety Monitor "
                "und Wolkensensor, ohne Plugin.",
                "en": "N.I.N.A., SGP or Voyager find the camera on the network as safety "
                "monitor and cloud sensor, without a plugin.",
            },
        },
        {
            "key": "alpaca_port",
            "type": "int",
            "label": {"de": "Alpaca-Port", "en": "Alpaca port"},
            "group": group,
            "min": 1024,
            "max": 65535,
            "step": 1,
        },
        {
            "key": "safe_cloud_max",
            "type": "int",
            "label": {"de": "Unsicher ab Bewölkung", "en": "Unsafe from cloud cover"},
            "group": group,
            "unit": "%",
            "min": 5,
            "max": 95,
            "step": 1,
        },
        {
            "key": "safe_hold_min",
            "type": "int",
            "label": {"de": "Wieder sicher nach", "en": "Safe again after"},
            "group": group,
            "unit": "min",
            "min": 0,
            "max": 120,
            "step": 1,
            "help": {
                "de": "So lange muss es klar bleiben (10 Punkte unter der Grenze). Nach einem "
                "Blitz bleibt es 30 Minuten unsicher.",
                "en": "This long it must stay clear (10 points below the limit). After "
                "lightning it stays unsafe for 30 minutes.",
            },
        },
        {
            "key": "nina_url",
            "type": "string",
            "label": {"de": "N.I.N.A.-Adresse", "en": "N.I.N.A. address"},
            "group": group,
            "max_length": 200,
            "help": {
                "de": "Mit dem Plugin „Advanced API“, z. B. http://192.168.1.30:1888. Die Kamera "
                "liest Ziel, Sequenz und letztes Bild und zeigt sie im Hub. Leer: aus.",
                "en": 'With the "Advanced API" plugin, e.g. http://192.168.1.30:1888. The camera '
                "reads target, sequence and last image and shows them on the hub. Empty: off.",
            },
        },
    ]


def build_schema(profile: Profile) -> list[SettingSpec]:
    night = profile.exposure.night
    max_exposure_s = night.max_exposure_us / 1e6  # the default for an empty field
    cap_s = profile.night_cap_us / 1e6
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
            "max": cap_s,
            "step": 1,
            "help": {
                "de": f"Leer: {max_exposure_s:g} s, höchstens {cap_s:g} s. Kürzer gibt rundere "
                "Sterne, länger zeigt mehr bei dunklem Himmel.",
                "en": f"Empty: {max_exposure_s:g} s, at most {cap_s:g} s. Shorter gives rounder "
                "stars, longer shows more under a dark sky.",
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
        {
            "key": "moon_mode",
            "type": "bool",
            "label": {"de": "Mond-Modus", "en": "Moon mode"},
            "group": {"de": "Aufnahme", "en": "Capture"},
            "help": {
                "de": "Steht ein mindestens halb beleuchteter Mond am Himmel, belichtet die "
                "Kamera nachts dunkler, damit Himmel und Landschaft nicht ausbrennen.",
                "en": "While an at least half-lit moon is up, the camera exposes darker at "
                "night so the sky and the landscape do not burn out.",
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
    entries.extend(_bad_frame_entries())
    entries.extend(_detector_entries())
    entries.extend(_product_entries())
    entries.extend(_observatory_entries())
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
    nina: object = changes.get("nina_url")
    if isinstance(nina, str) and not valid_nina_url(nina):
        bad.add("nina_url")
    if not bad:
        try:
            SetSettingsArgs.model_validate(dict(changes))
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]} or {"args"}
    return sorted(bad)

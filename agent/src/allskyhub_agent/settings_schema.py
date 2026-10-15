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
    valid_hostnames,
    valid_http_url,
    valid_mqtt,
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


CAPTURE = {"de": "Aufnahme", "en": "Capture"}


def _orientation_entries() -> list[dict[str, Any]]:
    """Flip and rotation of every frame (SPEC §4.1)."""
    group = {"de": "Bild", "en": "Image"}
    note = {
        "de": "Eine Änderung lernt Hotpixel, Himmelsmaske und Ausrichtung neu (eine klare Nacht).",
        "en": "A change learns hot pixels, sky mask and orientation again (one clear night).",
    }
    return [
        {
            "key": "image_flip",
            "type": "choice",
            "label": {"de": "Spiegeln", "en": "Flip"},
            "group": group,
            "choices": [
                {"value": "none", "label": {"de": "Nicht", "en": "No"}},
                {"value": "horizontal", "label": {"de": "Links–rechts", "en": "Left–right"}},
                {"value": "vertical", "label": {"de": "Oben–unten", "en": "Top–bottom"}},
            ],
            "help": note,
        },
        {
            "key": "image_rotate",
            "type": "choice",
            "label": {"de": "Drehen", "en": "Rotate"},
            "group": group,
            "choices": [
                {"value": v, "label": {"de": f"{v}°", "en": f"{v}°"}}
                for v in ("0", "90", "180", "270")
            ],
            "help": {
                "de": "Im Uhrzeigersinn, z. B. damit Norden oben ist.",
                "en": "Clockwise, e.g. to have north at the top.",
            },
        },
    ]


def _display_entries() -> list[dict[str, Any]]:
    """How the local UI shows texts and temperatures (SPEC §7)."""
    group = {"de": "Anzeige", "en": "Display"}
    return [
        {
            "key": "language",
            "type": "choice",
            "label": {"de": "Sprache", "en": "Language"},
            "group": group,
            "choices": [
                {"value": "de", "label": "Deutsch"},
                {"value": "en", "label": "English"},
            ],
        },
        {
            "key": "temperature_unit",
            "type": "choice",
            "label": {"de": "Temperatur", "en": "Temperature"},
            "group": group,
            "choices": [
                {"value": "c", "label": "°C"},
                {"value": "f", "label": "°F"},
            ],
        },
    ]


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
    ] + [
        {
            "key": "adsb_url",
            "type": "string",
            "label": {"de": "Eigener ADS-B-Empfänger", "en": "Own ADS-B receiver"},
            "group": {"de": "Erkennung", "en": "Detection"},
            "max_length": 200,
            "help": {
                "de": "Adresse der aircraft.json von readsb, dump1090 oder tar1090, z. B. "
                "http://192.168.1.40/tar1090/data/aircraft.json. Die Kamera markiert damit "
                "Flugzeuge unter den Meteoren, auch ohne Hub, und gibt sie an den Hub weiter. "
                "Leer: aus (der Hub nimmt dann adsb.lol).",
                "en": "Address of the aircraft.json of readsb, dump1090 or tar1090, e.g. "
                "http://192.168.1.40/tar1090/data/aircraft.json. The camera uses it to mark "
                "aircraft among the meteors, also without the hub, and passes them on to the "
                "hub. Empty: off (the hub then uses adsb.lol).",
            },
        },
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


def _weather_entries() -> list[dict[str, Any]]:
    """Weather sensor and dew heater (SPEC §7.11, roadmap L30)."""
    group = {"de": "Wetter und Heizung", "en": "Weather and heater"}
    return [
        {
            "key": "env_sensor",
            "type": "choice",
            "label": {"de": "Wettersensor", "en": "Weather sensor"},
            "group": group,
            "choices": [
                {"value": "off", "label": {"de": "Keiner", "en": "None"}},
                {"value": "bme280", "label": "BME280 (I²C)"},
            ],
            "help": {
                "de": "Temperatur, Feuchte, Luftdruck und Taupunkt für Diagramme, Overlay, "
                "MQTT und Alpaca. Angeschlossen an SDA (Pin 3) und SCL (Pin 5).",
                "en": "Temperature, humidity, pressure and dew point for charts, overlay, MQTT "
                "and Alpaca. Connected to SDA (pin 3) and SCL (pin 5).",
            },
        },
        {
            "key": "env_sensor_address",
            "type": "choice",
            "label": {"de": "Adresse des Sensors", "en": "Sensor address"},
            "group": group,
            "choices": [
                {"value": "0x76", "label": "0x76"},
                {"value": "0x77", "label": "0x77"},
            ],
            "help": {
                "de": "Die meisten Platinen nutzen 0x76, manche (z. B. Adafruit) 0x77.",
                "en": "Most boards use 0x76, some (e.g. Adafruit) 0x77.",
            },
        },
        {
            "key": "heater",
            "type": "choice",
            "label": {"de": "Kuppelheizung", "en": "Dome heater"},
            "group": group,
            "choices": [
                {"value": "off", "label": {"de": "Aus", "en": "Off"}},
                {
                    "value": "auto",
                    "label": {"de": "Automatisch (Taupunkt)", "en": "Automatic (dew point)"},
                },
                {"value": "on", "label": {"de": "Immer an", "en": "Always on"}},
            ],
            "help": {
                "de": "Automatisch heizt, wenn die Luft nahe am Taupunkt oder sehr feucht ist; "
                "ohne Sensor nachts. Über 45 °C immer aus.",
                "en": "Automatic heats when the air is close to its dew point or very humid; "
                "without a sensor at night. Always off above 45 °C.",
            },
        },
        {
            "key": "heater_gpio",
            "type": "int",
            "nullable": True,
            "label": {"de": "GPIO der Heizung", "en": "Heater GPIO"},
            "group": group,
            "min": 2,
            "max": 27,
            "step": 1,
            "help": {
                "de": "BCM-Nummer des Pins, der die Heizung schaltet (z. B. 17 für Pin 11). "
                "Leer: es wird nichts geschaltet.",
                "en": "BCM number of the pin that switches the heater (e.g. 17 for pin 11). "
                "Empty: nothing is switched.",
            },
        },
        {
            "key": "heater_active_low",
            "type": "bool",
            "label": {"de": "Heizung schaltet bei LOW", "en": "Heater switches on LOW"},
            "group": group,
            "help": {
                "de": "Für Schaltstufen, die mit 0 V einschalten. Die Stufe muss bei offenem "
                "Pin aus sein.",
                "en": "For stages that switch on at 0 V. The stage must be off while the pin "
                "is open.",
            },
        },
        {
            "key": "heater_dew_margin_c",
            "type": "number",
            "label": {"de": "Abstand zum Taupunkt", "en": "Margin to the dew point"},
            "group": group,
            "unit": "°C",
            "min": 0.5,
            "max": 10,
            "step": 0.5,
            "help": {
                "de": "Liegt die Temperatur so nah am Taupunkt, heizt die Kamera. Standard 3 °C.",
                "en": "With the temperature this close to the dew point the camera heats. "
                "Default 3 °C.",
            },
        },
    ]


def _lightning_sensor_entries() -> list[dict[str, Any]]:
    """Lightning sensor AS3935 (SPEC §7.12, roadmap L31)."""
    group = {"de": "Wetter und Heizung", "en": "Weather and heater"}
    return [
        {
            "key": "lightning_sensor",
            "type": "choice",
            "label": {"de": "Blitzsensor", "en": "Lightning sensor"},
            "group": group,
            "choices": [
                {"value": "off", "label": {"de": "Keiner", "en": "None"}},
                {"value": "as3935", "label": "AS3935 (I²C)"},
            ],
            "help": {
                "de": "Hört Blitze bis etwa 40 km, auch am Tag. Bestätigt die Blitze der Kamera; "
                "ein „Gewitter“ ohne einen gehörten Blitz wird als Fehlalarm markiert.",
                "en": "Hears strikes up to about 40 km, also by day. Confirms the camera's "
                'flashes; a "storm" without one strike heard is marked a false alarm.',
            },
        },
        {
            "key": "lightning_sensor_address",
            "type": "choice",
            "label": {"de": "Adresse des Blitzsensors", "en": "Lightning sensor address"},
            "group": group,
            "choices": [{"value": a, "label": a} for a in ("0x01", "0x02", "0x03")],
            "help": {
                "de": "Meist 0x03 (beide Adress-Lötbrücken offen).",
                "en": "Usually 0x03 (both address jumpers open).",
            },
        },
        {
            "key": "lightning_sensor_irq_gpio",
            "type": "int",
            "nullable": True,
            "label": {"de": "GPIO des Interrupts", "en": "Interrupt GPIO"},
            "group": group,
            "min": 2,
            "max": 27,
            "step": 1,
            "help": {
                "de": "BCM-Nummer des Pins, an dem IRQ des Sensors hängt (z. B. 4 für Pin 7). "
                "Ohne ihn arbeitet der Sensor nicht.",
                "en": "BCM number of the pin the sensor's IRQ is wired to (e.g. 4 for pin 7). "
                "The sensor does not work without it.",
            },
        },
        {
            "key": "lightning_sensor_indoor",
            "type": "bool",
            "label": {"de": "Blitzsensor im Gehäuse", "en": "Lightning sensor in a housing"},
            "group": group,
            "help": {
                "de": "Höhere Empfindlichkeit, wenn das Gehäuse die Signale dämpft.",
                "en": "Higher sensitivity when the housing dampens the signals.",
            },
        },
    ]


def _mqtt_entries() -> list[dict[str, Any]]:
    """MQTT for home automation (SPEC §7.7, roadmap L23)."""
    group = {"de": "Hausautomation (MQTT)", "en": "Home automation (MQTT)"}
    return [
        {
            "key": "mqtt_host",
            "type": "string",
            "label": {"de": "Broker", "en": "Broker"},
            "group": group,
            "max_length": 253,
            "help": {
                "de": "Name oder Adresse des MQTT-Brokers im Heimnetz, z. B. homeassistant.local. "
                "Leer: aus. Home Assistant findet die Kamera von selbst.",
                "en": "Name or address of the MQTT broker on the home network, e.g. "
                "homeassistant.local. Empty: off. Home Assistant finds the camera by itself.",
            },
        },
        {
            "key": "mqtt_port",
            "type": "int",
            "label": {"de": "Port", "en": "Port"},
            "group": group,
            "min": 1,
            "max": 65535,
            "step": 1,
            "help": {"de": "Meist 1883, mit TLS 8883.", "en": "Usually 1883, 8883 with TLS."},
        },
        {
            "key": "mqtt_tls",
            "type": "bool",
            "label": {"de": "Verschlüsselt (TLS)", "en": "Encrypted (TLS)"},
            "group": group,
        },
        {
            "key": "mqtt_user",
            "type": "string",
            "label": {"de": "Benutzer", "en": "User"},
            "group": group,
            "max_length": 100,
        },
        {
            "key": "mqtt_password",
            "type": "string",
            "label": {"de": "Passwort", "en": "Password"},
            "group": group,
            "max_length": 200,
            "write_only": True,
            "nullable": True,
        },
        {
            "key": "mqtt_prefix",
            "type": "string",
            "label": {"de": "Themen-Präfix", "en": "Topic prefix"},
            "group": group,
            "max_length": 40,
            "help": {
                "de": "Die Kamera sendet unter <Präfix>/<Geräte-ID>/…",
                "en": "The camera publishes under <prefix>/<device id>/…",
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
        {
            "key": "day_target_pct",
            "type": "int",
            "label": {"de": "Helligkeit am Tag", "en": "Brightness by day"},
            "group": CAPTURE,
            "unit": "%",
            "min": 5,
            "max": 80,
            "step": 1,
            "help": {
                "de": "Wie hell die Belichtungsautomatik das Bild macht. Standard 35 %.",
                "en": "How bright the auto exposure makes the picture. Default 35 %.",
            },
        },
        {
            "key": "night_target_pct",
            "type": "int",
            "label": {"de": "Helligkeit in der Nacht", "en": "Brightness at night"},
            "group": CAPTURE,
            "unit": "%",
            "min": 5,
            "max": 80,
            "step": 1,
            "help": {
                "de": "Standard 20 %. Heller zeigt mehr Sterne, aber auch mehr Rauschen und "
                "Lichtverschmutzung.",
                "en": "Default 20 %. Brighter shows more stars but also more noise and light "
                "pollution.",
            },
        },
        {
            "key": "day_capture",
            "type": "choice",
            "label": {"de": "Bilder am Tag", "en": "Pictures by day"},
            "group": CAPTURE,
            "choices": [
                {
                    "value": "save",
                    "label": {"de": "Aufnehmen und speichern", "en": "Take and keep"},
                },
                {"value": "live", "label": {"de": "Nur Livebild", "en": "Live picture only"}},
                {"value": "off", "label": {"de": "Keine", "en": "None"}},
            ],
            "help": {
                "de": "„Nur Livebild“ speichert tagsüber nichts (kein Tageszeitraffer), spart "
                "aber Speicher und SD-Karte.",
                "en": '"Live picture only" keeps nothing by day (no day timelapse) but saves '
                "storage and the SD card.",
            },
        },
    ]
    entries.extend(_orientation_entries())
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
    entries.extend(_weather_entries())
    entries.extend(_lightning_sensor_entries())
    entries.extend(_mqtt_entries())
    entries.extend(_display_entries())
    entries.append(
        {
            "key": "https",
            "type": "bool",
            "label": {"de": "HTTPS für die lokale Seite", "en": "HTTPS for the local page"},
            "group": {"de": "Sicherheit", "en": "Security"},
            "help": {
                "de": "Verschlüsselt auf Port 8443, mit einem Zertifikat der Kamera (der Browser "
                "warnt einmal) oder einem eigenen. Die Einrichtung per App bleibt bei HTTP.",
                "en": "Encrypted on port 8443, with the camera's own certificate (the browser "
                "warns once) or your own. Setting up with the app stays on HTTP.",
            },
        }
    )
    entries.append(
        {
            "key": "local_hostnames",
            "type": "string",
            "label": {"de": "Weitere Adressen", "en": "Further addresses"},
            "group": {"de": "Sicherheit", "en": "Security"},
            "max_length": 500,
            "help": {
                "de": "Eigene Namen im Heimnetz, unter denen die Kamera erreichbar ist, z. B. "
                "allsky.fritz.box (mehrere mit Komma). IP-Adressen, .local und Kurznamen gehen "
                "immer; andere Namen lehnt die Kamera zum Schutz ab.",
                "en": "Own names in the home network the camera is reached under, e.g. "
                "allsky.fritz.box (several with commas). IP addresses, .local and short names "
                "always work; the camera refuses other names for protection.",
            },
        }
    )
    return [SettingSpec.model_validate(e) for e in entries]


def values(s: AgentSettings, schema: list[SettingSpec]) -> dict[str, Any]:
    """`status.settings` (SPEC §6.3): every schema key, location as latitude/longitude."""
    out: dict[str, Any] = {"latitude": s.latitude, "longitude": s.longitude}
    for spec in schema:
        if spec.key == "location":
            continue
        if spec.write_only:  # SPEC §6.9: never the secret, only whether it is set
            out[f"{spec.key}_set"] = bool(getattr(s, spec.key))
        else:
            out[spec.key] = getattr(s, spec.key)
    return out


def keys(schema: list[SettingSpec]) -> set[str]:
    """The settable keys (no `<key>_set` flags), location as latitude/longitude."""
    out = {spec.key for spec in schema if spec.key != "location"}
    return out | ({"latitude", "longitude"} if any(x.key == "location" for x in schema) else set())


def check(schema: list[SettingSpec], changes: Mapping[str, Any]) -> list[str]:
    """Invalid keys of a partial update (SPEC §6.5): the schema, then the fixed checks
    (time zone name, location pair) of `SetSettingsArgs`."""
    bad = set(invalid_settings(schema, changes))
    for k in ("nina_url", "adsb_url"):
        url: object = changes.get(k)
        if isinstance(url, str) and not valid_http_url(url):
            bad.add(k)
    for k in ("mqtt_host", "mqtt_prefix", "mqtt_user"):
        v: object = changes.get(k)
        if isinstance(v, str) and not valid_mqtt(k, v):
            bad.add(k)
    # SPEC §7.11, §7.12: the heater and the sensor's interrupt cannot share a pin.
    pins = [changes.get(k) for k in ("heater_gpio", "lightning_sensor_irq_gpio")]
    if pins[0] is not None and pins[0] == pins[1]:
        bad.update({"heater_gpio", "lightning_sensor_irq_gpio"})
    names: object = changes.get("local_hostnames")
    if isinstance(names, str) and not valid_hostnames(names):
        bad.add("local_hostnames")
    if not bad:
        try:
            SetSettingsArgs.model_validate(dict(changes))
        except ValidationError as exc:
            bad = {str(e["loc"][0]) for e in exc.errors() if e["loc"]} or {"args"}
    return sorted(bad)

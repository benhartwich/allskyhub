"""German labels for detections (SPEC §6.4), for the web UI; the parts the app API returns
also in English (i18n.py)."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from allskyhub_server.devices import lightning_li
from allskyhub_server.i18n import english, tr
from allskyhub_server.models import EventRecord

EVENT_TITLES = {
    "meteor": "Meteor",
    "lightning": "Blitz",
    "aurora": "Polarlicht",
    "nlc": "Leuchtende Nachtwolken",
    "satellite": "Satellit",
    "clouds": "Wolken",
    "sky_quality": "Himmelsqualität",
}
EVENT_TITLES_EN = {
    "meteor": "Meteor",
    "lightning": "Lightning",
    "aurora": "Aurora",
    "nlc": "Noctilucent clouds",
    "satellite": "Satellite",
    "clouds": "Clouds",
    "sky_quality": "Sky quality",
}


def event_titles() -> dict[str, str]:
    """The titles in the language of the request."""
    return EVENT_TITLES_EN if english() else EVENT_TITLES


# SPEC §6.4: direction_deg is the trail's axis in the image (0..180, 0 = up), no sense of travel.
_AXES = (
    "senkrecht",
    "diagonal, oben rechts – unten links",
    "waagrecht",
    "diagonal, oben links – unten rechts",
)
_AXES_EN = (
    "vertical",
    "diagonal, top right – bottom left",
    "horizontal",
    "diagonal, top left – bottom right",
)


def _num(text: str) -> str:
    """A decimal number in the request's language: comma in German, point in English."""
    return text if english() else text.replace(".", ",")


# Agent shower names (SPEC §6.4, IMO) in German; English keeps the IMO names, and unknown
# names pass through.
SHOWER_NAMES = {
    "Quadrantids": "Quadrantiden",
    "Lyrids": "Lyriden",
    "Eta Aquariids": "Eta-Aquariiden",
    "Delta Aquariids": "Delta-Aquariiden",
    "Perseids": "Perseiden",
    "Orionids": "Orioniden",
    "Leonids": "Leoniden",
    "Geminids": "Geminiden",
    "Ursids": "Ursiden",
    # Not on the camera's list (detect/meteor.py), but from stations and other software
    # (SPEC §10) and older events.
    "Taurids": "Tauriden",
    "Southern Taurids": "Südliche Tauriden",
    "Northern Taurids": "Nördliche Tauriden",
    "Southern Delta Aquariids": "Südliche Delta-Aquariiden",
    "Alpha Capricornids": "Alpha-Capricorniden",
    "Kappa Cygnids": "Kappa-Cygniden",
    "Draconids": "Draconiden",
    "Monocerotids": "Monocerotiden",
    "Sigma Hydrids": "Sigma-Hydriden",
}


def shower_name(name: str) -> str:
    return name if english() else SHOWER_NAMES.get(name, name)


def shower_text(data: dict[str, Any]) -> str | None:
    """SPEC §6.4 ``shower`` by ``shower_match``: a radiant match is certain, a date match
    (also events without ``shower_match``) only a guess; None when there is nothing to say."""
    shower = data.get("shower")
    match = data.get("shower_match", "date")
    if match == "radiant":
        return shower_name(shower) if isinstance(shower, str) and shower else "sporadisch"
    if match == "date" and isinstance(shower, str) and shower:
        return f"vielleicht {shower_name(shower)} (nur nach Datum)"
    return None


def meteor_summary(events: list[EventRecord]) -> str | None:
    """One line per night: how many meteors, by shower (radiant matches only), sporadic and
    unassigned (SPEC §6.4 ``shower_match``)."""
    meteors = [e.data for e in events if e.kind == "meteor"]
    if not meteors:
        return None
    showers: dict[str, int] = {}
    sporadic = unknown = 0
    for data in meteors:
        shower = data.get("shower")
        if data.get("shower_match") != "radiant":
            unknown += 1
        elif isinstance(shower, str) and shower:
            showers[shower_name(shower)] = showers.get(shower_name(shower), 0) + 1
        else:
            sporadic += 1
    parts = [f"{n} {name}" for name, n in sorted(showers.items(), key=lambda kv: -kv[1])]
    if sporadic:
        parts.append(f"{sporadic} {tr('sporadisch', 'sporadic')}")
    if unknown and (showers or sporadic):
        parts.append(f"{unknown} {tr('ohne Radiant', 'without radiant')}")
    one = len(meteors) == 1
    total = f"{len(meteors)} {tr('Meteor' if one else 'Meteore', 'meteor' if one else 'meteors')}"
    return f"{total}: {', '.join(parts)}" if parts else total


COMPASS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")
COMPASS_EN = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass(azimuth: float, en: bool = False) -> str:
    """Azimuth (0 = north, clockwise) as one of eight compass points, German unless
    ``en`` (the public pages' filter, web/templating.py)."""
    return (COMPASS_EN if en else COMPASS)[round((azimuth % 360) / 45) % 8]


def orientation_line(status: dict[str, Any] | None) -> str | None:
    """SPEC §4.8 ``status.orientation``: where north is in the image and how well it is
    known, or None before the first calibration."""
    o = (status or {}).get("orientation")
    if not isinstance(o, dict):
        return None
    o = cast(dict[str, Any], o)
    north = o.get("north_deg")
    if not isinstance(north, int | float):
        return None
    parts = [
        tr(
            f"Norden bei {round(north) % 360}° im Bild",
            f"north at {round(north) % 360}° in the image",
        )
    ]
    if isinstance(stars := o.get("stars"), int):
        parts.append(tr(f"kalibriert auf {stars} Sternen", f"calibrated on {stars} stars"))
    if isinstance(rms := o.get("rms_deg"), int | float):
        parts.append(_num(f"±{rms:.1f}°"))
    if isinstance(solved := o.get("solved_at"), str):
        try:
            day = dt.datetime.fromisoformat(solved)
            parts.append(tr(f"{day.day}.{day.month}.", f"{day:%Y-%m-%d}"))
        except ValueError:
            pass
    return " · ".join(parts)


UPDATE_STATES = {
    "up_to_date": "aktuell",
    "downloading": "Update {v} wird geladen",
    "waiting": "Update {v} geladen, wird tagsüber installiert",
    "installing": "Update {v} wird installiert",
    "installed": "Update {v} installiert",
    "rolled_back": "Update {v} zurückgerollt",
    "failed": "Update {v} fehlgeschlagen",
}
UPDATE_STATES_EN = {
    "up_to_date": "up to date",
    "downloading": "downloading update {v}",
    "waiting": "update {v} downloaded, installs by day",
    "installing": "installing update {v}",
    "installed": "update {v} installed",
    "rolled_back": "update {v} rolled back",
    "failed": "update {v} failed",
}
UPDATE_CODES_EN = {
    "unhealthy": "the new version did not run cleanly",
    "checksum": "wrong checksum",
    "download": "download failed",
    "bad_bundle": "package damaged",
    "bad_signature": "invalid signature",
    "bad_manifest": "invalid update description",
    "no_space": "not enough space",
}
UPDATE_CODES = {
    "unhealthy": "die neue Version lief nicht sauber",
    "checksum": "Prüfsumme falsch",
    "download": "Download fehlgeschlagen",
    "bad_bundle": "Paket beschädigt",
    "bad_signature": "Signatur ungültig",
    "bad_manifest": "Update-Beschreibung ungültig",
    "no_space": "zu wenig Speicher",
}


def update_line(status: dict[str, Any] | None) -> tuple[str, bool] | None:
    """Roadmap #7 ``status.update``: German text and whether it is a warning, or None
    before the updater has run once."""
    u = (status or {}).get("update")
    if not isinstance(u, dict):
        return None
    u = cast(dict[str, Any], u)
    state = u.get("state")
    if not isinstance(state, str) or state not in UPDATE_STATES:
        return None
    states, codes = (
        (UPDATE_STATES_EN, UPDATE_CODES_EN) if english() else (UPDATE_STATES, UPDATE_CODES)
    )
    text = states[state].format(v=u.get("version") or "")
    code = u.get("code")
    if isinstance(code, str) and code:
        text += f" ({codes.get(code, code)})"
    if isinstance(at := u.get("at"), str) and state not in ("up_to_date", "downloading"):
        try:
            when = dt.datetime.fromisoformat(at)
            text += tr(f", {when.day}.{when.month}.", f", {when:%Y-%m-%d}")
        except ValueError:
            pass
    return text, state in ("rolled_back", "failed")


# Further keys of SPEC §6.4 with a plain label; anything else stays off the page (raw
# keys such as `track_ordered` told the owner nothing; Benjamin, 2026-11-05).
_LABELS = {
    "aircraft": "Flugzeug (eigener Empfänger)",
    "mag": "Helligkeit (mag)",
    "source": "Quelle",
}
_LABELS_EN = {
    "aircraft": "Aircraft (own receiver)",
    "mag": "Brightness (mag)",
    "source": "Source",
}
_SOURCES_EN = {
    "image_url": "Image address (SharedSky)",
    "indi-allsky": "indi-allsky (SharedSky)",
    "zenivo": "Zenivo (SharedSky)",
}
_SOURCES = {
    "image_url": "Bild-Adresse (SharedSky)",
    "indi-allsky": "indi-allsky (SharedSky)",
    "zenivo": "Zenivo (SharedSky)",
}


def _sky(azimuth: object, altitude: object) -> str | None:
    if not isinstance(azimuth, int | float) or not isinstance(altitude, int | float):
        return None
    en = english()
    high = "high" if en else "hoch"
    return f"{round(azimuth) % 360}° {compass(azimuth, en)}, {round(altitude)}° {high}"


def event_rows(event: EventRecord) -> list[tuple[str, str]]:
    """Label/value rows in plain words: the keys of SPEC §6.4 the page knows; others are
    left out."""
    en = english()
    rows = [(tr("Sicherheit", "Confidence"), f"{round(event.confidence * 100)} %")]
    seconds = (event.end - event.start).total_seconds()
    if seconds > 0:
        rows.append((tr("Dauer", "Duration"), _num(f"{seconds:.1f} s")))
    data: dict[str, Any] = dict(event.data)
    if event.kind == "meteor":
        if isinstance(length := data.pop("length_px", None), int | float):
            rows.append((tr("Spurlänge", "Track length"), f"{round(length)} px"))
        if isinstance(peak := data.pop("peak", None), int | float):
            rows.append((tr("Helligkeit", "Brightness"), f"{round(peak * 100)} %"))
        if isinstance(frames := data.pop("frames", None), int | float):
            rows.append((tr("Bilder", "Frames"), str(round(frames))))
        if isinstance(direction := data.pop("direction_deg", None), int | float):
            axis = round((direction % 180) / 45) % 4
            axes = _AXES_EN if en else _AXES
            rows.append((tr("Richtung", "Direction"), f"{round(direction)}° ({axes[axis]})"))
        if shower := shower_text(data):
            rows.append((tr("Meteorstrom", "Meteor shower"), shower))
        data.pop("shower", None)
        data.pop("shower_match", None)
        # The ends of the track on the sky (SPEC §6.4, for triangulation).
        start = _sky(data.pop("start_azimuth_deg", None), data.pop("start_altitude_deg", None))
        end = _sky(data.pop("end_azimuth_deg", None), data.pop("end_altitude_deg", None))
        if start and end:
            ordered = data.get("track_ordered") is True
            if ordered:
                text = tr(f"von {start} nach {end}", f"from {start} to {end}")
            else:
                text = tr(f"zwischen {start} und {end}", f"between {start} and {end}")
            rows.append((tr("Spur am Himmel", "Track on the sky"), text))
        for key in ("track_ordered", "track", "track_rms_deg", "track_px", "trail"):
            data.pop(key, None)
    if event.kind == "lightning":
        if isinstance(area := data.pop("area_frac", None), int | float):
            rows.append((tr("Erhellter Himmel", "Sky lit"), f"{round(area * 100)} %"))
        if isinstance(peak := data.pop("peak", None), int | float):
            rows.append((tr("Aufhellung", "Brightening"), f"{round(peak * 100)} %"))
        if isinstance(flashes := data.pop("storm_flashes", None), int | float):
            rows.append((tr("Blitze in 30 Min.", "Flashes in 30 min"), str(round(flashes))))
        data.pop("storm", None)  # shown as the storm the flash belongs to
        # SPEC §7.12: the camera's lightning sensor, when it has one.
        if "sensor_km" in data:
            km = data.pop("sensor_km")
            rows.append((
                tr("Blitzsensor", "Lightning sensor"),
                tr(f"Blitz in {round(km)} km", f"lightning at {round(km)} km")
                if isinstance(km, int | float) else tr("kein Blitz gehört", "no lightning heard"),
            ))  # fmt: skip
        if (li := lightning_li.summary(event)) is not None:
            rows.append(
                (
                    tr(
                        "Blitzsatellit (Quelle: EUMETSAT)", "Lightning satellite (source: EUMETSAT)"
                    ),
                    li,
                )
            )
    if event.kind in ("aurora", "nlc"):
        if isinstance(index := data.pop("peak_index", None), int | float):
            rows.append((tr("Stärke", "Strength"), f"{round(index)} %"))
        if isinstance(green := data.pop("green", None), int | float):
            rows.append((tr("Grünanteil", "Green share"), _num(f"{green:.0f}")))
        if isinstance(blue := data.pop("blue", None), int | float):
            rows.append((tr("Blauanteil", "Blue share"), _num(f"{blue:.0f}")))
        if isinstance(frames := data.pop("frames", None), int | float):
            rows.append((tr("Bilder", "Frames"), str(round(frames))))
        if isinstance(direction := data.pop("direction_deg", None), int | float):
            # Image-relative (0 = up), not a compass bearing: no N/E/S/W without calibration.
            rows.append(
                (tr("Bildrichtung", "Direction in the image"), f"{round(direction) % 360}°")
            )
        if data.pop("ongoing", None) is True:
            rows.append(("Status", tr("läuft noch", "ongoing")))
    data.pop("image_rev", None)
    # With a calibrated camera (SPEC §4.8) the agent adds the real direction on the sky.
    if isinstance(azimuth := data.pop("azimuth_deg", None), int | float):
        rows.append(
            (
                tr("Himmelsrichtung", "Direction"),
                f"{round(azimuth) % 360}° ({compass(azimuth, en)})",
            )
        )
    if isinstance(altitude := data.pop("altitude_deg", None), int | float):
        rows.append((tr("Höhe über dem Horizont", "Altitude"), f"{round(altitude)}°"))
    if event.kind == "transient":
        ra, dec = data.pop("ra_deg", None), data.pop("dec_deg", None)
        if isinstance(ra, int | float) and isinstance(dec, int | float):
            rows.append(("Position (J2000)", _num(f"RA {ra:.2f}°, Dec {dec:+.2f}°")))
        if isinstance(frames := data.pop("frames", None), int | float):
            rows.append((tr("Bilder", "Frames"), str(round(frames))))
        if data.pop("ongoing", None) is True:
            rows.append(("Status", tr("läuft noch", "ongoing")))
    if isinstance(source := data.get("source"), str):
        data["source"] = (_SOURCES_EN if en else _SOURCES).get(source, source)
    if isinstance(mag := data.get("mag"), int | float):
        data["mag"] = _num(f"{mag:+.1f}")
    labels = _LABELS_EN if en else _LABELS
    rows.extend(
        (label, str(data[key])) for key, label in labels.items() if data.get(key) is not None
    )
    return rows

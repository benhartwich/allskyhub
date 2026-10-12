"""The camera settings form, built from the device's settings schema (SPEC §6.9).

Web UI and app API use the schema the camera sent in its last `hello`; for an old agent
without one, ``FALLBACK`` describes the fixed fields of SPEC §6.5. Values are checked here
first (so errors show at once), the camera checks them again.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError

from allskyhub_protocol import (
    SetSettingsArgs,
    SettingSpec,
    SettingType,
    invalid_settings,
    localized,
    schema_problems,
)
from allskyhub_server.devices.settings import CAMERA_CHOICES
from allskyhub_server.models import Device

FALLBACK: list[SettingSpec] = [
    SettingSpec.model_validate(s)
    for s in (
        {"key": "location", "type": "location", "label": "Standort", "group": "Standort"},
        {
            "key": "timezone",
            "type": "string",
            "label": "Zeitzone",
            "group": "Standort",
            "max_length": 64,
        },
        {
            "key": "camera",
            "type": "choice",
            "label": "Kamera",
            "group": "Kamera",
            "choices": [{"value": v, "label": t} for v, t in CAMERA_CHOICES.items()],
        },
        {
            "key": "day_delay_s",
            "type": "number",
            "label": "Pause am Tag",
            "group": "Aufnahme",
            "unit": "s",
            "min": 0,
            "max": 3600,
        },
        {
            "key": "night_delay_s",
            "type": "number",
            "label": "Pause in der Nacht",
            "group": "Aufnahme",
            "unit": "s",
            "min": 0,
            "max": 3600,
        },
    )
]


def parse_schema(raw: object) -> list[SettingSpec] | None:
    """A schema from `hello`, or None when it is missing, malformed or inconsistent."""
    if not isinstance(raw, list) or not raw:
        return None
    try:
        schema = [SettingSpec.model_validate(item) for item in cast(list[Any], raw)]
    except ValidationError:
        return None
    return None if schema_problems(schema) else schema


def schema_of(device: Device) -> tuple[list[SettingSpec], bool]:
    """The device's schema and whether it is its own (False: the fixed fallback)."""
    schema = parse_schema(device.settings_schema)
    return (schema, True) if schema is not None else (FALLBACK, False)


def current_values(device: Device) -> dict[str, Any]:
    reported = (device.last_status or {}).get("settings")
    return cast(dict[str, Any], reported) if isinstance(reported, dict) else {}


@dataclass
class FormField:
    spec: SettingSpec
    label: str
    help: str | None
    value: str  # for text inputs and selects
    checked: bool = False  # for bool
    lat: str = ""  # for location
    lon: str = ""
    choices: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])


@dataclass
class FormGroup:
    name: str
    fields: list[FormField]


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def groups(
    schema: list[SettingSpec],
    current: dict[str, Any],
    submitted: dict[str, str] | None = None,
    lang: str = "de",
) -> list[FormGroup]:
    """Fields in schema order, grouped by first appearance of each group."""
    out: dict[str, FormGroup] = {}
    for spec in schema:
        group = localized(spec.group, lang)
        f = FormField(
            spec=spec,
            label=localized(spec.label, lang),
            help=localized(spec.help, lang) if spec.help is not None else None,
            value=submitted.get(spec.key, "") if submitted else _text(current.get(spec.key)),
        )
        if spec.type is SettingType.LOCATION:
            f.lat = submitted.get("latitude", "") if submitted else _text(current.get("latitude"))
            f.lon = submitted.get("longitude", "") if submitted else _text(current.get("longitude"))
        elif spec.type is SettingType.BOOL:
            f.checked = (spec.key in submitted) if submitted else current.get(spec.key) is True
        elif spec.type is SettingType.CHOICE:
            f.choices = [(c.value, localized(c.label, lang)) for c in spec.choices or []]
        out.setdefault(group, FormGroup(group, [])).fields.append(f)
    return list(out.values())


def _number(text: str) -> float | None:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def changes(
    schema: list[SettingSpec], current: dict[str, Any], form: dict[str, str], lang: str = "de"
) -> tuple[dict[str, Any], str | None]:
    """The keys that differ from the camera's values, or a German error. Empty fields stay
    unchanged; a nullable field emptied by the user becomes null (SPEC §6.5)."""
    labels = {s.key: localized(s.label, lang) for s in schema}
    values: dict[str, Any] = {}
    unreadable: list[str] = []
    for spec in schema:
        key = spec.key
        if spec.type is SettingType.LOCATION:
            lat, lon = form.get("latitude", "").strip(), form.get("longitude", "").strip()
            if not lat and not lon:
                continue
            la, lo = _number(lat), _number(lon)
            if la is None or lo is None:
                return {}, "Bitte Breite und Länge als Zahlen angeben (beide oder keine)."
            values["latitude"], values["longitude"] = la, lo
            continue
        if spec.type is SettingType.BOOL:
            if f"{key}__present" in form:
                values[key] = key in form
            continue
        if key not in form:
            continue
        text = form[key].strip()
        if not text:
            if spec.nullable and current.get(key) is not None:
                values[key] = None
            continue
        if spec.type in (SettingType.NUMBER, SettingType.INT):
            number = _number(text)
            if number is None:
                unreadable.append(labels[key])
                continue
            values[key] = (
                int(number) if spec.type is SettingType.INT and number.is_integer() else number
            )
        else:
            values[key] = text
    if unreadable:
        return {}, f"Bitte als Zahl angeben: {', '.join(unreadable)}."
    diff = {k: v for k, v in values.items() if current.get(k) != v}
    if ("latitude" in diff) != ("longitude" in diff) and "latitude" in values:
        diff["latitude"], diff["longitude"] = values["latitude"], values["longitude"]
    bad = invalid_settings(schema, diff)
    if not bad:
        try:
            SetSettingsArgs.model_validate(diff)  # the fixed keys' own checks (time zone)
        except ValidationError as exc:
            bad = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
    if bad:
        names = [labels.get("location", "Standort") if k in ("latitude", "longitude")
                 else labels.get(k, k) for k in bad]  # fmt: skip
        return {}, f"Ungültige Angabe: {', '.join(dict.fromkeys(names))}."
    return diff, None


def field_labels(schema: list[SettingSpec], lang: str = "de") -> dict[str, str]:
    """Key → label, for naming the fields a camera rejected."""
    labels = {s.key: localized(s.label, lang) for s in schema}
    if "location" in labels:
        labels["latitude"] = labels["longitude"] = labels["location"]
    return labels

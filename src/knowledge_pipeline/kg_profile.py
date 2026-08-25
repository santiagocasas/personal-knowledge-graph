"""Load and validate declarative personal knowledge graph profiles."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


VALID_FORMATS = {
    "text",
    "number",
    "select",
    "multi_select",
    "date",
    "files",
    "checkbox",
    "url",
    "email",
    "phone",
    "objects",
}
VALID_LAYOUTS = {"basic", "profile", "action", "note"}
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ProfileError(ValueError):
    """Raised when an ontology profile cannot be safely provisioned."""


def _required_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{location} must be a non-empty string")
    return value.strip()


def _key(value: Any, location: str) -> str:
    key = _required_string(value, location)
    if not KEY_PATTERN.fullmatch(key):
        raise ProfileError(f"{location} must be snake_case")
    return key


def load_profile(path: Path) -> dict[str, Any]:
    """Load the JSON-compatible YAML profile and validate its provisioning schema."""
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileError(f"profile not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"{path} must use YAML 1.2's JSON-compatible syntax: {exc.msg} at line {exc.lineno}"
        ) from exc

    if not isinstance(profile, dict):
        raise ProfileError("profile root must be an object")
    if profile.get("version") != 1:
        raise ProfileError("profile.version must be 1")

    _key(profile.get("key"), "profile.key")
    _required_string(profile.get("name"), "profile.name")
    _required_string(profile.get("space_id"), "profile.space_id")

    namespaces = profile.get("namespaces")
    if not isinstance(namespaces, dict) or not namespaces:
        raise ProfileError("profile.namespaces must be a non-empty object")
    for prefix, uri in namespaces.items():
        _key(prefix, f"namespace key {prefix!r}")
        _required_string(uri, f"namespace {prefix}")

    types = profile.get("types")
    if not isinstance(types, list) or not types:
        raise ProfileError("profile.types must be a non-empty list")

    seen_types: set[str] = set()
    for index, type_def in enumerate(types):
        location = f"profile.types[{index}]"
        if not isinstance(type_def, dict):
            raise ProfileError(f"{location} must be an object")
        type_key = _key(type_def.get("key"), f"{location}.key")
        if type_key in seen_types:
            raise ProfileError(f"duplicate type key: {type_key}")
        seen_types.add(type_key)

        _required_string(type_def.get("name"), f"{location}.name")
        _required_string(type_def.get("plural_name"), f"{location}.plural_name")
        rdf_class = _required_string(type_def.get("rdf_class"), f"{location}.rdf_class")
        prefix = rdf_class.partition(":")[0]
        if ":" not in rdf_class or prefix not in namespaces:
            raise ProfileError(f"{location}.rdf_class uses unknown namespace: {rdf_class}")

        layout = type_def.get("layout")
        if layout not in VALID_LAYOUTS:
            raise ProfileError(f"{location}.layout must be one of {sorted(VALID_LAYOUTS)}")

        properties = type_def.get("properties")
        if not isinstance(properties, list):
            raise ProfileError(f"{location}.properties must be a list")
        seen_properties: set[str] = set()
        for prop_index, prop in enumerate(properties):
            prop_location = f"{location}.properties[{prop_index}]"
            if not isinstance(prop, dict):
                raise ProfileError(f"{prop_location} must be an object")
            prop_key = _key(prop.get("key"), f"{prop_location}.key")
            if prop_key in seen_properties:
                raise ProfileError(f"duplicate property key {prop_key!r} on type {type_key!r}")
            seen_properties.add(prop_key)
            _required_string(prop.get("name"), f"{prop_location}.name")
            if prop.get("format") not in VALID_FORMATS:
                raise ProfileError(f"{prop_location}.format must be one of {sorted(VALID_FORMATS)}")

    return profile

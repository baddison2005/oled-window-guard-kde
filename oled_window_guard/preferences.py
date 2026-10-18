"""Canonical settings loading and explicit, atomic persistence.

Defaults and validation rules are read from ../schema/settings.schema.json. The
same metadata is intended for a future QML/JavaScript loader or build-time
consumer; neither side should copy defaults into a second table.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "settings.schema.json"


class SettingsError(ValueError):
    """Persisted or proposed settings are malformed or unsupported."""


@dataclass(frozen=True)
class LoadedSettings:
    values: dict[str, Any]
    # Presence allows callers to tell an absent selection from explicit [];
    # both have an empty effective default and neither selects new outputs.
    present_fields: frozenset[str]
    error: str | None = None


def schema_metadata() -> dict[str, Any]:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"cannot read canonical settings schema: {exc}") from exc


def defaults() -> dict[str, Any]:
    props = schema_metadata()["properties"]
    return {key: json.loads(json.dumps(spec["default"])) for key, spec in props.items() if "default" in spec}


def current_version() -> int:
    return schema_metadata()["properties"]["schemaVersion"]["const"]


def default_settings_path() -> Path:
    """Return the XDG settings path without creating its directory or file."""
    configured = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(configured).expanduser() if configured else Path.home() / ".config"
    if not config_home.is_absolute():
        config_home = Path.home() / ".config"
    return config_home / "oled-window-guard" / "settings.json"


def _backup_corrupt_file(path: Path) -> Path:
    """Copy original bytes to an exclusive backup, then persist file and directory."""
    original = path.read_bytes()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for attempt in range(100):
        suffix = f"{stamp}-{attempt}" if attempt else stamp
        backup = path.with_name(path.name + f".corrupt-{suffix}.bak")
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            try:
                backup.unlink()
            except OSError:
                pass
            raise
        return backup
    raise FileExistsError(f"could not allocate unique corrupt settings backup for {path}")


def _matches_type(value: Any, expected: str | list[str]) -> bool:
    types = [expected] if isinstance(expected, str) else expected
    for kind in types:
        if kind == "null" and value is None:
            return True
        if kind == "object" and isinstance(value, dict):
            return True
        if kind == "array" and isinstance(value, list):
            return True
        if kind == "string" and isinstance(value, str):
            return True
        if kind == "boolean" and isinstance(value, bool):
            return True
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return True
    return False


def _validate_value(value: Any, spec: Mapping[str, Any], path: str, defs: Mapping[str, Any]) -> None:
    if "$ref" in spec:
        ref = spec["$ref"]
        key = ref.rsplit("/", 1)[-1]
        _validate_value(value, defs[key], path, defs)
        return
    expected = spec.get("type")
    if expected and not _matches_type(value, expected):
        raise SettingsError(f"{path} must have type {expected}")
    if "const" in spec and value != spec["const"]:
        raise SettingsError(f"{path} must equal {spec['const']!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise SettingsError(f"{path} must be one of {spec['enum']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            raise SettingsError(f"{path} is below minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise SettingsError(f"{path} is above maximum {spec['maximum']}")
        if "multipleOf" in spec and abs(value / spec["multipleOf"] - round(value / spec["multipleOf"])) > 1e-9:
            raise SettingsError(f"{path} must be a multiple of {spec['multipleOf']}")
    if isinstance(value, list):
        item_spec = spec.get("items", {})
        for index, item in enumerate(value):
            _validate_value(item, item_spec, f"{path}[{index}]", defs)
        if spec.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise SettingsError(f"{path} must contain unique values")
    if isinstance(value, dict):
        props = spec.get("properties", {})
        extra_spec = spec.get("additionalProperties", False)
        for key, item in value.items():
            child_spec = props.get(key)
            if child_spec is None:
                if extra_spec is False:
                    raise SettingsError(f"{path}.{key} is not allowed")
                child_spec = extra_spec
            _validate_value(item, child_spec, f"{path}.{key}", defs)


def validate_settings(value: Mapping[str, Any], *, fill_defaults: bool = True) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsError("settings must be a JSON object")
    schema = schema_metadata()
    props = schema["properties"]
    candidate = defaults() if fill_defaults else {}
    candidate.update(dict(value))
    candidate.setdefault("schemaVersion", current_version())
    if "schemaVersion" not in candidate:
        raise SettingsError("schemaVersion is required")
    for key, item in candidate.items():
        spec = props.get(key)
        if spec is None:
            raise SettingsError(f"unknown setting: {key}")
        _validate_value(item, spec, key, schema.get("$defs", {}))
    return candidate


def load_settings(path: os.PathLike[str] | str) -> LoadedSettings:
    """Read settings without modifying the file; bad data yields safe defaults + diagnostic."""
    settings_path = Path(path)
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return LoadedSettings(validate_settings({}), frozenset())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return LoadedSettings(validate_settings({}), frozenset(), f"cannot read settings file {settings_path}: {exc}")
    if not isinstance(raw, dict):
        return LoadedSettings(validate_settings({}), frozenset(), "settings file must contain a JSON object")
    ver = raw.get("schemaVersion")
    if ver != current_version():
        if isinstance(ver, int) and ver > current_version():
            error = f"settings schema {ver} is newer than supported schema {current_version()}"
        else:
            error = f"settings schema {ver!r} requires an explicit migration"
        return LoadedSettings(validate_settings({}), frozenset(raw), error)
    try:
        return LoadedSettings(validate_settings(raw), frozenset(raw))
    except SettingsError as exc:
        return LoadedSettings(validate_settings({}), frozenset(raw), str(exc))


def migrate_example_unversioned_to_v1(old: Mapping[str, Any]) -> dict[str, Any]:
    """Example explicit migration for an unversioned legacy-shaped document.

    This is not a claim about a previously shipped settings format. Normal
    loading never guesses a migration; callers must deliberately opt into it.
    """
    if "schemaVersion" in old:
        raise SettingsError("v0 migration expects unversioned settings")
    migrated = dict(old)
    migrated["schemaVersion"] = current_version()
    # Example legacy spelling for this opt-in migration only.
    if "selectedDisplays" in migrated:
        if "movementDisplays" in migrated:
            raise SettingsError("legacy and current movement selections conflict")
        selected = migrated.pop("selectedDisplays")
        migrated["movementDisplays"] = selected
        # Preserve a legacy selection once, but never override an explicit
        # empty dimming selection in the source document.
        migrated.setdefault("dimmingDisplays", selected)
    return validate_settings(migrated)


def save_settings(path: os.PathLike[str] | str, value: Mapping[str, Any]) -> Path | None:
    """Explicitly save validated settings atomically; return corrupt-file backup path.

    A corrupt pre-existing file is copied byte-for-byte to a unique recoverable
    sibling before replacement. Merely loading never creates or changes files.
    """
    settings_path = Path(path)
    normalized = validate_settings(value)
    backup: Path | None = None
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            backup = _backup_corrupt_file(settings_path)
        else:
            if isinstance(existing, dict):
                ver = existing.get("schemaVersion")
                if isinstance(ver, int) and not isinstance(ver, bool) and ver != current_version():
                    direction = "newer than supported" if ver > current_version() else "requires an explicit migration"
                    raise SettingsError(f"settings schema {ver} {direction}")
            try:
                if not isinstance(existing, dict) or existing.get("schemaVersion") != current_version():
                    raise SettingsError("settings schema is missing or unsupported; explicit migration required")
                validate_settings(existing)
            except SettingsError:
                backup = _backup_corrupt_file(settings_path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{settings_path.name}.", suffix=".tmp", dir=settings_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(normalized, stream, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, settings_path)
        dir_fd = os.open(settings_path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return backup

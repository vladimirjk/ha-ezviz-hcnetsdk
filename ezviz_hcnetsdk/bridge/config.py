"""Supervisor option loading and validation."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CAMERA_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
LOG_LEVELS = frozenset({"debug", "info", "warning", "error"})


class ConfigurationError(ValueError):
    """Raised when app options are invalid."""


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """Connection details for one camera."""

    camera_id: str
    host: str
    port: int
    username: str
    password: str
    channel: int


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """Validated bridge configuration."""

    api_token: str
    default_duration_ms: int
    default_speed: int
    alarm_hold_seconds: int
    log_level: str
    cameras: dict[str, CameraConfig]
    listen_port: int = 8977


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{field} must be between {minimum} and {maximum}")
    return value


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{field} must be a string")
    if not allow_empty and not value:
        raise ConfigurationError(f"{field} must not be empty")
    return value


def _camera_config(raw: object, index: int) -> CameraConfig:
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"cameras[{index}] must be an object")

    prefix = f"cameras[{index}]"
    camera_id = _text(raw.get("id"), f"{prefix}.id")
    if not CAMERA_ID_PATTERN.fullmatch(camera_id):
        raise ConfigurationError(
            f"{prefix}.id must start with a lowercase letter and contain only "
            "lowercase letters, digits, underscores, or hyphens"
        )

    host = _text(raw.get("host"), f"{prefix}.host")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError as exc:
        raise ConfigurationError(f"{prefix}.host must be an IP address") from exc

    username = _text(raw.get("username"), f"{prefix}.username")
    password = _text(raw.get("password"), f"{prefix}.password")
    if len(username.encode()) > 63:
        raise ConfigurationError(f"{prefix}.username is too long for HCNetSDK")
    if len(password.encode()) > 63:
        raise ConfigurationError(f"{prefix}.password is too long for HCNetSDK")

    return CameraConfig(
        camera_id=camera_id,
        host=host,
        port=_integer(raw.get("port"), f"{prefix}.port", 1, 65535),
        username=username,
        password=password,
        channel=_integer(raw.get("channel"), f"{prefix}.channel", 1, 256),
    )


def parse_config(raw: object) -> BridgeConfig:
    """Validate a decoded Supervisor options object."""
    if not isinstance(raw, Mapping):
        raise ConfigurationError("options must be a JSON object")

    api_token = _text(raw.get("api_token"), "api_token")
    if len(api_token) < 16:
        raise ConfigurationError("api_token must contain at least 16 characters")

    log_level = _text(raw.get("log_level", "info"), "log_level").lower()
    if log_level not in LOG_LEVELS:
        raise ConfigurationError(f"log_level must be one of: {', '.join(sorted(LOG_LEVELS))}")

    cameras_raw = raw.get("cameras")
    if not isinstance(cameras_raw, list) or not cameras_raw:
        raise ConfigurationError("at least one camera must be configured")

    cameras: dict[str, CameraConfig] = {}
    for index, camera_raw in enumerate(cameras_raw):
        camera = _camera_config(camera_raw, index)
        if camera.camera_id in cameras:
            raise ConfigurationError(f"duplicate camera id: {camera.camera_id}")
        cameras[camera.camera_id] = camera

    return BridgeConfig(
        api_token=api_token,
        default_duration_ms=_integer(
            raw.get("default_duration_ms", 250), "default_duration_ms", 50, 1500
        ),
        default_speed=_integer(raw.get("default_speed", 3), "default_speed", 1, 7),
        alarm_hold_seconds=_integer(raw.get("alarm_hold_seconds", 10), "alarm_hold_seconds", 2, 60),
        log_level=log_level,
        cameras=cameras,
    )


def load_config(path: str | Path = "/data/options.json") -> BridgeConfig:
    """Load and validate Supervisor options from disk."""
    options_path = Path(path)
    try:
        raw: Any = json.loads(options_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read options file: {options_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid JSON in options file: {options_path}") from exc
    return parse_config(raw)

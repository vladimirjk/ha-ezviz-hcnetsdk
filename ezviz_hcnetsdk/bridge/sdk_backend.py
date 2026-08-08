"""Bounded HCNetSDK camera operations."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import BridgeConfig, CameraConfig
from .isapi_probe import NativeIsapiProbe
from .power_control import NativePowerController

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SdkBindings:
    """Late-bound HCNetSDK objects, allowing offline unit tests."""

    sdk_factory: Callable[[], Any]
    commands: dict[str, int]
    power_controller_factory: Callable[[Any], Any]
    isapi_probe_factory: Callable[[Any], Any]


@dataclass(slots=True)
class _Session:
    config: CameraConfig
    lock: threading.RLock
    device: Any | None = None
    active_command: int | None = None


def load_hikvision_bindings() -> SdkBindings:
    """Import the third-party wrapper only inside the amd64 runtime image."""
    from hikvision_sdk import (
        PTZ_DOWN,
        PTZ_LEFT,
        PTZ_RIGHT,
        PTZ_UP,
        HCNetSDK,
    )

    return SdkBindings(
        sdk_factory=HCNetSDK,
        commands={
            "up": PTZ_UP,
            "down": PTZ_DOWN,
            "left": PTZ_LEFT,
            "right": PTZ_RIGHT,
        },
        power_controller_factory=NativePowerController,
        isapi_probe_factory=NativeIsapiProbe,
    )


class HcNetSdkBackend:
    """Own one SDK instance and lazily authenticated camera sessions."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        bindings: SdkBindings | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bindings = bindings or load_hikvision_bindings()
        self._sleep = sleep
        self._sdk = self._bindings.sdk_factory()
        self._sdk.init(log_level=1)
        self._power_controller = self._bindings.power_controller_factory(self._sdk)
        self._isapi_probe = self._bindings.isapi_probe_factory(self._sdk)
        self._sdk_version = self._sdk.get_sdk_version()
        self._closed = False
        self._sessions = {
            camera_id: _Session(config=camera, lock=threading.RLock())
            for camera_id, camera in config.cameras.items()
        }
        LOGGER.info(
            "HCNetSDK %s initialized for %d camera(s)",
            self._sdk_version,
            len(self._sessions),
        )

    @property
    def sdk_version(self) -> str:
        return self._sdk_version

    def _session(self, camera_id: str) -> _Session:
        try:
            return self._sessions[camera_id]
        except KeyError as exc:
            raise KeyError(f"unknown camera: {camera_id}") from exc

    def _login_locked(self, session: _Session) -> Any:
        if self._closed:
            raise RuntimeError("HCNetSDK backend is closed")
        if session.device is None:
            camera = session.config
            session.device = self._sdk.login(
                camera.host,
                camera.port,
                camera.username,
                camera.password,
            )
            LOGGER.info(
                "Connected camera %s at %s:%d on channel %d",
                camera.camera_id,
                camera.host,
                camera.port,
                camera.channel,
            )
        return session.device

    def _disconnect_locked(self, session: _Session) -> None:
        device = session.device
        if device is None:
            return
        if session.active_command is not None:
            try:
                device.ptz_control_with_speed(
                    session.config.channel,
                    session.active_command,
                    1,
                    stop=True,
                )
            except Exception:
                LOGGER.exception(
                    "Emergency PTZ stop failed for camera %s",
                    session.config.camera_id,
                )
            finally:
                session.active_command = None
        try:
            device.logout()
        except Exception:
            LOGGER.exception("Logout failed for camera %s", session.config.camera_id)
        finally:
            session.device = None

    def status(self) -> dict[str, object]:
        return {
            "sdk_version": self._sdk_version,
            "cameras": {
                camera_id: {"connected": session.device is not None}
                for camera_id, session in self._sessions.items()
            },
        }

    def test_camera(self, camera_id: str) -> dict[str, object]:
        session = self._session(camera_id)
        with session.lock:
            self._disconnect_locked(session)
            device = self._login_locked(session)
            return {
                "camera": camera_id,
                "connected": True,
                "sdk_version": self._sdk_version,
                "configured_channel": session.config.channel,
                "device_start_channel": device.start_channel,
            }

    def move(
        self,
        camera_id: str,
        direction: str,
        duration_ms: int,
        speed: int,
    ) -> dict[str, object]:
        try:
            command = self._bindings.commands[direction]
        except KeyError as exc:
            raise ValueError(f"unsupported PTZ direction: {direction}") from exc

        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            try:
                device.ptz_control_with_speed(
                    session.config.channel,
                    command,
                    speed,
                    stop=False,
                )
                session.active_command = command
                try:
                    self._sleep(duration_ms / 1000)
                finally:
                    device.ptz_control_with_speed(
                        session.config.channel,
                        command,
                        speed,
                        stop=True,
                    )
                    session.active_command = None
            except Exception:
                self._disconnect_locked(session)
                raise

        return {
            "camera": camera_id,
            "direction": direction,
            "duration_ms": duration_ms,
            "speed": speed,
        }

    def set_sleep(self, camera_id: str, enabled: bool) -> dict[str, object]:
        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            previous: int | None = None
            try:
                if enabled:
                    previous = self._power_controller.enter_sleep(
                        device,
                        session.config.channel,
                    )
                else:
                    self._power_controller.wake(device)
            except Exception:
                self._disconnect_locked(session)
                raise
            self._disconnect_locked(session)

        result: dict[str, object] = {
            "camera": camera_id,
            "sleeping": enabled,
        }
        if previous is not None:
            result["previous_power_saving_control"] = previous
        return result

    def probe_sleep(self, camera_id: str) -> dict[str, object]:
        """Read sleep-related ISAPI capabilities without changing camera state."""
        paths = (
            "/ISAPI/EZVIZ/IPC/System/servicesSwitch?format=json",
            "/ISAPI/System/deviceInfo",
            "/ISAPI/System/capabilities",
            "/ISAPI/System/consumptionMode/capabilities?format=json",
            "/ISAPI/System/consumptionMode?format=json",
        )
        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            try:
                queries = [self._isapi_probe.get(device, path) for path in paths]
            except Exception:
                self._disconnect_locked(session)
                raise
            self._disconnect_locked(session)

        return {
            "camera": camera_id,
            "read_only": True,
            "request_framing": "app_observed_crlf",
            "queries": queries,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for session in self._sessions.values():
            with session.lock:
                self._disconnect_locked(session)
        self._sdk.cleanup()
        LOGGER.info("HCNetSDK stopped")

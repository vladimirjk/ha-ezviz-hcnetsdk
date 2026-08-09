"""Bounded HCNetSDK camera login, PTZ, recording, and state operations."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .alarm_events import HcNetSdkAlarmEventManager
from .config import BridgeConfig, CameraConfig
from .manual_recording import HcNetSdkManualRecordingController
from .state_snapshot import TRANSPORT_ERROR_CODES, HcNetSdkStateReader

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SdkBindings:
    """Late-bound HCNetSDK objects, allowing offline unit tests."""

    sdk_factory: Callable[[], Any]
    commands: dict[str, int]
    auto_pan_command: int
    preset_commands: dict[str, int]
    cruise_commands: dict[str, int]
    track_commands: dict[str, int]
    state_reader_factory: Callable[[Any], Any]
    recording_controller_factory: Callable[[Any], Any]
    event_manager_factory: Callable[[Any, int], Any]


@dataclass(slots=True)
class _Session:
    config: CameraConfig
    lock: threading.RLock
    device: Any | None = None
    active_command: int | None = None
    active_cruise_route: int | None = None
    track_recording: bool = False


def load_hikvision_bindings() -> SdkBindings:
    """Import the third-party wrapper only inside the amd64 runtime image."""
    from hikvision_sdk import (
        PTZ_AUTO_PAN,
        PTZ_DOWN,
        PTZ_DOWN_LEFT,
        PTZ_DOWN_RIGHT,
        PTZ_LEFT,
        PTZ_PRESET_CLEAR,
        PTZ_PRESET_GOTO,
        PTZ_PRESET_SET,
        PTZ_RIGHT,
        PTZ_UP,
        PTZ_UP_LEFT,
        PTZ_UP_RIGHT,
        PTZ_ZOOM_IN,
        PTZ_ZOOM_OUT,
        HCNetSDK,
    )

    return SdkBindings(
        sdk_factory=HCNetSDK,
        commands={
            "up": PTZ_UP,
            "down": PTZ_DOWN,
            "left": PTZ_LEFT,
            "right": PTZ_RIGHT,
            "up_left": PTZ_UP_LEFT,
            "up_right": PTZ_UP_RIGHT,
            "down_left": PTZ_DOWN_LEFT,
            "down_right": PTZ_DOWN_RIGHT,
            "zoom_in": PTZ_ZOOM_IN,
            "zoom_out": PTZ_ZOOM_OUT,
        },
        auto_pan_command=PTZ_AUTO_PAN,
        preset_commands={
            "set": PTZ_PRESET_SET,
            "clear": PTZ_PRESET_CLEAR,
            "goto": PTZ_PRESET_GOTO,
        },
        # The pinned wrapper's cruise/track constants are incorrect. These values
        # come from the bundled official HCNetSDK 6.1.9.48 header.
        cruise_commands={
            "set_preset": 30,
            "set_dwell": 31,
            "set_speed": 32,
            "clear_point": 33,
            "run": 37,
            "stop": 38,
        },
        track_commands={"record_start": 34, "record_stop": 35, "run": 36},
        state_reader_factory=HcNetSdkStateReader,
        recording_controller_factory=HcNetSdkManualRecordingController,
        event_manager_factory=HcNetSdkAlarmEventManager,
    )


class HcNetSdkBackend:
    """Own one SDK instance and lazily authenticated camera sessions."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        bindings: SdkBindings | None = None,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bindings = bindings or load_hikvision_bindings()
        self._wait = wait
        self._sdk = self._bindings.sdk_factory()
        self._sdk.init(log_level=1)
        self._state_reader = self._bindings.state_reader_factory(self._sdk)
        self._recording_controller = self._bindings.recording_controller_factory(self._sdk)
        self._event_manager = self._bindings.event_manager_factory(
            self._sdk, config.alarm_hold_seconds
        )
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
        if session.active_cruise_route is not None:
            try:
                device.ptz_cruise(
                    session.config.channel,
                    self._bindings.cruise_commands["stop"],
                    session.active_cruise_route,
                    0,
                    0,
                )
            except Exception:
                LOGGER.exception(
                    "Emergency cruise stop failed for camera %s",
                    session.config.camera_id,
                )
            finally:
                session.active_cruise_route = None
        if session.track_recording:
            try:
                device.ptz_track(
                    session.config.channel,
                    self._bindings.track_commands["record_stop"],
                )
            except Exception:
                LOGGER.exception(
                    "Emergency track recording stop failed for camera %s",
                    session.config.camera_id,
                )
            finally:
                session.track_recording = False
        try:
            self._event_manager.close_subscription(session.config.camera_id)
        except Exception:
            LOGGER.exception(
                "Alarm subscription close failed for camera %s",
                session.config.camera_id,
            )
        try:
            device.logout()
        except Exception:
            LOGGER.exception("Logout failed for camera %s", session.config.camera_id)
        finally:
            session.device = None

    @staticmethod
    def _stop_active_ptz_locked(session: _Session, device: Any) -> None:
        if session.active_command is None:
            return
        device.ptz_control_with_speed(
            session.config.channel,
            session.active_command,
            1,
            stop=True,
        )
        session.active_command = None

    def _stop_active_cruise_locked(self, session: _Session, device: Any) -> None:
        if session.active_cruise_route is None:
            return
        device.ptz_cruise(
            session.config.channel,
            self._bindings.cruise_commands["stop"],
            session.active_cruise_route,
            0,
            0,
        )
        session.active_cruise_route = None

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
                self._stop_active_ptz_locked(session, device)
                self._stop_active_cruise_locked(session, device)
                device.ptz_control_with_speed(
                    session.config.channel,
                    command,
                    speed,
                    stop=False,
                )
                session.active_command = command
                try:
                    self._wait(duration_ms / 1000)
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

    def set_auto_pan(self, camera_id: str, enabled: bool, speed: int) -> dict[str, object]:
        """Start or stop continuous camera auto-pan."""
        command = self._bindings.auto_pan_command
        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            try:
                if enabled:
                    if session.active_command != command:
                        self._stop_active_ptz_locked(session, device)
                        self._stop_active_cruise_locked(session, device)
                        device.ptz_control_with_speed(
                            session.config.channel,
                            command,
                            speed,
                            stop=False,
                        )
                        session.active_command = command
                else:
                    device.ptz_control_with_speed(
                        session.config.channel,
                        command,
                        speed,
                        stop=True,
                    )
                    if session.active_command == command:
                        session.active_command = None
            except Exception:
                self._disconnect_locked(session)
                raise

        return {"camera": camera_id, "enabled": enabled, "speed": speed}

    def preset(self, camera_id: str, action: str, preset: int) -> dict[str, object]:
        """Save, recall, or clear one camera-stored PTZ preset."""
        try:
            command = self._bindings.preset_commands[action]
        except KeyError as exc:
            raise ValueError(f"unsupported preset action: {action}") from exc

        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            try:
                self._stop_active_ptz_locked(session, device)
                self._stop_active_cruise_locked(session, device)
                device.ptz_preset(session.config.channel, command, preset)
            except Exception:
                self._disconnect_locked(session)
                raise

        return {"camera": camera_id, "action": action, "preset": preset}

    def cruise(
        self,
        camera_id: str,
        action: str,
        route: int,
        *,
        point: int = 0,
        preset: int = 0,
        dwell: int = 0,
        speed: int = 0,
    ) -> dict[str, object]:
        """Configure, run, or stop one firmware-dependent PTZ cruise route."""
        if action not in {"set_point", "clear_point", "run", "stop"}:
            raise ValueError(f"unsupported cruise action: {action}")

        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            try:
                self._stop_active_ptz_locked(session, device)
                if action != "stop":
                    self._stop_active_cruise_locked(session, device)
                if action == "set_point":
                    for command_name, value in (
                        ("set_preset", preset),
                        ("set_dwell", dwell),
                        ("set_speed", speed),
                    ):
                        device.ptz_cruise(
                            session.config.channel,
                            self._bindings.cruise_commands[command_name],
                            route,
                            point,
                            value,
                        )
                elif action == "clear_point":
                    device.ptz_cruise(
                        session.config.channel,
                        self._bindings.cruise_commands[action],
                        route,
                        point,
                        preset,
                    )
                elif action in {"run", "stop"}:
                    device.ptz_cruise(
                        session.config.channel,
                        self._bindings.cruise_commands[action],
                        route,
                        0,
                        0,
                    )
                    session.active_cruise_route = route if action == "run" else None
            except Exception:
                self._disconnect_locked(session)
                raise

        result: dict[str, object] = {"camera": camera_id, "action": action, "route": route}
        if action in {"set_point", "clear_point"}:
            result.update({"point": point, "preset": preset})
        if action == "set_point":
            result.update({"dwell": dwell, "speed": speed})
        return result

    def track(self, camera_id: str, action: str) -> dict[str, object]:
        """Record or run the camera's firmware-dependent PTZ track."""
        try:
            command = self._bindings.track_commands[action]
        except KeyError as exc:
            raise ValueError(f"unsupported track action: {action}") from exc

        session = self._session(camera_id)
        with session.lock:
            if action == "run" and session.track_recording:
                raise ValueError("stop track recording before running the track")
            device = self._login_locked(session)
            try:
                self._stop_active_ptz_locked(session, device)
                self._stop_active_cruise_locked(session, device)
                device.ptz_track(session.config.channel, command)
                if action == "record_start":
                    session.track_recording = True
                elif action == "record_stop":
                    session.track_recording = False
            except Exception:
                self._disconnect_locked(session)
                raise

        return {"camera": camera_id, "action": action}

    def set_manual_recording(self, camera_id: str, enabled: bool) -> dict[str, object]:
        """Start or stop device-side manual recording for one camera channel."""
        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            try:
                self._recording_controller.set_enabled(
                    device.user_id,
                    session.config.channel,
                    enabled,
                )
            except Exception:
                self._disconnect_locked(session)
                raise

        return {"camera": camera_id, "manual_recording_enabled": enabled}

    def state_snapshot(self, camera_id: str, *, include_raw: bool = False) -> dict[str, object]:
        """Read diagnostic state without changing camera configuration."""
        session = self._session(camera_id)
        with session.lock:
            try:
                device = self._login_locked(session)
            except Exception as exc:
                error_code = getattr(exc, "error_code", None)
                if not isinstance(error_code, int) or error_code not in TRANSPORT_ERROR_CODES:
                    raise
                snapshot = self._state_reader.unavailable_snapshot(error_code, stage="login")
                return {
                    "camera": camera_id,
                    "read_only": True,
                    "configured_channel": session.config.channel,
                    **snapshot,
                }
            try:
                snapshot = self._state_reader.snapshot(
                    device.user_id,
                    session.config.channel,
                    device.start_channel,
                    include_raw=include_raw,
                )
            except Exception:
                self._disconnect_locked(session)
                raise
            if snapshot.get("responsive") is False:
                self._disconnect_locked(session)

        return {
            "camera": camera_id,
            "read_only": True,
            "configured_channel": session.config.channel,
            **snapshot,
        }

    def alarm_events(self, camera_id: str) -> dict[str, object]:
        """Ensure local alarm upload is armed and return latched event state."""
        session = self._session(camera_id)
        with session.lock:
            device = self._login_locked(session)
            try:
                self._event_manager.ensure_subscription(
                    camera_id,
                    device.user_id,
                    session.config.channel,
                )
            except Exception:
                self._disconnect_locked(session)
                raise
            events = self._event_manager.snapshot(camera_id)

        return {"camera": camera_id, **events}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for session in self._sessions.values():
            with session.lock:
                self._disconnect_locked(session)
        self._sdk.cleanup()
        LOGGER.info("HCNetSDK stopped")

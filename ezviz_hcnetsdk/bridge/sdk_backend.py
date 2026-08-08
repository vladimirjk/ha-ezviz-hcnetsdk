"""Bounded HCNetSDK camera operations."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import BridgeConfig, CameraConfig
from .isapi_probe import NativeIsapiProbe
from .power_control import NativePowerController, SdkOperationError
from .tls_login import EZVIZ_TLS_PORT, NativeTlsLogin

LOGGER = logging.getLogger(__name__)
SERVICES_SWITCH_PATH = "/ISAPI/EZVIZ/IPC/System/servicesSwitch?format=json"


@dataclass(frozen=True, slots=True)
class SdkBindings:
    """Late-bound HCNetSDK objects, allowing offline unit tests."""

    sdk_factory: Callable[[], Any]
    commands: dict[str, int]
    power_controller_factory: Callable[[Any], Any]
    isapi_probe_factory: Callable[[Any], Any]
    tls_login_factory: Callable[[Any], Any]


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
        tls_login_factory=NativeTlsLogin,
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
        self._tls_login = self._bindings.tls_login_factory(self._sdk)
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
        """Read sleep-related ISAPI paths over SDK-over-TLS without changing state."""
        session = self._session(camera_id)
        camera = session.config
        video_input_path = f"/ISAPI/System/Video/inputs/channels/{camera.channel}/privacyMask"
        paths = (
            SERVICES_SWITCH_PATH,
            "/ISAPI/System/deviceInfo?format=json",
            "/ISAPI/System/deviceInfo",
            "/ISAPI/System/capabilities?format=json",
            "/ISAPI/System/capabilities",
            "/ISAPI/System/consumptionMode/capabilities?format=json",
            "/ISAPI/System/consumptionMode?format=json",
            f"{video_input_path}/capabilities",
            video_input_path,
        )
        with session.lock:
            self._disconnect_locked(session)
            device = self._tls_login.login(
                camera.host,
                camera.username,
                camera.password,
                port=EZVIZ_TLS_PORT,
            )
            try:
                queries = [self._isapi_probe.get(device, path) for path in paths]
            finally:
                try:
                    self._tls_login.logout(device)
                except Exception:
                    LOGGER.exception(
                        "SDK-over-TLS logout failed for camera %s",
                        camera.camera_id,
                    )

        return {
            "camera": camera_id,
            "read_only": True,
            "transport": "sdk_over_tls",
            "port": EZVIZ_TLS_PORT,
            "request_framing": "app_observed_crlf",
            "queries": queries,
        }

    @staticmethod
    def _require_isapi_success(result: dict[str, object], action: str) -> None:
        if result.get("sdk_ok") is True:
            return
        error_code = result.get("sdk_error_code")
        if isinstance(error_code, int):
            raise SdkOperationError(action, error_code)
        raise RuntimeError(action)

    def _read_services_switch(
        self,
        device: Any,
    ) -> tuple[dict[str, object], dict[str, object]]:
        result = self._isapi_probe.get(device, SERVICES_SWITCH_PATH)
        self._require_isapi_success(result, "failed to read EZVIZ service switches")
        body = result.get("body")
        if not isinstance(body, str):
            raise RuntimeError("camera returned no EZVIZ service-switch configuration")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("camera returned invalid EZVIZ service-switch JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("camera returned an invalid EZVIZ service-switch object")
        services = payload.get("servicesSwitch")
        if not isinstance(services, dict):
            raise RuntimeError("camera response is missing servicesSwitch")
        web = services.get("web")
        if isinstance(web, bool) or not isinstance(web, int) or web not in (0, 1):
            raise RuntimeError("camera response has an invalid servicesSwitch.web value")
        return payload, services

    @classmethod
    def _require_services_switch_update_success(
        cls,
        result: dict[str, object],
    ) -> None:
        cls._require_isapi_success(
            result,
            "failed to update EZVIZ local web service",
        )
        body = result.get("body")
        if not isinstance(body, str):
            raise RuntimeError("camera returned no EZVIZ service-switch update status")
        try:
            response = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("camera returned invalid EZVIZ service-switch update JSON") from exc
        if not isinstance(response, dict):
            raise RuntimeError("camera returned an invalid EZVIZ service-switch update object")
        status_code = response.get("statusCode")
        if isinstance(status_code, bool) or status_code != 1:
            raise RuntimeError(
                f"camera rejected the EZVIZ service-switch update (statusCode={status_code!r})"
            )

    def set_web(self, camera_id: str, enabled: bool) -> dict[str, object]:
        """Change only the EZVIZ local web-service switch and verify it."""
        session = self._session(camera_id)
        with session.lock:
            self._disconnect_locked(session)
            camera = session.config
            device = self._tls_login.login(
                camera.host,
                camera.username,
                camera.password,
                port=EZVIZ_TLS_PORT,
            )
            try:
                payload, before = self._read_services_switch(device)
                before_web = bool(before["web"])
                changed = before_web != enabled
                if changed:
                    updated_payload = dict(payload)
                    updated_services = dict(before)
                    updated_services["web"] = int(enabled)
                    updated_payload["servicesSwitch"] = updated_services
                    update_result = self._isapi_probe.put_json(
                        device,
                        SERVICES_SWITCH_PATH,
                        updated_payload,
                    )
                    self._require_services_switch_update_success(update_result)
                _verified_payload, after = self._read_services_switch(device)
                if bool(after["web"]) != enabled:
                    raise RuntimeError("camera did not apply the local web-service switch")
            finally:
                try:
                    self._tls_login.logout(device)
                except Exception:
                    LOGGER.exception(
                        "SDK-over-TLS logout failed for camera %s",
                        camera.camera_id,
                    )

        return {
            "camera": camera_id,
            "web_enabled": enabled,
            "changed": changed,
            "before": before,
            "after": after,
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

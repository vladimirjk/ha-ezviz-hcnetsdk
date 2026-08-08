"""Native HCNetSDK sleep and wake operations."""

from __future__ import annotations

import ctypes
from typing import Any

NET_DVR_GET_DEVSERVER_CFG = 3257
NET_DVR_SET_DEVSERVER_CFG = 3258
NET_DVR_REMOTECONTROL_POWER_ON = 6364
POWER_SAVING_SLEEP = 1

BOOL = ctypes.c_int32
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
BYTE = ctypes.c_uint8


class DevServerConfig(ctypes.Structure):
    """HCNetSDK ``NET_DVR_DEVSERVER_CFG`` from SDK 6.1.9.48."""

    _fields_ = [
        ("dwSize", DWORD),
        ("byIrLampServer", BYTE),
        ("bytelnetServer", BYTE),
        ("byABFServer", BYTE),
        ("byEnableLEDStatus", BYTE),
        ("byEnableAutoDefog", BYTE),
        ("byEnableSupplementLight", BYTE),
        ("byEnableDeicing", BYTE),
        ("byEnableVisibleMovementPower", BYTE),
        ("byEnableThermalMovementPower", BYTE),
        ("byEnablePtzPower", BYTE),
        ("byPowerSavingControl", BYTE),
        ("byCaptureWithSupplimentLightEnabled", BYTE),
        ("byRes", BYTE * 244),
    ]


DEVSERVER_CONFIG_SIZE = 260
if ctypes.sizeof(DevServerConfig) != DEVSERVER_CONFIG_SIZE:
    raise RuntimeError("unexpected NET_DVR_DEVSERVER_CFG layout")


class SdkOperationError(RuntimeError):
    """An HCNetSDK call failed with a device error code."""

    def __init__(self, message: str, error_code: int) -> None:
        super().__init__(f"{message} (HCNetSDK error {error_code})")
        self.error_code = error_code


class NativePowerController:
    """Bind the sleep APIs missing from the pinned Python wrapper."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self._raw = sdk._sdk
        self._raw.NET_DVR_GetDVRConfig.argtypes = [
            LONG,
            DWORD,
            LONG,
            ctypes.c_void_p,
            DWORD,
            ctypes.POINTER(DWORD),
        ]
        self._raw.NET_DVR_GetDVRConfig.restype = BOOL
        self._raw.NET_DVR_SetDVRConfig.argtypes = [
            LONG,
            DWORD,
            LONG,
            ctypes.c_void_p,
            DWORD,
        ]
        self._raw.NET_DVR_SetDVRConfig.restype = BOOL
        self._raw.NET_DVR_RemoteControl.argtypes = [
            LONG,
            DWORD,
            ctypes.c_void_p,
            DWORD,
        ]
        self._raw.NET_DVR_RemoteControl.restype = BOOL

    def _failure(self, action: str) -> SdkOperationError:
        return SdkOperationError(action, int(self._sdk.get_last_error()))

    def _get_config(self, user_id: int, channel: int) -> DevServerConfig:
        config = DevServerConfig()
        config.dwSize = DEVSERVER_CONFIG_SIZE
        bytes_returned = DWORD()
        result = self._raw.NET_DVR_GetDVRConfig(
            user_id,
            NET_DVR_GET_DEVSERVER_CFG,
            channel,
            ctypes.byref(config),
            DEVSERVER_CONFIG_SIZE,
            ctypes.byref(bytes_returned),
        )
        if not result:
            raise self._failure("failed to read module service configuration")

        required_size = DevServerConfig.byPowerSavingControl.offset + ctypes.sizeof(BYTE)
        if bytes_returned.value < required_size:
            raise RuntimeError(
                "camera returned an incomplete module service configuration "
                f"({bytes_returned.value} bytes)"
            )
        return config

    def enter_sleep(self, device: Any, channel: int) -> int:
        """Preserve the current module configuration and set its sleep flag."""
        config = self._get_config(device.user_id, channel)
        previous = int(config.byPowerSavingControl)
        config.byPowerSavingControl = POWER_SAVING_SLEEP
        result = self._raw.NET_DVR_SetDVRConfig(
            device.user_id,
            NET_DVR_SET_DEVSERVER_CFG,
            channel,
            ctypes.byref(config),
            DEVSERVER_CONFIG_SIZE,
        )
        if not result:
            raise self._failure("failed to enable camera sleep mode")
        return previous

    def wake(self, device: Any) -> None:
        """Use the SDK's parameterless remote-power-on command."""
        result = self._raw.NET_DVR_RemoteControl(
            device.user_id,
            NET_DVR_REMOTECONTROL_POWER_ON,
            None,
            0,
        )
        if not result:
            raise self._failure("failed to wake camera")

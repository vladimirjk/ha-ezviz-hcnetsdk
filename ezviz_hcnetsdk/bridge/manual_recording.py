"""Native HCNetSDK device-side manual recording control."""

from __future__ import annotations

import ctypes
from typing import Any

MANUAL_RECORD_TYPE = 0


class ManualRecordingError(RuntimeError):
    """Report an HCNetSDK manual-recording failure with its native error code."""

    def __init__(self, operation: str, error_code: int) -> None:
        self.error_code = error_code
        super().__init__(
            f"failed to {operation} device-side manual recording (HCNetSDK error {error_code})"
        )


class HcNetSdkManualRecordingController:
    """Bind the two device-side manual-recording functions absent from the wrapper."""

    def __init__(self, sdk: Any) -> None:
        self._native = sdk._sdk
        self._get_last_error = sdk.get_last_error

        self._native.NET_DVR_StartDVRRecord.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._native.NET_DVR_StartDVRRecord.restype = ctypes.c_int
        self._native.NET_DVR_StopDVRRecord.argtypes = [ctypes.c_int, ctypes.c_int]
        self._native.NET_DVR_StopDVRRecord.restype = ctypes.c_int

    def set_enabled(self, user_id: int, channel: int, enabled: bool) -> None:
        """Start or stop manual recording on the camera itself."""
        if enabled:
            ok = self._native.NET_DVR_StartDVRRecord(
                user_id,
                channel,
                MANUAL_RECORD_TYPE,
            )
            operation = "start"
        else:
            ok = self._native.NET_DVR_StopDVRRecord(user_id, channel)
            operation = "stop"

        if not ok:
            raise ManualRecordingError(operation, int(self._get_last_error()))

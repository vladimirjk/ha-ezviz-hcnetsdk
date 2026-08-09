"""Local HCNetSDK motion and tamper alarm subscriptions."""

from __future__ import annotations

import ctypes
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

COMM_ALARM = 0x1100
COMM_ALARM_V30 = 0x4000
COMM_ALARM_V40 = 0x4007
ALARM_TYPE_MOTION = 3
ALARM_TYPE_TAMPER = 6
V30_CHANNEL_OFFSET = 168
V30_CHANNEL_COUNT = 64
MAX_ALARM_HEADER_BYTES = 268


class _SetupAlarmParam(ctypes.Structure):
    """Exact 20-byte HCNetSDK 6.1.9.48 NET_DVR_SETUPALARM_PARAM."""

    _fields_ = [
        ("dwSize", ctypes.c_uint),
        ("byLevel", ctypes.c_ubyte),
        ("byAlarmInfoType", ctypes.c_ubyte),
        ("byRetAlarmTypeV40", ctypes.c_ubyte),
        ("byRetDevInfoVersion", ctypes.c_ubyte),
        ("byRetVQDAlarmType", ctypes.c_ubyte),
        ("byFaceAlarmDetection", ctypes.c_ubyte),
        ("bySupport", ctypes.c_ubyte),
        ("byBrokenNetHttp", ctypes.c_ubyte),
        ("wTaskNo", ctypes.c_ushort),
        ("byDeployType", ctypes.c_ubyte),
        ("bySubScription", ctypes.c_ubyte),
        ("byRes1", ctypes.c_ubyte * 2),
        ("byAlarmTypeURL", ctypes.c_ubyte),
        ("byCustomCtrl", ctypes.c_ubyte),
    ]


class _AlarmerHeader(ctypes.Structure):
    """Prefix of NET_DVR_ALARMER through its login user ID."""

    _fields_ = [
        ("validity", ctypes.c_ubyte * 8),
        ("user_id", ctypes.c_int),
    ]


_AlarmCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_void_p,
)


class AlarmSubscriptionError(RuntimeError):
    """Report an HCNetSDK alarm-channel failure with its native error code."""

    def __init__(self, operation: str, error_code: int) -> None:
        self.error_code = error_code
        super().__init__(f"failed to {operation} alarm subscription (HCNetSDK error {error_code})")


@dataclass(frozen=True, slots=True)
class _Subscription:
    camera_id: str
    user_id: int
    channel: int
    handle: int


@dataclass(slots=True)
class _EventState:
    count: int = 0
    last_seen: str | None = None
    active_until: float = 0.0


class HcNetSdkAlarmEventManager:
    """Register one global callback and route alarm uploads by SDK login ID."""

    def __init__(
        self,
        sdk: Any,
        hold_seconds: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._native = sdk._sdk
        self._get_last_error = sdk.get_last_error
        self._hold_seconds = hold_seconds
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._registered = False
        self._subscriptions: dict[str, _Subscription] = {}
        self._camera_by_user_id: dict[int, str] = {}
        self._events: dict[str, dict[str, _EventState]] = {}
        self._last_command: dict[str, int] = {}
        self._last_alarm_type: dict[str, int] = {}
        self._callback = _AlarmCallback(self._alarm_callback)

        self._native.NET_DVR_SetDVRMessageCallBack_V50.argtypes = [
            ctypes.c_int,
            _AlarmCallback,
            ctypes.c_void_p,
        ]
        self._native.NET_DVR_SetDVRMessageCallBack_V50.restype = ctypes.c_int
        self._native.NET_DVR_SetupAlarmChan_V41.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(_SetupAlarmParam),
        ]
        self._native.NET_DVR_SetupAlarmChan_V41.restype = ctypes.c_int
        self._native.NET_DVR_CloseAlarmChan_V30.argtypes = [ctypes.c_int]
        self._native.NET_DVR_CloseAlarmChan_V30.restype = ctypes.c_int

    def _ensure_callback_locked(self) -> None:
        if self._registered:
            return
        if not self._native.NET_DVR_SetDVRMessageCallBack_V50(0, self._callback, None):
            raise AlarmSubscriptionError("register", int(self._get_last_error()))
        self._registered = True

    def ensure_subscription(self, camera_id: str, user_id: int, channel: int) -> None:
        """Create a persistent upload channel for one authenticated camera."""
        with self._lock:
            current = self._subscriptions.get(camera_id)
            if current is not None and current.user_id == user_id:
                return
            if current is not None:
                self._close_locked(camera_id)

            self._ensure_callback_locked()
            param = _SetupAlarmParam()
            param.dwSize = ctypes.sizeof(param)
            param.byLevel = 0
            param.byAlarmInfoType = 1
            param.byRetAlarmTypeV40 = 0

            self._camera_by_user_id[user_id] = camera_id
            handle = self._native.NET_DVR_SetupAlarmChan_V41(user_id, ctypes.byref(param))
            if handle < 0:
                self._camera_by_user_id.pop(user_id, None)
                raise AlarmSubscriptionError("open", int(self._get_last_error()))

            self._subscriptions[camera_id] = _Subscription(
                camera_id=camera_id,
                user_id=user_id,
                channel=channel,
                handle=int(handle),
            )
            self._events.setdefault(
                camera_id,
                {"motion": _EventState(), "tamper": _EventState()},
            )

    def _close_locked(self, camera_id: str) -> None:
        subscription = self._subscriptions.pop(camera_id, None)
        if subscription is None:
            return
        self._camera_by_user_id.pop(subscription.user_id, None)
        if not self._native.NET_DVR_CloseAlarmChan_V30(subscription.handle):
            raise AlarmSubscriptionError("close", int(self._get_last_error()))

    def close_subscription(self, camera_id: str) -> None:
        """Close one camera's alarm upload channel if it exists."""
        with self._lock:
            self._close_locked(camera_id)

    def is_subscribed(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._subscriptions

    @staticmethod
    def _v30_active(data: bytes, channel: int) -> bool:
        channel_index = channel - 1
        if not 0 <= channel_index < V30_CHANNEL_COUNT:
            return True
        offset = V30_CHANNEL_OFFSET + channel_index
        return len(data) <= offset or data[offset] != 0

    def _alarm_callback(
        self,
        command: int,
        alarmer: int | None,
        alarm_info: int | None,
        buffer_length: int,
        _user: int | None,
    ) -> None:
        try:
            if not alarmer or not alarm_info or buffer_length < 4:
                return
            header = ctypes.cast(alarmer, ctypes.POINTER(_AlarmerHeader)).contents
            data = ctypes.string_at(alarm_info, min(buffer_length, MAX_ALARM_HEADER_BYTES))
            alarm_type = struct.unpack_from("<I", data)[0]

            with self._lock:
                camera_id = self._camera_by_user_id.get(int(header.user_id))
                subscription = self._subscriptions.get(camera_id or "")
                if camera_id is None or subscription is None:
                    return
                self._last_command[camera_id] = int(command)
                self._last_alarm_type[camera_id] = int(alarm_type)
                if command not in {COMM_ALARM, COMM_ALARM_V30, COMM_ALARM_V40}:
                    return
                event_name = {
                    ALARM_TYPE_MOTION: "motion",
                    ALARM_TYPE_TAMPER: "tamper",
                }.get(alarm_type)
                if event_name is None:
                    return

                active = (
                    self._v30_active(data, subscription.channel)
                    if command == COMM_ALARM_V30
                    else True
                )
                event = self._events[camera_id][event_name]
                event.count += 1
                event.last_seen = self._wall_clock().isoformat()
                event.active_until = self._monotonic() + self._hold_seconds if active else 0.0
        except Exception:
            # Exceptions must never escape an SDK-owned callback thread.
            return

    def snapshot(self, camera_id: str) -> dict[str, object]:
        """Return bounded event state without exposing raw alarm data."""
        with self._lock:
            subscribed = camera_id in self._subscriptions
            events = self._events.setdefault(
                camera_id,
                {"motion": _EventState(), "tamper": _EventState()},
            )
            now = self._monotonic()

            def event_result(event: _EventState) -> dict[str, object]:
                return {
                    "active": event.active_until > now,
                    "count": event.count,
                    "last_seen": event.last_seen,
                }

            return {
                "subscribed": subscribed,
                "hold_seconds": self._hold_seconds,
                "motion": event_result(events["motion"]),
                "tamper": event_result(events["tamper"]),
                "last_command": self._last_command.get(camera_id),
                "last_alarm_type": self._last_alarm_type.get(camera_id),
            }

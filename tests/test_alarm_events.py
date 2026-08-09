"""Native HCNetSDK alarm subscription and event decoding tests."""

from __future__ import annotations

import ctypes
import struct
import unittest
from datetime import UTC, datetime

from bridge.alarm_events import (
    COMM_ALARM_V30,
    AlarmSubscriptionError,
    HcNetSdkAlarmEventManager,
    _AlarmerHeader,
    _SetupAlarmParam,
)


class FakeFunction:
    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)


class FakeNative:
    def __init__(self) -> None:
        self.callback: object | None = None
        self.callback_result = 1
        self.callback_calls = 0
        self.setup_result = 7
        self.close_result = 1
        self.setup_calls: list[tuple[int, bytes]] = []
        self.close_calls: list[int] = []
        self.NET_DVR_SetDVRMessageCallBack_V50 = FakeFunction(self._set_callback)
        self.NET_DVR_SetupAlarmChan_V41 = FakeFunction(self._setup)
        self.NET_DVR_CloseAlarmChan_V30 = FakeFunction(self._close)

    def _set_callback(self, index: int, callback: object, user: object) -> int:
        self.callback_calls += 1
        self.callback_index = index
        self.callback = callback
        self.callback_user = user
        return self.callback_result

    def _setup(self, user_id: int, param_pointer: object) -> int:
        param = ctypes.cast(param_pointer, ctypes.POINTER(_SetupAlarmParam)).contents
        self.setup_calls.append(
            (user_id, ctypes.string_at(ctypes.byref(param), ctypes.sizeof(param)))
        )
        return self.setup_result

    def _close(self, handle: int) -> int:
        self.close_calls.append(handle)
        return self.close_result

    def upload(self, user_id: int, alarm_type: int, *, active: bool = True) -> None:
        assert self.callback is not None
        alarmer = _AlarmerHeader()
        alarmer.user_id = user_id
        payload = bytearray(268)
        struct.pack_into("<I", payload, 0, alarm_type)
        payload[168] = int(active)
        alarm_buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        self.callback(
            COMM_ALARM_V30,
            ctypes.byref(alarmer),
            ctypes.byref(alarm_buffer),
            len(payload),
            None,
        )


class FakeSdk:
    def __init__(self, native: FakeNative) -> None:
        self._sdk = native
        self.last_error = 23

    def get_last_error(self) -> int:
        return self.last_error


class AlarmEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.native = FakeNative()
        self.sdk = FakeSdk(self.native)
        self.now = 100.0
        self.manager = HcNetSdkAlarmEventManager(
            self.sdk,
            10,
            monotonic=lambda: self.now,
            wall_clock=lambda: datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
        )

    def test_exact_native_abi_and_setup_structure(self) -> None:
        self.assertEqual(ctypes.sizeof(_SetupAlarmParam), 20)
        self.assertEqual(_SetupAlarmParam.bySubScription.offset, 15)
        self.assertEqual(_AlarmerHeader.user_id.offset, 8)

        self.manager.ensure_subscription("cam1", 42, 1)

        self.assertEqual(self.native.callback_index, 0)
        self.assertEqual(len(self.native.setup_calls), 1)
        user_id, payload = self.native.setup_calls[0]
        self.assertEqual(user_id, 42)
        self.assertEqual(len(payload), 20)
        self.assertEqual(struct.unpack_from("<I", payload, 0)[0], 20)
        self.assertEqual(payload[5], 1)
        self.assertEqual(payload[6], 0)

    def test_callback_is_registered_only_once_for_multiple_cameras(self) -> None:
        self.manager.ensure_subscription("cam1", 42, 1)
        self.native.setup_result = 8
        self.manager.ensure_subscription("cam2", 43, 1)

        self.assertEqual(self.native.callback_calls, 1)
        self.assertEqual(len(self.native.setup_calls), 2)

    def test_motion_event_is_latched_then_expires(self) -> None:
        self.manager.ensure_subscription("cam1", 42, 1)
        self.native.upload(42, 3)

        active = self.manager.snapshot("cam1")
        self.assertTrue(active["motion"]["active"])
        self.assertEqual(active["motion"]["count"], 1)
        self.assertEqual(active["motion"]["last_seen"], "2026-08-09T04:00:00+00:00")

        self.now = 111.0
        expired = self.manager.snapshot("cam1")
        self.assertFalse(expired["motion"]["active"])

    def test_tamper_and_explicit_clear_are_decoded(self) -> None:
        self.manager.ensure_subscription("cam1", 42, 1)
        self.native.upload(42, 6)
        self.assertTrue(self.manager.snapshot("cam1")["tamper"]["active"])

        self.native.upload(42, 6, active=False)
        state = self.manager.snapshot("cam1")
        self.assertFalse(state["tamper"]["active"])
        self.assertEqual(state["tamper"]["count"], 2)

    def test_unknown_user_and_alarm_type_are_ignored(self) -> None:
        self.manager.ensure_subscription("cam1", 42, 1)
        self.native.upload(99, 3)
        self.native.upload(42, 2)

        state = self.manager.snapshot("cam1")
        self.assertEqual(state["motion"]["count"], 0)
        self.assertEqual(state["last_alarm_type"], 2)

    def test_close_uses_returned_alarm_handle(self) -> None:
        self.manager.ensure_subscription("cam1", 42, 1)
        self.manager.close_subscription("cam1")

        self.assertEqual(self.native.close_calls, [7])
        self.assertFalse(self.manager.snapshot("cam1")["subscribed"])

    def test_registration_and_setup_errors_preserve_sdk_code(self) -> None:
        self.native.callback_result = 0
        with self.assertRaisesRegex(AlarmSubscriptionError, "HCNetSDK error 23"):
            self.manager.ensure_subscription("cam1", 42, 1)

        self.native.callback_result = 1
        self.native.setup_result = -1
        with self.assertRaisesRegex(AlarmSubscriptionError, "HCNetSDK error 23"):
            self.manager.ensure_subscription("cam1", 42, 1)


if __name__ == "__main__":
    unittest.main()

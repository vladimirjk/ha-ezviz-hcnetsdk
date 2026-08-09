"""Native device-side manual recording ABI and error tests."""

from __future__ import annotations

import ctypes
import unittest

from bridge.manual_recording import HcNetSdkManualRecordingController, ManualRecordingError


class FakeFunction:
    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)


class FakeNative:
    def __init__(self) -> None:
        self.start_calls: list[tuple[int, int, int]] = []
        self.stop_calls: list[tuple[int, int]] = []
        self.result = 1
        self.NET_DVR_StartDVRRecord = FakeFunction(self._start)
        self.NET_DVR_StopDVRRecord = FakeFunction(self._stop)

    def _start(self, user_id: int, channel: int, record_type: int) -> int:
        self.start_calls.append((user_id, channel, record_type))
        return self.result

    def _stop(self, user_id: int, channel: int) -> int:
        self.stop_calls.append((user_id, channel))
        return self.result


class FakeSdk:
    def __init__(self, native: FakeNative) -> None:
        self._sdk = native
        self.last_error = 23

    def get_last_error(self) -> int:
        return self.last_error


class ManualRecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.native = FakeNative()
        self.sdk = FakeSdk(self.native)
        self.controller = HcNetSdkManualRecordingController(self.sdk)

    def test_binds_exact_native_signatures(self) -> None:
        self.assertEqual(
            self.native.NET_DVR_StartDVRRecord.argtypes,
            [ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self.assertIs(self.native.NET_DVR_StartDVRRecord.restype, ctypes.c_int)
        self.assertEqual(
            self.native.NET_DVR_StopDVRRecord.argtypes,
            [ctypes.c_int, ctypes.c_int],
        )
        self.assertIs(self.native.NET_DVR_StopDVRRecord.restype, ctypes.c_int)

    def test_start_uses_manual_record_type_zero(self) -> None:
        self.controller.set_enabled(42, 3, True)

        self.assertEqual(self.native.start_calls, [(42, 3, 0)])
        self.assertEqual(self.native.stop_calls, [])

    def test_stop_uses_camera_channel(self) -> None:
        self.controller.set_enabled(42, 3, False)

        self.assertEqual(self.native.start_calls, [])
        self.assertEqual(self.native.stop_calls, [(42, 3)])

    def test_native_error_is_propagated(self) -> None:
        self.native.result = 0
        self.sdk.last_error = 19

        with self.assertRaisesRegex(ManualRecordingError, "HCNetSDK error 19") as raised:
            self.controller.set_enabled(42, 3, True)

        self.assertEqual(raised.exception.error_code, 19)


if __name__ == "__main__":
    unittest.main()

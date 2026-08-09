"""HCNetSDK operation sequencing tests."""

from __future__ import annotations

import unittest

from bridge.config import parse_config
from bridge.sdk_backend import HcNetSdkBackend, SdkBindings
from test_config import valid_options


class FakeDevice:
    start_channel = 1
    user_id = 42

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int, bool]] = []
        self.logged_out = False

    def ptz_control_with_speed(self, channel: int, command: int, speed: int, *, stop: bool) -> None:
        self.calls.append((channel, command, speed, stop))

    def logout(self) -> None:
        self.logged_out = True


class FailingStopDevice(FakeDevice):
    def ptz_control_with_speed(self, channel: int, command: int, speed: int, *, stop: bool) -> None:
        super().ptz_control_with_speed(channel, command, speed, stop=stop)
        if stop:
            raise RuntimeError("stop failed")


class FakeSdk:
    def __init__(self) -> None:
        self.device = FakeDevice()
        self.login_calls: list[tuple[str, int, str, str]] = []
        self.cleaned_up = False
        self.login_error: Exception | None = None

    def init(self, *, log_level: int) -> None:
        self.log_level = log_level

    def get_sdk_version(self) -> str:
        return "6.1.9.48"

    def login(self, host: str, port: int, username: str, password: str) -> FakeDevice:
        self.login_calls.append((host, port, username, password))
        if self.login_error is not None:
            raise self.login_error
        return self.device

    def cleanup(self) -> None:
        self.cleaned_up = True


class FakeStateReader:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int, bool]] = []
        self.result: dict[str, object] = {
            "responsive": True,
            "supported_queries": 2,
            "failed_queries": 7,
            "skipped_queries": 0,
            "queries": {},
        }

    def snapshot(
        self,
        user_id: int,
        channel: int,
        start_channel: int,
        *,
        include_raw: bool = False,
    ) -> dict[str, object]:
        self.calls.append((user_id, channel, start_channel, include_raw))
        return self.result

    @staticmethod
    def unavailable_snapshot(error_code: int, *, stage: str) -> dict[str, object]:
        return {
            "responsive": False,
            "failure_stage": stage,
            "transport_error_code": error_code,
            "supported_queries": 0,
            "failed_queries": 0,
            "skipped_queries": 9,
            "queries": {},
        }


class FakeSdkError(RuntimeError):
    def __init__(self, error_code: int) -> None:
        self.error_code = error_code
        super().__init__(f"SDK error {error_code}")


class SdkBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = FakeSdk()
        self.state_reader = FakeStateReader()
        bindings = SdkBindings(
            sdk_factory=lambda: self.sdk,
            commands={"up": 21, "down": 22, "left": 23, "right": 24},
            state_reader_factory=lambda _sdk: self.state_reader,
        )
        self.waits: list[float] = []
        self.backend = HcNetSdkBackend(
            parse_config(valid_options()),
            bindings=bindings,
            wait=self.waits.append,
        )

    def tearDown(self) -> None:
        self.backend.close()

    def test_test_camera_logs_in(self) -> None:
        result = self.backend.test_camera("cam1")
        self.assertTrue(result["connected"])
        self.assertEqual(result["device_start_channel"], 1)
        self.assertEqual(len(self.sdk.login_calls), 1)

    def test_move_always_sends_start_then_stop(self) -> None:
        result = self.backend.move("cam1", "left", 250, 3)
        self.assertEqual(result["direction"], "left")
        self.assertEqual(self.waits, [0.25])
        self.assertEqual(
            self.sdk.device.calls,
            [(1, 23, 3, False), (1, 23, 3, True)],
        )

    def test_state_snapshot_uses_authenticated_camera_session(self) -> None:
        result = self.backend.state_snapshot("cam1", include_raw=True)

        self.assertTrue(result["read_only"])
        self.assertEqual(result["supported_queries"], 2)
        self.assertEqual(self.state_reader.calls, [(42, 1, 1, True)])

    def test_unresponsive_snapshot_disconnects_stale_session(self) -> None:
        self.state_reader.result = {
            "responsive": False,
            "transport_error_code": 10,
            "supported_queries": 0,
            "failed_queries": 1,
            "skipped_queries": 8,
            "queries": {},
        }

        result = self.backend.state_snapshot("cam1")

        self.assertFalse(result["responsive"])
        self.assertTrue(self.sdk.device.logged_out)

    def test_transport_login_failure_is_returned_as_unresponsive_state(self) -> None:
        self.sdk.login_error = FakeSdkError(7)

        result = self.backend.state_snapshot("cam1")

        self.assertFalse(result["responsive"])
        self.assertEqual(result["failure_stage"], "login")
        self.assertEqual(result["transport_error_code"], 7)

    def test_authentication_login_failure_is_not_reported_as_sleep(self) -> None:
        self.sdk.login_error = FakeSdkError(1)

        with self.assertRaisesRegex(FakeSdkError, "SDK error 1"):
            self.backend.state_snapshot("cam1")

    def test_unknown_camera_is_rejected(self) -> None:
        with self.assertRaisesRegex(KeyError, "unknown camera"):
            self.backend.test_camera("missing")

    def test_stop_failure_disconnects_camera(self) -> None:
        self.sdk.device = FailingStopDevice()

        with (
            self.assertLogs("bridge.sdk_backend", level="ERROR"),
            self.assertRaisesRegex(RuntimeError, "stop failed"),
        ):
            self.backend.move("cam1", "right", 250, 3)

        self.assertTrue(self.sdk.device.logged_out)
        self.assertEqual(
            self.sdk.device.calls,
            [
                (1, 24, 3, False),
                (1, 24, 3, True),
                (1, 24, 1, True),
            ],
        )

    def test_close_logs_out_and_cleans_up(self) -> None:
        self.backend.test_camera("cam1")
        self.backend.close()
        self.assertTrue(self.sdk.device.logged_out)
        self.assertTrue(self.sdk.cleaned_up)


if __name__ == "__main__":
    unittest.main()

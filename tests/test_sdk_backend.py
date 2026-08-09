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
        self.preset_calls: list[tuple[int, int, int]] = []
        self.cruise_calls: list[tuple[int, int, int, int, int]] = []
        self.track_calls: list[tuple[int, int]] = []
        self.logged_out = False

    def ptz_control_with_speed(self, channel: int, command: int, speed: int, *, stop: bool) -> None:
        self.calls.append((channel, command, speed, stop))

    def ptz_preset(self, channel: int, command: int, preset: int) -> None:
        self.preset_calls.append((channel, command, preset))

    def ptz_cruise(self, channel: int, command: int, route: int, point: int, value: int) -> None:
        self.cruise_calls.append((channel, command, route, point, value))

    def ptz_track(self, channel: int, command: int) -> None:
        self.track_calls.append((channel, command))

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


class FakeRecordingController:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, bool]] = []
        self.error: Exception | None = None

    def set_enabled(self, user_id: int, channel: int, enabled: bool) -> None:
        self.calls.append((user_id, channel, enabled))
        if self.error is not None:
            raise self.error


class FakeEventManager:
    def __init__(self) -> None:
        self.ensure_calls: list[tuple[str, int, int]] = []
        self.close_calls: list[str] = []
        self.error: Exception | None = None

    def ensure_subscription(self, camera_id: str, user_id: int, channel: int) -> None:
        self.ensure_calls.append((camera_id, user_id, channel))
        if self.error is not None:
            raise self.error

    def close_subscription(self, camera_id: str) -> None:
        self.close_calls.append(camera_id)

    @staticmethod
    def snapshot(_camera_id: str) -> dict[str, object]:
        return {
            "subscribed": True,
            "hold_seconds": 10,
            "motion": {"active": False, "count": 0, "last_seen": None},
            "tamper": {"active": False, "count": 0, "last_seen": None},
        }


class FakeSdkError(RuntimeError):
    def __init__(self, error_code: int) -> None:
        self.error_code = error_code
        super().__init__(f"SDK error {error_code}")


class SdkBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = FakeSdk()
        self.state_reader = FakeStateReader()
        self.recording_controller = FakeRecordingController()
        self.event_manager = FakeEventManager()
        bindings = SdkBindings(
            sdk_factory=lambda: self.sdk,
            commands={
                "up": 21,
                "down": 22,
                "left": 23,
                "right": 24,
                "up_left": 25,
                "zoom_in": 11,
            },
            auto_pan_command=29,
            preset_commands={"set": 8, "clear": 9, "goto": 39},
            cruise_commands={
                "set_preset": 30,
                "set_dwell": 31,
                "set_speed": 32,
                "clear_point": 33,
                "run": 37,
                "stop": 38,
            },
            track_commands={"record_start": 34, "record_stop": 35, "run": 36},
            state_reader_factory=lambda _sdk: self.state_reader,
            recording_controller_factory=lambda _sdk: self.recording_controller,
            event_manager_factory=lambda _sdk, _hold: self.event_manager,
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

    def test_diagonal_and_zoom_use_bounded_ptz_commands(self) -> None:
        self.backend.move("cam1", "up_left", 100, 2)
        self.backend.move("cam1", "zoom_in", 150, 4)

        self.assertEqual(self.waits, [0.1, 0.15])
        self.assertEqual(
            self.sdk.device.calls,
            [
                (1, 25, 2, False),
                (1, 25, 2, True),
                (1, 11, 4, False),
                (1, 11, 4, True),
            ],
        )

    def test_auto_pan_starts_and_stops_continuous_command(self) -> None:
        self.backend.set_auto_pan("cam1", True, 3)
        self.backend.set_auto_pan("cam1", False, 3)

        self.assertEqual(
            self.sdk.device.calls,
            [(1, 29, 3, False), (1, 29, 3, True)],
        )

    def test_state_snapshot_uses_authenticated_camera_session(self) -> None:
        result = self.backend.state_snapshot("cam1", include_raw=True)

        self.assertTrue(result["read_only"])
        self.assertEqual(result["supported_queries"], 2)
        self.assertEqual(self.state_reader.calls, [(42, 1, 1, True)])

    def test_preset_uses_camera_channel_and_exact_command(self) -> None:
        result = self.backend.preset("cam1", "goto", 2)

        self.assertEqual(result, {"camera": "cam1", "action": "goto", "preset": 2})
        self.assertEqual(self.sdk.device.preset_calls, [(1, 39, 2)])

    def test_unknown_preset_action_is_rejected_before_login(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported preset action"):
            self.backend.preset("cam1", "rename", 2)

        self.assertEqual(self.sdk.login_calls, [])

    def test_cruise_set_point_uses_official_command_values(self) -> None:
        result = self.backend.cruise(
            "cam1",
            "set_point",
            1,
            point=2,
            preset=3,
            dwell=10,
            speed=4,
        )

        self.assertEqual(result["action"], "set_point")
        self.assertEqual(
            self.sdk.device.cruise_calls,
            [
                (1, 30, 1, 2, 3),
                (1, 31, 1, 2, 10),
                (1, 32, 1, 2, 4),
            ],
        )

    def test_unknown_cruise_action_is_rejected_before_login(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported cruise action"):
            self.backend.cruise("cam1", "rename", 1)

        self.assertEqual(self.sdk.login_calls, [])

    def test_bounded_move_stops_running_cruise_first(self) -> None:
        self.backend.cruise("cam1", "run", 2)
        self.backend.move("cam1", "right", 50, 1)

        self.assertEqual(
            self.sdk.device.cruise_calls,
            [(1, 37, 2, 0, 0), (1, 38, 2, 0, 0)],
        )
        self.assertEqual(
            self.sdk.device.calls,
            [(1, 24, 1, False), (1, 24, 1, True)],
        )

    def test_track_record_and_run_use_official_command_values(self) -> None:
        self.backend.track("cam1", "record_start")
        self.backend.move("cam1", "right", 50, 1)
        self.backend.track("cam1", "record_stop")
        self.backend.track("cam1", "run")

        self.assertEqual(
            self.sdk.device.track_calls,
            [(1, 34), (1, 35), (1, 36)],
        )

    def test_running_track_while_recording_is_rejected_without_disconnect(self) -> None:
        self.backend.track("cam1", "record_start")

        with self.assertRaisesRegex(ValueError, "stop track recording"):
            self.backend.track("cam1", "run")

        self.assertFalse(self.sdk.device.logged_out)
        self.assertEqual(self.sdk.device.track_calls, [(1, 34)])

    def test_manual_recording_uses_authenticated_session(self) -> None:
        result = self.backend.set_manual_recording("cam1", False)

        self.assertEqual(result, {"camera": "cam1", "manual_recording_enabled": False})
        self.assertEqual(self.recording_controller.calls, [(42, 1, False)])

    def test_manual_recording_failure_disconnects_camera(self) -> None:
        self.recording_controller.error = FakeSdkError(19)

        with self.assertRaisesRegex(FakeSdkError, "SDK error 19"):
            self.backend.set_manual_recording("cam1", True)

        self.assertTrue(self.sdk.device.logged_out)

    def test_alarm_events_arms_authenticated_camera(self) -> None:
        result = self.backend.alarm_events("cam1")

        self.assertTrue(result["subscribed"])
        self.assertEqual(self.event_manager.ensure_calls, [("cam1", 42, 1)])

    def test_alarm_subscription_failure_disconnects_camera(self) -> None:
        self.event_manager.error = FakeSdkError(23)

        with self.assertRaisesRegex(FakeSdkError, "SDK error 23"):
            self.backend.alarm_events("cam1")

        self.assertTrue(self.sdk.device.logged_out)
        self.assertEqual(self.event_manager.close_calls, ["cam1"])

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

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

    def init(self, *, log_level: int) -> None:
        self.log_level = log_level

    def get_sdk_version(self) -> str:
        return "6.1.9.48"

    def login(self, host: str, port: int, username: str, password: str) -> FakeDevice:
        self.login_calls.append((host, port, username, password))
        return self.device

    def cleanup(self) -> None:
        self.cleaned_up = True


class FakePowerController:
    def __init__(self, _sdk: FakeSdk) -> None:
        self.sleep_calls: list[tuple[FakeDevice, int]] = []
        self.wake_calls: list[FakeDevice] = []

    def enter_sleep(self, device: FakeDevice, channel: int) -> int:
        self.sleep_calls.append((device, channel))
        return 0

    def wake(self, device: FakeDevice) -> None:
        self.wake_calls.append(device)


class FakeIsapiProbe:
    def __init__(self, _sdk: FakeSdk) -> None:
        self.get_calls: list[tuple[FakeDevice, str]] = []

    def get(self, device: FakeDevice, path: str) -> dict[str, object]:
        self.get_calls.append((device, path))
        return {"request": f"GET {path}", "sdk_ok": True, "body": "{}"}


class SdkBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = FakeSdk()
        bindings = SdkBindings(
            sdk_factory=lambda: self.sdk,
            commands={"up": 21, "down": 22, "left": 23, "right": 24},
            power_controller_factory=FakePowerController,
            isapi_probe_factory=FakeIsapiProbe,
        )
        self.sleeps: list[float] = []
        self.backend = HcNetSdkBackend(
            parse_config(valid_options()),
            bindings=bindings,
            sleep=self.sleeps.append,
        )
        self.power = self.backend._power_controller
        self.isapi_probe = self.backend._isapi_probe

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
        self.assertEqual(self.sleeps, [0.25])
        self.assertEqual(
            self.sdk.device.calls,
            [(1, 23, 3, False), (1, 23, 3, True)],
        )

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

    def test_sleep_uses_configured_channel_and_disconnects(self) -> None:
        result = self.backend.set_sleep("cam1", True)

        self.assertEqual(result["sleeping"], True)
        self.assertEqual(result["previous_power_saving_control"], 0)
        self.assertEqual(self.power.sleep_calls, [(self.sdk.device, 1)])
        self.assertTrue(self.sdk.device.logged_out)

    def test_wake_uses_remote_power_control_and_disconnects(self) -> None:
        result = self.backend.set_sleep("cam1", False)

        self.assertEqual(result, {"camera": "cam1", "sleeping": False})
        self.assertEqual(self.power.wake_calls, [self.sdk.device])
        self.assertTrue(self.sdk.device.logged_out)

    def test_sleep_probe_uses_only_expected_get_paths_and_disconnects(self) -> None:
        result = self.backend.probe_sleep("cam1")

        self.assertTrue(result["read_only"])
        self.assertEqual(len(result["queries"]), 4)
        self.assertEqual(
            [path for _device, path in self.isapi_probe.get_calls],
            [
                "/ISAPI/System/consumptionMode/capabilities?format=json",
                "/ISAPI/System/consumptionMode?format=json",
                "/ISAPI/System/consumptionMode/capabilities",
                "/ISAPI/System/consumptionMode",
            ],
        )
        self.assertTrue(self.sdk.device.logged_out)


if __name__ == "__main__":
    unittest.main()

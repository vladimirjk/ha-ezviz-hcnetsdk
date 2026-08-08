"""Native sleep and wake binding tests."""

from __future__ import annotations

import ctypes
import unittest

from bridge.power_control import (
    DEVSERVER_CONFIG_SIZE,
    NET_DVR_GET_DEVSERVER_CFG,
    NET_DVR_REMOTECONTROL_POWER_ON,
    NET_DVR_SET_DEVSERVER_CFG,
    DevServerConfig,
    NativePowerController,
    SdkOperationError,
)


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class FakeRawSdk:
    def __init__(self) -> None:
        self.source = DevServerConfig()
        self.source.dwSize = DEVSERVER_CONFIG_SIZE
        self.source.byEnableLEDStatus = 1
        self.source.byPowerSavingControl = 2
        self.source.byRes[0] = 0xA5
        self.get_calls: list[tuple[int, int, int, int]] = []
        self.set_calls: list[tuple[int, int, int, bytes]] = []
        self.remote_calls: list[tuple[int, int, object, int]] = []
        self.fail_get = False
        self.NET_DVR_GetDVRConfig = FakeFunction(self._get_config)
        self.NET_DVR_SetDVRConfig = FakeFunction(self._set_config)
        self.NET_DVR_RemoteControl = FakeFunction(self._remote_control)

    def _get_config(self, user_id, command, channel, output, size, returned):
        self.get_calls.append((user_id, command, channel, size))
        if self.fail_get:
            return 0
        ctypes.memmove(output, ctypes.byref(self.source), DEVSERVER_CONFIG_SIZE)
        ctypes.cast(
            returned, ctypes.POINTER(ctypes.c_uint32)
        ).contents.value = DEVSERVER_CONFIG_SIZE
        return 1

    def _set_config(self, user_id, command, channel, input_buffer, size):
        payload = ctypes.string_at(input_buffer, size)
        self.set_calls.append((user_id, command, channel, payload))
        return 1

    def _remote_control(self, user_id, command, input_buffer, size):
        self.remote_calls.append((user_id, command, input_buffer, size))
        return 1


class FakeSdk:
    def __init__(self) -> None:
        self._sdk = FakeRawSdk()

    def get_last_error(self) -> int:
        return 23


class FakeDevice:
    user_id = 42


class PowerControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = FakeSdk()
        self.controller = NativePowerController(self.sdk)
        self.device = FakeDevice()

    def test_structure_matches_official_sdk_size(self) -> None:
        self.assertEqual(ctypes.sizeof(DevServerConfig), 260)

    def test_sleep_preserves_config_and_changes_only_power_saving_byte(self) -> None:
        before = bytearray(
            ctypes.string_at(ctypes.byref(self.sdk._sdk.source), DEVSERVER_CONFIG_SIZE)
        )

        previous = self.controller.enter_sleep(self.device, 1)

        self.assertEqual(previous, 2)
        self.assertEqual(
            self.sdk._sdk.get_calls,
            [(42, NET_DVR_GET_DEVSERVER_CFG, 1, DEVSERVER_CONFIG_SIZE)],
        )
        self.assertEqual(len(self.sdk._sdk.set_calls), 1)
        user_id, command, channel, payload = self.sdk._sdk.set_calls[0]
        self.assertEqual((user_id, command, channel), (42, NET_DVR_SET_DEVSERVER_CFG, 1))
        before[DevServerConfig.byPowerSavingControl.offset] = 1
        self.assertEqual(payload, bytes(before))

    def test_wake_uses_parameterless_remote_power_on(self) -> None:
        self.controller.wake(self.device)

        self.assertEqual(
            self.sdk._sdk.remote_calls,
            [(42, NET_DVR_REMOTECONTROL_POWER_ON, None, 0)],
        )

    def test_sdk_error_code_is_preserved(self) -> None:
        self.sdk._sdk.fail_get = True

        with self.assertRaises(SdkOperationError) as raised:
            self.controller.enter_sleep(self.device, 1)

        self.assertEqual(raised.exception.error_code, 23)
        self.assertEqual(self.sdk._sdk.set_calls, [])


if __name__ == "__main__":
    unittest.main()

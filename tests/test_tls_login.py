"""HCNetSDK SDK-over-TLS login tests."""

from __future__ import annotations

import ctypes
import unittest

from bridge.tls_login import (
    DEVICE_INFO_V40_SIZE,
    LOGIN_INFO_SIZE,
    DeviceInfoV40,
    NativeTlsLogin,
    TlsLoginInfo,
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
        self.login_result = 51
        self.logout_result = 1
        self.login_values: dict[str, object] = {}
        self.logout_calls: list[int] = []
        self.NET_DVR_Login_V40 = FakeFunction(self._login)
        self.NET_DVR_Logout_V30 = FakeFunction(self._logout)

    def _login(self, login_pointer, device_info_pointer):
        login = ctypes.cast(login_pointer, ctypes.POINTER(TlsLoginInfo)).contents
        device_info = ctypes.cast(
            device_info_pointer,
            ctypes.POINTER(DeviceInfoV40),
        ).contents
        self.login_values = {
            "host": login.sDeviceAddress.decode(),
            "port": login.wPort,
            "username": login.sUserName.decode(),
            "password": login.sPassword.decode(),
            "async": login.bUseAsynLogin,
            "https": login.byHttps,
            "device_info_size": ctypes.sizeof(device_info),
        }
        return self.login_result

    def _logout(self, user_id):
        self.logout_calls.append(user_id)
        return self.logout_result


class FakeSdk:
    def __init__(self) -> None:
        self._sdk = FakeRawSdk()

    def get_last_error(self) -> int:
        return 23


class TlsLoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = FakeSdk()
        self.login = NativeTlsLogin(self.sdk)

    def test_structures_match_official_64_bit_sdk_layout(self) -> None:
        self.assertEqual(ctypes.sizeof(TlsLoginInfo), LOGIN_INFO_SIZE)
        self.assertEqual(ctypes.sizeof(DeviceInfoV40), DEVICE_INFO_V40_SIZE)
        self.assertEqual(TlsLoginInfo.wPort.offset, 130)
        self.assertEqual(TlsLoginInfo.cbLoginResult.offset, 264)
        self.assertEqual(TlsLoginInfo.byHttps.offset, 287)
        self.assertEqual(TlsLoginInfo.byRes3.offset, 293)

    def test_login_sets_https_flag_and_default_tls_port(self) -> None:
        device = self.login.login("192.168.0.138", "admin", "code")

        self.assertEqual(device.user_id, 51)
        self.assertEqual(
            self.sdk._sdk.login_values,
            {
                "host": "192.168.0.138",
                "port": 8443,
                "username": "admin",
                "password": "code",
                "async": 0,
                "https": 1,
                "device_info_size": 344,
            },
        )

    def test_login_failure_reports_immediate_sdk_error(self) -> None:
        self.sdk._sdk.login_result = -1

        with self.assertRaisesRegex(RuntimeError, "HCNetSDK error 23"):
            self.login.login("192.168.0.138", "admin", "code")

    def test_logout_uses_tls_user_id(self) -> None:
        device = self.login.login("192.168.0.138", "admin", "code")
        self.login.logout(device)

        self.assertEqual(self.sdk._sdk.logout_calls, [51])


if __name__ == "__main__":
    unittest.main()

"""Native HCNetSDK V40 login over the EZVIZ SDK TLS port."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any

BOOL = ctypes.c_int32
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
WORD = ctypes.c_uint16
BYTE = ctypes.c_uint8

LOGIN_INFO_SIZE = 416
DEVICE_INFO_V40_SIZE = 344
EZVIZ_TLS_PORT = 8443


class TlsLoginInfo(ctypes.Structure):
    """HCNetSDK 6.1.9.48 ``NET_DVR_USER_LOGIN_INFO`` on x86-64."""

    _fields_ = [
        ("sDeviceAddress", ctypes.c_char * 129),
        ("byUseTransport", BYTE),
        ("wPort", WORD),
        ("sUserName", ctypes.c_char * 64),
        ("sPassword", ctypes.c_char * 64),
        ("cbLoginResult", ctypes.c_void_p),
        ("pUser", ctypes.c_void_p),
        ("bUseAsynLogin", BOOL),
        ("byProxyType", BYTE),
        ("byUseUTCTime", BYTE),
        ("byLoginMode", BYTE),
        ("byHttps", BYTE),
        ("iProxyID", LONG),
        ("byVerifyMode", BYTE),
        ("byRes3", BYTE * 119),
    ]


class DeviceInfoV40(ctypes.Structure):
    """Opaque output storage for ``NET_DVR_DEVICEINFO_V40``."""

    _fields_ = [("raw", BYTE * DEVICE_INFO_V40_SIZE)]


if ctypes.sizeof(TlsLoginInfo) != LOGIN_INFO_SIZE:
    raise RuntimeError("unexpected NET_DVR_USER_LOGIN_INFO layout")
if ctypes.sizeof(DeviceInfoV40) != DEVICE_INFO_V40_SIZE:
    raise RuntimeError("unexpected NET_DVR_DEVICEINFO_V40 layout")


@dataclass(frozen=True, slots=True)
class TlsDevice:
    """The user ID returned by one synchronous TLS login."""

    user_id: int


class NativeTlsLogin:
    """Create short-lived V40 sessions with the native ``byHttps`` flag set."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self._raw = sdk._sdk
        # The wrapper binds this function to its own equivalent ctypes classes.
        # Void pointers keep both its normal login and this exact ABI structure usable.
        self._raw.NET_DVR_Login_V40.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._raw.NET_DVR_Login_V40.restype = LONG
        self._raw.NET_DVR_Logout_V30.argtypes = [LONG]
        self._raw.NET_DVR_Logout_V30.restype = BOOL

    def login(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = EZVIZ_TLS_PORT,
    ) -> TlsDevice:
        """Log in synchronously over SDK-over-TLS."""
        address_bytes = host.encode()
        username_bytes = username.encode()
        password_bytes = password.encode()
        if len(address_bytes) > 128:
            raise ValueError("camera address is too long for HCNetSDK")
        if len(username_bytes) > 63:
            raise ValueError("camera username is too long for HCNetSDK")
        if len(password_bytes) > 63:
            raise ValueError("camera password is too long for HCNetSDK")

        login_info = TlsLoginInfo()
        login_info.sDeviceAddress = address_bytes
        login_info.wPort = port
        login_info.sUserName = username_bytes
        login_info.sPassword = password_bytes
        login_info.bUseAsynLogin = 0
        login_info.byHttps = 1
        device_info = DeviceInfoV40()

        user_id = int(
            self._raw.NET_DVR_Login_V40(
                ctypes.byref(login_info),
                ctypes.byref(device_info),
            )
        )
        if user_id < 0:
            error_code = int(self._sdk.get_last_error())
            raise RuntimeError(
                f"failed SDK-over-TLS login to {host}:{port} (HCNetSDK error {error_code})"
            )
        return TlsDevice(user_id=user_id)

    def logout(self, device: TlsDevice) -> None:
        """Close a TLS user session."""
        if not bool(self._raw.NET_DVR_Logout_V30(device.user_id)):
            error_code = int(self._sdk.get_last_error())
            raise RuntimeError(f"failed SDK-over-TLS logout (HCNetSDK error {error_code})")

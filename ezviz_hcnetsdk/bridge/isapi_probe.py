"""Read-only ISAPI requests tunneled through an HCNetSDK login."""

from __future__ import annotations

import ctypes
from typing import Any

BOOL = ctypes.c_int32
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
BYTE = ctypes.c_uint8

OUTPUT_BUFFER_SIZE = 32 * 1024
STATUS_BUFFER_SIZE = 8 * 1024
XML_CONFIG_INPUT_SIZE = 72
XML_CONFIG_OUTPUT_SIZE = 72


class XmlConfigInput(ctypes.Structure):
    """HCNetSDK 6.1.9.48 ``NET_DVR_XML_CONFIG_INPUT`` on x86-64."""

    _fields_ = [
        ("dwSize", DWORD),
        ("lpRequestUrl", ctypes.c_void_p),
        ("dwRequestUrlLen", DWORD),
        ("lpInBuffer", ctypes.c_void_p),
        ("dwInBufferSize", DWORD),
        ("dwRecvTimeOut", DWORD),
        ("byForceEncrpt", BYTE),
        ("byNumOfMultiPart", BYTE),
        ("byMIMEType", BYTE),
        ("byRes1", BYTE),
        ("dwSendTimeOut", DWORD),
        ("byRes", BYTE * 24),
    ]


class XmlConfigOutput(ctypes.Structure):
    """HCNetSDK 6.1.9.48 ``NET_DVR_XML_CONFIG_OUTPUT`` on x86-64."""

    _fields_ = [
        ("dwSize", DWORD),
        ("lpOutBuffer", ctypes.c_void_p),
        ("dwOutBufferSize", DWORD),
        ("dwReturnedXMLSize", DWORD),
        ("lpStatusBuffer", ctypes.c_void_p),
        ("dwStatusSize", DWORD),
        ("lpDataBuffer", ctypes.c_void_p),
        ("byNumOfMultiPart", BYTE),
        ("byRes", BYTE * 23),
    ]


if ctypes.sizeof(XmlConfigInput) != XML_CONFIG_INPUT_SIZE:
    raise RuntimeError("unexpected NET_DVR_XML_CONFIG_INPUT layout")
if ctypes.sizeof(XmlConfigOutput) != XML_CONFIG_OUTPUT_SIZE:
    raise RuntimeError("unexpected NET_DVR_XML_CONFIG_OUTPUT layout")


def _decode_buffer(buffer: ctypes.Array[ctypes.c_char], length: int) -> str:
    bounded_length = min(max(length, 0), len(buffer.raw))
    payload = buffer.raw[:bounded_length].split(b"\0", 1)[0]
    return payload.decode("utf-8", errors="replace")


class NativeIsapiProbe:
    """Issue bounded GET-only ISAPI requests through ``NET_DVR_STDXMLConfig``."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self._raw = sdk._sdk
        self._raw.NET_DVR_STDXMLConfig.argtypes = [
            LONG,
            ctypes.POINTER(XmlConfigInput),
            ctypes.POINTER(XmlConfigOutput),
        ]
        self._raw.NET_DVR_STDXMLConfig.restype = BOOL

    def get(self, device: Any, path: str) -> dict[str, object]:
        """Run one read-only request and return both protocol and SDK results."""
        if not path.startswith("/") or any(character.isspace() for character in path):
            raise ValueError("ISAPI probe path must be an absolute path without whitespace")

        request_text = f"GET {path}"
        # EZVIZ's Android HCNetSDK helper terminates the method/path line with CRLF.
        # Some EZVIZ firmware rejects the otherwise valid un-terminated form with
        # NET_DVR_NOSUPPORT before returning an ISAPI status body.
        request_bytes = f"{request_text}\r\n".encode("ascii")
        request_buffer = ctypes.create_string_buffer(request_bytes)
        body_buffer = ctypes.create_string_buffer(OUTPUT_BUFFER_SIZE)
        status_buffer = ctypes.create_string_buffer(STATUS_BUFFER_SIZE)

        input_config = XmlConfigInput()
        input_config.dwSize = XML_CONFIG_INPUT_SIZE
        input_config.lpRequestUrl = ctypes.cast(request_buffer, ctypes.c_void_p)
        input_config.dwRequestUrlLen = len(request_bytes)
        input_config.dwRecvTimeOut = 5000
        input_config.dwSendTimeOut = 5000

        output_config = XmlConfigOutput()
        output_config.dwSize = XML_CONFIG_OUTPUT_SIZE
        output_config.lpOutBuffer = ctypes.cast(body_buffer, ctypes.c_void_p)
        output_config.dwOutBufferSize = OUTPUT_BUFFER_SIZE
        output_config.lpStatusBuffer = ctypes.cast(status_buffer, ctypes.c_void_p)
        output_config.dwStatusSize = STATUS_BUFFER_SIZE

        sdk_ok = bool(
            self._raw.NET_DVR_STDXMLConfig(
                device.user_id,
                ctypes.byref(input_config),
                ctypes.byref(output_config),
            )
        )
        error_code = None if sdk_ok else int(self._sdk.get_last_error())

        returned_size = int(output_config.dwReturnedXMLSize)
        body = _decode_buffer(body_buffer, returned_size)
        status = _decode_buffer(status_buffer, STATUS_BUFFER_SIZE)
        result: dict[str, object] = {
            "request": request_text,
            "sdk_ok": sdk_ok,
        }
        if error_code is not None:
            result["sdk_error_code"] = error_code
        if body:
            result["body"] = body
        if returned_size > OUTPUT_BUFFER_SIZE:
            result["body_truncated"] = True
        if status:
            result["status"] = status
        return result

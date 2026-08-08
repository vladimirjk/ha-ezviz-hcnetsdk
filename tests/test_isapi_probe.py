"""Read-only HCNetSDK ISAPI tunnel tests."""

from __future__ import annotations

import ctypes
import unittest

from bridge.isapi_probe import (
    OUTPUT_BUFFER_SIZE,
    XML_CONFIG_INPUT_SIZE,
    XML_CONFIG_OUTPUT_SIZE,
    NativeIsapiProbe,
    XmlConfigInput,
    XmlConfigOutput,
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
        self.calls: list[tuple[int, str, int, int]] = []
        self.fail = False
        self.body = b'{"ConsumptionMode":{"devWorkMode":"sleepOrWakeup"}}'
        self.status = b""
        self.NET_DVR_STDXMLConfig = FakeFunction(self._stdxml)

    def _stdxml(self, user_id, input_pointer, output_pointer):
        input_config = ctypes.cast(input_pointer, ctypes.POINTER(XmlConfigInput)).contents
        output_config = ctypes.cast(output_pointer, ctypes.POINTER(XmlConfigOutput)).contents
        request = ctypes.string_at(input_config.lpRequestUrl, input_config.dwRequestUrlLen).decode(
            "ascii"
        )
        self.calls.append(
            (
                user_id,
                request,
                input_config.dwRecvTimeOut,
                input_config.dwSendTimeOut,
            )
        )
        if self.fail:
            return 0
        ctypes.memmove(output_config.lpOutBuffer, self.body, len(self.body))
        output_config.dwReturnedXMLSize = len(self.body)
        if self.status:
            ctypes.memmove(output_config.lpStatusBuffer, self.status, len(self.status))
        return 1


class FakeSdk:
    def __init__(self) -> None:
        self._sdk = FakeRawSdk()

    def get_last_error(self) -> int:
        return 23


class FakeDevice:
    user_id = 42


class IsapiProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sdk = FakeSdk()
        self.probe = NativeIsapiProbe(self.sdk)

    def test_structures_match_official_64_bit_sdk_layout(self) -> None:
        self.assertEqual(ctypes.sizeof(XmlConfigInput), XML_CONFIG_INPUT_SIZE)
        self.assertEqual(ctypes.sizeof(XmlConfigOutput), XML_CONFIG_OUTPUT_SIZE)
        self.assertEqual(XmlConfigInput.lpRequestUrl.offset, 8)
        self.assertEqual(XmlConfigOutput.lpDataBuffer.offset, 40)

    def test_get_builds_bounded_read_only_request(self) -> None:
        result = self.probe.get(
            FakeDevice(),
            "/ISAPI/System/consumptionMode/capabilities?format=json",
        )

        self.assertEqual(
            self.sdk._sdk.calls,
            [
                (
                    42,
                    "GET /ISAPI/System/consumptionMode/capabilities?format=json",
                    5000,
                    5000,
                )
            ],
        )
        self.assertTrue(result["sdk_ok"])
        self.assertEqual(result["body"], self.sdk._sdk.body.decode())
        self.assertNotIn("sdk_error_code", result)

    def test_failed_sdk_call_reports_error_without_a_write(self) -> None:
        self.sdk._sdk.fail = True

        result = self.probe.get(FakeDevice(), "/ISAPI/System/consumptionMode")

        self.assertFalse(result["sdk_ok"])
        self.assertEqual(result["sdk_error_code"], 23)

    def test_rejects_non_path_and_whitespace(self) -> None:
        for path in ("ISAPI/System", "/ISAPI/System\nPUT /danger"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.probe.get(FakeDevice(), path)

    def test_marks_oversized_return_as_truncated(self) -> None:
        original = self.sdk._sdk.NET_DVR_STDXMLConfig.callback

        def oversized(*args):
            result = original(*args)
            output_config = ctypes.cast(args[2], ctypes.POINTER(XmlConfigOutput)).contents
            output_config.dwReturnedXMLSize = OUTPUT_BUFFER_SIZE + 1
            return result

        self.sdk._sdk.NET_DVR_STDXMLConfig.callback = oversized

        result = self.probe.get(FakeDevice(), "/ISAPI/System/consumptionMode")

        self.assertTrue(result["body_truncated"])


if __name__ == "__main__":
    unittest.main()

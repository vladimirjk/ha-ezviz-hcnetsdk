"""Read-only native state query tests."""

from __future__ import annotations

import ctypes
import struct
import unittest

from bridge.state_snapshot import HcNetSdkStateReader


class FakeFunction:
    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)


class FakeNative:
    def __init__(self) -> None:
        self.config_payloads: dict[int, bytes] = {}
        self.config_calls: list[tuple[int, int, int, int, int]] = []
        self.work_payload: bytes | None = None
        self.work_calls: list[int] = []
        self.NET_DVR_GetDVRConfig = FakeFunction(self._get_config)
        self.NET_DVR_GetDVRWorkState_V30 = FakeFunction(self._get_work_state)

    def _get_config(
        self,
        user_id: int,
        command: int,
        channel: int,
        out_buffer: object,
        size: int,
        returned: object,
    ) -> int:
        initial_size = ctypes.cast(out_buffer, ctypes.POINTER(ctypes.c_uint))[0]
        self.config_calls.append((user_id, command, channel, size, initial_size))
        payload = self.config_payloads.get(command)
        if payload is None:
            return 0
        ctypes.memmove(out_buffer, payload, len(payload))
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint))[0] = len(payload)
        return 1

    def _get_work_state(self, user_id: int, out_buffer: object) -> int:
        self.work_calls.append(user_id)
        if self.work_payload is None:
            return 0
        ctypes.memmove(out_buffer, self.work_payload, len(self.work_payload))
        return 1


class FakeSdk:
    def __init__(self, native: FakeNative) -> None:
        self._sdk = native

    @staticmethod
    def get_last_error() -> int:
        return 23


def buffer(size: int) -> bytearray:
    return bytearray(size)


class StateSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.native = FakeNative()
        self.reader = HcNetSdkStateReader(FakeSdk(self.native))

    def test_snapshot_preserves_failures_and_decodes_supported_queries(self) -> None:
        ptz = buffer(8)
        struct.pack_into("<HHHH", ptz, 0, 0, 0x1750, 0x0789, 0x1100)
        self.native.config_payloads[293] = bytes(ptz)

        recording = buffer(508)
        struct.pack_into("<I", recording, 0, 508)
        struct.pack_into("<I", recording, 4, 1)
        struct.pack_into("<H", recording, 8, 1)
        recording[10] = 1
        recording[497] = 1
        self.native.config_payloads[1004] = bytes(recording)

        device = buffer(180)
        struct.pack_into("<I", device, 0, 180)
        struct.pack_into("<I", device, 40, 1)
        device[122] = 1
        device[124] = 1
        device[125] = 1
        device[140:144] = b"H6c\0"
        self.native.config_payloads[1100] = bytes(device)

        work = buffer(57760)
        struct.pack_into("<III", work, 4, 1024, 768, 0)
        work[400] = 1
        struct.pack_into("<I", work, 404, 2048)
        struct.pack_into("<I", work, 408, 2)
        struct.pack_into("<I", work, 1288, 1)
        self.native.work_payload = bytes(work)

        result = self.reader.snapshot(42, 1, 1, include_raw=True)

        self.assertEqual(result["supported_queries"], 4)
        self.assertEqual(result["failed_queries"], 5)
        queries = result["queries"]
        self.assertEqual(queries["ptz_position"]["values"]["pan_degrees"], 175.0)
        self.assertEqual(queries["ptz_position"]["values"]["tilt_degrees"], 78.9)
        self.assertTrue(queries["recording"]["values"]["recording_enabled"])
        self.assertTrue(queries["recording"]["values"]["audio_recording_enabled"])
        self.assertEqual(queries["device"]["values"]["device_type_name"], "H6c")
        self.assertNotIn("raw_base64", queries["device"])
        self.assertIn("raw_base64", queries["recording"])
        self.assertTrue(queries["work_state"]["values"]["channel"]["recording"])
        self.assertEqual(queries["work_state"]["values"]["channel"]["client_links"], 2)
        self.assertEqual(queries["work_state"]["values"]["disks"][0]["capacity"], 1024)
        self.assertNotIn("raw_base64", queries["work_state"])
        self.assertFalse(queries["picture"]["sdk_ok"])
        self.assertEqual(queries["picture"]["sdk_error_code"], 23)

    def test_commands_use_exact_sizes_channels_and_initialized_dwsize(self) -> None:
        self.reader.snapshot(42, 5, 1)

        calls = {
            command: (channel, size, initial_size)
            for _, command, channel, size, initial_size in self.native.config_calls
        }
        self.assertEqual(calls[293], (5, 8, 0))
        self.assertEqual(calls[1002], (5, 7752, 7752))
        self.assertEqual(calls[1004], (5, 508, 508))
        self.assertEqual(calls[1040], (5, 116, 116))
        self.assertEqual(calls[1100], (-1, 180, 180))
        self.assertEqual(calls[3291], (5, 132, 132))
        self.assertEqual(calls[3293], (5, 136, 136))
        self.assertEqual(calls[3314], (5, 144, 144))
        self.assertEqual(self.native.work_calls, [42])

    def test_work_state_rejects_channel_outside_v30_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the V30 work-state"):
            self.reader.snapshot(42, 65, 1)

    def test_zero_device_disks_does_not_invent_empty_disks(self) -> None:
        device = buffer(180)
        struct.pack_into("<I", device, 0, 180)
        self.native.config_payloads[1100] = bytes(device)
        self.native.work_payload = bytes(buffer(57760))

        result = self.reader.snapshot(42, 1, 1)

        self.assertEqual(result["queries"]["work_state"]["values"]["disks"], [])


if __name__ == "__main__":
    unittest.main()

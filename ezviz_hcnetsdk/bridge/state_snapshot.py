"""Read-only HCNetSDK camera state queries and bounded decoders."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """One fixed-size NET_DVR_GetDVRConfig query."""

    name: str
    command: int
    structure: str
    size: int
    channel_scoped: bool
    initialize_size: bool
    allow_raw: bool
    decoder: Callable[[bytes], dict[str, object]]


def _u8(data: bytes, offset: int) -> int:
    return data[offset]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _text(data: bytes, offset: int, size: int) -> str:
    return data[offset : offset + size].split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _enabled(value: int) -> bool | None:
    if value in (0, 1):
        return bool(value)
    return None


def _bcd_tenths(value: int) -> float | None:
    encoded = f"{value:04x}"
    if not encoded.isdecimal():
        return None
    return int(encoded) / 10


def _decode_ptz(data: bytes) -> dict[str, object]:
    pan = _u16(data, 2)
    tilt = _u16(data, 4)
    zoom = _u16(data, 6)
    return {
        "pan_raw": pan,
        "tilt_raw": tilt,
        "zoom_raw": zoom,
        "pan_degrees": _bcd_tenths(pan),
        "tilt_degrees": _bcd_tenths(tilt),
        "zoom": _bcd_tenths(zoom),
    }


def _decode_picture(data: bytes) -> dict[str, object]:
    motion_offset = 768
    motion_scope = data[motion_offset : motion_offset + 6144]
    return {
        "video_format": _u32(data, 36),
        "motion_detection_enabled": _enabled(_u8(data, motion_offset + 6145)),
        "motion_detection_sensitivity": _u8(data, motion_offset + 6144),
        "motion_detection_display_enabled": _enabled(_u8(data, motion_offset + 6146)),
        "motion_zone_cells_enabled": sum(value != 0 for value in motion_scope),
        "fixed_privacy_mask_enabled": _enabled(_u32(data, 7640)),
        "osd_enabled": _enabled(_u32(data, 7676)),
    }


def _decode_recording(data: bytes) -> dict[str, object]:
    all_day_offset = 8
    all_day_enabled = [_enabled(_u16(data, all_day_offset + day * 4)) for day in range(7)]
    all_day_types = [_u8(data, all_day_offset + day * 4 + 2) for day in range(7)]
    return {
        "recording_enabled": _enabled(_u32(data, 4)),
        "all_day_enabled": all_day_enabled,
        "all_day_record_types": all_day_types,
        "post_record_time_code": _u32(data, 484),
        "pre_record_time_code": _u32(data, 488),
        "audio_recording_enabled": _enabled(_u8(data, 497)),
        "record_management_disabled": _enabled(_u8(data, 504)),
    }


def _decode_compression_profile(data: bytes, offset: int) -> dict[str, object]:
    return {
        "stream_type": _u8(data, offset),
        "resolution_code": _u8(data, offset + 1),
        "bitrate_type": _u8(data, offset + 2),
        "picture_quality": _u8(data, offset + 3),
        "video_bitrate_code": _u32(data, offset + 4),
        "video_frame_rate_code": _u32(data, offset + 8),
        "video_encoding_type": _u8(data, offset + 16),
        "audio_encoding_type": _u8(data, offset + 17),
        "svc_enabled": _u8(data, offset + 19),
    }


def _decode_compression(data: bytes) -> dict[str, object]:
    return {
        "normal_record": _decode_compression_profile(data, 4),
        "event_record": _decode_compression_profile(data, 60),
        "network": _decode_compression_profile(data, 88),
    }


def _decode_device(data: bytes) -> dict[str, object]:
    software = _u32(data, 92)
    build_date = _u32(data, 96)
    return {
        "recycle_recording_enabled": _enabled(_u32(data, 40)),
        "software_version_raw": software,
        "software_version_hex": f"0x{software:08x}",
        "software_build_date_raw": build_date,
        "disk_count": _u8(data, 122),
        "channel_count": _u8(data, 124),
        "start_channel": _u8(data, 125),
        "storage_mode": _u8(data, 136),
        "device_type": _u16(data, 138),
        "device_type_name": _text(data, 140, 24),
        "remote_power_on_enabled": _enabled(_u8(data, 171)),
    }


def _decode_privacy_mask(data: bytes) -> dict[str, object]:
    return {"enabled": _enabled(_u8(data, 4))}


def _decode_smart_tracking(data: bytes) -> dict[str, object]:
    return {"enabled": _enabled(_u8(data, 4)), "duration_seconds": _u32(data, 8)}


def _decode_park_action(data: bytes) -> dict[str, object]:
    return {
        "enabled": _enabled(_u8(data, 4)),
        "one_touch_enabled": _enabled(_u8(data, 5)),
        "delay_seconds": _u32(data, 8),
        "action_type": _u16(data, 12),
        "action_id": _u16(data, 14),
    }


# Sizes and offsets were compiled from the pinned official HCNetSDK 6.1.9.48
# x86-64 header. Every operation here is a GET.
CONFIG_QUERIES = (
    QuerySpec("ptz_position", 293, "NET_DVR_PTZPOS", 8, True, False, True, _decode_ptz),
    QuerySpec(
        "picture",
        1002,
        "NET_DVR_PICCFG_V30",
        7752,
        True,
        True,
        True,
        _decode_picture,
    ),
    QuerySpec(
        "recording",
        1004,
        "NET_DVR_RECORD_V30",
        508,
        True,
        True,
        True,
        _decode_recording,
    ),
    QuerySpec(
        "compression",
        1040,
        "NET_DVR_COMPRESSIONCFG_V30",
        116,
        True,
        True,
        True,
        _decode_compression,
    ),
    QuerySpec(
        "device",
        1100,
        "NET_DVR_DEVICECFG_V40",
        180,
        False,
        True,
        False,
        _decode_device,
    ),
    QuerySpec(
        "privacy_mask",
        3291,
        "NET_DVR_PRIVACY_MASKS_ENABLECFG",
        132,
        True,
        True,
        True,
        _decode_privacy_mask,
    ),
    QuerySpec(
        "smart_tracking",
        3293,
        "NET_DVR_SMARTTRACKCFG",
        136,
        True,
        True,
        True,
        _decode_smart_tracking,
    ),
    QuerySpec(
        "ptz_park_action",
        3314,
        "NET_DVR_PTZ_PARKACTION_CFG",
        144,
        True,
        True,
        True,
        _decode_park_action,
    ),
)

WORK_STATE_SIZE = 57760
CHANNEL_STATE_SIZE = 892
WORK_STATE_CHANNELS_OFFSET = 400
WORK_STATE_DISKS_OFFSET = 4
DISK_STATE_SIZE = 12
MAX_WORK_STATE_DISKS = 33
MAX_WORK_STATE_CHANNELS = 64


def _status_name(value: int, names: dict[int, str]) -> str:
    return names.get(value, "unknown")


def _decode_work_state(
    data: bytes, channel: int, channel_index: int, disk_count: int | None
) -> dict[str, object]:
    channel_offset = WORK_STATE_CHANNELS_OFFSET + channel_index * CHANNEL_STATE_SIZE
    disks = []
    disk_limit = (
        MAX_WORK_STATE_DISKS if disk_count is None else min(disk_count, MAX_WORK_STATE_DISKS)
    )
    for index in range(disk_limit):
        offset = WORK_STATE_DISKS_OFFSET + index * DISK_STATE_SIZE
        capacity = _u32(data, offset)
        free_space = _u32(data, offset + 4)
        status = _u32(data, offset + 8)
        if disk_count is None and capacity == 0 and free_space == 0 and status == 0:
            continue
        disks.append(
            {
                "index": index,
                "capacity": capacity,
                "free_space": free_space,
                "status": status,
                "status_name": _status_name(
                    status,
                    {
                        0: "active",
                        1: "sleeping",
                        2: "abnormal",
                        3: "sleep_error",
                        4: "unformatted",
                        5: "disconnected",
                        6: "formatting",
                        7: "full",
                        8: "other_error",
                    },
                ),
            }
        )

    device_status = _u32(data, 0)
    return {
        "device_status": device_status,
        "device_status_name": _status_name(
            device_status, {0: "normal", 1: "high_cpu", 2: "hardware_error"}
        ),
        "channel": {
            "number": channel,
            "recording": _enabled(_u8(data, channel_offset)),
            "signal_lost": _enabled(_u8(data, channel_offset + 1)),
            "hardware_error": _enabled(_u8(data, channel_offset + 2)),
            "bitrate": _u32(data, channel_offset + 4),
            "client_links": _u32(data, channel_offset + 8),
            "ip_channel_links": _u32(data, channel_offset + 876),
            "all_bitrate": _u32(data, channel_offset + 884),
            "reported_channel_number": _u32(data, channel_offset + 888),
        },
        "disks": disks,
        "local_display_status": _u32(data, 57744),
    }


class HcNetSdkStateReader:
    """Issue bounded GET-only calls through the pinned wrapper's native SDK handle."""

    def __init__(self, sdk: Any) -> None:
        self._native = sdk._sdk
        self._get_last_error = sdk.get_last_error

        self._native.NET_DVR_GetDVRConfig.argtypes = [
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_uint),
        ]
        self._native.NET_DVR_GetDVRConfig.restype = ctypes.c_int
        self._native.NET_DVR_GetDVRWorkState_V30.argtypes = [ctypes.c_int, ctypes.c_void_p]
        self._native.NET_DVR_GetDVRWorkState_V30.restype = ctypes.c_int

    @staticmethod
    def _base_result(spec: QuerySpec, channel: int) -> dict[str, object]:
        return {
            "command": spec.command,
            "structure": spec.structure,
            "structure_size": spec.size,
            "channel": channel if spec.channel_scoped else None,
        }

    def _get_config(
        self, user_id: int, channel: int, spec: QuerySpec, include_raw: bool
    ) -> dict[str, object]:
        query_channel = channel if spec.channel_scoped else -1
        result = self._base_result(spec, query_channel)
        buffer = ctypes.create_string_buffer(spec.size)
        if spec.initialize_size:
            ctypes.c_uint.from_buffer(buffer).value = spec.size
        returned = ctypes.c_uint(0)

        ok = self._native.NET_DVR_GetDVRConfig(
            user_id,
            spec.command,
            query_channel,
            ctypes.byref(buffer),
            spec.size,
            ctypes.byref(returned),
        )
        if not ok:
            result.update({"sdk_ok": False, "sdk_error_code": int(self._get_last_error())})
            return result

        data = bytes(buffer.raw)
        result.update(
            {
                "sdk_ok": True,
                "bytes_returned": int(returned.value),
                "sha256": hashlib.sha256(data).hexdigest(),
                "values": spec.decoder(data),
            }
        )
        if include_raw and spec.allow_raw:
            result["raw_base64"] = base64.b64encode(data).decode("ascii")
        return result

    def _get_work_state(
        self, user_id: int, channel: int, channel_index: int, disk_count: int | None
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "function": "NET_DVR_GetDVRWorkState_V30",
            "structure": "NET_DVR_WORKSTATE_V30",
            "structure_size": WORK_STATE_SIZE,
            "channel": channel,
        }
        buffer = ctypes.create_string_buffer(WORK_STATE_SIZE)
        ok = self._native.NET_DVR_GetDVRWorkState_V30(user_id, ctypes.byref(buffer))
        if not ok:
            result.update({"sdk_ok": False, "sdk_error_code": int(self._get_last_error())})
            return result

        data = bytes(buffer.raw)
        result.update(
            {
                "sdk_ok": True,
                "sha256": hashlib.sha256(data).hexdigest(),
                "values": _decode_work_state(data, channel, channel_index, disk_count),
            }
        )
        return result

    def snapshot(
        self,
        user_id: int,
        channel: int,
        start_channel: int,
        *,
        include_raw: bool = False,
    ) -> dict[str, object]:
        """Return all supported states while preserving per-query failures."""
        channel_index = channel - start_channel
        if not 0 <= channel_index < MAX_WORK_STATE_CHANNELS:
            raise ValueError("configured channel is outside the V30 work-state channel range")

        queries = {
            spec.name: self._get_config(user_id, channel, spec, include_raw)
            for spec in CONFIG_QUERIES
        }
        device = queries["device"]
        disk_count = None
        if device.get("sdk_ok") is True:
            values = device.get("values")
            if isinstance(values, dict) and isinstance(values.get("disk_count"), int):
                disk_count = values["disk_count"]
        queries["work_state"] = self._get_work_state(user_id, channel, channel_index, disk_count)

        supported = sum(query.get("sdk_ok") is True for query in queries.values())
        return {
            "supported_queries": supported,
            "failed_queries": len(queries) - supported,
            "queries": queries,
        }

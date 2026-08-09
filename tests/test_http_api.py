"""HTTP API tests."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from bridge.config import parse_config
from bridge.http_api import create_server
from test_config import valid_options


class FakeBackend:
    def __init__(self) -> None:
        self.moves: list[tuple[str, str, int, int]] = []
        self.presets: list[tuple[str, str, int]] = []
        self.auto_pan_changes: list[tuple[str, bool, int]] = []
        self.cruise_calls: list[tuple[str, str, int, dict[str, int]]] = []
        self.track_calls: list[tuple[str, str]] = []
        self.recording_changes: list[tuple[str, bool]] = []
        self.snapshots: list[tuple[str, bool]] = []

    def status(self) -> dict[str, object]:
        return {"sdk_version": "test", "cameras": {"cam1": {"connected": False}}}

    def test_camera(self, camera_id: str) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        return {"camera": camera_id, "connected": True}

    def move(
        self, camera_id: str, direction: str, duration_ms: int, speed: int
    ) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.moves.append((camera_id, direction, duration_ms, speed))
        return {"camera": camera_id, "direction": direction}

    def state_snapshot(self, camera_id: str, *, include_raw: bool) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.snapshots.append((camera_id, include_raw))
        return {
            "camera": camera_id,
            "read_only": True,
            "responsive": True,
            "supported_queries": 3,
            "skipped_queries": 0,
            "queries": {},
        }

    def alarm_events(self, camera_id: str) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        return {
            "camera": camera_id,
            "subscribed": True,
            "motion": {"active": False},
            "tamper": {"active": False},
        }

    def preset(self, camera_id: str, action: str, preset: int) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.presets.append((camera_id, action, preset))
        return {"camera": camera_id, "action": action, "preset": preset}

    def set_auto_pan(self, camera_id: str, enabled: bool, speed: int) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.auto_pan_changes.append((camera_id, enabled, speed))
        return {"camera": camera_id, "enabled": enabled, "speed": speed}

    def cruise(
        self,
        camera_id: str,
        action: str,
        route: int,
        **kwargs: int,
    ) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.cruise_calls.append((camera_id, action, route, kwargs))
        return {"camera": camera_id, "action": action, "route": route, **kwargs}

    def track(self, camera_id: str, action: str) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.track_calls.append((camera_id, action))
        return {"camera": camera_id, "action": action}

    def set_manual_recording(self, camera_id: str, enabled: bool) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.recording_changes.append((camera_id, enabled))
        return {"camera": camera_id, "manual_recording_enabled": enabled}


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = parse_config(valid_options())
        self.backend = FakeBackend()
        self.server = create_server("127.0.0.1", 0, self.config, self.backend)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        *,
        token: str | None = None,
    ) -> tuple[int, object | None]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            method=method,
            data=data,
            headers={
                **({"Authorization": f"Bearer {token}"} if token else {}),
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            response = urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as exc:
            with exc:
                payload = exc.read()
                return exc.code, json.loads(payload) if payload else None
        with response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None

    def test_health_does_not_require_token(self) -> None:
        status, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_control_requires_token(self) -> None:
        status, _ = self.request("POST", "/v1/cameras/cam1/test")
        self.assertEqual(status, 401)

    def test_state_snapshot_requires_token(self) -> None:
        status, _ = self.request("GET", "/v1/cameras/cam1/state-snapshot")
        self.assertEqual(status, 401)

    def test_alarm_events_requires_token(self) -> None:
        status, _ = self.request("GET", "/v1/cameras/cam1/events")
        self.assertEqual(status, 401)

    def test_alarm_events(self) -> None:
        status, body = self.request(
            "GET",
            "/v1/cameras/cam1/events",
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        assert isinstance(body, dict)
        self.assertTrue(body["subscribed"])

    def test_state_snapshot(self) -> None:
        status, body = self.request(
            "GET",
            "/v1/cameras/cam1/state-snapshot?raw=1",
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        assert isinstance(body, dict)
        self.assertTrue(body["read_only"])
        self.assertTrue(body["responsive"])
        self.assertEqual(self.backend.snapshots, [("cam1", True)])

    def test_state_snapshot_rejects_invalid_raw_option(self) -> None:
        status, body = self.request(
            "GET",
            "/v1/cameras/cam1/state-snapshot?raw=yes",
            token=self.config.api_token,
        )
        self.assertEqual(status, 400)
        assert isinstance(body, dict)
        self.assertEqual(body["error"], "invalid_request")

    def test_state_snapshot_unknown_camera_is_404(self) -> None:
        status, body = self.request(
            "GET",
            "/v1/cameras/missing/state-snapshot",
            token=self.config.api_token,
        )
        self.assertEqual(status, 404)
        assert isinstance(body, dict)
        self.assertEqual(body["error"], "unknown_camera")

    def test_camera_login_test(self) -> None:
        status, body = self.request("POST", "/v1/cameras/cam1/test", token=self.config.api_token)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"camera": "cam1", "connected": True})

    def test_ptz_defaults(self) -> None:
        status, _ = self.request(
            "POST",
            "/v1/cameras/cam1/ptz",
            {"direction": "right"},
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.backend.moves, [("cam1", "right", 250, 3)])

    def test_ptz_accepts_zoom_and_diagonal(self) -> None:
        for direction in ("zoom_in", "zoom_out", "up_left", "down_right"):
            status, _ = self.request(
                "POST",
                "/v1/cameras/cam1/ptz",
                {"direction": direction},
                token=self.config.api_token,
            )
            self.assertEqual(status, 200)

        self.assertEqual(
            [call[1] for call in self.backend.moves],
            [
                "zoom_in",
                "zoom_out",
                "up_left",
                "down_right",
            ],
        )

    def test_auto_pan_command(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/cameras/cam1/auto-pan",
            {"enabled": True, "speed": 4},
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"camera": "cam1", "enabled": True, "speed": 4})
        self.assertEqual(self.backend.auto_pan_changes, [("cam1", True, 4)])

    def test_ptz_rejects_unbounded_duration(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/cameras/cam1/ptz",
            {"direction": "right", "duration_ms": 5000},
            token=self.config.api_token,
        )
        self.assertEqual(status, 400)
        assert isinstance(body, dict)
        self.assertEqual(body["error"], "invalid_request")

    def test_preset_command(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/cameras/cam1/preset",
            {"action": "set", "preset": 2},
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"camera": "cam1", "action": "set", "preset": 2})
        self.assertEqual(self.backend.presets, [("cam1", "set", 2)])

    def test_preset_rejects_missing_or_invalid_values(self) -> None:
        for request_body in (
            {"action": "goto"},
            {"action": "rename", "preset": 2},
            {"action": "goto", "preset": 0},
            {"action": "goto", "preset": True},
        ):
            with self.subTest(request_body=request_body):
                status, body = self.request(
                    "POST",
                    "/v1/cameras/cam1/preset",
                    request_body,
                    token=self.config.api_token,
                )
                self.assertEqual(status, 400)
                assert isinstance(body, dict)
                self.assertEqual(body["error"], "invalid_request")

    def test_cruise_set_point_command(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/cameras/cam1/cruise",
            {
                "action": "set_point",
                "route": 1,
                "point": 2,
                "preset": 3,
                "dwell": 10,
                "speed": 4,
            },
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        assert isinstance(body, dict)
        self.assertEqual(body["preset"], 3)
        self.assertEqual(
            self.backend.cruise_calls,
            [("cam1", "set_point", 1, {"point": 2, "preset": 3, "dwell": 10, "speed": 4})],
        )

    def test_cruise_run_and_stop_commands(self) -> None:
        for action in ("run", "stop"):
            status, _ = self.request(
                "POST",
                "/v1/cameras/cam1/cruise",
                {"action": action, "route": 1},
                token=self.config.api_token,
            )
            self.assertEqual(status, 200)

    def test_cruise_rejects_invalid_or_missing_values(self) -> None:
        for request_body in (
            {"action": "run"},
            {"action": "run", "route": 33},
            {"action": "set_point", "route": 1},
            {
                "action": "set_point",
                "route": 1,
                "point": 1,
                "preset": 1,
                "dwell": 1,
                "speed": 41,
            },
        ):
            with self.subTest(request_body=request_body):
                status, body = self.request(
                    "POST",
                    "/v1/cameras/cam1/cruise",
                    request_body,
                    token=self.config.api_token,
                )
                self.assertEqual(status, 400)
                assert isinstance(body, dict)
                self.assertEqual(body["error"], "invalid_request")

    def test_track_command(self) -> None:
        for action in ("record_start", "record_stop", "run"):
            status, _ = self.request(
                "POST",
                "/v1/cameras/cam1/track",
                {"action": action},
                token=self.config.api_token,
            )
            self.assertEqual(status, 200)

        self.assertEqual(
            self.backend.track_calls,
            [("cam1", "record_start"), ("cam1", "record_stop"), ("cam1", "run")],
        )

    def test_manual_recording_command(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/cameras/cam1/recording",
            {"enabled": False},
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"camera": "cam1", "manual_recording_enabled": False})
        self.assertEqual(self.backend.recording_changes, [("cam1", False)])

    def test_manual_recording_requires_boolean(self) -> None:
        for enabled in (None, 0, 1, "false"):
            with self.subTest(enabled=enabled):
                status, body = self.request(
                    "POST",
                    "/v1/cameras/cam1/recording",
                    {"enabled": enabled},
                    token=self.config.api_token,
                )
                self.assertEqual(status, 400)
                assert isinstance(body, dict)
                self.assertEqual(body["error"], "invalid_request")

    def test_unknown_camera_is_404(self) -> None:
        status, body = self.request("POST", "/v1/cameras/missing/test", token=self.config.api_token)
        self.assertEqual(status, 404)
        assert isinstance(body, dict)
        self.assertEqual(body["error"], "unknown_camera")

    def test_removed_experimental_endpoints_are_404(self) -> None:
        for method, path in (
            ("POST", "/v1/cameras/cam1/sleep"),
            ("GET", "/v1/cameras/cam1/sleep-probe"),
            ("POST", "/v1/cameras/cam1/web"),
        ):
            with self.subTest(method=method, path=path):
                status, body = self.request(
                    method,
                    path,
                    token=self.config.api_token,
                )
                self.assertEqual(status, 404)
                self.assertEqual(body, {"error": "not_found"})


if __name__ == "__main__":
    unittest.main()

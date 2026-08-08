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
        self.sleep_calls: list[tuple[str, bool]] = []

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

    def set_sleep(self, camera_id: str, enabled: bool) -> dict[str, object]:
        if camera_id != "cam1":
            raise KeyError(camera_id)
        self.sleep_calls.append((camera_id, enabled))
        return {"camera": camera_id, "sleeping": enabled}


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

    def test_unknown_camera_is_404(self) -> None:
        status, body = self.request("POST", "/v1/cameras/missing/test", token=self.config.api_token)
        self.assertEqual(status, 404)
        assert isinstance(body, dict)
        self.assertEqual(body["error"], "unknown_camera")

    def test_sleep_accepts_boolean(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/cameras/cam1/sleep",
            {"enabled": True},
            token=self.config.api_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"camera": "cam1", "sleeping": True})
        self.assertEqual(self.backend.sleep_calls, [("cam1", True)])

    def test_sleep_rejects_non_boolean(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/cameras/cam1/sleep",
            {"enabled": "true"},
            token=self.config.api_token,
        )
        self.assertEqual(status, 400)
        assert isinstance(body, dict)
        self.assertEqual(body["error"], "invalid_request")


if __name__ == "__main__":
    unittest.main()

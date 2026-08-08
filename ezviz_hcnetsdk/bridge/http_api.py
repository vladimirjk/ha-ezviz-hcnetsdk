"""Small token-protected HTTP API for Home Assistant calls."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .config import BridgeConfig

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 4096
DIRECTIONS = frozenset({"up", "down", "left", "right"})


class BridgeHTTPServer(ThreadingHTTPServer):
    """Threaded server that waits for active camera commands during shutdown."""

    allow_reuse_address = True
    daemon_threads = False
    block_on_close = True


def handler_factory(config: BridgeConfig, backend: Any) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one configuration and backend."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ezviz-hcnetsdk-bridge/0.2.1"
        sys_version = ""

        def _json(self, status: HTTPStatus, body: object) -> None:
            payload = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            value = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not value.startswith(prefix) or not hmac.compare_digest(
                value[len(prefix) :], config.api_token
            ):
                self.send_response(HTTPStatus.UNAUTHORIZED.value)
                self.send_header("WWW-Authenticate", "Bearer")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return False
            return True

        def _request_json(self) -> dict[str, object]:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise OverflowError("request body is too large")
            if length == 0:
                return {}
            try:
                body = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body must be valid JSON") from exc
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")
            return body

        @staticmethod
        def _bounded_integer(
            body: dict[str, object], key: str, default: int, minimum: int, maximum: int
        ) -> int:
            value = body.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
            return value

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/health":
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            if path == "/v1/cameras":
                if self._authorized():
                    self._json(HTTPStatus.OK, backend.status())
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["v1", "cameras"] and parts[3] == "sleep-probe":
                if not self._authorized():
                    return
                camera_id = parts[2]
                try:
                    result = backend.probe_sleep(camera_id)
                except KeyError as exc:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "unknown_camera", "detail": str(exc)},
                    )
                    return
                except Exception as exc:
                    LOGGER.exception("Camera operation failed for %s", camera_id)
                    error_code = getattr(exc, "error_code", None)
                    response: dict[str, object] = {"error": "camera_operation_failed"}
                    if isinstance(error_code, int):
                        response["sdk_error_code"] = error_code
                    self._json(HTTPStatus.BAD_GATEWAY, response)
                    return
                self._json(HTTPStatus.OK, result)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return

            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            if len(parts) != 4 or parts[:2] != ["v1", "cameras"]:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return

            camera_id, operation = parts[2], parts[3]
            try:
                body = self._request_json()
                if operation == "test":
                    result = backend.test_camera(camera_id)
                elif operation == "ptz":
                    direction = body.get("direction")
                    if not isinstance(direction, str) or direction not in DIRECTIONS:
                        raise ValueError("direction must be one of: up, down, left, right")
                    duration_ms = self._bounded_integer(
                        body,
                        "duration_ms",
                        config.default_duration_ms,
                        50,
                        1500,
                    )
                    speed = self._bounded_integer(body, "speed", config.default_speed, 1, 7)
                    result = backend.move(camera_id, direction, duration_ms, speed)
                elif operation == "sleep":
                    enabled = body.get("enabled")
                    if not isinstance(enabled, bool):
                        raise ValueError("enabled must be a boolean")
                    result = backend.set_sleep(camera_id, enabled)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
            except KeyError as exc:
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "unknown_camera", "detail": str(exc)},
                )
                return
            except OverflowError as exc:
                self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "invalid_request", "detail": str(exc)},
                )
                return
            except ValueError as exc:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_request", "detail": str(exc)},
                )
                return
            except Exception as exc:
                LOGGER.exception("Camera operation failed for %s", camera_id)
                error_code = getattr(exc, "error_code", None)
                response: dict[str, object] = {"error": "camera_operation_failed"}
                if isinstance(error_code, int):
                    response["sdk_error_code"] = error_code
                self._json(HTTPStatus.BAD_GATEWAY, response)
                return

            self._json(HTTPStatus.OK, result)

        def log_message(self, message: str, *args: object) -> None:
            LOGGER.info("%s - %s", self.address_string(), message % args)

    return Handler


def create_server(
    host: str,
    port: int,
    config: BridgeConfig,
    backend: Any,
    server_factory: Callable[..., BridgeHTTPServer] = BridgeHTTPServer,
) -> BridgeHTTPServer:
    """Construct the configured HTTP server."""
    return server_factory((host, port), handler_factory(config, backend))

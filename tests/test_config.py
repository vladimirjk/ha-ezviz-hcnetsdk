"""Configuration validation tests."""

from __future__ import annotations

import unittest

from bridge.config import ConfigurationError, parse_config


def valid_options() -> dict[str, object]:
    return {
        "api_token": "a" * 32,
        "default_duration_ms": 250,
        "default_speed": 3,
        "log_level": "info",
        "cameras": [
            {
                "id": "cam1",
                "host": "192.168.0.10",
                "port": 8000,
                "username": "admin",
                "password": "ABCDEF",
                "channel": 1,
            }
        ],
    }


class ConfigTests(unittest.TestCase):
    def test_valid_config(self) -> None:
        config = parse_config(valid_options())
        self.assertEqual(config.cameras["cam1"].host, "192.168.0.10")
        self.assertEqual(config.default_duration_ms, 250)

    def test_short_token_is_rejected(self) -> None:
        options = valid_options()
        options["api_token"] = "short"
        with self.assertRaisesRegex(ConfigurationError, "at least 16"):
            parse_config(options)

    def test_duplicate_camera_id_is_rejected(self) -> None:
        options = valid_options()
        cameras = options["cameras"]
        assert isinstance(cameras, list)
        cameras.append(dict(cameras[0]))
        with self.assertRaisesRegex(ConfigurationError, "duplicate camera id"):
            parse_config(options)

    def test_hostname_is_rejected(self) -> None:
        options = valid_options()
        cameras = options["cameras"]
        assert isinstance(cameras, list)
        cameras[0]["host"] = "camera.local"
        with self.assertRaisesRegex(ConfigurationError, "must be an IP address"):
            parse_config(options)

    def test_boolean_is_not_accepted_as_integer(self) -> None:
        options = valid_options()
        options["default_speed"] = True
        with self.assertRaisesRegex(ConfigurationError, "must be an integer"):
            parse_config(options)


if __name__ == "__main__":
    unittest.main()

# EZVIZ HCNetSDK Home Assistant app

Experimental Home Assistant app (formerly add-on) for controlling and reading state
from compatible EZVIZ/Hikvision cameras locally through HCNetSDK on TCP port 8000.

The bridge implements:

- HCNetSDK initialization
- Per-camera login checks
- Verified bounded cardinal PTZ on the tested H6c
- Firmware-dependent diagonal/zoom, auto-pan, preset, cruise, and track APIs
- Local motion and tamper alarm events
- Device-side manual recording start/stop
- HA-ready connectivity, bitrate, signal, recording, motion, and SD state
- A token-protected HTTP API

It does not replace RTSP/go2rtc video or the built-in EZVIZ integration.

See [the app documentation](ezviz_hcnetsdk/DOCS.md) for installation and configuration.

Install it as a custom Home Assistant app repository using:

```text
https://github.com/vladimirjk/ha-ezviz-hcnetsdk
```

## Supported platform

- Home Assistant OS or Supervised
- `amd64` / x86-64 only

## Development checks

```bash
PYTHONPATH=ezviz_hcnetsdk python3 -m unittest discover -s tests -v
ruff check .
ruff format --check .
docker build --check ezviz_hcnetsdk
```

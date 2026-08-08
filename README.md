# EZVIZ HCNetSDK Home Assistant app

Experimental Home Assistant app (formerly add-on) for controlling compatible
EZVIZ/Hikvision cameras locally through the HCNetSDK service on TCP port 8000.

The first release deliberately implements only:

- HCNetSDK initialization
- Per-camera login checks
- Bounded local PTZ movement
- A token-protected HTTP API

It does not replace RTSP/go2rtc video or the built-in EZVIZ integration. Sleep and
privacy control are not implemented because their local SDK commands are model-specific.

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

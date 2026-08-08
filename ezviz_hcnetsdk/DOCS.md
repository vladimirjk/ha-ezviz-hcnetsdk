# EZVIZ HCNetSDK Bridge

This app exposes bounded PTZ commands over a small local HTTP API. It downloads
Hikvision's official Linux x86-64 HCNetSDK during the image build and does not store
camera credentials in the repository.

## Installation

This repository must be available on GitHub before Home Assistant OS can install it.
In Home Assistant:

1. Open **Settings > Apps** (called **Add-ons** in older releases).
2. Open the app store, then its **Repositories** menu.
3. Add `https://github.com/vladimirjk/ha-ezviz-hcnetsdk`.
4. Refresh the store, open **EZVIZ HCNetSDK Bridge**, and select **Install**.

The first installation builds an amd64 image and downloads the pinned 64-bit HCNetSDK
archive, so it can take several minutes. The app intentionally supports only x86-64
Home Assistant hosts.

## Configuration

Generate an API token on another computer:

```bash
openssl rand -hex 24
```

Configure the app, replacing all example values:

```yaml
api_token: "replace-with-generated-token"
default_duration_ms: 250
default_speed: 3
log_level: info
cameras:
  - id: cam1
    host: 192.168.0.10
    port: 8000
    username: admin
    password: CAMERA_VERIFICATION_CODE
    channel: 1
  - id: cam2
    host: 192.168.0.11
    port: 8000
    username: admin
    password: SECOND_CAMERA_VERIFICATION_CODE
    channel: 1
```

The API listens on host port `8977` by default. Do not forward this port from your
router to the internet.

## Login test

Starting the app initializes HCNetSDK but does not immediately contact the cameras.
Test one camera explicitly from a machine on the LAN:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/test
```

A successful response resembles:

```json
{"camera":"cam2","connected":true,"sdk_version":"6.1.9.48","configured_channel":1,"device_start_channel":1}
```

## Manual PTZ test

This request moves the selected camera left for 200 milliseconds and then always sends
the matching stop command:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"direction":"left","duration_ms":200,"speed":2}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/ptz
```

Allowed directions are `up`, `down`, `left`, and `right`. Duration is restricted to
50-1500 milliseconds and speed to 1-7.

## Read-only sleep capability probe

Some EZVIZ firmware does not support the module-service command used by the sleep
endpoint. The bridge includes an ISAPI probe through the authenticated HCNetSDK session:

```bash
curl --fail-with-body \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/sleep-probe
```

The probe sends only `GET` requests. Version 0.2.2 uses the CRLF request framing seen
in the EZVIZ Android app and checks the app's `servicesSwitch` endpoint, generic ISAPI
reads, and consumption-mode capabilities/current state. It does not write configuration
or change the camera. Its response includes the ISAPI body or status for each request.

## Local web and ISAPI service

If the probe returns a `servicesSwitch` body with `"web":0`, the camera's local HTTP
and standard ISAPI service is disabled. Version 0.3.0 can change only that switch while
preserving the complete service configuration. Enabling it changes camera state:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled":true}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/web
```

The bridge reads the complete `servicesSwitch` object, changes only `web`, writes it,
then reads it again and fails unless the requested value is verified. Disable it by
sending `{"enabled":false}` to the same endpoint.

After enabling it, check the camera directly:

```bash
nc -zv CAMERA_IP 80
curl --fail-with-body --digest --user admin http://CAMERA_IP/ISAPI/System/deviceInfo
```

The second command prompts for the camera password.

## Manual sleep and wake test

Sleep uses the camera module's HCNetSDK power-saving setting. The bridge first reads
the complete setting, changes only the sleep byte, and writes it back. Wake uses the
SDK's dedicated remote-power-on command.

This legacy sleep path is not supported by every camera. In particular, an HCNetSDK
error `23` while reading the module-service configuration means the camera rejected
the command before any write was attempted. Use the read-only probe above instead of
repeating that sleep request.

Keep the built-in EZVIZ sleep switch available as a fallback during the first test.
Put `cam2` to sleep with:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled":true}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/sleep
```

Wake it with the same endpoint and `false`:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled":false}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/sleep
```

The video stream can take several seconds to reconnect after wake.

## Home Assistant configuration

Add this to `secrets.yaml`:

```yaml
ezviz_hcnetsdk_authorization: "Bearer YOUR_API_TOKEN"
```

Add this to `configuration.yaml`, using the LAN IP of the Home Assistant NUC:

```yaml
rest_command:
  ezviz_local_ptz:
    url: "http://HOME_ASSISTANT_IP:8977/v1/cameras/{{ camera }}/ptz"
    method: POST
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
      Content-Type: application/json
    payload: >-
      {"direction":"{{ direction }}","duration_ms":{{ duration_ms | default(250) }},"speed":{{ speed | default(3) }}}
  ezviz_local_sleep:
    url: "http://HOME_ASSISTANT_IP:8977/v1/cameras/{{ camera }}/sleep"
    method: POST
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
      Content-Type: application/json
    payload: >-
      {"enabled":{{ enabled | tojson }}}
```

If `configuration.yaml` already has a top-level `rest_command:` section, add only
`ezviz_local_ptz:` beneath the existing section. YAML cannot contain a second
top-level `rest_command:` key.

Check configuration and restart Home Assistant. A WebRTC card can then call the local
bridge directly:

```yaml
type: custom:webrtc-camera
streams:
  - url: ezviz2
    mode: webrtc
    media: video,audio
audio: true
muted: false
media_player: true
ui: true
ptz:
  opacity: 0.7
  service: rest_command.ezviz_local_ptz
  data_left:
    camera: cam2
    direction: left
    duration_ms: 250
    speed: 3
  data_right:
    camera: cam2
    direction: right
    duration_ms: 250
    speed: 3
  data_up:
    camera: cam2
    direction: up
    duration_ms: 250
    speed: 3
  data_down:
    camera: cam2
    direction: down
    duration_ms: 250
    speed: 3
shortcuts:
  - name: Sleep
    icon: mdi:sleep
    service: rest_command.ezviz_local_sleep
    service_data:
      camera: cam2
      enabled: true
  - name: Wake
    icon: mdi:weather-sunny
    service: rest_command.ezviz_local_sleep
    service_data:
      camera: cam2
      enabled: false
```

Local sleep and wake are experimental. The existing built-in EZVIZ integration can
remain configured as a fallback.

## Errors

Common HCNetSDK error codes:

- `1`: wrong username or password
- `7`: network connection failed
- `10`: receive timeout
- `23`: command unsupported by this camera
- `47`: user temporarily locked after failed logins

Stop testing after an authentication error; repeated failed logins can temporarily lock
the local camera account.

# EZVIZ HCNetSDK Bridge

This app exposes bounded PTZ commands and read-only state snapshots over a small local
HTTP API. It downloads Hikvision's official Linux x86-64 HCNetSDK during the image
build and does not store camera credentials in the repository.

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

## Read-only state snapshot

The snapshot endpoint performs only HCNetSDK GET calls. It independently checks PTZ
position, picture/motion configuration, recording configuration, compression, device
state, privacy-mask enable, smart tracking, park action, and live work state:

```bash
curl --fail-with-body \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/state-snapshot | jq
```

An unsupported camera command remains in the response with `sdk_ok: false` and its
`sdk_error_code`; the other reads continue. A successful query contains decoded
`values` and a SHA-256 fingerprint of the exact SDK structure.

To identify firmware fields changed by EZVIZ cloud Sleep Mode, capture both states:

```bash
curl --fail-with-body \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  'http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/state-snapshot?raw=1' \
  > cam2-awake.json

# Enable Sleep Mode in the EZVIZ mobile app, then run the same request:
curl --fail-with-body \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  'http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/state-snapshot?raw=1' \
  > cam2-sleep.json

diff -u <(jq -S . cam2-awake.json) <(jq -S . cam2-sleep.json)
```

`raw=1` includes bounded Base64 structures for field discovery. Raw device and work
state structures are always omitted because they can contain a serial number or LAN
client addresses.

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
```

The same endpoint can be polled as a REST sensor. Do not add another top-level
`sensor:` or `template:` key if one already exists; merge the entries instead:

```yaml
sensor:
  - platform: rest
    name: EZVIZ cam2 SDK state
    unique_id: ezviz_cam2_sdk_state
    resource: "http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/state-snapshot"
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
    scan_interval: 30
    timeout: 15
    value_template: "{{ value_json.supported_queries }}"
    json_attributes:
      - failed_queries
      - queries

template:
  - binary_sensor:
      - name: EZVIZ cam2 recording
        unique_id: ezviz_cam2_recording
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('values', {}).get('channel', {}).get('recording') }}

      - name: EZVIZ cam2 motion detection configured
        unique_id: ezviz_cam2_motion_detection_configured
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('picture', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('picture', {}).get('values', {}).get('motion_detection_enabled') }}

      - name: EZVIZ cam2 privacy mask configured
        unique_id: ezviz_cam2_privacy_mask_configured
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('privacy_mask', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('privacy_mask', {}).get('values', {}).get('enabled') }}

  - sensor:
      - name: EZVIZ cam2 pan position
        unique_id: ezviz_cam2_pan_position
        unit_of_measurement: "°"
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('ptz_position', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('ptz_position', {}).get('values', {}).get('pan_degrees') }}
```

The recording entity is live status from `NET_DVR_GetDVRWorkState_V30`. The motion and
privacy entities show whether those features are configured, not whether motion is
currently being detected.

## Errors

Common HCNetSDK error codes:

- `1`: wrong username or password
- `7`: network connection failed
- `10`: receive timeout
- `23`: command unsupported by this camera
- `47`: user temporarily locked after failed logins

Stop testing after an authentication error; repeated failed logins can temporarily lock
the local camera account.

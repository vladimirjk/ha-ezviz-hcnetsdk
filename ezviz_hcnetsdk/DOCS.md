# EZVIZ HCNetSDK Bridge

This app exposes verified bounded PTZ, local motion/tamper alarms, and state snapshots
over a small local HTTP API. It also contains firmware-dependent extended PTZ APIs for
testing compatible cameras. It downloads Hikvision's official Linux x86-64 HCNetSDK
during the image build and does not store camera credentials in the repository.

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
alarm_hold_seconds: 10
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

The API accepts `up`, `down`, `left`, `right`, `up_left`, `up_right`, `down_left`,
`down_right`, `zoom_in`, and `zoom_out`. Duration is restricted to 50-1500 milliseconds
and speed to 1-7. Every request sends a matching stop command.

Only `up`, `down`, `left`, and `right` are verified on the tested
CS-H6c-R101-1G2WF. That camera rejects diagonal and zoom commands with HCNetSDK error
`11`. Its lens has no SDK-controlled optical zoom; use the WebRTC card's client-side
digital zoom instead.

## Firmware-dependent PTZ APIs

The bridge implements the standard HCNetSDK operations below for testing other camera
models, but they are not part of the default H6c Home Assistant setup:

| Endpoint | Actions | Tested H6c result |
| --- | --- | --- |
| `/auto-pan` | `enabled: true/false`, speed 1-7 | Start rejected with error `11` |
| `/preset` | `set`, `goto`, `clear`; presets 1-255 | `set` rejected with error `11` |
| `/cruise` | `set_point`, `clear_point`, `run`, `stop` | Not usable because presets are unsupported |
| `/track` | `record_start`, `record_stop`, `run` | Playback without a recorded track rejected with error `11` |

Error `11` (`NET_DVR_NETWORK_ERRORDATA`) is misleadingly worded: it means the data
sent to the device is illegal or unsupported, or its response is invalid. It does not
by itself indicate a Wi-Fi failure. Do not repeatedly retry a command returning `11`.

The accepted JSON bodies are:

```text
POST /v1/cameras/{camera}/auto-pan  {"enabled":true,"speed":3}
POST /v1/cameras/{camera}/preset    {"action":"set","preset":1}
POST /v1/cameras/{camera}/cruise    {"action":"run","route":1}
POST /v1/cameras/{camera}/track     {"action":"run"}
```

Cruise configuration additionally uses `point` 1-32, `preset` 1-255, `dwell` 1-255,
and `speed` 1-40. Recorded tracks must be created with `record_start`, bounded PTZ
moves, and `record_stop` before `run`. HCNetSDK exposes no separate stop-playback call;
a bounded PTZ move interrupts playback.

## Manual recording test

The recording endpoint calls HCNetSDK's device-side manual-recording start/stop
functions. It does not change the persistent recording schedule and it is not known to
control EZVIZ cloud recording. Test it separately from PTZ:

```bash
# Stop device-side manual recording
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled":false}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/recording

# Start device-side manual recording
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled":true}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/recording
```

The current cam2 snapshot reports no disk and no active device recording. The camera
may therefore reject these calls, commonly with an SDK error such as `19` or `23`, or
accept them without changing cloud behavior. Recording control is independent of the
verified cardinal PTZ path and is omitted from the default HA configuration.

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

The bridge bounds HCNetSDK connection and receive waits to three seconds. If the
camera stops responding, the first transport failure stops the remaining reads and
the endpoint returns HTTP 200 with `responsive: false`, `transport_error_code`, and
the remaining query count in `skipped_queries`. This distinguishes an unavailable
local SDK from unsupported individual commands without hanging a Home Assistant poll.

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

## Local motion and tamper events

The events endpoint lazily opens one persistent HCNetSDK alarm-upload channel for the
camera, then returns bounded motion and video-tamper state:

```bash
curl --fail-with-body \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/events | jq
```

Example response:

```json
{
  "camera": "cam2",
  "subscribed": true,
  "hold_seconds": 10,
  "motion": {"active": false, "count": 0, "last_seen": null},
  "tamper": {"active": false, "count": 0, "last_seen": null},
  "last_command": null,
  "last_alarm_type": null
}
```

Some firmware sends an alarm start without a reliable clear message. The bridge
therefore keeps a received motion/tamper event active for `alarm_hold_seconds`, which
is configurable from 2 to 60 seconds. Calling the endpoint once arms the subscription;
regular Home Assistant polling keeps the SDK session in use. No alarm image, device
serial number, or raw callback body is returned.

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
the `ezviz_local_ptz` command beneath the existing section. YAML cannot contain a second
top-level `rest_command:` key.

Check configuration and restart Home Assistant. The WebRTC card keeps video on WebRTC,
sends the four verified motor controls through the local REST bridge, and performs zoom
inside the browser. Replace `ezviz2` if your go2rtc stream has a different name:

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
digital_ptz:
  mouse_drag_pan: true
  mouse_wheel_zoom: true
  mouse_double_click_zoom: true
  touch_drag_pan: true
  touch_pinch_zoom: true
  touch_tap_drag_zoom: true
  persist: true
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

Add both REST sensors to the same existing top-level `sensor:` section. The events
sensor's first successful poll arms the local alarm subscription:

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
      - responsive
      - failure_stage
      - transport_error_code
      - failed_queries
      - skipped_queries
      - queries

  - platform: rest
    name: EZVIZ cam2 local events
    unique_id: ezviz_cam2_local_events
    resource: "http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/events"
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
    scan_interval: 2
    timeout: 10
    value_template: "{{ value_json.subscribed }}"
    json_attributes:
      - subscribed
      - hold_seconds
      - motion
      - tamper
      - last_command
      - last_alarm_type
```

Add these entries beneath the same existing top-level `template:` section:

```yaml
template:
  - binary_sensor:
      - name: EZVIZ cam2 local SDK responsive
        unique_id: ezviz_cam2_local_sdk_responsive
        device_class: connectivity
        availability: "{{ has_value('sensor.ezviz_cam2_sdk_state') }}"
        state: >-
          {{ state_attr('sensor.ezviz_cam2_sdk_state', 'responsive') == true }}

      - name: EZVIZ cam2 recording
        unique_id: ezviz_cam2_recording
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('values', {}).get('channel', {}).get('recording') }}

      - name: EZVIZ cam2 signal lost
        unique_id: ezviz_cam2_signal_lost
        device_class: problem
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('values', {}).get('channel', {}).get('signal_lost') }}

      - name: EZVIZ cam2 motion detection configured
        unique_id: ezviz_cam2_motion_detection_configured
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('picture', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('picture', {}).get('values', {}).get('motion_detection_enabled') }}

      - name: EZVIZ cam2 local motion
        unique_id: ezviz_cam2_local_motion
        device_class: motion
        availability: >-
          {{ has_value('sensor.ezviz_cam2_local_events') }}
        state: >-
          {% set event = state_attr('sensor.ezviz_cam2_local_events', 'motion') | default({}, true) %}
          {{ event.get('active') == true }}

      - name: EZVIZ cam2 local tamper
        unique_id: ezviz_cam2_local_tamper
        device_class: tamper
        availability: >-
          {{ has_value('sensor.ezviz_cam2_local_events') }}
        state: >-
          {% set event = state_attr('sensor.ezviz_cam2_local_events', 'tamper') | default({}, true) %}
          {{ event.get('active') == true }}

  - sensor:
      - name: EZVIZ cam2 bitrate
        unique_id: ezviz_cam2_bitrate
        device_class: data_rate
        state_class: measurement
        unit_of_measurement: "bit/s"
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('work_state', {}).get('values', {}).get('channel', {}).get('bitrate') }}

      - name: EZVIZ cam2 SD status
        unique_id: ezviz_cam2_sd_status
        icon: mdi:micro-sd
        availability: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {{ q.get('device', {}).get('sdk_ok') == true }}
        state: >-
          {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
          {% set count = q.get('device', {}).get('values', {}).get('disk_count', 0) | int(0) %}
          {% set disks = q.get('work_state', {}).get('values', {}).get('disks', []) %}
          {% if count == 0 %}not_installed
          {% elif disks | length > 0 %}{{ disks[0].get('status_name', 'unknown') }}
          {% else %}present{% endif %}
        attributes:
          disk_count: >-
            {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
            {{ q.get('device', {}).get('values', {}).get('disk_count', 0) }}
          disks: >-
            {% set q = state_attr('sensor.ezviz_cam2_sdk_state', 'queries') | default({}, true) %}
            {{ q.get('work_state', {}).get('values', {}).get('disks', []) }}
```

Do not create a second top-level `sensor:`, `template:`, or `rest_command:` key—merge
the entries under the key already present. The `recording` entity is live work-state,
while `motion detection configured` only reports whether camera-side detection is
enabled. `local motion` and `local tamper` are actual alarm events. `local SDK
responsive` also turns off for a Wi-Fi outage or powered-off camera, so it is a
connectivity signal rather than a dedicated Sleep Mode indicator.

## Errors

Common HCNetSDK error codes:

- `1`: wrong username or password
- `7`: network connection failed
- `10`: receive timeout
- `11`: command data is illegal or unsupported by the device
- `23`: command unsupported by this camera
- `47`: user temporarily locked after failed logins

Stop testing after an authentication error; repeated failed logins can temporarily lock
the local camera account.

# EZVIZ HCNetSDK Bridge

This app exposes bounded and continuous PTZ commands, camera-stored presets, cruises,
recorded PTZ tracks, local motion/tamper alarms, and state snapshots over a small local
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

Allowed directions are `up`, `down`, `left`, `right`, `up_left`, `up_right`,
`down_left`, `down_right`, `zoom_in`, and `zoom_out`. Duration is restricted to
50-1500 milliseconds and speed to 1-7. Every request sends a matching stop command,
including zoom and diagonal movement.

## Auto-pan test

Auto-pan is continuous, so always test its stop request immediately after its start
request:

```bash
# Start
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled":true,"speed":3}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/auto-pan

# Stop
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"enabled":false,"speed":3}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/auto-pan
```

Any bounded PTZ move, preset recall, cruise, or track command also stops an active
auto-pan first.

## PTZ preset test

The tested H6c accepts a PTZ-position query but returns zero for every coordinate, so
the bridge cannot reliably read and restore an absolute position. Camera-stored PTZ
presets avoid that problem.

With the camera at its normal viewing position, save preset 1:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"set","preset":1}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/preset
```

Use the bounded PTZ endpoint to point the camera at a wall or into its enclosure, then
save that as preset 2 by changing `preset` to `2`. Test both positions without
overwriting them:

```bash
# Normal position
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"goto","preset":1}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/preset

# Privacy position
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"goto","preset":2}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/preset
```

Actions are `set`, `goto`, and `clear`; preset numbers are restricted to 1-255. These
writes use the camera's preset storage and should be tested before adding an HA script.

## Cruises and recorded PTZ tracks

These operations depend on camera firmware. Configure cruise route 1, point 1 to use
preset 1, wait 10 seconds, and move at speed 3:

```bash
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"set_point","route":1,"point":1,"preset":1,"dwell":10,"speed":3}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/cruise

# Add a second point by changing point and preset, then run route 1
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"run","route":1}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/cruise

# Stop route 1
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"stop","route":1}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/cruise
```

Routes and points are 1-32, presets 1-255, dwell 1-255, and speed 1-40. Remove a route
point with `{"action":"clear_point","route":1,"point":1,"preset":1}`.

A recorded PTZ track stores manual moves performed between `record_start` and
`record_stop`, then replays them with `run`:

```bash
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"record_start"}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/track

# Move the camera with /ptz requests, then stop recording the track
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"record_stop"}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/track

# Replay the recorded track
curl --fail-with-body --request POST \
  --header "Authorization: Bearer YOUR_API_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"action":"run"}' \
  http://HOME_ASSISTANT_IP:8977/v1/cameras/cam2/track
```

HCNetSDK has no separate stop-playback command for this track API. A bounded PTZ move
interrupts it. An SDK error such as `23` means that operation is unavailable on the
camera firmware.

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
accept them without changing cloud behavior. A recording failure does not affect the
separate preset operation.

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

  ezviz_local_auto_pan:
    url: "http://HOME_ASSISTANT_IP:8977/v1/cameras/{{ camera }}/auto-pan"
    method: POST
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
      Content-Type: application/json
    payload: >-
      {"enabled":{{ enabled | bool | lower }},"speed":{{ speed | default(3) }}}

  ezviz_local_preset:
    url: "http://HOME_ASSISTANT_IP:8977/v1/cameras/{{ camera }}/preset"
    method: POST
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
      Content-Type: application/json
    payload: >-
      {"action":"{{ action }}","preset":{{ preset }}}

  ezviz_local_cruise:
    url: "http://HOME_ASSISTANT_IP:8977/v1/cameras/{{ camera }}/cruise"
    method: POST
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
      Content-Type: application/json
    payload: >-
      {"action":"{{ action }}","route":{{ route | default(1) }},"point":{{ point | default(1) }},"preset":{{ preset | default(1) }},"dwell":{{ dwell | default(10) }},"speed":{{ speed | default(3) }}}

  ezviz_local_track:
    url: "http://HOME_ASSISTANT_IP:8977/v1/cameras/{{ camera }}/track"
    method: POST
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
      Content-Type: application/json
    payload: >-
      {"action":"{{ action }}"}

  ezviz_local_manual_recording:
    url: "http://HOME_ASSISTANT_IP:8977/v1/cameras/{{ camera }}/recording"
    method: POST
    headers:
      Authorization: !secret ezviz_hcnetsdk_authorization
      Content-Type: application/json
    payload: >-
      {"enabled":{{ enabled | lower }}}
```

If `configuration.yaml` already has a top-level `rest_command:` section, add only
the named commands beneath the existing section. YAML cannot contain a second
top-level `rest_command:` key.

Check configuration and restart Home Assistant. First use **Developer tools > Actions**
to test `rest_command.ezviz_local_preset` with this data before configuring cruises:

```yaml
camera: cam2
action: set
preset: 1
```

The WebRTC card keeps video on WebRTC and sends only controls through the local REST
bridge. Replace `ezviz2` if your go2rtc stream has a different name:

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
  data_zoom_in:
    camera: cam2
    direction: zoom_in
    duration_ms: 250
    speed: 3
  data_zoom_out:
    camera: cam2
    direction: zoom_out
    duration_ms: 250
    speed: 3
shortcuts:
  - name: Up-left
    icon: mdi:arrow-top-left
    service: rest_command.ezviz_local_ptz
    service_data:
      camera: cam2
      direction: up_left
      duration_ms: 250
      speed: 3
  - name: Up-right
    icon: mdi:arrow-top-right
    service: rest_command.ezviz_local_ptz
    service_data:
      camera: cam2
      direction: up_right
      duration_ms: 250
      speed: 3
  - name: Down-left
    icon: mdi:arrow-bottom-left
    service: rest_command.ezviz_local_ptz
    service_data:
      camera: cam2
      direction: down_left
      duration_ms: 250
      speed: 3
  - name: Down-right
    icon: mdi:arrow-bottom-right
    service: rest_command.ezviz_local_ptz
    service_data:
      camera: cam2
      direction: down_right
      duration_ms: 250
      speed: 3
  - name: Preset 1
    icon: mdi:numeric-1-box
    service: rest_command.ezviz_local_preset
    service_data:
      camera: cam2
      action: goto
      preset: 1
  - name: Preset 2
    icon: mdi:numeric-2-box
    service: rest_command.ezviz_local_preset
    service_data:
      camera: cam2
      action: goto
      preset: 2
  - name: Auto-pan
    icon: mdi:pan-horizontal
    service: rest_command.ezviz_local_auto_pan
    service_data:
      camera: cam2
      enabled: true
      speed: 3
  - name: Stop pan
    icon: mdi:stop
    service: rest_command.ezviz_local_auto_pan
    service_data:
      camera: cam2
      enabled: false
      speed: 3
  - name: Cruise 1
    icon: mdi:map-marker-path
    service: rest_command.ezviz_local_cruise
    service_data:
      camera: cam2
      action: run
      route: 1
  - name: Stop cruise
    icon: mdi:stop-circle-outline
    service: rest_command.ezviz_local_cruise
    service_data:
      camera: cam2
      action: stop
      route: 1
  - name: Play track
    icon: mdi:motion-play-outline
    service: rest_command.ezviz_local_track
    service_data:
      camera: cam2
      action: run
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
- `23`: command unsupported by this camera
- `47`: user temporarily locked after failed logins

Stop testing after an authentication error; repeated failed logins can temporarily lock
the local camera account.

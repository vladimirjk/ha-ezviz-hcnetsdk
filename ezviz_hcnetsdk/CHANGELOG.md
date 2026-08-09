# Changelog

## 0.7.0

- Add bounded zoom and diagonal PTZ movement plus continuous auto-pan start/stop.
- Add preset-cruise route configuration and firmware-dependent recorded PTZ tracks.
- Add persistent local HCNetSDK motion/tamper alarm subscriptions with configurable
  event hold time.
- Document live H6c compatibility results: cardinal PTZ works, while diagonal, optical
  zoom, auto-pan, and preset storage are rejected with SDK error 11.
- Keep firmware-dependent APIs documented for other models, but use only verified
  cardinal PTZ and browser-side digital zoom in the default cam2 card.

## 0.6.0

- Add camera-stored PTZ preset set, recall, and clear operations.
- Add separate device-side manual-recording start and stop operations using the native
  HCNetSDK API.
- Document isolated live tests and Home Assistant REST commands for both operations.

## 0.5.1

- Bound local SDK connect and receive waits to three seconds.
- Stop a state snapshot after the first transport failure instead of waiting through
  every remaining SDK query.
- Return `responsive: false` as normal JSON when cloud Sleep Mode or a network outage
  makes the local SDK unavailable.

## 0.5.0

- Remove unsupported sleep/wake, ISAPI probe, and local web-service endpoints.
- Add an authenticated, GET-only HCNetSDK snapshot for PTZ position, recording,
  motion/picture, stream compression, device, privacy, tracking, park action, and
  live work state.
- Decode stable state fields for Home Assistant REST/template entities and retain
  per-query SDK failures for unsupported camera commands.

## 0.4.1

- Run the read-only sleep probe over SDK-over-TLS on port 8443.
- Probe JSON/XML device capabilities, consumption mode, and privacy-mask paths.

## 0.4.0

- Use EZVIZ Studio's SDK-over-TLS V40 login on port 8443 for `servicesSwitch`.
- Require the camera's `statusCode: 1` response before verifying a switch update.

## 0.3.0

- Add an explicit read-modify-verify endpoint for the EZVIZ local `web` service.

## 0.2.2

- Match the EZVIZ Android app's CRLF-terminated HCNetSDK ISAPI request framing.
- Probe the app-observed EZVIZ `servicesSwitch` endpoint and generic device/system
  reads to distinguish unsupported sleep paths from an unsupported ISAPI tunnel.

## 0.2.1

- Add an authenticated, GET-only ISAPI sleep capability probe over HCNetSDK.
- Query JSON and XML consumption-mode capabilities and current state without changing
  camera configuration.

## 0.2.0

- Add native HCNetSDK sleep and remote-wake control.
- Preserve the full device module configuration when enabling sleep.
- Add Home Assistant REST commands and WebRTC card shortcuts for sleep and wake.

## 0.1.0

- Add HCNetSDK initialization and per-camera login checks.
- Add bounded local PTZ controls for up, down, left, and right.
- Add bearer-token authentication and request validation.

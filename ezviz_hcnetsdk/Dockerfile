# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS sdk-fetch

ARG SDK_URL="https://assets.hikvision.com/prd/normal/all/files/202605/EN-HCNetSDKV6.1.9.48_build20230410_linux64.zip"
ARG SDK_SHA256="8de553fb2e8dbb0ac441ee1bd73c5ecb73d720c1359396750479a6e169abf93f"
ARG SDK_DIR="EN-HCNetSDKV6.1.9.48_build20230410_linux64"

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --retry 3 \
      --user-agent "Mozilla/5.0" \
      --referer "https://display.hikvision.com/apac/support/tools/hitools/clf4633a00e385d6ea/" \
      "${SDK_URL}" --output /tmp/hcnetsdk.zip \
    && echo "${SDK_SHA256}  /tmp/hcnetsdk.zip" | sha256sum --check --strict \
    && unzip -q /tmp/hcnetsdk.zip \
      "${SDK_DIR}/lib/*" \
      "${SDK_DIR}/doc/Open Source Software Licenses-HCNetSDK.txt" \
      -d /tmp/hcnetsdk \
    && mkdir -p /opt/hikvision-sdk /usr/share/licenses/hikvision-hcnetsdk \
    && cp -a "/tmp/hcnetsdk/${SDK_DIR}/lib/." /opt/hikvision-sdk/ \
    && cp "/tmp/hcnetsdk/${SDK_DIR}/doc/Open Source Software Licenses-HCNetSDK.txt" \
      /usr/share/licenses/hikvision-hcnetsdk/ \
    && find /opt/hikvision-sdk -type f -name '*.so*' -exec chmod 0755 {} + \
    && rm -rf /tmp/hcnetsdk /tmp/hcnetsdk.zip

FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ARG BUILD_VERSION="dev"

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="amd64" \
    org.opencontainers.image.source="https://github.com/vladimirjk/ha-ezviz-hcnetsdk" \
    org.opencontainers.image.licenses="MIT"

ENV HIKVISION_SDK_PATH="/opt/hikvision-sdk" \
    LD_LIBRARY_PATH="/opt/hikvision-sdk:/opt/hikvision-sdk/HCNetSDKCom" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1"

COPY --from=sdk-fetch /opt/hikvision-sdk /opt/hikvision-sdk
COPY --from=sdk-fetch /usr/share/licenses/hikvision-hcnetsdk \
    /usr/share/licenses/hikvision-hcnetsdk

WORKDIR /opt/bridge

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --only-binary=:all: \
      --require-hashes \
      --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY bridge /opt/bridge/bridge

EXPOSE 8977

CMD ["python", "-m", "bridge"]

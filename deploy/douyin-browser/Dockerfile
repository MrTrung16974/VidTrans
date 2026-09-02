FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1440x900x24 \
    CHROMIUM_PROFILE_DIR=/data/profile

RUN apt-get update -o Acquire::Retries=3 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=3 \
        ca-certificates \
        chromium \
        curl \
        dbus-x11 \
        fluxbox \
        fonts-noto-cjk \
        novnc \
        procps \
        tini \
        websockify \
        x11-utils \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY start-browser.sh /usr/local/bin/start-douyin-browser
COPY cdp-proxy.py /usr/local/bin/douyin-cdp-proxy
RUN chmod 0755 /usr/local/bin/start-douyin-browser /usr/local/bin/douyin-cdp-proxy \
    && mkdir -p /data/profile

EXPOSE 5900 6080 9222
VOLUME ["/data/profile"]

HEALTHCHECK --interval=15s --timeout=8s --start-period=40s --retries=5 \
    CMD curl -fsS http://127.0.0.1:6080/vnc_lite.html >/dev/null \
        && curl -fsS http://127.0.0.1:9222/json/version | grep -q '"Browser"' \
        || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/start-douyin-browser"]

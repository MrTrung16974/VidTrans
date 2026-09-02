#!/bin/sh
set -eu

display_number="${DISPLAY#:}"
screen_geometry="${SCREEN_GEOMETRY:-1024x720x24}"
profile_dir="${CHROMIUM_PROFILE_DIR:-/data/profile}"

mkdir -p "$profile_dir"
rm -f "/tmp/.X${display_number}-lock"
rm -rf "/tmp/.X11-unix/X${display_number}"

# The profile volume survives container replacement. Chromium's singleton /
# lock files contain the previous container's hostname and PID; they are stale
# and safe to remove because Compose runs exactly one browser per volume.
# These files are symlinks — "test -e" returns false for dangling symlinks, so
# always use "rm -f" unconditionally rather than a conditional check.
# DevToolsActivePort and lockfile are also left behind after an unclean exit.
for stale_lock in \
        "$profile_dir/SingletonLock" \
        "$profile_dir/SingletonSocket" \
        "$profile_dir/SingletonCookie" \
        "$profile_dir/DevToolsActivePort" \
        "$profile_dir/lockfile"; do
    if [ -e "$stale_lock" ] || [ -L "$stale_lock" ]; then
        echo "startup: removing stale lock: $stale_lock"
        rm -f "$stale_lock"
    fi
done

chromium_pid=""
vnc_pid=""
fluxbox_pid=""
xvfb_pid=""
cdp_proxy_pid=""

Xvfb "$DISPLAY" -screen 0 "$screen_geometry" -ac -nolisten tcp &
xvfb_pid=$!

cleanup() {
    for process_id in "$cdp_proxy_pid" "$chromium_pid" "$vnc_pid" "$fluxbox_pid" "$xvfb_pid"; do
        if [ -n "$process_id" ]; then
            kill "$process_id" 2>/dev/null || true
        fi
    done
}
trap cleanup INT TERM EXIT

for attempt in 1 2 3 4 5; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    if [ "$attempt" -eq 5 ]; then
        echo "Xvfb did not become ready" >&2
        exit 1
    fi
    sleep 1
done

# Fluxbox: remove all window decorations for the Chromium app window so
# noVNC shows only the Douyin page with no title bar or border.
mkdir -p /root/.fluxbox
cat > /root/.fluxbox/apps << 'FLUXEOF'
[app] (class=Chromium-browser)
[Decorations]	{NONE}
[end]
[app] (class=chromium)
[Decorations]	{NONE}
[end]
FLUXEOF

# --app removes Chromium's own browser chrome (tabs, address bar, toolbar).
# The page content fills the window directly, putting the Douyin login modal
# front and centre with no surrounding browser UI.
# Fluxbox must be running before Chromium so it reads the apps config and
# applies the no-decoration rule as soon as the window is mapped.
fluxbox -display "$DISPLAY" >/tmp/fluxbox.log 2>&1 &
fluxbox_pid=$!

chromium \
    --no-sandbox \
    --disable-setuid-sandbox \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    --password-store=basic \
    --lang=zh-CN \
    --force-device-scale-factor=1 \
    --window-size=1024,720 \
    --window-position=0,0 \
    --app=https://www.douyin.com/ \
    --remote-debugging-port=9223 \
    --user-data-dir="$profile_dir" \
    >/tmp/chromium.log 2>&1 &
chromium_pid=$!

# Current Chromium releases intentionally bind DevTools to loopback even when
# remote-debugging-address is supplied. This small raw TCP bridge exposes CDP
# only to the private Docker network; Compose never publishes port 9222.
python3 /usr/local/bin/douyin-cdp-proxy 0.0.0.0 9222 127.0.0.1 9223 douyin-browser \
    >/tmp/cdp-proxy.log 2>&1 &
cdp_proxy_pid=$!

x11vnc \
    -display "$DISPLAY" \
    -forever \
    -shared \
    -nopw \
    -listen 0.0.0.0 \
    -rfbport 5900 \
    -noxdamage \
    >/tmp/x11vnc.log 2>&1 &
vnc_pid=$!

# Keep websockify in the foreground so Docker can supervise the complete
# interactive browser stack. VNC/CDP remain internal or loopback-only.
exec websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900

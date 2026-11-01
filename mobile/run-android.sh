#!/usr/bin/env bash
set -euo pipefail

ADB="${ANDROID_HOME:-$HOME/Library/Android/sdk}/platform-tools/adb"
API_PORT="${VIDTRANS_API_PORT:-5210}"

if [[ ! -x "$ADB" ]]; then
  echo "Không tìm thấy adb tại: $ADB" >&2
  exit 1
fi

DEVICES=($("$ADB" devices | awk 'NR > 1 && $2 == "device" {print $1}'))
if [[ ${#DEVICES[@]} -eq 0 ]]; then
  echo "Không tìm thấy điện thoại Android đã được cấp quyền USB debugging." >&2
  exit 1
fi

DEVICE_ID="${1:-${DEVICES[0]}}"
if ! printf '%s\n' "${DEVICES[@]}" | grep -Fxq "$DEVICE_ID"; then
  echo "Thiết bị $DEVICE_ID chưa kết nối hoặc chưa được cấp quyền." >&2
  exit 1
fi

echo "Thiết lập kết nối API cho $DEVICE_ID: tcp:$API_PORT -> tcp:$API_PORT"
"$ADB" -s "$DEVICE_ID" reverse "tcp:$API_PORT" "tcp:$API_PORT"

exec flutter run -d "$DEVICE_ID" \
  --dart-define="VIDTRANS_API_BASE=http://127.0.0.1:$API_PORT"

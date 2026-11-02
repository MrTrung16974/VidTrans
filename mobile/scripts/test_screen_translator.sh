#!/usr/bin/env bash
# Drives integration_test/screen_translator_test.dart on an Android device or
# emulator: grants the overlay permission, accepts the screen-capture dialog,
# taps the floating "Dịch" button and saves screenshots to OUT.
#   scripts/test_screen_translator.sh [device-id] [out-dir]
set -u
DEVICE="${1:-emulator-5554}"
OUT="${2:-build/screen_translator_test}"
PKG="cloud.vidtrans.app"
ADB="${ADB:-adb} -s $DEVICE"
mkdir -p "$OUT"
LOG="$OUT/flutter_test.log"

shot() { $ADB exec-out screencap -p > "$OUT/$1.png"; echo "screenshot $OUT/$1.png"; }
wait_log() { # pattern timeout_seconds
  for _ in $(seq 1 "$2"); do grep -q "$1" "$LOG" 2>/dev/null && return 0; sleep 1; done; return 1
}

$ADB logcat -c
flutter test integration_test/screen_translator_test.dart -d "$DEVICE" > "$LOG" 2>&1 &
TEST_PID=$!

# 1. "Display over other apps" as soon as the app is installed.
for _ in $(seq 1 300); do
  $ADB shell pm path "$PKG" >/dev/null 2>&1 && break; sleep 1
done
for _ in $(seq 1 30); do $ADB shell appops set "$PKG" SYSTEM_ALERT_WINDOW allow && break; sleep 1; done
echo "overlay permission granted"

# 2. Accept the MediaProjection consent dialog.
wait_log "requesting capture consent" 120 || { echo "test never asked for consent"; tail -30 "$LOG"; exit 1; }
accepted=0
for _ in $(seq 1 20); do
  $ADB shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
  bounds=$($ADB shell cat /sdcard/ui.xml | tr '>' '\n' | grep -iE 'text="(Start now|Start|Share screen|Bắt đầu ngay|Bắt đầu|Chia sẻ màn hình)"' \
    | grep -o 'bounds="[^"]*"' | tail -1)
  if [ -n "$bounds" ]; then
    read -r x1 y1 x2 y2 <<<"$(echo "$bounds" | grep -oE '[0-9]+' | tr '\n' ' ')"
    $ADB shell input tap $(( (x1 + x2) / 2 )) $(( (y1 + y2) / 2 ))
    accepted=1; echo "consent accepted ($bounds)"; break
  fi
  sleep 1
done
[ "$accepted" = 1 ] || { echo "consent button not found"; shot consent_dialog; }

# 3. Tap the floating button, found by its accessibility label.
wait_log "SCREEN_TRANSLATOR: started" 30 || { echo "service did not start"; tail -30 "$LOG"; }
sleep 3
shot 1_before
size=$($ADB shell wm size | grep -oE '[0-9]+x[0-9]+' | tail -1)
W=${size%x*}; H=${size#*x}
# The service logs where it placed the button (overlay windows are not in
# uiautomator dumps).
pos=$($ADB logcat -d -s ScreenTranslator:I | grep -oE 'bubble at [0-9]+,[0-9]+,[0-9]+,[0-9]+' | tail -1 | grep -oE '[0-9]+' | tr '\n' ' ')
read -r x y w h <<<"$pos"
if [ -z "${h:-}" ]; then echo "floating button position not logged"; else
  BX=$(( x + w / 2 )); BY=$(( y + h / 2 ))
  $ADB shell input tap "$BX" "$BY"; echo "tapped floating button at $BX,$BY"
fi
sleep 6
shot 2_translated

# 4. Tap the overlay to close it.
$ADB shell input tap $(( W / 2 )) $(( H / 2 ))
sleep 2
shot 3_closed

wait $TEST_PID
STATUS=$?
grep -E "SCREEN_TRANSLATOR|All tests|Some tests|\[E\]" "$LOG"
exit $STATUS

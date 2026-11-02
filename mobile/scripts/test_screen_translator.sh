#!/usr/bin/env bash
# Drives integration_test/screen_translator_test.dart on an Android device or
# emulator: grants the overlay permission, accepts the screen-capture dialog,
# taps the floating "Dịch" button and saves screenshots to OUT.
#   scripts/test_screen_translator.sh [device-id] [out-dir] [live]
# With "live" it tests continuous mode on the Android Settings app (another
# app, so Android's untrusted-touch rules apply): the translation must appear
# by itself, hide while scrolling, come back after, and let the swipe through.
set -u
DEVICE="${1:-emulator-5554}"
OUT="${2:-build/screen_translator_test}"
LIVE="${3:-}"
PKG="cloud.vidtrans.app"
ADB="${ADB:-adb} -s $DEVICE"
mkdir -p "$OUT"
LOG="$OUT/flutter_test.log"

shot() { $ADB exec-out screencap -p > "$OUT/$1.png"; echo "screenshot $OUT/$1.png"; }
wait_log() { # pattern timeout_seconds
  for _ in $(seq 1 "$2"); do grep -q "$1" "$LOG" 2>/dev/null && return 0; sleep 1; done; return 1
}

$ADB logcat -c
DEFINES=()
[ "$LIVE" = live ] && DEFINES=(--dart-define=LIVE=true)
# MODE=offline: real ML Kit translation instead of the debug echo translator.
[ -n "${MODE:-}" ] && DEFINES+=("--dart-define=MODE=$MODE")
# ${arr[@]+...}: macOS bash 3.2 treats an empty array as unbound under set -u.
flutter test integration_test/screen_translator_test.dart -d "$DEVICE" ${DEFINES[@]+"${DEFINES[@]}"} > "$LOG" 2>&1 &
TEST_PID=$!

# 1. "Display over other apps" as soon as the app is installed.
for _ in $(seq 1 300); do
  $ADB shell pm path "$PKG" >/dev/null 2>&1 && break; sleep 1
done
for _ in $(seq 1 30); do $ADB shell appops set "$PKG" SYSTEM_ALERT_WINDOW allow && break; sleep 1; done
echo "overlay permission granted"

# 2. Accept the MediaProjection consent dialog.
wait_log "requesting capture consent" 240 || { echo "test never asked for consent"; tail -30 "$LOG"; exit 1; }
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

if [ "$LIVE" = live ]; then
  wait_log "SCREEN_TRANSLATOR: started" 30 || { echo "service did not start"; tail -30 "$LOG"; }
  # Another app with a long Chinese chat: Chrome on a data: page (Android's own
  # Settings hides third-party overlays, so it cannot be used here).
  $ADB shell am set-debug-app --persistent com.android.chrome >/dev/null
  $ADB shell 'echo "_ --disable-fre --no-default-browser-check --no-first-run" > /data/local/tmp/chrome-command-line'
  PAGE=$(python3 -c '
import urllib.parse
msgs = ["你今天有时间开会吗？", "我们下午三点在会议室见面吧", "好的，没问题", "记得带上项目报告",
        "客户明天上午来公司参观", "请提前准备好演示文稿", "晚上一起吃饭吗？", "这家餐厅的菜很好吃"] * 3
rows = "".join(
    "<div style=\"margin:22px 0;text-align:%s\"><span style=\"display:inline-block;padding:12px 16px;"
    "border-radius:6px;font-size:20px;background:%s\">%s</span></div>"
    % ("right" if i % 2 else "left", "#95EC69" if i % 2 else "#fff", m)
    for i, m in enumerate(msgs))
html = "<html><head><meta name=viewport content=\"width=device-width\"></head>" \
       "<body style=\"background:#EDEDED;font-family:sans-serif;margin:16px\">" + rows + "</body></html>"
print("data:text/html;charset=utf-8," + urllib.parse.quote(html))')
  $ADB shell am start -n com.android.chrome/com.google.android.apps.chrome.Main -a android.intent.action.VIEW -d "'$PAGE'" >/dev/null
  sleep 6
  # Chrome's notification prompt sits under our overlay: tapping "No thanks"
  # also proves taps reach the app below the live translation.
  $ADB shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
  nothanks=$($ADB shell cat /sdcard/ui.xml | tr '>' '\n' | grep -E 'text="No thanks"' | grep -o 'bounds="[^"]*"' | head -1 | grep -oE '[0-9]+' | tr '\n' ' ')
  if [ -n "$nothanks" ]; then
    read -r x1 y1 x2 y2 <<<"$nothanks"
    $ADB shell input tap $(( (x1 + x2) / 2 )) $(( (y1 + y2) / 2 ))
    echo "tapped Chrome's No thanks through the overlay"
  fi
  sleep 5
  shot live_1_chrome
  size=$($ADB shell wm size | grep -oE '[0-9]+x[0-9]+' | tail -1)
  W=${size%x*}; H=${size#*x}
  $ADB shell input swipe $(( W / 2 )) $(( H * 3 / 4 )) $(( W / 2 )) $(( H / 4 )) 300
  shot live_2_scrolling
  sleep 5
  shot live_3_after_scroll
  # Back to the test app so it can stop the service and finish.
  $ADB shell am start -n "$PKG/.MainActivity" >/dev/null
else
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

fi

wait $TEST_PID
STATUS=$?
grep -E "SCREEN_TRANSLATOR|All tests|Some tests|\[E\]" "$LOG"
exit $STATUS

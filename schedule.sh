#!/bin/bash
# Turn the 7:00am daily job email on or off.
#   bash schedule.sh on     # start daily emails
#   bash schedule.sh off    # stop them
#   bash schedule.sh status
set -e
LABEL="com.mbavosa.dailyjobs"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"

case "$1" in
  on)
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$DIR/daily.py</string><string>--email</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$DIR/daily.log</string>
  <key>StandardErrorPath</key><string>$DIR/daily.log</string>
</dict></plist>
EOF
    rm -f "$DIR/unsubscribed.txt"
    date +%F > "$DIR/subscribed_at.txt"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "Daily job email is ON (7:00am). Log: $DIR/daily.log"
    ;;
  off)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Daily job email is OFF."
    ;;
  status)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then echo "ON"; else echo "OFF"; fi
    ;;
  *)
    echo "Usage: bash $0 on|off|status"; exit 1
    ;;
esac

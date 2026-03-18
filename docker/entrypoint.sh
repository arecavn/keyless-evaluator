#!/bin/sh
# Start Xvfb virtual display, then run the application.
# Xvfb lets Chromium run in "headed" mode on a virtual screen (no real monitor needed).
# This avoids Cloudflare bot-detection that targets headless Chromium.

Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Give Xvfb a moment to initialize
sleep 1

# Verify Xvfb started
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "[entrypoint] WARNING: Xvfb failed to start — falling back to headless mode"
    export CHATGPT_WEB_HEADLESS=1
fi

exec "$@"

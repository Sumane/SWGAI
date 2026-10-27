#!/usr/bin/env bash
# Start SWGEmu under Wine in its OWN X display, so JedAI can capture it and
# send keys to it without touching your other windows.
#
#   scripts/linux/start_swg.sh                  # fullscreen game display (default)
#   SWG_WINDOW=windowed RES=1920x1080 scripts/linux/start_swg.sh
#
# On Wayland (Ubuntu's default) it opens a "rootful" Xwayland display on :2 —
# GPU-accelerated — and runs the game inside it. Fullscreen is the default:
# a window gets shrunk by the desktop's top bar and title bar, which cuts off
# the right and bottom of the game (the minimap coordinates and menu bar).
# Use Super or Alt+Tab to get back to your terminals.
# Then set in config/settings.local.yaml:
#     platform: {x_display: ":2"}
#     screen:   {monitor_index: 1}
# On an Xorg session it just runs the game on your normal display.
set -euo pipefail

SWG_DIR="${SWG_DIR:-/run/media/marc/New Volume/Swgemu}"
PREFIX="${WINEPREFIX:-$HOME/.wine-swg}"
DISP="${JEDAI_DISPLAY:-:2}"
SWG_WINDOW="${SWG_WINDOW:-fullscreen}"   # fullscreen | windowed
RES="${RES:-2560x1440}"          # windowed only; match screenWidth/screenHeight in options.cfg

XPID=""
cleanup() { [[ -n "$XPID" ]] && kill "$XPID" 2>/dev/null || true; }
trap cleanup EXIT

if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    command -v Xwayland >/dev/null || { echo "Xwayland not found: sudo apt install xwayland"; exit 1; }
    n="${DISP#:}"
    if [[ -e "/tmp/.X11-unix/X$n" ]]; then
        echo "Display $DISP already exists — reusing it"
    else
        echo "Starting game display $DISP ($SWG_WINDOW)..."
        if [[ "$SWG_WINDOW" == "fullscreen" ]]; then
            Xwayland "$DISP" -fullscreen -noreset &
        # -decorate gives the window a title bar (needs a recent Xwayland); fall back without it
        elif Xwayland -help 2>&1 | grep -q -- "-decorate"; then
            Xwayland "$DISP" -geometry "$RES" -decorate -noreset &
        else
            Xwayland "$DISP" -geometry "$RES" -noreset &
        fi
        XPID=$!
        for _ in $(seq 50); do [[ -e "/tmp/.X11-unix/X$n" ]] && break; sleep 0.1; done
        [[ -e "/tmp/.X11-unix/X$n" ]] || { echo "Xwayland didn't start"; exit 1; }
    fi
    export DISPLAY="$DISP"
    echo "Game display: $DISPLAY  (set platform.x_display: \"$DISPLAY\", screen.monitor_index: 1)"
else
    echo "Xorg session: running on your normal display $DISPLAY"
fi

cd "$SWG_DIR"
WINEPREFIX="$PREFIX" wine SWGEmu.exe

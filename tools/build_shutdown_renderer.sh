#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
PYTHON="${PYTHON:-python3}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/shutdown}"
CC="${CC:-cc}"
# The player TOML (standard location unless DISPLAY_CONFIG is set) is mandatory;
# never reuse stale device-specific headers.
DISPLAY_CONFIG="${DISPLAY_CONFIG:-${SONOSCDPLAYER_CONFIG_DIR:-/etc/sonoscdplayer}/player.toml}"
[ -f "$DISPLAY_CONFIG" ] || { echo "DISPLAY_CONFIG file not found: $DISPLAY_CONFIG" >&2; exit 2; }
BACKEND=$("$PYTHON" "$ROOT/tools/display_config.py" --config "$DISPLAY_CONFIG")
mkdir -p "$BUILD_DIR"
"$PYTHON" "$ROOT/tools/display_config.py" \
    --config "$DISPLAY_CONFIG" \
    --output "$BUILD_DIR/display_config.h"
"$PYTHON" "$ROOT/tools/build_shutdown_renderer.py" \
    --display-config "$DISPLAY_CONFIG" \
    --wait "$ROOT/src/sonoscdplayer/assets/screens/shutdown_screen.png" \
    --final "$ROOT/src/sonoscdplayer/assets/screens/pleasewait_screen.png" \
    --reboot "$ROOT/src/sonoscdplayer/assets/screens/reboot_screen.png" \
    --transition "$ROOT/src/sonoscdplayer/assets/screens/shutdown_screen.png" \
    --output "$BUILD_DIR/shutdown_frames.h"
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -I"$BUILD_DIR" \
    "$ROOT/src/native/shutdown/display_shutdown.c" \
    "$ROOT/src/native/shutdown/backends/$BACKEND.c" \
    "$ROOT/src/native/shutdown/transport/linux_spi_gpio.c" \
    -o "$BUILD_DIR/sonoscdplayer-shutdown-display"
"$BUILD_DIR/sonoscdplayer-shutdown-display" transition --dry-run
"$BUILD_DIR/sonoscdplayer-shutdown-display" reboot --dry-run
"$BUILD_DIR/sonoscdplayer-shutdown-display" wait --dry-run
"$BUILD_DIR/sonoscdplayer-shutdown-display" final --dry-run

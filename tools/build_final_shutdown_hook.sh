#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build and dry-run only. NEVER install or invoke systemd shutdown.
set -eu
PYTHON="${PYTHON:-python3}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/shutdown-hook}"
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
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -static \
    -I"$BUILD_DIR" -I"$ROOT/src/native/shutdown" \
    "$ROOT/src/native/shutdown/final_shutdown_hook.c" \
    "$ROOT/src/native/shutdown/backends/$BACKEND.c" \
    "$ROOT/src/native/shutdown/transport/linux_spi_gpio.c" \
    -o "$BUILD_DIR/sonoscdplayer-final-shutdown-hook"
chmod 0755 "$BUILD_DIR/sonoscdplayer-final-shutdown-hook"
for verb in poweroff reboot halt kexec; do
    "$BUILD_DIR/sonoscdplayer-final-shutdown-hook" "$verb" --dry-run
done

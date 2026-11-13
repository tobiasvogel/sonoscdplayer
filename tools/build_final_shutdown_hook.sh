#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build and dry-run only. NEVER install or invoke systemd shutdown.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/shutdown-hook}"
CC="${CC:-cc}"
# An explicit local TOML is mandatory; never reuse stale device-specific headers.
: "${DISPLAY_CONFIG:?Set DISPLAY_CONFIG to the local player TOML containing [display]}"
[ -f "$DISPLAY_CONFIG" ] || { echo "DISPLAY_CONFIG file not found: $DISPLAY_CONFIG" >&2; exit 2; }
mkdir -p "$BUILD_DIR"
python3 "$ROOT/tools/generate_ssd1327_display_header.py" \
    --config "$DISPLAY_CONFIG" \
    --output "$BUILD_DIR/ssd1327_display_config.h"
python3 "$ROOT/tools/build_shutdown_renderer.py" \
    --backend ssd1327 \
    --wait "$ROOT/src/data/shutdown_screen.png" \
    --final "$ROOT/src/data/pleasewait_screen.png" \
    --reboot "$ROOT/src/data/reboot_screen.png" \
    --transition "$ROOT/src/data/shutdown_screen.png" \
    --output "$BUILD_DIR/shutdown_frames.h"
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -static \
    -I"$BUILD_DIR" -I"$ROOT/src/shutdown" \
    "$ROOT/src/shutdown/final_shutdown_hook.c" \
    "$ROOT/src/shutdown/backends/ssd1327.c" \
    "$ROOT/src/shutdown/transport/linux_spi_gpio.c" \
    -o "$BUILD_DIR/sonoscdplayer-final-shutdown-hook"
chmod 0755 "$BUILD_DIR/sonoscdplayer-final-shutdown-hook"
for verb in poweroff reboot halt kexec; do
    "$BUILD_DIR/sonoscdplayer-final-shutdown-hook" "$verb" --dry-run
done

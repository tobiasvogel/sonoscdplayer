#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build only: never installs, stages, enables, or triggers shutdown.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/exitrd}"
CC="${CC:-cc}"
BACKEND="${BACKEND:-ssd1327}"
[ "$BACKEND" = ssd1327 ] || { echo "Unsupported backend: $BACKEND" >&2; exit 2; }
mkdir -p "$BUILD_DIR"
python3 "$ROOT/tools/build_shutdown_renderer.py" \
    --backend "$BACKEND" \
    --wait "$ROOT/src/data/shutdown_screen.png" \
    --final "$ROOT/src/data/pleasewait_screen.png" \
    --reboot "$ROOT/src/data/reboot_screen.png" \
    --transition "$ROOT/src/data/transition_screen.png" \
    --safe "$ROOT/src/data/safetounplug_screen.png" \
    --output "$BUILD_DIR/shutdown_frames.h"
"$CC" -std=gnu11 -O2 -Wall -Wextra -Werror -static \
    -I"$BUILD_DIR" -I"$ROOT/src/shutdown" \
    "$ROOT/src/shutdown/exitrd.c" \
    "$ROOT/src/shutdown/backends/ssd1327.c" \
    "$ROOT/src/shutdown/transport/linux_spi_gpio.c" \
    -o "$BUILD_DIR/shutdown"
# Enforce a non-group-writable binary regardless of the invoking user\x27s umask.
chmod 0755 "$BUILD_DIR/shutdown"
"$BUILD_DIR/shutdown" --check

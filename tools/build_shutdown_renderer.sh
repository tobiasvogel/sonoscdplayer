#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/shutdown}"
CC="${CC:-cc}"
BACKEND="${BACKEND:-ssd1327}"
if [ "$BACKEND" != ssd1327 ]; then
    echo "unsupported shutdown backend: $BACKEND" >&2
    exit 2
fi
mkdir -p "$BUILD_DIR"
if [ ! -f "$BUILD_DIR/ssd1327_display_config.h" ]; then
    echo "Missing $BUILD_DIR/ssd1327_display_config.h" >&2
    echo "Generate it with: python3 tools/generate_ssd1327_display_header.py --config YOUR_PLAYER_TOML --output $BUILD_DIR/ssd1327_display_config.h" >&2
    exit 2
fi
python3 "$ROOT/tools/build_shutdown_renderer.py" \
    --backend "$BACKEND" \
    --wait "$ROOT/src/data/shutdown_screen.png" \
    --final "$ROOT/src/data/pleasewait_screen.png" \
    --reboot "$ROOT/src/data/reboot_screen.png" \
    --transition "$ROOT/src/data/shutdown_screen.png" \
    --output "$BUILD_DIR/shutdown_frames.h"
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -I"$BUILD_DIR" \
    "$ROOT/src/shutdown/display_shutdown.c" \
    "$ROOT/src/shutdown/backends/ssd1327.c" \
    "$ROOT/src/shutdown/transport/linux_spi_gpio.c" \
    -o "$BUILD_DIR/sonoscdplayer-shutdown-display"
"$BUILD_DIR/sonoscdplayer-shutdown-display" transition --dry-run
"$BUILD_DIR/sonoscdplayer-shutdown-display" reboot --dry-run
"$BUILD_DIR/sonoscdplayer-shutdown-display" wait --dry-run
"$BUILD_DIR/sonoscdplayer-shutdown-display" final --dry-run

#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build only: never installs, stages, enables, or triggers shutdown.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DIAGNOSTIC="${EXITRD_DIAGNOSTIC:-0}"
UNVERIFIED="${EXITRD_UNVERIFIED_SAFE:-0}"
RISK_ACK="${EXITRD_UNVERIFIED_SAFE_ACK:-}"
case "$DIAGNOSTIC" in 0|1) ;; *) echo "EXITRD_DIAGNOSTIC must be 0 or 1" >&2; exit 2 ;; esac
case "$UNVERIFIED" in 0|1) ;; *) echo "EXITRD_UNVERIFIED_SAFE must be 0 or 1" >&2; exit 2 ;; esac
if [ "$UNVERIFIED" = 1 ]; then
    [ "$DIAGNOSTIC" = 0 ] || { echo "Diagnostic mode cannot be combined with unverified safe screen" >&2; exit 2; }
    [ "$RISK_ACK" = "I_ACCEPT_EARLY_SAFE_SCREEN_RISK" ] || {
        echo "WARNING: this graphic appears BEFORE final kernel poweroff and ACT LED completion." >&2
        echo "For experimental build set EXITRD_UNVERIFIED_SAFE_ACK=I_ACCEPT_EARLY_SAFE_SCREEN_RISK" >&2
        exit 2
    }
    BUILD_DIR="${BUILD_DIR:-$ROOT/build/exitrd-unverified-safe}"
    echo "WARNING: opt-in safe graphic is NOT verified. Do NOT unplug until ACT LED shutdown sequence completes." >&2
elif [ "$DIAGNOSTIC" = 1 ]; then
    BUILD_DIR="${BUILD_DIR:-$ROOT/build/exitrd-diagnostic}"
else
    BUILD_DIR="${BUILD_DIR:-$ROOT/build/exitrd}"
fi
CC="${CC:-cc}"
BACKEND="${BACKEND:-ssd1327}"
[ "$BACKEND" = ssd1327 ] || { echo "Unsupported backend: $BACKEND" >&2; exit 2; }
# The player TOML (standard location unless DISPLAY_CONFIG is set) is mandatory;
# never reuse stale device-specific headers.
DISPLAY_CONFIG="${DISPLAY_CONFIG:-${SONOSCDPLAYER_CONFIG_DIR:-/etc/sonoscdplayer}/player.toml}"
[ -f "$DISPLAY_CONFIG" ] || { echo "DISPLAY_CONFIG file not found: $DISPLAY_CONFIG" >&2; exit 2; }
mkdir -p "$BUILD_DIR"
python3 "$ROOT/tools/generate_ssd1327_display_header.py" \
    --config "$DISPLAY_CONFIG" \
    --output "$BUILD_DIR/ssd1327_display_config.h"
python3 "$ROOT/tools/build_shutdown_renderer.py" \
    --backend "$BACKEND" \
    --wait "$ROOT/src/data/shutdown_screen.png" \
    --final "$ROOT/src/data/pleasewait_screen.png" \
    --reboot "$ROOT/src/data/reboot_screen.png" \
    --transition "$ROOT/src/data/shutdown_screen.png" \
    --safe "$ROOT/src/data/safetounplug_screen.png" \
    $(if [ "$DIAGNOSTIC" = 1 ]; then printf %s '--diagnostic'; fi) \
    --output "$BUILD_DIR/shutdown_frames.h"
"$CC" -std=gnu11 -O2 -Wall -Wextra -Werror -static \
    -DSONOSCD_EXITRD_SAFE_APPROVED=0 -DSONOSCD_EXITRD_DIAGNOSTIC="$DIAGNOSTIC" \
    -DSONOSCD_EXITRD_UNVERIFIED_SAFE="$UNVERIFIED" \
    -I"$BUILD_DIR" -I"$ROOT/src/shutdown" \
    "$ROOT/src/shutdown/exitrd.c" \
    "$ROOT/src/shutdown/backends/ssd1327.c" \
    "$ROOT/src/shutdown/transport/linux_spi_gpio.c" \
    -o "$BUILD_DIR/shutdown"
# Enforce a non-group-writable binary regardless of the invoking user\x27s umask.
chmod 0755 "$BUILD_DIR/shutdown"
"$BUILD_DIR/shutdown" --check

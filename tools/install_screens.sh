#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Install the boot setup and (re)build every installed screen program.
#
# Screen texts are rendered into the programs at build time, so after an update
# of the artwork, the texts or the locale files run this script again.
#   1. Firmware boot splash (start screen): built in the current language and
#      assembled with splash_assembler.py from the Raspberry Pi utils, installed
#      as BOOT_DIR/display.bin.
#   2. config.txt: early pin states from player.toml (power LED on, connection
#      LED off, OLED RESET high) and splash_screen=, in one managed block.
#      A backup is kept; different manual settings stop the script unchanged.
#   3. Shutdown programs, only where already installed: the display program,
#      a statically built hook in /usr/lib/systemd/system-shutdown, and the
#      experimental exitrd (asks for your confirmation, as on first install).
# Never shuts down or reboots. Boot changes take effect at the next start.
#
# Usage: sh tools/install_screens.sh [--dry-run]
set -eu
PYTHON="${PYTHON:-python3}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DEST=${SHUTDOWN_SCREENS_ROOT:-}            # tests only: prefix for the system paths
BOOT_DIR="$DEST${BOOT_DIR:-/boot/firmware}"
SPLASH_NAME=display.bin
PLAYER_CONFIG="${DISPLAY_CONFIG:-${SONOSCDPLAYER_CONFIG_DIR:-/etc/sonoscdplayer}/player.toml}"
DISPLAY_PROGRAM="$DEST/usr/local/libexec/sonoscdplayer-shutdown-display"
HOOK_DIR="$DEST/usr/lib/systemd/system-shutdown"
EXITRD="$DEST/usr/local/libexec/sonoscdplayer-exitrd/shutdown"
DRY_RUN=0
case "${1:-}" in
    '') ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '3,20p' "$0"; exit 0 ;;
    *) echo "usage: sh tools/install_screens.sh [--dry-run]" >&2; exit 2 ;;
esac

run() {
    if [ "$DRY_RUN" = 1 ]; then echo "would run: $*"; else "$@"; fi
}
is_elf() {
    [ "$(head -c 4 "$1" | od -An -tx1 | tr -d ' \n')" = 7f454c46 ]
}

find_assembler() {
    for candidate in "${SPLASH_ASSEMBLER:-}" \
            "$HOME/src/utils/splashasm/splash_assembler.py" \
            "$HOME/utils/splashasm/splash_assembler.py"; do
        if [ -n "$candidate" ] && [ -f "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

[ -f "$PLAYER_CONFIG" ] || { echo "player config not found: $PLAYER_CONFIG" >&2; exit 2; }

# 1. Firmware boot splash
SPLASH_BUILD="$ROOT/build/boot-splash"
splash_arg=""
if assembler=$(find_assembler); then
    run mkdir -p "$SPLASH_BUILD"
    run "$PYTHON" "$ROOT/tools/build_display_splash.py" --display-config "$PLAYER_CONFIG" \
        -o "$SPLASH_BUILD/display.splash"
    run "$PYTHON" "$assembler" "$SPLASH_BUILD/display.splash" -o "$SPLASH_BUILD/$SPLASH_NAME"
    run sudo install -m 0644 "$SPLASH_BUILD/$SPLASH_NAME" "$BOOT_DIR/$SPLASH_NAME"
    echo "updated: $BOOT_DIR/$SPLASH_NAME"
    splash_arg="--splash $SPLASH_NAME"
elif [ -e "$BOOT_DIR/$SPLASH_NAME" ] || [ -e "$BOOT_DIR/ssd1327.bin" ]; then
    echo "Cannot refresh the installed boot splash without splash_assembler.py; refusing a stale controller program." >&2
    exit 3
else
    echo "no boot splash: splash_assembler.py not found (Raspberry Pi utils, splashasm folder)" >&2
fi

# 2. config.txt: early pin states and splash
CONFIG_TXT="$BOOT_DIR/config.txt"
if [ -f "$CONFIG_TXT" ]; then
    NEW_CONFIG="$ROOT/build/boot-config/config.txt"
    mkdir -p "$(dirname "$NEW_CONFIG")"
    # shellcheck disable=SC2086
    state=$("$PYTHON" "$ROOT/tools/boot_config.py" --player-config "$PLAYER_CONFIG" \
        --config-txt "$CONFIG_TXT" $splash_arg --output "$NEW_CONFIG") || exit 3
    if [ "$state" = changed ]; then
        if [ "$DRY_RUN" = 1 ]; then
            diff -u "$CONFIG_TXT" "$NEW_CONFIG" || true
        fi
        run sudo cp "$CONFIG_TXT" "$CONFIG_TXT.sonoscdplayer.bak"
        run sudo cp "$NEW_CONFIG" "$CONFIG_TXT"
        echo "updated: $CONFIG_TXT (backup: $CONFIG_TXT.sonoscdplayer.bak); takes effect at the next start"
    else
        echo "unchanged: $CONFIG_TXT"
    fi
else
    echo "config.txt not found in $BOOT_DIR: early pin states NOT set" >&2
fi

# 3. Shutdown programs (only where installed)
found=0
if [ -e "$DISPLAY_PROGRAM" ]; then
    found=1
    run sh "$ROOT/tools/build_shutdown_renderer.sh"
    run sudo install -m 0755 "$ROOT/build/shutdown/sonoscdplayer-shutdown-display" "$DISPLAY_PROGRAM"
    echo "updated: $DISPLAY_PROGRAM"
fi

hook_built=0
for hook in "$HOOK_DIR"/sonoscdplayer-*; do
    [ -f "$hook" ] || continue
    found=1
    if is_elf "$hook"; then
        if [ "$hook_built" = 0 ]; then
            run sh "$ROOT/tools/build_final_shutdown_hook.sh"
            hook_built=1
        fi
        run sudo install -m 0755 "$ROOT/build/shutdown-hook/sonoscdplayer-final-shutdown-hook" "$hook"
        echo "updated: $hook"
    else
        echo "unchanged: $hook (script, draws through $DISPLAY_PROGRAM)"
    fi
done

if [ -e "$EXITRD" ]; then
    found=1
    if [ "$DRY_RUN" = 1 ]; then
        echo "would rebuild the experimental exitrd (--uninstall, then --install with confirmation)"
    elif [ -t 0 ]; then
        echo "experimental exitrd installed: rebuilding it needs your confirmation"
        sh "$ROOT/tools/manage_experimental_exitrd.sh" --uninstall
        sh "$ROOT/tools/manage_experimental_exitrd.sh" --install
    else
        echo "experimental exitrd NOT updated (no terminal): run" >&2
        echo "  sh tools/manage_experimental_exitrd.sh --uninstall && sh tools/manage_experimental_exitrd.sh --install" >&2
        exit 3
    fi
fi

if [ "$found" = 0 ]; then
    echo "No installed shutdown screen programs found." >&2
fi
echo "Done. Shutdown screens follow the menu language; the boot splash uses the language at install time."

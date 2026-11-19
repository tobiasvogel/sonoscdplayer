#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Fetch an immutable version of the official Raspberry Pi splash assembler.
# Prints only the resulting path on stdout; diagnostics go to stderr.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM=https://github.com/raspberrypi/utils.git
REVISION=5e9e2da862917107d5819be4a8903d9560595b9e
DEST="$ROOT/build/raspberrypi-utils-$REVISION"
ASSEMBLER="$DEST/splashasm/splash_assembler.py"
command -v git >/dev/null || { echo 'git is required for splash assembler setup' >&2; exit 1; }
if [[ -e "$DEST" || -L "$DEST" ]]; then
  [[ -d "$DEST/.git" ]] || { echo "Unexpected pre-existing path: $DEST" >&2; exit 1; }
  [[ "$(git -C "$DEST" rev-parse HEAD)" == "$REVISION" ]] || {
    echo "Wrong Raspberry Pi utils revision in $DEST" >&2; exit 1;
  }
else
  mkdir -p -- "$ROOT/build"
  temp="$(mktemp -d "$ROOT/build/.raspberrypi-utils.XXXXXXXX")"
  trap 'rm -rf -- "$temp"' EXIT
  git -C "$temp" init -q
  git -C "$temp" remote add origin "$UPSTREAM"
  git -C "$temp" fetch -q --depth 1 origin "$REVISION"
  git -C "$temp" checkout -q --detach FETCH_HEAD
  [[ "$(git -C "$temp" rev-parse HEAD)" == "$REVISION" ]] || {
    echo 'Unexpected fetched revision' >&2; exit 1;
  }
  mv -- "$temp" "$DEST"
  trap - EXIT
fi
[[ -f "$ASSEMBLER" ]] || { echo "Missing upstream splash assembler: $ASSEMBLER" >&2; exit 1; }
printf '%s\n' "$ASSEMBLER"

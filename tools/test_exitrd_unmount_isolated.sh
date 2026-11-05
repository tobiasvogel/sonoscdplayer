#!/bin/bash
# Test ONLY: compile the existing exitrd unmount routine into a dedicated harness.
# Isolated mount namespace + chroot; never stages /run/initramfs/shutdown.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HARNESS="$ROOT/build/exitrd/unmount-harness"

if [[ "${1:-}" != "--inside" ]]; then
    if [[ "$(id -u)" == 0 ]]; then
        echo "Run the outer build as an ordinary user, not root" >&2
        exit 2
    fi
    test -f "$ROOT/build/exitrd/shutdown_frames.h" || {
        echo "Build exitrd frames with tools/build_exitrd.sh first" >&2
        exit 2
    }
    cc -std=gnu11 -O2 -Wall -Wextra -Werror -static \
       -I"$ROOT/build/exitrd" -I"$ROOT/src/shutdown" \
       "$ROOT/tests/exitrd_unmount_harness.c" \
       "$ROOT/src/shutdown/backends/ssd1327.c" \
       "$ROOT/src/shutdown/transport/linux_spi_gpio.c" \
       -o "$HARNESS"
    chmod 0755 "$HARNESS"
    echo "=== RUN ISOLATED HARNESS ==="
    exec sudo unshare --mount --propagation private \
        /bin/bash "$ROOT/tools/test_exitrd_unmount_isolated.sh" --inside
fi

[[ "$(id -u)" == 0 ]] || { echo "Root required inside namespace" >&2; exit 2; }
test -x "$HARNESS"
# A different mount namespace than host PID1 is mandatory.
[[ "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)" ]] || {
    echo "ABORT: no private mount namespace" >&2
    exit 2
}
BASE="$(mktemp -d /tmp/sonoscd-exitrd-unmount.XXXXXX)"
ROOTFS="$BASE/root"
cleanup() {
    umount "$ROOTFS/oldroot/run/cache" 2>/dev/null || true
    umount "$ROOTFS/oldroot/run" 2>/dev/null || true
    umount "$ROOTFS/oldroot" 2>/dev/null || true
    umount "$ROOTFS/proc" 2>/dev/null || true
    umount "$ROOTFS" 2>/dev/null || true
    [[ "$BASE" == /tmp/sonoscd-exitrd-unmount.* ]] && rm -rf -- "$BASE"
}
trap cleanup EXIT
mkdir -p "$ROOTFS"
mount -t tmpfs -o size=8m,mode=0700 tmpfs "$ROOTFS"
mkdir -p "$ROOTFS/proc" "$ROOTFS/oldroot"
touch "$ROOTFS/exitrd-test-fixture"
install -m 0700 "$HARNESS" "$ROOTFS/unmount-harness"
mount -t tmpfs -o size=4m tmpfs "$ROOTFS/oldroot"
touch "$ROOTFS/oldroot/exitrd-test-fixture"
mkdir -p "$ROOTFS/oldroot/run"
mount -t tmpfs -o size=2m tmpfs "$ROOTFS/oldroot/run"
mkdir -p "$ROOTFS/oldroot/run/cache"
mount -t tmpfs -o size=1m tmpfs "$ROOTFS/oldroot/run/cache"
mount -t proc -o nosuid,nodev,noexec proc "$ROOTFS/proc"
echo "=== FIXTURE MOUNTS ==="
findmnt -R "$ROOTFS" -o TARGET,FSTYPE
echo "=== EXECUTE PRODUCTION release_oldroot() IN CHROOT ==="
chroot "$ROOTFS" /unmount-harness
echo "=== ISOLATED C UNMOUNT TEST PASSED ==="

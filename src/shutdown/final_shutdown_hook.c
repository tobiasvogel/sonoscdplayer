/* SPDX-License-Identifier: GPL-2.0-only */
/* Experimental, standalone systemd-shutdown hook. Build-only, never installed
 * automatically. The final phase is NOT a safe-to-unplug declaration. */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "display_backend.h"
#include "shutdown_frames.h"

static const char *phase_for_action(const char *verb, int exitrd_present) {
    if (strcmp(verb, "reboot") == 0)
        return "reboot";
    if (strcmp(verb, "poweroff") == 0 && !exitrd_present)
        return "final";
    /* halt, kexec and unsupported actions must not claim poweroff. */
    return NULL;
}

int main(int argc, char **argv) {
    if ((argc != 2 && argc != 3) ||
        (argc == 3 && strcmp(argv[2], "--dry-run") != 0)) {
        fprintf(stderr, "usage: %s poweroff|reboot|halt|kexec [--dry-run]\n", argv[0]);
        return 2;
    }
    /* An executable exitrd takes ownership of the late poweroff frame.
     * As in the legacy shell hook, reboot remains this hook's responsibility.
     * No real exitrd is written or executed here. */
    int exitrd_present = access("/run/initramfs/shutdown", X_OK) == 0;
    const char *phase = phase_for_action(argv[1], exitrd_present);
    if (argc == 3) {
        printf("action=%s phase=%s exitrd_present=%d backend=%s safe-approved=0\n",
               argv[1], phase ? phase : "none",
               exitrd_present, shutdown_display_backend.name);
        return 0;
    }
    if (!phase)
        return 0;
    const uint8_t *frame = strcmp(phase, "reboot") == 0
        ? shutdown_frame_reboot : shutdown_frame_final;
    size_t size = sizeof(shutdown_frame_final);
    if (sizeof(shutdown_frame_reboot) != size ||
        shutdown_display_backend.width != SHUTDOWN_FRAME_WIDTH ||
        shutdown_display_backend.height != SHUTDOWN_FRAME_HEIGHT ||
        shutdown_display_backend.frame_bytes != size ||
        strcmp(shutdown_display_backend.pixel_format, SHUTDOWN_FRAME_FORMAT) != 0)
        return 1;
    /* Best effort: shutdown must proceed even when SPI/GPIO are unavailable. */
    if (shutdown_display_backend.show(frame, size) != 0) {
        fputs("shutdown-hook: OLED write unavailable\n", stderr);
    }
    return 0;
}

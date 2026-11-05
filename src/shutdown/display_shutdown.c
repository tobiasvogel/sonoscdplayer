/* SPDX-License-Identifier: GPL-2.0-only */
/* Late-shutdown frame selection. No disk or network access at runtime. */
#include <stdio.h>
#include <string.h>
#include "display_backend.h"
#include "shutdown_frames.h" /* generated at build time from source PNGs */

static int frame_for_phase(const char *phase, const uint8_t **frame, size_t *size) {
    if (strcmp(phase, "transition") == 0) {
        *frame = shutdown_frame_transition;
        *size = sizeof(shutdown_frame_transition);
        return 0;
    }
    if (strcmp(phase, "reboot") == 0) {
        *frame = shutdown_frame_reboot;
        *size = sizeof(shutdown_frame_reboot);
        return 0;
    }
    if (strcmp(phase, "wait") == 0) {
        *frame = shutdown_frame_wait;
        *size = sizeof(shutdown_frame_wait);
        return 0;
    }
    if (strcmp(phase, "final") == 0) {
        *frame = shutdown_frame_final;
        *size = sizeof(shutdown_frame_final);
        return 0;
    }
    return -1;
}

int main(int argc, char **argv) {
    const uint8_t *frame = NULL;
    size_t size = 0;
    if ((argc != 2 && argc != 3) || frame_for_phase(argv[1], &frame, &size) != 0 ||
        (argc == 3 && strcmp(argv[2], "--dry-run") != 0)) {
        fprintf(stderr, "usage: %s transition|wait|final|reboot [--dry-run]\n", argv[0]);
        return 2;
    }
    const display_backend_t *backend = &shutdown_display_backend;
    if (size != backend->frame_bytes ||
        SHUTDOWN_FRAME_WIDTH != backend->width ||
        SHUTDOWN_FRAME_HEIGHT != backend->height ||
        strcmp(SHUTDOWN_FRAME_FORMAT, backend->pixel_format) != 0) {
        fprintf(stderr, "shutdown-display: frame/backend mismatch\n");
        return 1;
    }
    if (argc == 3) {
        printf("phase=%s backend=%s %ux%u format=%s size=%zu\n", argv[1],
               backend->name, backend->width, backend->height,
               backend->pixel_format, size);
        return 0;
    }
    if (backend->show(frame, size) != 0) {
        fprintf(stderr, "shutdown-display: backend write failed\n");
        return 1;
    }
    return 0;
}

/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef SONOSCDPLAYER_DISPLAY_BACKEND_H
#define SONOSCDPLAYER_DISPLAY_BACKEND_H
#include <stddef.h>
#include <stdint.h>

/* Generated frames have the dimensions and pixel format of the selected backend. */
typedef struct {
    const char *name;
    unsigned width;
    unsigned height;
    size_t frame_bytes;
    const char *pixel_format;
    int (*show)(const uint8_t *frame, size_t length);
} display_backend_t;

extern const display_backend_t shutdown_display_backend;
#endif

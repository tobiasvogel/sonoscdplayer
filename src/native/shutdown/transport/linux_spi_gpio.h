/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef SONOSCDPLAYER_LINUX_SPI_GPIO_H
#define SONOSCDPLAYER_LINUX_SPI_GPIO_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
    int chip;
    int line;
    int spi;
    uint32_t speed_hz;
} display_io_t;

/* GPIO-v2 DC line + Linux spidev; no display reset control. */
int display_io_open(display_io_t *io, const char *gpiochip, unsigned dc_offset,
                    const char *spidev, uint32_t speed_hz);
int display_io_write(display_io_t *io, int data_mode, const uint8_t *buf, size_t size);
void display_io_close(display_io_t *io);
#endif

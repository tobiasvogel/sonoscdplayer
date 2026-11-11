/* SPDX-License-Identifier: GPL-2.0-only */
/* SSD1327 128x128 SPI0/CE0 handover: no reset and no DISPLAY OFF. */
#include "../display_backend.h"
#include "../transport/linux_spi_gpio.h"
#include <stdint.h>
#include <stdio.h>

#ifndef SONOSCDPLAYER_OLED_DC_PIN
#define SONOSCDPLAYER_OLED_DC_PIN 25u
#endif

#define SSD1327_WIDTH 128u
#define SSD1327_HEIGHT 128u
#define SSD1327_FRAME_BYTES (SSD1327_WIDTH * SSD1327_HEIGHT / 2u)

static int ssd1327_show(const uint8_t *frame, size_t length) {
    if (frame == NULL || length != SSD1327_FRAME_BYTES) return -1;
    display_io_t io;
    if (display_io_open(&io, "/dev/gpiochip0", SONOSCDPLAYER_OLED_DC_PIN, "/dev/spidev0.0", 2000000u) != 0) {
        perror("shutdown-display: opening SSD1327 transport");
        return -1;
    }
    const uint8_t columns[] = {0x15, 0, 63};
    const uint8_t rows[] = {0x75, 0, 127};
    const uint8_t write_ram[] = {0x5c};
    int status = 0;
    if (display_io_write(&io, 0, columns, sizeof(columns)) != 0 ||
        display_io_write(&io, 0, rows, sizeof(rows)) != 0 ||
        display_io_write(&io, 0, write_ram, sizeof(write_ram)) != 0 ||
        display_io_write(&io, 1, frame, length) != 0) {
        status = -1;
        perror("shutdown-display: SSD1327 transfer");
    }
    display_io_close(&io);
    return status;
}

const display_backend_t shutdown_display_backend = {
    .name = "ssd1327", .width = SSD1327_WIDTH, .height = SSD1327_HEIGHT,
    .frame_bytes = SSD1327_FRAME_BYTES, .pixel_format = "gray4-packed",
    .show = ssd1327_show,
};

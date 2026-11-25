/* SPDX-License-Identifier: GPL-2.0-only */
/* SSD1351 handover: preserve initialization/RAM; no reset or display-off. */
#include "../display_backend.h"
#include "../transport/linux_spi_gpio.h"
#include "display_config.h"
#include <stdio.h>

static int ssd1351_show(const uint8_t *frame, size_t length) {
    if (frame == NULL || length != 32768u) return -1;
    display_io_t io;
    if (display_io_open(&io, "/dev/gpiochip0", SONOSCDPLAYER_OLED_DC_PIN,
                        "/dev/spidev0.0", SONOSCDPLAYER_DISPLAY_SPI_HZ) != 0)
        return -1;
    const uint8_t columns = 0x15, rows = 0x75, ram = 0x5c, on = 0xaf;
    const uint8_t limits[] = {0, 127};
    int result = 0;
    /* Unlike SSD1327, address parameters use DC HIGH. RGB565 is high byte first. */
    if (display_io_write(&io, 0, &columns, 1) != 0 ||
        display_io_write(&io, 1, limits, sizeof(limits)) != 0 ||
        display_io_write(&io, 0, &rows, 1) != 0 ||
        display_io_write(&io, 1, limits, sizeof(limits)) != 0 ||
        display_io_write(&io, 0, &ram, 1) != 0 ||
        display_io_write(&io, 1, frame, length) != 0 ||
        display_io_write(&io, 0, &on, 1) != 0) {
        result = -1;
        perror("shutdown-display: SSD1351 transfer");
    }
    display_io_close(&io);
    return result;
}

const display_backend_t shutdown_display_backend = {
    .name = "ssd1351", .width = 128u, .height = 128u,
    .frame_bytes = 32768u, .pixel_format = "rgb565", .show = ssd1351_show,
};

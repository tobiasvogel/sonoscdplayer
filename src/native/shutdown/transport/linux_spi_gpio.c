/* SPDX-License-Identifier: GPL-2.0-only */
#define _POSIX_C_SOURCE 200809L
#include "linux_spi_gpio.h"
#include <fcntl.h>
#include <linux/gpio.h>
#include <linux/spi/spidev.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <unistd.h>

void display_io_close(display_io_t *io) {
    if (io->spi >= 0) close(io->spi);
    if (io->line >= 0) close(io->line);
    if (io->chip >= 0) close(io->chip);
    io->spi = io->line = io->chip = -1;
}

int display_io_open(display_io_t *io, const char *gpiochip, unsigned dc_offset,
                    const char *spidev, uint32_t speed_hz) {
    *io = (display_io_t){.chip = -1, .line = -1, .spi = -1, .speed_hz = speed_hz};
    struct gpio_v2_line_request req = {0};
    io->chip = open(gpiochip, O_RDONLY | O_CLOEXEC);
    if (io->chip < 0) goto fail;
    req.offsets[0] = dc_offset;
    req.num_lines = 1;
    req.config.flags = GPIO_V2_LINE_FLAG_OUTPUT;
    req.config.num_attrs = 1;
    req.config.attrs[0].attr.id = GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES;
    req.config.attrs[0].attr.values = 0;
    req.config.attrs[0].mask = 1;
    snprintf(req.consumer, sizeof(req.consumer), "sonoscd-shutdown");
    if (ioctl(io->chip, GPIO_V2_GET_LINE_IOCTL, &req) < 0) goto fail;
    io->line = req.fd;
    io->spi = open(spidev, O_RDWR | O_CLOEXEC);
    if (io->spi < 0) goto fail;
    uint8_t mode = SPI_MODE_0, bits = 8;
    if (ioctl(io->spi, SPI_IOC_WR_MODE, &mode) < 0 ||
        ioctl(io->spi, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
        ioctl(io->spi, SPI_IOC_WR_MAX_SPEED_HZ, &speed_hz) < 0) goto fail;
    return 0;
fail:
    display_io_close(io);
    return -1;
}

int display_io_write(display_io_t *io, int data_mode, const uint8_t *buf, size_t size) {
    struct gpio_v2_line_values values = {
        .bits = data_mode ? 1u : 0u,
        .mask = 1u,
    };
    if (ioctl(io->line, GPIO_V2_LINE_SET_VALUES_IOCTL, &values) < 0) return -1;
    /* Bounded transfers match the initial PoC's conservative 256-byte chunks. */
    for (size_t offset = 0; offset < size;) {
        size_t n = size - offset;
        if (n > 256) n = 256;
        struct spi_ioc_transfer tr = {0};
        tr.tx_buf = (uintptr_t)(buf + offset);
        tr.len = (uint32_t)n;
        tr.speed_hz = io->speed_hz;
        tr.bits_per_word = 8;
        if (ioctl(io->spi, SPI_IOC_MESSAGE(1), &tr) < 0) return -1;
        offset += n;
    }
    return 0;
}

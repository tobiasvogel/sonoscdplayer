
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <string.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>

#define I2C_DEVICE "/dev/i2c-1"
#define APDS_ADDR  0x52

#define MAIN_CTRL     0x00
#define ALS_MEAS_RATE 0x04
#define ALS_GAIN      0x05
#define PART_ID       0x06
#define MAIN_STATUS   0x07
#define ALS_DATA_0    0x0D

static int write_reg(int fd, uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return write(fd, buf, 2) == 2 ? 0 : -1;
}

static int read_regs(int fd, uint8_t reg,
                     uint8_t *buf, size_t len)
{
    if (write(fd, &reg, 1) != 1)
        return -1;

    return read(fd, buf, len) == (ssize_t)len ? 0 : -1;
}

static void sleep_ms(long ms)
{
    struct timespec ts = {
        .tv_sec = ms / 1000,
        .tv_nsec = (ms % 1000) * 1000000L
    };

    while (nanosleep(&ts, &ts) != 0)
        ;
}

int main(int argc, char **argv)
{
    // Standardeinstellung: 25 ms
    int integration_ms = 25;

    if (argc > 2) {
        fprintf(stderr, "Verwendung: %s [3|25|50|100|200|400]\n",
                argv[0]);
        return 1;
    }

    if (argc == 2) {
        char *end;
        long value = strtol(argv[1], &end, 10);

        if (*end != '\0') {
            fprintf(stderr, "Ungueltige Integrationszeit\n");
            return 1;
        }
        integration_ms = (int)value;
    }

    uint8_t resolution;
    double actual_ms;

    switch (integration_ms) {
        case 3:
            resolution = 0x50;
            actual_ms = 3.125;
            break;
        case 25:
            resolution = 0x40;
            actual_ms = 25.0;
            break;
        case 50:
            resolution = 0x30;
            actual_ms = 50.0;
            break;
        case 100:
            resolution = 0x20;
            actual_ms = 100.0;
            break;
        case 200:
            resolution = 0x10;
            actual_ms = 200.0;
            break;
        case 400:
            resolution = 0x00;
            actual_ms = 400.0;
            break;
        default:
            fprintf(stderr,
                    "Erlaubt: 3, 25, 50, 100, 200, 400 ms\n");
            return 1;
    }

    int fd = open(I2C_DEVICE, O_RDWR);
    if (fd < 0) {
        perror("open i2c");
        return 1;
    }

    if (ioctl(fd, I2C_SLAVE, APDS_ADDR) < 0) {
        perror("I2C_SLAVE");
        close(fd);
        return 1;
    }

    uint8_t id;
    if (read_regs(fd, PART_ID, &id, 1) < 0 ||
        (id != 0xB3 && id != 0xB1)) {
        fprintf(stderr, "APDS9306 nicht gefunden\n");
        close(fd);
        return 1;
    }

    // Messung anhalten und konfigurieren
    // Gain 3, Wiederholrate 1000 ms
    if (write_reg(fd, MAIN_CTRL, 0x00) < 0 ||
        write_reg(fd, ALS_MEAS_RATE, resolution | 0x05) < 0 ||
        write_reg(fd, ALS_GAIN, 0x01) < 0 ||
        write_reg(fd, MAIN_CTRL, 0x02) < 0) {
        perror("Sensor konfigurieren");
        close(fd);
        return 1;
    }

    // Abwarten, bis die erste Messung abgeschlossen ist.
    // Aufgerundet und mit etwas Reserve.
    long wait_ms = (long)(actual_ms + 2.0);
    sleep_ms(wait_ms);

    uint8_t status = 0;
    int ready = 0;

    // Data-Ready mit Timeout abfragen
    for (int i = 0; i < 50; i++) {
        if (read_regs(fd, MAIN_STATUS, &status, 1) < 0) {
            perror("Status lesen");
            goto error;
        }

        if (status & 0x08) {
            ready = 1;
            break;
        }

        sleep_ms(2);
    }

    if (!ready) {
        fprintf(stderr, "Timeout: keine Messdaten\n");
        goto error;
    }

    uint8_t data[3];
    if (read_regs(fd, ALS_DATA_0, data, 3) < 0) {
        perror("Messdaten lesen");
        goto error;
    }

    uint32_t raw = (uint32_t)data[0]
                 | ((uint32_t)data[1] << 8)
                 | ((uint32_t)data[2] << 16);

    // Lux-Faktor für 100 ms und Gain 3
    // Unterschiedliche Empfindlichkeit der Sensorvarianten
    double base_factor = (id == 0xB3)
                       ? 0.146845
                       : 0.170067;

    // Umrechnung auf die gewählte Integrationszeit
    double lux = raw * base_factor * (100.0 / actual_ms);

    printf("%.2f\n", lux);

    // Sensor in Standby versetzen
    write_reg(fd, MAIN_CTRL, 0x00);
    close(fd);
    return 0;

error:
    write_reg(fd, MAIN_CTRL, 0x00);
    close(fd);
    return 1;
}


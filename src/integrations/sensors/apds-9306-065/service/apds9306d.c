#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "apds9306_logic.h"

#define I2C_DEV "/dev/i2c-1"
#define ADDRESS 0x52
#define SOCKET_PATH "/run/apds9306.sock"
#define MAIN_CTRL 0x00
#define MEAS_RATE 0x04
#define ALS_GAIN 0x05
#define PART_ID 0x06
#define STATUS 0x07
#define ALS_DATA 0x0d
#define STATUS_DATA 0x08      /* new ALS data */
#define STATUS_POWER_ON 0x20  /* sensor was reset: configuration lost */
#define RETRY_MS 1000

static volatile sig_atomic_t running = 1;
static void stop(int sig) { (void)sig; running = 0; }

static int wr(int fd, uint8_t reg, uint8_t value) {
    uint8_t b[2] = {reg, value};
    return write(fd, b, sizeof b) == (ssize_t)sizeof b ? 0 : -1;
}
static int rd(int fd, uint8_t reg, uint8_t *out, size_t size) {
    return write(fd, &reg, 1) == 1 && read(fd, out, size) == (ssize_t)size ? 0 : -1;
}
static int integration(int ms, uint8_t *config, double *actual) {
    const struct { int arg; uint8_t bits; double duration; uint8_t period; } options[] = {
        {3, 0x50, 3.125, 0x00}, {25, 0x40, 25, 0x00},
        {50, 0x30, 50, 0x01}, {100, 0x20, 100, 0x02},
        {200, 0x10, 200, 0x03}, {400, 0x00, 400, 0x04}
    };
    for (size_t i = 0; i < sizeof options / sizeof options[0]; ++i) {
        if (options[i].arg == ms) {
            *config = options[i].bits | options[i].period;
            *actual = options[i].duration;
            return 0;
        }
    }
    return -1;
}
static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)(ts.tv_nsec / 1000000);
}

/* Open the bus, check the part and configure it. Returns the fd or -1. */
static int sensor_open(uint8_t cfg, uint8_t *id) {
    int fd = open(I2C_DEV, O_RDWR | O_CLOEXEC);
    if (fd < 0 || ioctl(fd, I2C_SLAVE, ADDRESS) < 0) {
        if (fd >= 0) close(fd);
        return -1;
    }
    *id = 0;
    uint8_t status;
    /* Reading the status clears the power-on flag of a fresh start. */
    if (rd(fd, PART_ID, id, 1) || (*id != 0xb1 && *id != 0xb3) || rd(fd, STATUS, &status, 1) ||
        wr(fd, MAIN_CTRL, 0x00) || wr(fd, MEAS_RATE, cfg) ||
        wr(fd, ALS_GAIN, 0x01) || wr(fd, MAIN_CTRL, 0x02)) {
        close(fd);
        return -1;
    }
    return fd;
}

int main(int argc, char **argv) {
    int ms = 25;
    if (argc > 2) {
        fprintf(stderr, "Aufruf: %s [3|25|50|100|200|400]\n", argv[0]);
        return 1;
    }
    if (argc == 2) {
        char *end;
        errno = 0;
        long n = strtol(argv[1], &end, 10);
        if (errno || *end || n < 0 || n > 400) {
            fprintf(stderr, "Ungueltige Integrationszeit\n"); return 1;
        }
        ms = (int)n;
    }
    uint8_t cfg;
    double actual;
    if (integration(ms, &cfg, &actual)) {
        fprintf(stderr, "Erlaubte Zeiten: 3, 25, 50, 100, 200, 400 ms\n");
        return 1;
    }
    signal(SIGINT, stop);
    signal(SIGTERM, stop);
    signal(SIGPIPE, SIG_IGN);

    int server = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (server < 0) { perror("socket"); return 1; }
    struct sockaddr_un addr = { .sun_family = AF_UNIX };
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", SOCKET_PATH);
    // Dieser Dienst sollte exklusiv die Socket-Datei verwalten.
    unlink(SOCKET_PATH);
    if (bind(server, (struct sockaddr *)&addr, sizeof addr) || listen(server, 16)) {
        perror("bind/listen"); close(server); return 1;
    }
    // Clientzugriff auch ohne Root; nur auf lokalem Raspberry Pi.
    // Zugriffsrechte werden vorzugsweise ueber systemd UMask=0002 kontrolliert.

    const uint64_t stale_ms = apds_stale_limit_ms(actual);
    const int wait_ms = ms / 2 > 5 ? ms / 2 : 5;   /* status polls per period */
    apds_state state = { .i2c_error = 1 };
    uint8_t id = 0;
    int i2c = -1;
    uint64_t next_open = 0;
    int reported = 0;                              /* 1 ok, -1 error: log changes only */
    struct pollfd pfd = {.fd = server, .events = POLLIN};
    while (running) {
        uint64_t now = now_ms();
        if (i2c < 0 && now >= next_open) {
            i2c = sensor_open(cfg, &id);
            next_open = now + RETRY_MS;
            if (i2c >= 0) {
                state.i2c_error = 0;
                state.valid = 0;                   /* no stale value after a restart */
                fprintf(stderr, "APDS9306: ID=0x%02x, Integration=%.3f ms, Socket=%s\n",
                        id, actual, SOCKET_PATH);
                reported = 1;
            } else if (reported != -1) {
                fprintf(stderr, "APDS9306 nicht erreichbar (0x52); neuer Versuch jede Sekunde\n");
                reported = -1;
            }
        }
        if (i2c >= 0) {
            uint8_t status = 0;
            uint8_t data[3];
            if (rd(i2c, STATUS, &status, 1)) {
                perror("Sensorstatus");
                state.i2c_error = 1;
            } else if (status & STATUS_POWER_ON) {
                /* Reset (e.g. supply dip): it no longer measures. Reconfigure. */
                fprintf(stderr, "APDS9306 wurde zurueckgesetzt; neu konfigurieren\n");
                state.i2c_error = 1;
            } else if ((status & STATUS_DATA) && rd(i2c, ALS_DATA, data, sizeof data)) {
                perror("Sensordaten");
                state.i2c_error = 1;
            } else if (!(status & STATUS_DATA) && state.valid && now > state.sample_ms
                       && now - state.sample_ms > stale_ms) {
                /* Answers, but measures no more (e.g. switched off): start it again. */
                fprintf(stderr, "APDS9306 liefert keine neuen Messwerte; neu konfigurieren\n");
                state.i2c_error = 1;
            } else if (status & STATUS_DATA) {
                uint32_t raw = (uint32_t)data[0] | ((uint32_t)data[1] << 8) | ((uint32_t)data[2] << 16);
                double base = id == 0xb3 ? 0.146845 : 0.170067;
                state.lux = raw * base * (100.0 / actual);
                state.sample_ms = now;
                state.valid = 1;
            }
            if (state.i2c_error) {
                close(i2c);
                i2c = -1;
                next_open = now;                   /* reconfigure right away, then every second */
                reported = 0;
            }
        }
        // Zwischen Sensor-Polls bleiben Clients ansprechbar.
        int rc = poll(&pfd, 1, wait_ms);
        if (rc < 0) { if (errno == EINTR) continue; perror("poll"); break; }
        if (rc > 0 && (pfd.revents & POLLIN)) {
            int client = accept4(server, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
            if (client >= 0) {
                char out[64];
                int n = apds_reply(&state, now_ms(), stale_ms, out, sizeof out);
                send(client, out, (size_t)n, MSG_NOSIGNAL);
                close(client);
            } else if (errno != EINTR && errno != EAGAIN) {
                perror("accept");
            }
        }
    }
    close(server);
    unlink(SOCKET_PATH);
    if (i2c >= 0) {
        wr(i2c, MAIN_CTRL, 0x00);
        close(i2c);
    }
    return running ? 1 : 0;
}

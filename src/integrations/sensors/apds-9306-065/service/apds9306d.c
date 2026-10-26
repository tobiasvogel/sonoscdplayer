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

#define I2C_DEV "/dev/i2c-1"
#define ADDRESS 0x52
#define SOCKET_PATH "/run/apds9306.sock"
#define MAIN_CTRL 0x00
#define MEAS_RATE 0x04
#define ALS_GAIN 0x05
#define PART_ID 0x06
#define STATUS 0x07
#define ALS_DATA 0x0d

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

    int i2c = open(I2C_DEV, O_RDWR);
    if (i2c < 0 || ioctl(i2c, I2C_SLAVE, ADDRESS) < 0) {
        perror("I2C oeffnen"); if (i2c >= 0) close(i2c); return 1;
    }
    uint8_t id = 0;
    if (rd(i2c, PART_ID, &id, 1) || (id != 0xb1 && id != 0xb3)) {
        fprintf(stderr, "Kein APDS9306 unter 0x52 (ID: 0x%02x)\n", id);
        close(i2c); return 1;
    }
    if (wr(i2c, MAIN_CTRL, 0x00) || wr(i2c, MEAS_RATE, cfg) ||
        wr(i2c, ALS_GAIN, 0x01) || wr(i2c, MAIN_CTRL, 0x02)) {
        perror("Sensor konfigurieren"); close(i2c); return 1;
    }

    int server = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (server < 0) { perror("socket"); goto fail_sensor; }
    struct sockaddr_un addr = { .sun_family = AF_UNIX };
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", SOCKET_PATH);
    // Dieser Dienst sollte exklusiv die Socket-Datei verwalten.
    unlink(SOCKET_PATH);
    if (bind(server, (struct sockaddr *)&addr, sizeof addr) || listen(server, 16)) {
        perror("bind/listen"); close(server); goto fail_sensor;
    }
    // Clientzugriff auch ohne Root; nur auf lokalem Raspberry Pi.
    // Zugriffsrechte werden vorzugsweise ueber systemd UMask=0002 kontrolliert.
    fprintf(stderr, "APDS9306: ID=0x%02x, Integration=%.3f ms, Socket=%s\n", id, actual, SOCKET_PATH);
    double latest = 0;
    int valid = 0;
    struct pollfd pfd = {.fd = server, .events = POLLIN};
    while (running) {
        uint8_t status = 0;
        if (rd(i2c, STATUS, &status, 1)) {
            perror("Sensorstatus"); break;
        }
        if (status & 0x08) {
            uint8_t data[3];
            if (rd(i2c, ALS_DATA, data, sizeof data)) {
                perror("Sensordaten"); break;
            }
            uint32_t raw = (uint32_t)data[0] | ((uint32_t)data[1] << 8) | ((uint32_t)data[2] << 16);
            double base = id == 0xb3 ? 0.146845 : 0.170067;
            latest = raw * base * (100.0 / actual);
            valid = 1;
        }
        // Zwischen Sensor-Polls bleiben Clients ansprechbar.
        int rc = poll(&pfd, 1, 5);
        if (rc < 0) { if (errno == EINTR) continue; perror("poll"); break; }
        if (rc > 0 && (pfd.revents & POLLIN)) {
            int client = accept4(server, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
            if (client >= 0) {
                char out[64];
                int n = valid ? snprintf(out, sizeof out, "%.2f\n", latest)
                              : snprintf(out, sizeof out, "ERR not-ready\n");
                send(client, out, (size_t)n, MSG_NOSIGNAL);
                close(client);
            } else if (errno != EINTR && errno != EAGAIN) {
                perror("accept");
            }
        }
    }
    close(server);
    unlink(SOCKET_PATH);
fail_sensor:
    wr(i2c, MAIN_CTRL, 0x00);
    close(i2c);
    return running ? 1 : 0;
}

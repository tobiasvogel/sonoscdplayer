#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#define SOCKET_PATH "/run/apds9306.sock"
int main(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return 1; }
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", SOCKET_PATH);
    if (connect(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        perror("apds9306d nicht erreichbar"); close(fd); return 1;
    }
    char b[64];
    ssize_t n = read(fd, b, sizeof b - 1);
    close(fd);
    if (n <= 0) { fprintf(stderr, "Keine Antwort vom Dienst\n"); return 1; }
    b[n] = '\0';
    if (strncmp(b, "ERR", 3) == 0) { fprintf(stderr, "%s", b); return 1; }
    fputs(b, stdout);
    return 0;
}

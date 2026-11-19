/* SPDX-License-Identifier: GPL-2.0-only */
/* Pick the shutdown frame set for the language chosen in the display menu.
 *
 * The OLED service writes {"version":1,"language":"xx"} to ui.json in the
 * state directory. Only that one value is read here, read-only and bounded;
 * a missing or unknown language falls back to SHUTDOWN_DEFAULT_LANGUAGE
 * (ui.toml at build time). Include after "shutdown_frames.h". */
#ifndef SONOSCDPLAYER_FRAME_LANGUAGE_H
#define SONOSCDPLAYER_FRAME_LANGUAGE_H
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SHUTDOWN_STATE_DIR "/var/lib/sonoscdplayer"

static inline const struct shutdown_frame_set *shutdown_frames_find(const char *language) {
    for (size_t i = 0; i < SHUTDOWN_FRAME_SET_COUNT; i++)
        if (strcmp(shutdown_frame_sets[i].language, language) == 0)
            return &shutdown_frame_sets[i];
    return NULL;
}

/* Copies the "language" string value of ui.json into out; 0 on success. */
static inline int shutdown_read_language(const char *state_dir, char *out, size_t out_size) {
    char path[256];
    char buf[512];
    if (snprintf(path, sizeof(path), "%s/ui.json", state_dir) >= (int)sizeof(path)) return -1;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return -1;
    buf[n] = '\0';
    const char *p = strstr(buf, "\"language\"");
    if (p == NULL) return -1;
    p += strlen("\"language\"");
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
    if (*p++ != ':') return -1;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
    if (*p++ != '"') return -1;
    size_t len = 0;
    while (p[len] != '"' && p[len] != '\0') {
        char c = p[len];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-'))
            return -1;
        if (++len >= out_size) return -1;
    }
    if (p[len] != '"' || len == 0) return -1;
    memcpy(out, p, len);
    out[len] = '\0';
    return 0;
}

static inline const struct shutdown_frame_set *shutdown_frames_select(const char *state_dir) {
    char language[32];
    const struct shutdown_frame_set *set = NULL;
    if (state_dir != NULL && shutdown_read_language(state_dir, language, sizeof(language)) == 0)
        set = shutdown_frames_find(language);
    if (set == NULL) set = shutdown_frames_find(SHUTDOWN_DEFAULT_LANGUAGE);
    return set != NULL ? set : &shutdown_frame_sets[0];
}

/* Programs running on the normal root honour SONOSCDPLAYER_STATE_DIR (tests). */
static inline const char *shutdown_state_dir(void) {
    const char *dir = getenv("SONOSCDPLAYER_STATE_DIR");
    return (dir != NULL && dir[0] != '\0') ? dir : SHUTDOWN_STATE_DIR;
}
#endif

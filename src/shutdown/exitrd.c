/* SPDX-License-Identifier: GPL-2.0-only */
/* Experimental exitrd PID 1. Never installed/staged automatically. */
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <linux/reboot.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "display_backend.h"
#include "shutdown_frames.h"

#ifndef SONOSCD_EXITRD_SAFE_APPROVED
#define SONOSCD_EXITRD_SAFE_APPROVED 0
#endif
#if SONOSCD_EXITRD_SAFE_APPROVED != 0 && SONOSCD_EXITRD_SAFE_APPROVED != 1
#error SONOSCD_EXITRD_SAFE_APPROVED must be 0 or 1
#endif

#ifndef SONOSCD_EXITRD_DIAGNOSTIC
#define SONOSCD_EXITRD_DIAGNOSTIC 0
#endif
#if SONOSCD_EXITRD_DIAGNOSTIC != 0 && SONOSCD_EXITRD_DIAGNOSTIC != 1
#error SONOSCD_EXITRD_DIAGNOSTIC must be 0 or 1
#endif
#if SONOSCD_EXITRD_DIAGNOSTIC && SONOSCD_EXITRD_SAFE_APPROVED
#error Diagnostic display must never be combined with safe-approved
#endif

#define EXITRD_MAX_MOUNTS 512
#define EXITRD_MAX_LINE (64u * 1024u)
#define EXITRD_MAX_PASSES 16
struct mount_point { char path[PATH_MAX]; size_t length; };
static struct mount_point points[EXITRD_MAX_MOUNTS];

static bool is_oldroot(const char *name) {
    return strcmp(name, "/oldroot") == 0 || strncmp(name, "/oldroot/", 9) == 0;
}

/* Kernel mountinfo escapes space/tab/newline/backslash as octal triples. */
static int decode_mount(char *out, size_t cap, const char *encoded) {
    size_t used = 0;
    for (size_t i = 0; encoded[i]; i++) {
        unsigned char c = (unsigned char)encoded[i];
        if (c == '\\') {
            if (!encoded[i + 1] || !encoded[i + 2] || !encoded[i + 3] ||
                encoded[i + 1] < '0' || encoded[i + 1] > '7' ||
                encoded[i + 2] < '0' || encoded[i + 2] > '7' ||
                encoded[i + 3] < '0' || encoded[i + 3] > '7') return -1;
            c = (unsigned char)(((encoded[i + 1] - '0') << 6) |
                                ((encoded[i + 2] - '0') << 3) |
                                (encoded[i + 3] - '0'));
            i += 3;
            if (c == 0) return -1;
        }
        if (used + 1 >= cap) return -1;
        out[used++] = (char)c;
    }
    out[used] = '\0';
    return 0;
}

static int mounts_from_stream(FILE *stream, struct mount_point *out,
                              size_t capacity, size_t *count, bool *root_seen) {
    char *line = NULL;
    size_t linecap = 0;
    ssize_t len;
    *count = 0;
    *root_seen = false;
    while ((len = getline(&line, &linecap, stream)) >= 0) {
        if ((size_t)len > EXITRD_MAX_LINE) { errno = E2BIG; goto fail; }
        char *save = NULL;
        char *token = strtok_r(line, " ", &save);
        for (unsigned col = 1; col < 5; col++) {
            if (!token) { errno = EINVAL; goto fail; }
            token = strtok_r(NULL, " ", &save);
        }
        if (!token) { errno = EINVAL; goto fail; }
        char decoded[PATH_MAX];
        if (decode_mount(decoded, sizeof(decoded), token) != 0) {
            errno = EINVAL;
            goto fail;
        }
        if (is_oldroot(decoded)) {
            if (*count >= capacity) { errno = E2BIG; goto fail; }
            snprintf(out[*count].path, sizeof(out[*count].path), "%s", decoded);
            out[*count].length = strlen(decoded);
            if (strcmp(decoded, "/oldroot") == 0) *root_seen = true;
            (*count)++;
        }
    }
    if (ferror(stream)) { errno = EIO; goto fail; }
    free(line);
    return 0;
fail:
    free(line);
    return -1;
}

static int read_mounts(size_t *count, bool *root_seen, const char *path) {
    FILE *stream = fopen(path, "re");
    if (!stream) return -1;
    int result = mounts_from_stream(stream, points, EXITRD_MAX_MOUNTS, count, root_seen);
    int saved_errno = errno;
    fclose(stream);
    errno = saved_errno;
    return result;
}

static int deep_first(const void *left, const void *right) {
    const struct mount_point *a = left, *b = right;
    if (a->length < b->length) return 1;
    if (a->length > b->length) return -1;
    return strcmp(b->path, a->path);
}

static int release_oldroot(void) {
    size_t count = 0;
    bool root_seen = false;
    if (read_mounts(&count, &root_seen, "/proc/self/mountinfo") < 0) return -1;
    if (!root_seen) { errno = ENOENT; return -1; }
    for (unsigned pass = 0; pass < EXITRD_MAX_PASSES; pass++) {
        if (read_mounts(&count, &root_seen, "/proc/self/mountinfo") < 0) return -1;
        if (count == 0) return 0;
        qsort(points, count, sizeof(points[0]), deep_first);
        unsigned progress = 0;
        for (size_t i = 0; i < count; i++)
            if (umount2(points[i].path, 0) == 0) progress++;
        if (progress == 0) { errno = EBUSY; return -1; }
    }
    errno = EBUSY;
    return -1;
}

/* Conservative post-unmount mount inventory. This is NOT media durability proof.
 * All unknown filesystem types count as potential persistent backing.
 * Device holders and volatile hardware caches are not observable here. */
static bool volatile_fs(const char *name) {
    static const char *const types[] = {
        "tmpfs", "ramfs", "devtmpfs", "proc", "sysfs", "devpts",
        "cgroup", "cgroup2", "securityfs", "debugfs", "tracefs",
        "configfs", "pstore", "efivarfs", "mqueue", "hugetlbfs",
        "fusectl", "bpf", "autofs", "binfmt_misc", "nsfs"
    };
    for (size_t i = 0; i < sizeof(types) / sizeof(types[0]); i++)
        if (strcmp(name, types[i]) == 0) return true;
    return false;
}

static int audit_mounts(const char *path, size_t *persistent, size_t *oldroot) {
    FILE *stream = fopen(path, "re");
    if (!stream) return -1;
    char *line = NULL;
    size_t cap = 0;
    ssize_t len;
    *persistent = 0;
    *oldroot = 0;
    int result = -1;
    while ((len = getline(&line, &cap, stream)) >= 0) {
        if ((size_t)len > EXITRD_MAX_LINE) { errno = E2BIG; goto done; }
        char *save = NULL;
        char *token = strtok_r(line, " ", &save);
        char *mountpoint = NULL;
        char *fstype = NULL;
        for (unsigned col = 1; col <= 5; col++) {
            if (!token) { errno = EINVAL; goto done; }
            if (col == 5) mountpoint = token;
            token = strtok_r(NULL, " ", &save);
        }
        while (token && strcmp(token, "-") != 0)
            token = strtok_r(NULL, " ", &save);
        if (!token || !(fstype = strtok_r(NULL, " ", &save)) ||
            !strtok_r(NULL, " ", &save) ||
            !strtok_r(NULL, " \n", &save)) {
            errno = EINVAL;
            goto done;
        }
        char decoded[PATH_MAX];
        if (decode_mount(decoded, sizeof(decoded), mountpoint) != 0) {
            errno = EINVAL;
            goto done;
        }
        if (is_oldroot(decoded)) (*oldroot)++;
        if (!volatile_fs(fstype)) (*persistent)++;
    }
    if (ferror(stream)) { errno = EIO; goto done; }
    result = 0;
done:
    {
        int saved = errno;
        free(line);
        fclose(stream);
        errno = saved;
    }
    return result;
}

/* A passing mount inventory alone MUST NEVER authorize safe-to-unplug. */
static bool safe_to_unplug_allowed(size_t persistent, size_t oldroot) {
    (void)persistent;
    (void)oldroot;
    return false; /* no device-holder or durable-cache-flush proof yet */
}

static bool backend_matches(size_t size) {
    return shutdown_display_backend.width == SHUTDOWN_FRAME_WIDTH &&
           shutdown_display_backend.height == SHUTDOWN_FRAME_HEIGHT &&
           shutdown_display_backend.frame_bytes == size &&
           strcmp(shutdown_display_backend.pixel_format, SHUTDOWN_FRAME_FORMAT) == 0;
}

static void show_post_unmount(void) {
#if SONOSCD_EXITRD_DIAGNOSTIC
    /* Experimental verification only, NEVER a safe-to-unplug indicator. */
    const uint8_t *frame = shutdown_frame_diagnostic;
    const size_t length = sizeof(shutdown_frame_diagnostic);
#elif SONOSCD_EXITRD_SAFE_APPROVED
    const uint8_t *frame = shutdown_frame_safe;
    const size_t length = sizeof(shutdown_frame_safe);
#else
    /* Storage durability and OLED persistence are not hardware-verified. */
    const uint8_t *frame = shutdown_frame_final;
    const size_t length = sizeof(shutdown_frame_final);
#endif
    if (!backend_matches(length) || shutdown_display_backend.show(frame, length) != 0)
        fputs("exitrd: final OLED frame unavailable\n", stderr);
}

static int system_action(const char *verb, bool *poweroff) {
    *poweroff = false;
    if (strcmp(verb, "poweroff") == 0) { *poweroff = true; return LINUX_REBOOT_CMD_POWER_OFF; }
    if (strcmp(verb, "reboot") == 0) return LINUX_REBOOT_CMD_RESTART;
    if (strcmp(verb, "halt") == 0) return LINUX_REBOOT_CMD_HALT;
    /* Unknown modes, including kexec, must never be mistaken for poweroff. */
    fputs("exitrd: unsupported action, falling back to restart\n", stderr);
    return LINUX_REBOOT_CMD_RESTART;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--check") == 0) {
        printf("exitrd: offline check; backend=%s format=%s safe-approved=%d diagnostic=%d\n",
               shutdown_display_backend.name, shutdown_display_backend.pixel_format,
               SONOSCD_EXITRD_SAFE_APPROVED, SONOSCD_EXITRD_DIAGNOSTIC);
        return 0;
    }
    if (argc == 3 && strcmp(argv[1], "--probe-mountinfo") == 0) {
        size_t count;
        bool root_seen;
        int result = read_mounts(&count, &root_seen, argv[2]);
        if (result < 0) return 1;
        printf("oldroot_mounts=%zu oldroot_present=%d\n", count, root_seen);
        return 0;
    }
    if (argc == 3 && strcmp(argv[1], "--audit-mountinfo") == 0) {
        size_t persistent = 0, oldroot = 0;
        if (audit_mounts(argv[2], &persistent, &oldroot) != 0) return 1;
        printf("persistent_mounts=%zu oldroot_mounts=%zu safe-eligible=%d\n",
               persistent, oldroot, safe_to_unplug_allowed(persistent, oldroot));
        return 0;
    }
    if (getpid() != 1 || argc < 2) {
        fputs("exitrd: only executable as shutdown PID 1\n", stderr);
        return 2;
    }
    /* Console-only diagnostics for an externally captured shutdown log.
     * Do not write to the old root or claim storage safety. */
    fprintf(stderr, "sonoscd-exitrd: entered PID1 verb=%s safe-approved=%d\n",
            argv[1], SONOSCD_EXITRD_SAFE_APPROVED);
    bool poweroff;
    int action = system_action(argv[1], &poweroff);
    if (access("/proc/self/mountinfo", R_OK) != 0 &&
        mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) < 0 &&
        errno != EBUSY)
        perror("exitrd: proc mount");
    sync();
    if (release_oldroot() == 0) {
        fputs("sonoscd-exitrd: oldroot_unmount=success\n", stderr);
        size_t persistent = 0, oldroot = 0;
        if (audit_mounts("/proc/self/mountinfo", &persistent, &oldroot) == 0) {
            fprintf(stderr,
                    "sonoscd-exitrd: residual_persistent_mounts=%zu residual_oldroot_mounts=%zu safe-eligible=0\n",
                    persistent, oldroot);
        } else {
            fprintf(stderr, "sonoscd-exitrd: mount_audit=failed safe-eligible=0 errno=%d\n", errno);
        }
        sync();
        if (poweroff) show_post_unmount();
    } else {
        fprintf(stderr, "sonoscd-exitrd: oldroot_unmount=failed errno=%d (%s)\n", errno, strerror(errno));
        sync();
    }
    /* No filesystem writes intentionally issued after the post-unmount frame. */
    syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, action, NULL);
    perror("exitrd: reboot syscall failed");
    /* PID 1 must not return with its previous root already unmounted. */
    for (;;) pause();
}

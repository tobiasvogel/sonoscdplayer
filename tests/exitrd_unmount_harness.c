/* Hardware-independent integration harness for the production unmount routine.
 * Never install as /run/initramfs/shutdown.
 * Must run inside the dedicated chroot fixture created by the test script.
 */
#define main sonoscd_exitrd_production_main
#include "../src/shutdown/exitrd.c"
#undef main

int main(void) {
    if (getpid() == 1) {
        fputs("refusing execution as PID 1\n", stderr);
        return 2;
    }
    if (access("/exitrd-test-fixture", R_OK) != 0 ||
        access("/oldroot/exitrd-test-fixture", R_OK) != 0) {
        fputs("refusing execution outside isolated mount fixture\n", stderr);
        return 2;
    }
    size_t before = 0, after = 0;
    bool root_before = false, root_after = false;
    if (read_mounts(&before, &root_before, "/proc/self/mountinfo") != 0 ||
        !root_before || before != 3) {
        fputs("unexpected fixture mount structure\n", stderr);
        return 2;
    }
    printf("before=%zu oldroot_present=%d\n", before, root_before);
    if (release_oldroot() != 0) {
        perror("release_oldroot");
        return 1;
    }
    if (read_mounts(&after, &root_after, "/proc/self/mountinfo") != 0 ||
        after != 0 || root_after) {
        fputs("oldroot mounts remain\n", stderr);
        return 1;
    }
    printf("after=%zu oldroot_present=%d\n", after, root_after);
    return 0;
}

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
    /* Hold an oldroot child as our cwd: the production unmount routine must
     * refuse to report success while any oldroot mount is still busy.
     * The test never calls show_post_unmount or the reboot syscall.
     */
    if (chdir("/oldroot/run/cache") != 0) {
        perror("chdir busy fixture");
        return 2;
    }
    if (release_oldroot() == 0) {
        fputs("unexpected success with busy oldroot mount\n", stderr);
        return 1;
    }
    if (chdir("/") != 0) {
        perror("chdir fixture root");
        return 2;
    }
    size_t blocked_count = 0;
    bool blocked_root = false;
    if (read_mounts(&blocked_count, &blocked_root, "/proc/self/mountinfo") != 0 ||
        !blocked_root || blocked_count == 0) {
        fputs("busy oldroot not retained\n", stderr);
        return 1;
    }
    printf("busy_mount_rejected=1 oldroot_still_present=1\n");
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

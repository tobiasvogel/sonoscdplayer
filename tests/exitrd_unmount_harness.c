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
    /* Audit is executed from production exitrd.c, not synthetic CLI values. */
    size_t persistent = 0, oldroot = 0;
    struct shutdown_evidence blocked = evidence_start();
    if (audit_mounts("/proc/self/mountinfo", &persistent, &oldroot) != 0 ||
        oldroot != 3 || persistent != 0) {
        fputs("unexpected pre-unmount audit\n", stderr);
        return 1;
    }
    evidence_audit(&blocked, true, persistent, oldroot);
    if (blocked.audit != AUDIT_SKIPPED || evidence_safe(&blocked)) {
        fputs("audit ran before successful unmount\n", stderr);
        return 1;
    }

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
    evidence_unmount(&blocked, false);
    evidence_audit(&blocked, true, persistent, oldroot);
    evidence_power_request(&blocked);
    if (blocked.unmount != UNMOUNT_FAILED ||
        blocked.audit != AUDIT_SKIPPED || evidence_safe(&blocked)) {
        fputs("busy mount failure incorrectly eligible\n", stderr);
        return 1;
    }
    printf("busy_mount_rejected=1 oldroot_still_present=1\n");
    fputs("busy_evidence: ", stdout);
    evidence_print(stdout, &blocked);
    if (release_oldroot() != 0) {
        perror("release_oldroot");
        return 1;
    }
    if (read_mounts(&after, &root_after, "/proc/self/mountinfo") != 0 ||
        after != 0 || root_after) {
        fputs("oldroot mounts remain\n", stderr);
        return 1;
    }
    struct shutdown_evidence succeeded = evidence_start();
    evidence_unmount(&succeeded, true);
    persistent = 99;
    oldroot = 99;
    if (audit_mounts("/proc/self/mountinfo", &persistent, &oldroot) != 0 ||
        persistent != 0 || oldroot != 0) {
        fputs("post-unmount audit did not clear mounts\n", stderr);
        return 1;
    }
    evidence_audit(&succeeded, true, persistent, oldroot);
    evidence_power_request(&succeeded);
    if (succeeded.unmount != UNMOUNT_OK ||
        succeeded.audit != AUDIT_CLEAN || evidence_safe(&succeeded)) {
        fputs("clean audit incorrectly eligible\n", stderr);
        return 1;
    }
    evidence_power_returned(&succeeded);
    if (succeeded.power != POWER_RETURNED_ERROR || evidence_safe(&succeeded)) {
        fputs("returned syscall incorrectly eligible\n", stderr);
        return 1;
    }
    printf("after=%zu oldroot_present=%d\n", after, root_after);
    printf("audit_after persistent=%zu oldroot=%zu safe-eligible=0\n",
           persistent, oldroot);
    fputs("returned_power_evidence: ", stdout);
    evidence_print(stdout, &succeeded);
    return 0;
}

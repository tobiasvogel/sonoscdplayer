# Experimental exitrd one-boot staging

`tools/exitrd_once.py` is **read-only by default**. It does not install a systemd service or trigger shutdown.

The explicit activation option must not be used until a supervised recovery plan has been agreed. Activation temporarily mounts an executable RAM filesystem on `/run/initramfs`, preserving copies of the existing fsck files and requiring both an expected SHA-256 digest and an acknowledgement token. The original files remain underneath the mount; the `--deactivate` option restores visibility before shutdown. The mount is not persistent across boots.

**Fallback change (source only):** The final-screen shell and static C hooks now render the `final` frame even when exitrd is staged; a working exitrd may repaint after `/oldroot` is released. The already-installed static hook on the Pi is not changed by pulling source. Rebuild, verify, and explicitly install the revised C binary before a live test. A fallback image is not proof of filesystem safety. The exitrd's `safe-approved` flag stays `0`. Neither PID-1 handover nor real post-unmount display persistence has been proven.

Only after offline checks and an isolated staging/rollback test should a supervised live shutdown be considered. Do not arm during normal operation.

The default-mode and activation-gating regression cases are included in `tests/test_exitrd_preflight.py`. Run those tests and the shell/static hook tests before any installation. A successful source-level test does not replace an installed-hook hash check.

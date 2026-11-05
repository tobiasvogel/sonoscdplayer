# Experimental exitrd one-boot staging

`tools/exitrd_once.py` is **read-only by default**. It does not install a systemd service or trigger shutdown.

The explicit activation option must not be used until a supervised recovery plan has been agreed. Activation temporarily mounts an executable RAM filesystem on `/run/initramfs`, preserving copies of the existing fsck files and requiring both an expected SHA-256 digest and an acknowledgement token. The original files remain underneath the mount; the `--deactivate` option restores visibility before shutdown. The mount is not persistent across boots.

**Known risk:** The existing final-screen hook skips rendering whenever an executable exitrd is installed. A failed systemd handover therefore may not show the expected final screen. The executable's `safe-approved` flag stays `0`. Neither PID-1 handover nor real post-unmount display persistence has been proven.

Only after offline checks and an isolated staging/rollback test should a supervised live shutdown be considered. Do not arm during normal operation.

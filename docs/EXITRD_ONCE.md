# Experimental exitrd one-boot staging

`tools/exitrd_once.py` is **read-only by default**. It does not install a systemd service or trigger shutdown.

The explicit activation option must not be used until a supervised recovery plan has been agreed. Activation temporarily mounts an executable RAM filesystem on `/run/initramfs`, preserving copies of the existing fsck files and requiring both an expected SHA-256 digest and an acknowledgement token. The original files remain underneath the mount; the `--deactivate` option restores visibility before shutdown. The mount is not persistent across boots.

**Fallback change (source only):** The final-screen shell and static C hooks now render the `final` frame even when exitrd is staged; a working exitrd may repaint after `/oldroot` is released. The already-installed static hook on the Pi is not changed by pulling source. Rebuild, verify, and explicitly install the revised C binary before a live test. A fallback image is not proof of filesystem safety. The exitrd's `safe-approved` flag stays `0`. Neither PID-1 handover nor real post-unmount display persistence has been proven.

Only after offline checks and an isolated staging/rollback test should a supervised live shutdown be considered. Do not arm during normal operation.

The default-mode and activation-gating regression cases are included in `tests/test_exitrd_preflight.py`. Run those tests and the shell/static hook tests before any installation. A successful source-level test does not replace an installed-hook hash check.

## PID-1 live-handover readiness (not yet activated)

The staging helper now creates the required `oldroot`, `dev`, `proc`, `sys` and `run` directory targets in the separate RAM root. Exitrd reports its PID-1 entry and oldroot unmount result on the shutdown console (`sonoscd-exitrd: ...`). Such console messages **do not persist automatically across poweroff**; record them through an already configured external console before claiming the handover was verified. The pre-exitrd final OLED frame is a fallback, not diagnostic proof.

Before the first live handover, rebuild `build/exitrd/shutdown`, run the offline tests and re-run isolated staging/rollback against the new build. Do not use an old digest for activation. Do not activate `safe-approved=1`.

## Opt-in OLED visual proof for first supervised poweroff

The default `sh tools/build_exitrd.sh` produces `build/exitrd/shutdown`, with `diagnostic=0`. It retains the usual final frame after successful `/oldroot` unmount.

For an **offline-only** diagnostic build use:

```sh
EXITRD_DIAGNOSTIC=1 sh tools/build_exitrd.sh
build/exitrd-diagnostic/shutdown --check
```

The second binary is stored separately at `build/exitrd-diagnostic/shutdown`. Its generated framebuffer has a white outline, contrasting bars and the labels `EXITRD / TEST / OK`. It is neither a confirmation of data durability nor a `Safe to unplug` screen. The build enforces `safe-approved=0` and rejects a diagnostic/safe-approved combination. The graphic is only shown by the PID-1 exitrd after `release_oldroot()` succeeds during poweroff; the previously installed static final hook should remain the fallback.

The image is an **in-band observation only**: seeing it is evidence that the program reached the post-unmount rendering path, not independent proof of physical SD-card safety or of the exact state of every storage device. Record a video of the OLED during the first supervised test. With no serial console, the precise PID-1 console diagnostics will not be preserved for audit. If the diagnostic frame does not appear, do not infer why from the OLED alone.

**No automatic activation:** the build and tests cannot arm `/run/initramfs/shutdown`, trigger `poweroff`, or touch the installed fallback hook. Always validate new binary hashes and the latest staging preflight before deciding on a separately approved live test. Do not set `safe-approved=1`.

## 2026-11-05 supervised Raspberry Pi poweroff acceptance

**Observed by operator:** With the separately built `diagnostic=1`, `safe-approved=0` binary staged under `/run/initramfs/shutdown`, the existing late C hook briefly displayed the `final` frame. The OLED then changed to `EXITRD / TEST / OK` and held this frame. The Raspberry Pi powered off normally, booted again, and the normal OLED interface returned.

**Interpretation:** The experimental exitrd reached its post-`release_oldroot()` success/display path during real shutdown; SPI/GPIO remained usable late enough to render the distinct test frame. The normal start after the next boot is also confirmed. This is one supervised successful end-to-end test, **not** an independent proof of every storage flush or a blanket safe-to-unplug authorization. There was no external serial console to capture PID-1 diagnostics. `SONOSCD_EXITRD_SAFE_APPROVED=0` remains mandatory.

**Next milestone:** Preserve the evidence and convert from the temporary test frame to a clearly non-safety normal final image only after controlled regression. Any future `Safe to unplug` label requires separate documented criteria and approval. Neither this result nor any source commit automatically enables persistent exitrd staging.
